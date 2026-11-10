"""Map CUSIPs to tickers, company names and SIC-based sectors, cached in one JSON file."""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .net import Client

OPENFIGI = "https://api.openfigi.com/v3/mapping"
SEC_TICKERS = "https://www.sec.gov/files/company_tickers_exchange.json"
SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
TICKERS_TTL = 7 * 86400
# OpenFIGI without a key: 25 requests a minute, 10 CUSIPs each. With a key: 25 per 6 seconds, 100 each.
FIGI_LIMITS = {False: (10, 2.5), True: (100, 0.25)}
# OpenFIGI gets a neutral User-Agent. The contact address is meant for SEC only.
FIGI_AGENT = "13f-edgar-fund-analyst"
LISTING_FORMS = {"8-A12B", "CERT"}  # exchange registration and certification
REGISTRATION_FORMS = {"S-1", "S-1/A", "F-1", "F-1/A", "10-12B", "10-12B/A"}  # IPO, foreign IPO, spin-off
FUND_TYPES = {"ETP", "Closed-End Fund", "Open-End Fund", "Mutual Fund", "Unit Trust"}

# First match wins. SIC codes are coarse, so these sectors are approximations.
SECTORS: List[Tuple[int, int, str]] = [
    (3674, 3674, "Semiconductors"),
    (3559, 3559, "Semiconductor equipment and machinery"),
    (7370, 7379, "Software and internet"),
    (3570, 3579, "Hardware and electronics"),
    (3600, 3699, "Hardware and electronics"),
    (3812, 3812, "Aerospace and defense"),
    (3720, 3729, "Aerospace and defense"),
    (3760, 3769, "Aerospace and defense"),
    (3820, 3829, "Hardware and electronics"),
    (3711, 3716, "Autos and mobility"),
    (2830, 2836, "Healthcare"),
    (3840, 3851, "Healthcare"),
    (5122, 5122, "Healthcare"),
    (6324, 6324, "Healthcare"),
    (8000, 8099, "Healthcare"),
    (8731, 8734, "Healthcare"),
    (6500, 6553, "Real estate"),
    (6798, 6798, "Real estate"),
    (6000, 6799, "Financials"),
    (4800, 4899, "Media and telecom"),
    (7800, 7849, "Media and telecom"),
    (2700, 2799, "Media and telecom"),
    (5000, 5999, "Retail and consumer"),
    (7900, 7999, "Retail and consumer"),
    (4900, 4999, "Energy and utilities"),
    (1300, 1399, "Energy and utilities"),
    (2900, 2999, "Energy and utilities"),
    (1000, 1499, "Materials"),
    (4000, 4799, "Transport and logistics"),
    (2000, 3999, "Industrials and manufacturing"),
    (1500, 1799, "Industrials and manufacturing"),
    (7000, 8999, "Business and consumer services"),
]
DERIVATIVES = [
    (re.compile(r"^\*?W\b|WARRANT|\bWTS?\b"), "warrants"),
    (re.compile(r"\bRIGHTS?\b"), "rights"),
    (re.compile(r"\bUNITS?\b"), "units"),
]
SUFFIXES = {
    "INC", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED", "PLC", "HOLDINGS", "HOLDING", "GROUP",
    "SA", "NV", "AG", "SE", "LP", "LLC", "THE", "COR", "HLDG", "HLDGS", "HLDNGS", "INTL", "CL", "CLASS", "A", "B", "C", "COM", "NEW", "ADR", "SPONSORED",
}


def sector(sic: Optional[int], figi_type: Optional[str]) -> str:
    if figi_type in FUND_TYPES:
        return "Funds and ETFs"
    if sic:
        for low, high, label in SECTORS:
            if low <= sic <= high:
                return label
    return "Unclassified"


