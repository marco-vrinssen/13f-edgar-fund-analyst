"""Turn raw 13F rows into positions, quarter-over-quarter changes and cross-fund patterns."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .config import Fund
from .edgar import Holdings, Row

SPLIT_RATIOS = (2, 3, 4, 5, 8, 10, 15, 20, 25, 30, 40, 50)


@dataclass
class Position:
    cusip: str
    ticker: Optional[str]
    name: str
    sector: str
    cik: Optional[int] = None
    listed: Optional[str] = None  # date of the latest exchange registration
    shares: int = 0
    value: int = 0
    weight: float = 0.0

    @property
    def label(self) -> str:
        return self.ticker or self.name

    @property
    def key(self) -> str:
        """Ticker or name, so a security matches across quarters even when the filer changed its CUSIP."""
        return self.ticker or self.name.upper()

    @property
    def issuer(self) -> str:
        """The company behind all its share classes: CIK if known, else the CUSIP's issuer part."""
        return str(self.cik) if self.cik else self.cusip[:6]


@dataclass
class Change:
    now: Optional[Position]
    before: Optional[Position]
    status: str  # new, exit, increase, reduce, unchanged
    trade_value: int  # estimated USD bought (+) or sold (-) at quarter-end prices
    split_hint: bool = False  # looks like a split, so left out of flows
    split_confirmed: bool = False  # the company's shares outstanding moved by the same ratio
    listed_in_quarter: bool = False  # a new position in a company that listed during the quarter

    @property
    def counts_as_trade(self) -> bool:
        return not (self.split_hint or self.listed_in_quarter)

    @property
    def position(self) -> Position:
        return self.now or self.before

    @property
    def share_change(self) -> Optional[float]:
        if self.now and self.before and self.before.shares:
            return self.now.shares / self.before.shares - 1
        return None


@dataclass
class Book:
    """Long equity positions plus what sits outside them."""
    positions: List[Position]
    total: int
    options: Dict[str, Tuple[int, int]]  # Put/Call -> (rows, underlying value)
    debt: Tuple[int, int]  # (rows, value)


@dataclass
class FundView:
    fund: Fund
    current: Holdings
    previous: Optional[Holdings]
    book: Book
    prior: Optional[Book]
    changes: List[Change] = field(default_factory=list)

    def sectors(self) -> List[Tuple[str, float, float]]:
        now = sector_weights(self.book)
        before = sector_weights(self.prior) if self.prior else {}
        keys = sorted(set(now) | set(before), key=lambda k: -now.get(k, 0))
        return [(k, now.get(k, 0.0), before.get(k, 0.0)) for k in keys]

    def flows(self) -> Tuple[int, int]:
        bought = sum(c.trade_value for c in self.changes if c.trade_value > 0 and c.counts_as_trade)
        sold = -sum(c.trade_value for c in self.changes if c.trade_value < 0 and c.counts_as_trade)
        return bought, sold

    def listings(self) -> List[Change]:
        return sorted((c for c in self.changes if c.listed_in_quarter), key=lambda c: -c.trade_value)

    def by_status(self, status: str) -> List[Change]:
        return sorted((c for c in self.changes if c.status == status), key=lambda c: -abs(c.trade_value))


def build_book(rows: List[Row], securities: Dict[str, dict]) -> Book:
    positions: Dict[str, Position] = {}
    options: Dict[str, List[int]] = defaultdict(lambda: [0, 0])
    debt = [0, 0]
    for row in rows:
        if row.put_call:
            options[row.put_call][0] += 1
            options[row.put_call][1] += row.value
            continue
        if row.share_type == "PRN":
            debt[0] += 1
            debt[1] += row.value
            continue
        sec = securities.get(row.cusip, {})
        pos = Position(row.cusip, sec.get("ticker"), sec.get("name") or row.name,
                       sec.get("sector", "Unclassified"), sec.get("cik"), sec.get("listed"))
        pos = positions.setdefault(pos.key, pos)
        pos.shares += row.shares
        pos.value += row.value
    total = sum(p.value for p in positions.values())
    for pos in positions.values():
        pos.weight = pos.value / total if total else 0.0
    ordered = sorted(positions.values(), key=lambda p: -p.value)
    return Book(ordered, total, {k: (v[0], v[1]) for k, v in options.items()}, (debt[0], debt[1]))


