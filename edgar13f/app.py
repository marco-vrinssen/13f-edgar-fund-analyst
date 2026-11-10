"""The shared core behind the CLI and the MCP server: one object that loads, compares and tracks filings."""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import edgar
from .analysis import FundView, build_book, compare, mark_listings
from .config import Fund, Settings, find_fund, load_funds, load_settings, openfigi_key, require_user_agent
from .edgar import Filing
from .net import Client, HttpError
from .securities import Resolver

SHARES_OUTSTANDING = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/dei/EntityCommonStockSharesOutstanding.json"


class App:
    def __init__(self, settings: Optional[Settings] = None, funds: Optional[List[Fund]] = None):
        self.settings = settings or load_settings()
        self.funds = funds or load_funds()
        self.client = Client(require_user_agent(self.settings), self.settings.data_dir / "cache")
        self.resolver = Resolver(self.client, self.settings.data_dir / "securities.json", openfigi_key())
        self.state_path = self.settings.data_dir / "state.json"

    def fund(self, query: str) -> Fund:
        return find_fund(self.funds, query)

    def filings(self, fund: Fund, ttl: float = edgar.SUBMISSIONS_TTL) -> List[Filing]:
        found = edgar.filings(self.client, fund.cik, ttl)
        if not found:
            raise edgar.EdgarError(f"{fund.name} (CIK {fund.cik}) has no 13F-HR filings on EDGAR.")
        return found

    def view(self, fund: Fund, period: Optional[str] = None) -> FundView:
        """Holdings for a period (default: latest) compared with the period before it."""
        found = self.filings(fund)
        periods = edgar.periods(found)
        period = period or periods[0]
        if period not in periods:
            raise edgar.EdgarError(f"{fund.name} has no 13F for {period}. Periods: {', '.join(periods[:8])}")
        current = edgar.holdings(self.client, found, period)
        older = [p for p in periods if p < period]
        previous = edgar.holdings(self.client, found, older[0]) if older else None
        rows = current.rows + (previous.rows if previous else [])
        securities = self.resolver.resolve({r.cusip: (r.name, r.title) for r in rows})
        book = build_book(current.rows, securities)
        prior = build_book(previous.rows, securities) if previous else None
        view = FundView(fund, current, previous, book, prior, compare(book, prior))
        if previous:
            mark_listings(view.changes, previous.period, current.period)
            self._check_splits(view)
        return view

    def _check_splits(self, view: FundView) -> None:
        """A share count that looks split-like is a split only if the company's shares outstanding moved alike."""
        for ch in view.changes:
            if not (ch.split_hint and ch.position.cik):
                continue
            moved = self._outstanding_ratio(ch.position.cik, view.previous.period, view.current.period)
            if moved is not None:
                ch.split_hint = abs(moved / (ch.now.shares / ch.before.shares) - 1) < 0.15
                ch.split_confirmed = ch.split_hint

    def _outstanding_ratio(self, cik: int, before: str, after: str) -> Optional[float]:
        try:
            facts = self.client.get_json(SHARES_OUTSTANDING.format(cik=cik), ttl=7 * 86400)["units"]["shares"]
        except (HttpError, KeyError):
            return None
        old = [f for f in facts if f["end"] <= before]
        new = [f for f in facts if f["end"] >= after]
        if not old or not new:
            return None
        start = max(old, key=lambda f: f["end"])["val"]
        return min(new, key=lambda f: f["end"])["val"] / start if start else None

    def views(self) -> List[FundView]:
        result = []
        for fund in self.funds:
            print(f"Loading {fund.name}", file=sys.stderr)
            result.append(self.view(fund))
        return result

    def latest_fingerprints(self) -> Dict[str, Tuple[str, List[str]]]:
        """Per fund: the latest period and the accessions that make up its holdings."""
        result = {}
        for fund in self.funds:
            found = self.filings(fund, ttl=0)
            period = edgar.periods(found)[0]
            result[str(fund.cik)] = (period, [f.accession for f in edgar.period_filings(found, period)])
        return result

    def check(self) -> List[Tuple[Fund, str, List[str]]]:
        """Funds whose latest 13F differs from the one in the last report."""
        reported = self._state().get("reported", {})
        fresh = []
        for cik, (period, accessions) in self.latest_fingerprints().items():
            if reported.get(cik, {}).get("accessions") != accessions:
                fund = next(f for f in self.funds if str(f.cik) == cik)
                fresh.append((fund, period, accessions))
        return fresh

    def mark_reported(self) -> Dict[str, Tuple[str, List[str]]]:
        prints = self.latest_fingerprints()
        state = self._state()
        state["reported"] = {cik: {"period": p, "accessions": a} for cik, (p, a) in prints.items()}
        state["updated"] = dt.datetime.now().isoformat(timespec="seconds")
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state, indent=1))
        return prints

    def reported_ciks(self) -> Dict[str, str]:
        return {cik: v.get("period", "") for cik, v in self._state().get("reported", {}).items()}

    def note_path(self, title: Optional[str] = None) -> Path:
        if not self.settings.vault_dir:
            raise edgar.EdgarError("Set vault_dir in config.local.json or EDGAR13F_VAULT_DIR to write notes.")
        title = title or self.settings.note_title.format(date=dt.date.today().isoformat())
        return self.settings.vault_dir / self.settings.note_folder / f"{title}.md"

    def write_note(self, markdown: str, title: Optional[str] = None, force: bool = False) -> Path:
        path = self.note_path(title)
        if path.exists() and not force:
            raise edgar.EdgarError(f"{path.name} already exists. Pass --force to replace it.")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown if markdown.endswith("\n") else markdown + "\n")
        return path

    def _state(self) -> dict:
        return json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