class Resolver:
    """Resolve CUSIPs once and remember them. Unresolved CUSIPs are remembered too."""

    def __init__(self, client: Client, path: Path, figi_key: Optional[str] = None):
        self.client = client
        self.path = path
        self.figi_key = figi_key
        self.cache: Dict[str, dict] = json.loads(path.read_text()) if path.exists() else {}
        self._tickers: Optional[Dict[str, Tuple[int, str]]] = None
        self._names: Optional[Dict[str, Tuple[int, str, str]]] = None

    def resolve(self, issuers: Dict[str, Tuple[str, str]]) -> Dict[str, dict]:
        """issuers maps CUSIP to the issuer name and title of class in the filing."""
        missing = sorted(c for c in issuers if c not in self.cache)
        batch, interval = FIGI_LIMITS[bool(self.figi_key)]
        self.client.intervals["api.openfigi.com"] = interval
        for start in range(0, len(missing), batch):
            chunk = missing[start:start + batch]
            if start == 0 or start % (batch * 10) == 0:
                print(f"Mapping CUSIPs {start + 1}-{min(start + batch * 10, len(missing))} of {len(missing)}", file=sys.stderr)
            figi = self._openfigi(chunk)
            for cusip in chunk:
                self.cache[cusip] = self._entry(*issuers[cusip], figi.get(cusip, {}))
            self._save()
        return {c: self.cache[c] for c in issuers}

    def _entry(self, issuer: str, cls: str, figi: dict) -> dict:
        ticker = (figi.get("ticker") or "").replace("/", "-").upper() or None
        cik, title = self.tickers().get(ticker, (None, None)) if ticker else (None, None)
        kind = derivative(cls)
        if cik is None:
            cik, title, matched = self.match_name(issuer)
            if not ticker and not kind:  # a name match yields the common stock's ticker, wrong for warrants
                ticker = matched
        name = _title(title or figi.get("name") or issuer)
        sic, listed = self._issuer(cik) if cik else (None, None)
        figi_type = figi.get("securityType")
        return {
            "ticker": ticker,
            "name": f"{name} {kind}" if kind and not ticker else name,
            "cik": cik,
            "sic": sic,
            "listed": listed,
            "figi_type": figi_type,
            "sector": sector(sic, figi_type),
        }

    def _openfigi(self, cusips: List[str]) -> Dict[str, dict]:
        # Codes for issuers outside the US start with a letter and are CINS, not CUSIP, to OpenFIGI.
        jobs = [{"idType": "ID_CINS" if c[0].isalpha() else "ID_CUSIP", "idValue": c, "exchCode": "US"} for c in cusips]
        headers = {"Content-Type": "application/json", "User-Agent": FIGI_AGENT}
        if self.figi_key:
            headers["X-OPENFIGI-APIKEY"] = self.figi_key
        answers = json.loads(self.client.request(OPENFIGI, json.dumps(jobs).encode(), headers))
        return {c: a["data"][0] for c, a in zip(cusips, answers) if a.get("data")}

    def tickers(self) -> Dict[str, Tuple[int, str]]:
        if self._tickers is None:
            data = self.client.get_json(SEC_TICKERS, TICKERS_TTL)
            self._tickers = {}
            for cik, name, ticker, _exchange in data["data"]:
                if ticker:
                    self._tickers.setdefault(ticker.upper(), (int(cik), name))
        return self._tickers

    def match_name(self, issuer: str) -> Tuple[Optional[int], Optional[str], Optional[str]]:
        """(CIK, name, ticker) for a CUSIP OpenFIGI cannot map. Filings cut names at about 28 characters,
        so a unique prefix match counts too."""
        if self._names is None:
            self._names = {}
            for ticker, (cik, name) in self.tickers().items():
                self._names.setdefault(_norm(name), (cik, name, ticker))
        exact = self._names.get(_norm(issuer))
        if exact:
            return exact
        loose = _loose(issuer)
        if len(loose) < 12:
            return None, None, None
        hits = {}
        for ticker, (cik, name) in self.tickers().items():
            if _loose(name).startswith(loose):
                hits.setdefault(cik, (cik, name, ticker))
        return next(iter(hits.values())) if len(hits) == 1 else (None, None, None)

    def _issuer(self, cik: int) -> Tuple[Optional[int], Optional[str]]:
        """SIC code and the listing date: an exchange registration within a year after an IPO or spin-off
        registration. Exchange registrations alone also cover bond listings of established companies."""
        data = self.client.get_json(SEC_SUBMISSIONS.format(cik=cik), ttl=0)
        sic = str(data.get("sic") or "").strip()
        recent = data.get("filings", {}).get("recent", {})
        filed = list(zip(recent.get("form", []), recent.get("filingDate", [])))
        registered = [dt.date.fromisoformat(d) for f, d in filed if f in REGISTRATION_FORMS]
        listed = [d for f, d in filed if f in LISTING_FORMS
                  and any(0 <= (dt.date.fromisoformat(d) - r).days <= 365 for r in registered)]
        return (int(sic) if sic.isdigit() else None), max(listed, default=None)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.cache, indent=1, sort_keys=True))
        tmp.replace(self.path)


def derivative(cls: str) -> str:
    """Warrants, rights and units share the issuer name with the common stock, so name them apart."""
    for pattern, kind in DERIVATIVES:
        if pattern.search(cls.upper()):
            return kind
    return ""


def _norm(name: str) -> str:
    words = re.sub(r"[^A-Z0-9 ]", " ", name.upper().replace("&", " AND ")).split()
    return " ".join(w for w in words if w not in SUFFIXES)


def _loose(name: str) -> str:
    return " ".join(re.sub(r"[^A-Z0-9 ]", " ", name.upper()).split())


def _title(name: str) -> str:
    return name.title() if name.isupper() else name