def mark_listings(changes: List[Change], before: str, after: str) -> None:
    """New positions in companies that listed between the two quarter ends are often private stakes
    that became reportable, so they are kept apart from buying."""
    for ch in changes:
        listed = ch.position.listed
        ch.listed_in_quarter = ch.status == "new" and bool(listed) and before < listed <= after


def compare(book: Book, prior: Optional[Book]) -> List[Change]:
    now = {p.key: p for p in book.positions}
    before = {p.key: p for p in prior.positions} if prior else {}
    changes = []
    for key in set(now) | set(before):
        cur, old = now.get(key), before.get(key)
        if old is None:
            changes.append(Change(cur, None, "new", cur.value))
        elif cur is None:
            changes.append(Change(None, old, "exit", -old.value))
        else:
            price = cur.value / cur.shares if cur.shares else 0
            delta = cur.shares - old.shares
            status = "increase" if delta > 0 else "reduce" if delta < 0 else "unchanged"
            changes.append(Change(cur, old, status, round(delta * price), _split_hint(cur, old)))
    return changes


def _split_hint(cur: Position, old: Position) -> bool:
    """Share counts in 13F are not split-adjusted. Flag changes that look like a split, not a trade."""
    if not (cur.shares and old.shares and cur.value and old.value):
        return False
    ratio = cur.shares / old.shares
    price_ratio = (old.value / old.shares) / (cur.value / cur.shares)
    for r in SPLIT_RATIOS:
        for target in (r, 1 / r):
            if abs(ratio / target - 1) < 0.02 and 0.6 < price_ratio / target < 1.6:
                return True
    return False


def sector_weights(book: Book) -> Dict[str, float]:
    weights: Dict[str, float] = defaultdict(float)
    for pos in book.positions:
        weights[pos.sector] += pos.weight
    return dict(weights)


@dataclass
class CrossFund:
    held: List[Tuple[str, List[str], int]]  # label, funds, combined value
    bought: List[Tuple[str, List[str], int]]  # label, funds, combined estimated buying
    sold: List[Tuple[str, List[str], int]]
    listed: List[Tuple[str, List[str], int]]  # new positions in companies that listed during the quarter
    sectors: List[Tuple[str, float, float]]  # equal-weighted average across funds, now and before


def cross_fund(views: List[FundView], min_funds: int = 2) -> CrossFund:
    held: Dict[str, dict] = {}
    bought: Dict[str, dict] = {}
    sold: Dict[str, dict] = {}
    listed: Dict[str, dict] = {}
    for view in views:
        for pos in view.book.positions:
            _add(held, pos, view.fund.name, pos.value)
        for ch in view.changes:
            if ch.listed_in_quarter:
                _add(listed, ch.position, view.fund.name, ch.trade_value)
            elif ch.split_hint:
                continue
            elif ch.status in ("new", "increase"):
                _add(bought, ch.position, view.fund.name, ch.trade_value)
            elif ch.status in ("exit", "reduce"):
                _add(sold, ch.position, view.fund.name, -ch.trade_value)
    now: Dict[str, float] = defaultdict(float)
    before: Dict[str, float] = defaultdict(float)
    priors = [v for v in views if v.prior]
    for view in views:
        for name, weight in sector_weights(view.book).items():
            now[name] += weight / len(views)
    for view in priors:
        for name, weight in sector_weights(view.prior).items():
            before[name] += weight / len(priors)
    sectors = sorted(((k, now.get(k, 0.0), before.get(k, 0.0)) for k in set(now) | set(before)), key=lambda s: -s[1])
    return CrossFund(_rank(held, 3), _rank(bought, min_funds), _rank(sold, min_funds), _rank(listed, 1), sectors)


def _add(table: Dict[str, dict], pos: Position, fund: str, amount: int) -> None:
    entry = table.setdefault(pos.issuer, {"label": pos.label, "funds": [], "amount": 0})
    if fund not in entry["funds"]:
        entry["funds"].append(fund)
    entry["amount"] += amount


def _rank(table: Dict[str, dict], min_funds: int) -> List[Tuple[str, List[str], int]]:
    rows = [(e["label"], e["funds"], e["amount"]) for e in table.values() if len(e["funds"]) >= min_funds]
    return sorted(rows, key=lambda r: (-len(r[1]), -r[2]))
