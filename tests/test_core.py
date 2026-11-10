"""Offline tests: parsing, comparison, sectors and the MCP protocol. Run with python3 -m unittest."""
import io
import json
import unittest
import xml.etree.ElementTree as ET

from edgar13f import edgar, mcp
from edgar13f.analysis import build_book, compare, cross_fund, FundView, mark_listings
from edgar13f.config import Fund
from edgar13f.edgar import Filing, Holdings, Row
from edgar13f.securities import derivative, sector

TABLE = """<?xml version="1.0"?>
<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <infoTable><nameOfIssuer>NVIDIA CORP</nameOfIssuer><titleOfClass>COM</titleOfClass><cusip>67066G104</cusip>
    <value>2000</value><shrsOrPrnAmt><sshPrnamt>10</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt></infoTable>
  <infoTable><nameOfIssuer>NVIDIA CORP</nameOfIssuer><titleOfClass>COM</titleOfClass><cusip>67066G104</cusip>
    <value>500</value><shrsOrPrnAmt><sshPrnamt>5</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
    <putCall>PUT</putCall></infoTable>
  <infoTable><nameOfIssuer>SOME CO</nameOfIssuer><titleOfClass>NOTE 1%</titleOfClass><cusip>123456AB1</cusip>
    <value>300</value><shrsOrPrnAmt><sshPrnamt>300</sshPrnamt><sshPrnamtType>PRN</sshPrnamtType></shrsOrPrnAmt></infoTable>
</informationTable>"""

COVER = """<edgarSubmission xmlns="http://www.sec.gov/edgar/thirteenffiler"><formData>
  <coverPage><amendmentInfo><amendmentType>RESTATEMENT</amendmentType></amendmentInfo>
  <reportType>13F HOLDINGS REPORT</reportType></coverPage>
  <summaryPage><tableEntryTotal>3</tableEntryTotal><tableValueTotal>2800</tableValueTotal></summaryPage>
</formData></edgarSubmission>"""

SECS = {
    "A": {"ticker": "AAA", "name": "Alpha", "sector": "Semiconductors", "cik": 1},
    "B": {"ticker": "BBB", "name": "Beta", "sector": "Software and internet", "cik": 2},
    "C": {"ticker": "CCC", "name": "Gamma", "sector": "Software and internet", "cik": 3},
}


def row(cusip, shares, value):
    return Row(cusip, "COM", cusip, value, shares, "SH", "")


class ParseTests(unittest.TestCase):
    def test_table_rows_and_scaling(self):
        rows = edgar.parse_table(ET.fromstring(TABLE), filed="2026-08-14")
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0].cusip, "67066G104")
        self.assertEqual(rows[1].put_call, "Put")
        self.assertEqual(rows[2].share_type, "PRN")
        old = edgar.parse_table(ET.fromstring(TABLE), filed="2022-11-14")
        self.assertEqual(old[0].value, 2_000_000)

    def test_cover_and_check(self):
        cover = edgar.parse_cover(ET.fromstring(COVER))
        self.assertEqual((cover.amendment_type, cover.entry_total, cover.value_total), ("RESTATEMENT", 3, 2800))
        rows = edgar.parse_table(ET.fromstring(TABLE), filed="2026-08-14")
        filing = Filing(1, "0000000001-26-000001", "13F-HR", "2026-08-14", "2026-06-30")
        self.assertTrue(edgar._check(filing, cover, rows).endswith("ok"))
        self.assertIn("mismatch", edgar._check(filing, cover, rows[:2]))

    def test_period_filings_takes_latest_original_and_later_amendments(self):
        found = [
            Filing(1, "a1", "13F-HR/A", "2026-05-01", "2026-03-31"),
            Filing(1, "o1", "13F-HR", "2026-05-10", "2026-03-31"),
            Filing(1, "a2", "13F-HR/A", "2026-06-01", "2026-03-31"),
        ]
        self.assertEqual([f.accession for f in edgar.period_filings(found, "2026-03-31")], ["o1", "a2"])


