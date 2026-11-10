"""EDGAR access: 13F filing lists, cover pages and information tables."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .net import Client

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/"
FORMS = ("13F-HR", "13F-HR/A")
SUBMISSIONS_TTL = 3600
# Filings made before this date report value in thousands of USD, later ones in whole USD.
WHOLE_DOLLARS_SINCE = "2023-01-03"


class EdgarError(Exception):
    pass


@dataclass(frozen=True)
class Filing:
    cik: int
    accession: str
    form: str
    filed: str
    period: str

    @property
    def folder_url(self) -> str:
        return ARCHIVE.format(cik=self.cik, folder=self.accession.replace("-", ""))

    @property
    def index_url(self) -> str:
        return f"{self.folder_url}{self.accession}-index.htm"


@dataclass(frozen=True)
class Row:
    name: str
    title: str
    cusip: str
    value: int  # USD
    shares: int
    share_type: str  # SH or PRN
    put_call: str  # "", "Put" or "Call"


@dataclass
class Cover:
    report_type: str = ""
    amendment_type: str = ""
    entry_total: Optional[int] = None
    value_total: Optional[int] = None
    confidential_omitted: bool = False


@dataclass
class Holdings:
    """All rows for one period, after applying amendments."""
    period: str
    filings: List[Filing]
    rows: List[Row]
    checks: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def verified(self) -> bool:
        return all(c.endswith("ok") for c in self.checks) and bool(self.checks)


def filings(client: Client, cik: int, ttl: float = SUBMISSIONS_TTL) -> List[Filing]:
    """13F-HR filings and amendments, newest first."""
    recent = client.get_json(SUBMISSIONS.format(cik=cik), ttl)["filings"]["recent"]
    found = [
        Filing(cik, recent["accessionNumber"][i], form, recent["filingDate"][i], recent["reportDate"][i])
        for i, form in enumerate(recent["form"])
        if form in FORMS
    ]
    return sorted(found, key=lambda f: (f.filed, f.accession), reverse=True)


def periods(found: List[Filing]) -> List[str]:
    return sorted({f.period for f in found}, reverse=True)


def period_filings(found: List[Filing], period: str) -> List[Filing]:
    """The latest original 13F-HR for a period plus the amendments filed after it, oldest first."""
    same = sorted((f for f in found if f.period == period), key=lambda f: (f.filed, f.accession))
    originals = [f for f in same if f.form == "13F-HR"]
    if not originals:
        raise EdgarError(f"No original 13F-HR for period {period}")
    base = originals[-1]
    return [base] + [f for f in same if f.form == "13F-HR/A" and (f.filed, f.accession) > (base.filed, base.accession)]


def holdings(client: Client, found: List[Filing], period: str) -> Holdings:
    result = Holdings(period=period, filings=[], rows=[])
    for filing in period_filings(found, period):
        cover, rows = documents(client, filing)
        if filing.form == "13F-HR/A" and cover.amendment_type == "RESTATEMENT":
            result.rows, result.filings, result.checks = [], [], []
        elif filing.form == "13F-HR/A" and cover.amendment_type != "NEW HOLDINGS":
            result.notes.append(f"Skipped amendment {filing.accession} of unknown type '{cover.amendment_type}'.")
            continue
        result.rows.extend(rows)
        result.filings.append(filing)
        result.checks.append(_check(filing, cover, rows))
        if cover.confidential_omitted:
            result.notes.append(f"{filing.accession} omits some positions under confidential treatment.")
        if "NOTICE" in cover.report_type.upper():
            result.notes.append(f"{filing.accession} is a notice: another manager reports these holdings.")
    return result


def documents(client: Client, filing: Filing) -> Tuple[Cover, List[Row]]:
    """Find the cover page and information table among the filing's XML files."""
    items = client.get_json(filing.folder_url + "index.json")["directory"]["item"]
    cover, rows = None, []
    for name in (i["name"] for i in items if i["name"].lower().endswith(".xml")):
        root = ET.fromstring(client.get(filing.folder_url + name))
        tag = _local(root.tag)
        if tag == "edgarSubmission":
            cover = parse_cover(root)
        elif tag == "informationTable":
            rows = parse_table(root, filing.filed)
    if cover is None:
        raise EdgarError(f"No cover page XML in {filing.index_url}")
    return cover, rows


def parse_cover(root: ET.Element) -> Cover:
    entries = _text(root, "tableEntryTotal")
    value = _text(root, "tableValueTotal")
    return Cover(
        report_type=_text(root, "reportType"),
        amendment_type=_text(root, "amendmentType").upper(),
        entry_total=int(float(entries)) if entries else None,
        value_total=int(float(value)) if value else None,
        confidential_omitted=_text(root, "isConfidentialOmitted").lower() in ("true", "y", "yes"),
    )


def parse_table(root: ET.Element, filed: str) -> List[Row]:
    scale = 1000 if filed < WHOLE_DOLLARS_SINCE else 1
    rows = []
    for info in (el for el in root.iter() if _local(el.tag) == "infoTable"):
        put_call = _text(info, "putCall").capitalize()
        rows.append(Row(
            name=_text(info, "nameOfIssuer"),
            title=_text(info, "titleOfClass"),
            cusip=_text(info, "cusip").upper(),
            value=round(float(_text(info, "value") or 0) * scale),
            shares=round(float(_text(info, "sshPrnamt") or 0)),
            share_type=_text(info, "sshPrnamtType").upper() or "SH",
            put_call=put_call if put_call in ("Put", "Call") else "",
        ))
    return rows


def _check(filing: Filing, cover: Cover, rows: List[Row]) -> str:
    """Compare parsed rows with the totals on the filing's summary page."""
    value = sum(r.value for r in rows)
    if cover.entry_total is None or cover.value_total is None:
        return f"{filing.accession}: no summary totals to check against"
    if filing.filed < WHOLE_DOLLARS_SINCE:
        expected = cover.value_total * 1000
    else:
        expected = cover.value_total
    if len(rows) != cover.entry_total or abs(value - expected) > max(1000, expected * 0.001):
        return (f"{filing.accession}: mismatch, parsed {len(rows)} rows worth {value:,} USD, "
                f"summary says {cover.entry_total} rows worth {expected:,} USD")
    return f"{filing.accession}: {len(rows)} rows and {value:,} USD match the summary page, ok"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(el: ET.Element, name: str) -> str:
    for child in el.iter():
        if _local(child.tag) == name:
            return (child.text or "").strip()
    return ""
