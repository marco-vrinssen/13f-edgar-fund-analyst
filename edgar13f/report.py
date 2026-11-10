"""Plain-text and Markdown rendering of filings, holdings, changes and the cross-fund data pack."""
from __future__ import annotations

import datetime as dt
from typing import Dict, List, Optional

from .analysis import Change, CrossFund, FundView, cross_fund
from .edgar import Filing, Holdings

MOVES_SHOWN = 8
LIMITS = """## What 13F data can and cannot show

- It lists long positions in US-listed equities and listed options at quarter end, nothing else.
- It arrives up to 45 days after quarter end, so positions may already have changed.
- It leaves out short positions, cash, bonds, non-US listings and private stakes.
- Trade values are estimates: share change times the quarter-end price implied by the filing.
- A new position can be a private stake that became reportable when the company listed, not a purchase.
- Share counts are not split-adjusted. Moves that look like splits are checked against the company's shares outstanding, flagged and left out of flows.
- Sectors come from SEC SIC codes and are approximate."""


def usd(value: float) -> str:
    if abs(value) >= 1e9:
        return f"{value / 1e9:.2f}bn"
    if abs(value) >= 1e6:
        return f"{value / 1e6:.1f}M"
    return f"{value / 1e6:.2f}M"


def pct(share: float) -> str:
    return f"{share * 100:.1f}%"


def pp(delta: float) -> str:
    return f"{delta * 100:+.1f} pp"


def filings_text(name: str, found: List[Filing], limit: int) -> str:
    lines = [f"13F filings for {name}, newest first", "",
             "| Form | Period | Filed | Accession | Index |", "| --- | --- | --- | --- | --- |"]
    lines += [f"| {f.form} | {f.period} | {f.filed} | {f.accession} | {f.index_url} |" for f in found[:limit]]
    return "\n".join(lines)