class AnalysisTests(unittest.TestCase):
    def test_book_keeps_options_and_notes_outside_equity(self):
        rows = edgar.parse_table(ET.fromstring(TABLE), filed="2026-08-14")
        book = build_book(rows, {})
        self.assertEqual((book.total, len(book.positions)), (2000, 1))
        self.assertEqual(book.options["Put"], (1, 500))
        self.assertEqual(book.debt, (1, 300))

    def test_compare_statuses_and_trade_values(self):
        prior = build_book([row("A", 100, 1000), row("B", 50, 500)], SECS)
        book = build_book([row("A", 150, 1800), row("C", 10, 200)], SECS)
        changes = {c.position.ticker: c for c in compare(book, prior)}
        self.assertEqual(changes["AAA"].status, "increase")
        self.assertEqual(changes["AAA"].trade_value, 600)  # 50 shares at 12 USD
        self.assertEqual(changes["BBB"].status, "exit")
        self.assertEqual(changes["CCC"].status, "new")

    def test_listing_in_quarter_is_not_buying(self):
        secs = {"A": {**SECS["A"], "listed": "2026-06-10"}, "B": {**SECS["B"], "listed": "2019-01-01"}}
        book = build_book([row("A", 10, 100), row("B", 10, 100)], secs)
        changes = compare(book, build_book([], secs))
        mark_listings(changes, "2026-03-31", "2026-06-30")
        flags = {c.position.ticker: c.listed_in_quarter for c in changes}
        self.assertEqual(flags, {"AAA": True, "BBB": False})

    def test_split_is_flagged(self):
        prior = build_book([row("A", 100, 10_000)], SECS)
        book = build_book([row("A", 1000, 11_000)], SECS)
        (change,) = compare(book, prior)
        self.assertTrue(change.split_hint)

    def test_cross_fund_groups_by_issuer(self):
        views = []
        for name in ("One", "Two", "Three"):
            prior = build_book([row("A", 100, 1000)], SECS)
            book = build_book([row("A", 200, 2000), row("B", 10, 100)], SECS)
            views.append(FundView(Fund(name.lower(), name, name, 1, "", "", ""),
                                  Holdings("2026-06-30", [], []), None, book, prior, compare(book, prior)))
        cf = cross_fund(views)
        self.assertEqual(cf.held[0][0], "AAA")
        self.assertEqual(len(cf.held[0][1]), 3)
        self.assertEqual({label for label, _, _ in cf.bought}, {"AAA", "BBB"})


class SecurityTests(unittest.TestCase):
    def test_sector_mapping(self):
        self.assertEqual(sector(3674, None), "Semiconductors")
        self.assertEqual(sector(7372, None), "Software and internet")
        self.assertEqual(sector(6798, None), "Real estate")
        self.assertEqual(sector(None, "ETP"), "Funds and ETFs")
        self.assertEqual(sector(None, None), "Unclassified")

    def test_derivative_titles(self):
        self.assertEqual(derivative("W EXP 12/1/202"), "warrants")
        self.assertEqual(derivative("*W EXP 07/01/202"), "warrants")
        self.assertEqual(derivative("COM CL A"), "")


class McpTests(unittest.TestCase):
    def test_protocol_round_trip(self):
        class FakeApp:
            funds = [Fund("demo", "Demo Fund", "Demo LLC", 42, "Berlin", "Ann", "Tech")]

        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "list_funds", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 4, "method": "nope"},
        ]
        out = io.StringIO()
        mcp.serve(FakeApp(), io.StringIO("\n".join(json.dumps(r) for r in requests) + "\n"), out)
        replies = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2, 3, 4])
        self.assertEqual(replies[0]["result"]["protocolVersion"], "2025-06-18")
        self.assertIn("get_report_data", [t["name"] for t in replies[1]["result"]["tools"]])
        self.assertIn("Demo Fund", replies[2]["result"]["content"][0]["text"])
        self.assertEqual(replies[3]["error"]["code"], -32601)


if __name__ == "__main__":
    unittest.main()
