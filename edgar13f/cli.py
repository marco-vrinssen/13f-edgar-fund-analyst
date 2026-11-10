"""Command line interface. Run `13f --help` for the commands."""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from typing import List, Optional

from . import __version__, edgar, report
from .config import ConfigError, load_funds
from .net import HttpError


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="13f", description="Track 13F filings of growth and tech funds.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("funds", help="list the tracked funds")
    sub.add_parser("verify", help="confirm every tracked fund files 13F-HR reports with holdings")
    p = sub.add_parser("filings", help="list a fund's 13F filings")
    p.add_argument("fund", help="fund id, CIK or part of its name")
    p.add_argument("--limit", type=int, default=8)
    for name, text in (("holdings", "show a fund's holdings"), ("changes", "compare a fund's holdings with the quarter before")):
        p = sub.add_parser(name, help=text)
        p.add_argument("fund", help="fund id, CIK or part of its name")
        p.add_argument("--period", help="quarter end as YYYY-MM-DD, default latest")
    sub.add_parser("check", help="list new filings since the last report; exit 0 if any, 1 if none")
    sub.add_parser("data", help="print the data pack for all funds as Markdown")
    p = sub.add_parser("note", help="write Markdown from stdin as a new note in the vault")
    p.add_argument("--title", help="note title, default from note_title")
    p.add_argument("--force", action="store_true", help="replace a note with the same title")
    p = sub.add_parser("report", help="write a data-only report note, without interpretation")
    p.add_argument("--force", action="store_true", help="replace a note with the same title")
    sub.add_parser("note-path", help="print the path today's report note gets")
    sub.add_parser("mark-reported", help="record the latest filings as reported")
    sub.add_parser("mcp", help="run the MCP server on stdio")
    args = parser.parse_args(argv)

    try:
        return run(args)
    except (ConfigError, HttpError, edgar.EdgarError) as err:
        print(f"Error: {err}", file=sys.stderr)
        return 2


def run(args: argparse.Namespace) -> int:
    if args.command == "funds":
        for f in load_funds():
            print(f"{f.id:14} CIK {f.cik:<8} {f.name} ({f.manager}, {f.city})")
        return 0

    from .app import App  # needs a User-Agent, so loaded only for commands that fetch
    app = App()

    if args.command == "mcp":
        from .mcp import serve
        serve(app)
        return 0
    if args.command == "verify":
        return verify(app)
    if args.command == "filings":
        fund = app.fund(args.fund)
        print(report.filings_text(fund.name, app.filings(fund), args.limit))
    elif args.command == "holdings":
        print(report.holdings_text(app.view(app.fund(args.fund), args.period)))
    elif args.command == "changes":
        print(report.changes_text(app.view(app.fund(args.fund), args.period)))
    elif args.command == "check":
        fresh = app.check()
        print(report.check_text(fresh, app.reported_ciks()))
        return 0 if fresh else 1
    elif args.command == "data":
        print(report.data_pack(app.views(), app.reported_ciks()), end="")
    elif args.command == "note":
        print(app.write_note(sys.stdin.read(), args.title, args.force))
    elif args.command == "report":
        title = app.settings.note_title.format(date=dt.date.today().isoformat())
        note = report.data_note(app.views(), app.reported_ciks(), title)
        print(app.write_note(note, title, args.force))
    elif args.command == "note-path":
        print(app.note_path())
    elif args.command == "mark-reported":
        for cik, (period, accessions) in app.mark_reported().items():
            print(f"CIK {cik}: {period} {' '.join(accessions)}")
    return 0


def verify(app) -> int:
    """A fund without recent 13F-HR filings and holdings yields no data, so it should be replaced."""
    cutoff = (dt.date.today() - dt.timedelta(days=200)).isoformat()
    failed = 0
    for fund in app.funds:
        try:
            found = app.filings(fund, ttl=0)
            latest = found[0]
            holdings = edgar.holdings(app.client, found, edgar.periods(found)[0])
            ok = latest.filed >= cutoff and len(holdings.rows) > 0
            status = "ok" if ok else "stale or empty"
            print(f"{status:15} {fund.name}: {len(holdings.rows)} rows for {holdings.period}, filed {latest.filed}")
        except (HttpError, edgar.EdgarError) as err:
            ok = False
            print(f"{'failed':15} {fund.name}: {err}")
        failed += not ok
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