def holdings_text(view: FundView) -> str:
    book = view.book
    lines = [f"{view.fund.name}: holdings for {view.current.period}", "",
             f"Long equity {usd(book.total)} USD in {len(book.positions)} positions.", ""]
    lines += ["| # | Ticker | Company | Sector | Weight | Value USD | Shares |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for i, p in enumerate(book.positions, 1):
        lines.append(f"| {i} | {p.ticker or '-'} | {p.name} | {p.sector} | {pct(p.weight)} | {usd(p.value)} | {p.shares:,} |")
    lines += _outside_equity(view) + [""] + _checks(view.current)
    return "\n".join(lines)


def changes_text(view: FundView) -> str:
    prev = view.previous.period if view.previous else "nothing (first filing)"
    lines = [f"{view.fund.name}: {view.current.period} compared with {prev}", ""]
    for status, label in (("new", "New"), ("increase", "Added"), ("reduce", "Trimmed"), ("exit", "Exited"),
                          ("unchanged", "Unchanged")):
        moves = view.by_status(status)
        lines.append(f"{label} ({len(moves)})")
        lines += [f"- {_move(c)}" for c in moves] or ["- none"]
        lines.append("")
    return "\n".join(lines).rstrip()


def check_text(fresh, reported: Dict[str, str]) -> str:
    if not fresh:
        return "No new 13F filings since the last report."
    lines = ["New 13F filings since the last report:"]
    for fund, period, accessions in fresh:
        before = reported.get(str(fund.cik))
        note = f"last report had {before}" if before else "not in a report yet"
        lines.append(f"- {fund.name}: period {period}, {', '.join(accessions)} ({note})")
    return "\n".join(lines)


def data_pack(views: List[FundView], reported: Dict[str, str], today: Optional[str] = None) -> str:
    """Everything the report needs, as facts only. The skill adds the interpretation."""
    today = today or dt.date.today().isoformat()
    parts = [f"# 13F data pack, {today}",
             "Source: Form 13F-HR information tables from SEC EDGAR, parsed and compared by 13f-edgar-fund-analyst.",
             LIMITS, _coverage(views, reported)]
    parts += [_fund_section(v) for v in views]
    parts.append(_cross_section(cross_fund(views)))
    return "\n\n".join(parts) + "\n"


def data_note(views: List[FundView], reported: Dict[str, str], title: str) -> str:
    """Fallback note when no agent is available to write the interpretation."""
    body = data_pack(views, reported).split("\n", 1)[1].lstrip()
    return (f"---\ntags:\n  - invest\n  - 13f\n---\n# {title}\n\n"
            "Data only: written by the weekly check without interpretation. "
            "Run the 13f-fund-analyst skill to add strategy reads and implications.\n\n" + body)


def _coverage(views: List[FundView], reported: Dict[str, str]) -> str:
    lines = ["## Coverage", "",
             "| Fund | Quarter | Filed | Compared with | New since last report | Data check |",
             "| --- | --- | --- | --- | --- | --- |"]
    for v in views:
        filed = max(f.filed for f in v.current.filings)
        prev = v.previous.period if v.previous else "-"
        new = "yes" if reported.get(str(v.fund.cik)) != v.current.period else "no"
        check = "ok" if v.current.verified else "see fund"
        lines.append(f"| {v.fund.name} | {v.current.period} | {filed} | {prev} | {new} | {check} |")
    return "\n".join(lines)


def _fund_section(v: FundView) -> str:
    f, book, prior = v.fund, v.book, v.prior
    base = v.current.filings[0]
    lines = [f"## {f.name}", "",
             f"{f.manager}, {f.city}. {f.style}. Filer {f.filer}, CIK {f.cik}.", "",
             f"- 13F-HR for {v.current.period}, filed {base.filed}: {base.index_url}"]
    if prior:
        lines.append(f"- Long equity {usd(book.total)} USD in {len(book.positions)} positions, "
                     f"before {usd(prior.total)} USD in {len(prior.positions)} ({v.previous.period}).")
        lines.append(f"- Top 10 share of book {pct(_top10(book))}, before {pct(_top10(prior))}.")
        bought, sold = v.flows()
        avg = (book.total + prior.total) / 2 or 1
        lines.append(f"- Estimated buying {usd(bought)} USD and selling {usd(sold)} USD, "
                     f"{pct(bought / avg)} and {pct(sold / avg)} of the average book, "
                     "without splits and companies that listed during the quarter.")
    else:
        lines.append(f"- Long equity {usd(book.total)} USD in {len(book.positions)} positions. No earlier 13F to compare.")
    lines += _outside_equity(v)
    lines += [f"- Data check: {c}" for c in v.current.checks] + [f"- Note: {n}" for n in v.current.notes]

    lines += ["", "Top holdings", "", "| # | Ticker | Company | Sector | Weight | Value USD | Shares vs before |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    changes = {c.position.key: c for c in v.changes}
    for i, p in enumerate(book.positions[:10], 1):
        lines.append(f"| {i} | {p.ticker or '-'} | {p.name} | {p.sector} | {pct(p.weight)} | {usd(p.value)} | "
                     f"{_short(changes.get(p.key))} |")

    if prior:
        lines += ["", "Moves, largest first by estimated trade value", ""]
        listings = v.listings()
        if listings:
            lines.append(f"- Listed during the quarter, not counted as buying ({len(listings)}): "
                         + "; ".join(_move(c) for c in listings))
        for status, label in (("new", "New"), ("increase", "Added"), ("reduce", "Trimmed"), ("exit", "Exited")):
            moves = [c for c in v.by_status(status) if c.counts_as_trade]
            shown = "; ".join(_move(c) for c in moves[:MOVES_SHOWN]) or "none"
            more = f"; and {len(moves) - MOVES_SHOWN} more" if len(moves) > MOVES_SHOWN else ""
            lines.append(f"- {label} ({len(moves)}): {shown}{more}")
        lines.append(f"- Unchanged: {len(v.by_status('unchanged'))} positions")
        splits = [c for c in v.changes if c.split_hint]
        if splits:
            lines.append("- Splits, left out of flows: " + "; ".join(_move(c) for c in splits))

    lines += ["", "Sectors", "", "| Sector | Weight | Before | Change |", "| --- | --- | --- | --- |"]
    for name, now, before in v.sectors():
        lines.append(f"| {name} | {pct(now)} | {pct(before) if prior else '-'} | {pp(now - before) if prior else '-'} |")
    return "\n".join(lines)


def _cross_section(cf: CrossFund) -> str:
    lines = ["## Across funds", "", "Issuer level, so share classes count once.", "",
             "Held by three or more funds", "", "| Company | Funds | Count | Combined value USD |",
             "| --- | --- | --- | --- |"]
    lines += [f"| {label} | {', '.join(funds)} | {len(funds)} | {usd(v)} |" for label, funds, v in cf.held[:20]]
    for title, rows in (("New positions in companies that listed during the quarter", cf.listed),
                        ("Bought by two or more funds (new or added)", cf.bought),
                        ("Sold by two or more funds (exited or trimmed)", cf.sold)):
        lines += ["", title, "", "| Company | Funds | Estimated USD |", "| --- | --- | --- |"]
        lines += [f"| {label} | {', '.join(funds)} | {usd(v)} |" for label, funds, v in rows[:15]] or ["| none | | |"]
    lines += ["", "Average sector weights, each fund weighted equally", "",
              "| Sector | Now | Before | Change |", "| --- | --- | --- | --- |"]
    lines += [f"| {name} | {pct(now)} | {pct(before)} | {pp(now - before)} |" for name, now, before in cf.sectors]
    return "\n".join(lines)


def _outside_equity(v: FundView) -> List[str]:
    lines = []
    for kind in ("Call", "Put"):
        if kind in v.book.options:
            rows, value = v.book.options[kind]
            lines.append(f"- {kind} options: {rows} rows on underlying worth {usd(value)} USD, outside the equity weights.")
    if v.book.debt[0]:
        lines.append(f"- Convertible or other notes: {v.book.debt[0]} rows worth {usd(v.book.debt[1])} USD, outside the equity weights.")
    return lines


def _checks(h: Holdings) -> List[str]:
    return [f"Data check: {c}" for c in h.checks] + [f"Note: {n}" for n in h.notes]


def _top10(book) -> float:
    return sum(p.weight for p in book.positions[:10])


def _short(c: Optional[Change]) -> str:
    if c is None:
        return "-"
    if c.status == "new":
        return "new"
    if c.status == "unchanged":
        return "held"
    return f"{c.share_change * 100:+.1f}%" + (" (split?)" if c.split_hint else "")


def _move(c: Change) -> str:
    p = c.position
    if c.status == "new":
        listed = f", listed {p.listed}" if c.listed_in_quarter else ""
        return f"{p.label} {pct(p.weight)} ({usd(p.value)}{listed})"
    if c.status == "exit":
        return f"{p.label} (was {pct(p.weight)}, {usd(p.value)})"
    if c.status == "unchanged":
        return f"{p.label} {pct(p.weight)}"
    split = ", split confirmed by shares outstanding" if c.split_confirmed else ", possible split" if c.split_hint else ""
    return f"{p.label} {c.share_change * 100:+.1f}% shares ({'+' if c.trade_value > 0 else ''}{usd(c.trade_value)}, now {pct(p.weight)}{split})"
