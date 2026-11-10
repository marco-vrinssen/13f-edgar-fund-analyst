"""MCP server over stdio with read-only tools. Plain JSON-RPC, no SDK, so it runs on the stock Python."""
from __future__ import annotations

import json
import sys
import traceback
from typing import Callable, Dict

from . import __version__, report

PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
INSTRUCTIONS = (
    "Read-only access to Form 13F-HR filings of the tracked growth and tech funds. "
    "13F shows long US-listed equity and option positions at quarter end only, up to 45 days late, "
    "without shorts, cash or non-US holdings. Keep what a filing shows apart from your interpretation of why. "
    "The first call for a fund can take a minute while CUSIPs are mapped; later calls read the local cache."
)
FUND = {"type": "string", "description": "Fund id, CIK or part of its name, see list_funds"}
PERIOD = {"type": "string", "description": "Quarter end as YYYY-MM-DD, default latest"}


def _tools(app) -> Dict[str, dict]:
    def schema(props=None, required=()):
        return {"type": "object", "properties": props or {}, "required": list(required)}

    def funds_text(_):
        return "\n".join(f"{f.id}: {f.name}, {f.filer}, CIK {f.cik}, {f.manager}, {f.city}. {f.style}." for f in app.funds)

    return {
        "list_funds": {
            "description": "List the tracked funds with id, filer name, CIK, manager and style.",
            "inputSchema": schema(),
            "run": funds_text,
        },
        "list_filings": {
            "description": "List a fund's 13F-HR filings and amendments, newest first, with EDGAR links.",
            "inputSchema": schema({"fund": FUND, "limit": {"type": "integer", "description": "Default 8"}}, ["fund"]),
            "run": lambda a: report.filings_text(app.fund(a["fund"]).name, app.filings(app.fund(a["fund"])), int(a.get("limit", 8))),
        },
        "get_holdings": {
            "description": "A fund's long equity positions for one quarter with ticker, sector, weight, value and shares.",
            "inputSchema": schema({"fund": FUND, "period": PERIOD}, ["fund"]),
            "run": lambda a: report.holdings_text(app.view(app.fund(a["fund"]), a.get("period"))),
        },
        "get_changes": {
            "description": "Compare a fund's quarter with the one before: new positions, additions, trims and exits.",
            "inputSchema": schema({"fund": FUND, "period": PERIOD}, ["fund"]),
            "run": lambda a: report.changes_text(app.view(app.fund(a["fund"]), a.get("period"))),
        },
        "check_new_filings": {
            "description": "List funds with a 13F filing that is newer than the last written report.",
            "inputSchema": schema(),
            "run": lambda a: report.check_text(app.check(), app.reported_ciks()),
        },
        "get_report_data": {
            "description": "The full data pack for all funds as Markdown: coverage, per-fund facts, moves, sectors and cross-fund overlaps.",
            "inputSchema": schema(),
            "run": lambda a: report.data_pack(app.views(), app.reported_ciks()),
        },
    }


def serve(app, stdin=sys.stdin, stdout=sys.stdout) -> None:
    tools = _tools(app)
    for line in stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            _send(stdout, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            continue
        if "id" not in msg:
            continue  # notifications need no answer
        _send(stdout, _answer(msg, tools))


def _answer(msg: dict, tools: Dict[str, dict]) -> dict:
    method, params = msg.get("method"), msg.get("params") or {}
    reply = {"jsonrpc": "2.0", "id": msg["id"]}
    if method == "initialize":
        asked = params.get("protocolVersion")
        reply["result"] = {
            "protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "13f-edgar-fund-analyst", "version": __version__},
            "instructions": INSTRUCTIONS,
        }
    elif method == "ping":
        reply["result"] = {}
    elif method == "tools/list":
        reply["result"] = {"tools": [{"name": n, "description": t["description"], "inputSchema": t["inputSchema"],
                                      "annotations": {"readOnlyHint": True}} for n, t in tools.items()]}
    elif method == "tools/call":
        tool = tools.get(params.get("name"))
        if tool is None:
            reply["error"] = {"code": -32602, "message": f"Unknown tool {params.get('name')}"}
        else:
            reply["result"] = _call(tool["run"], params.get("arguments") or {})
    else:
        reply["error"] = {"code": -32601, "message": f"Method not found: {method}"}
    return reply


def _call(run: Callable, args: dict) -> dict:
    try:
        return {"content": [{"type": "text", "text": run(args)}], "isError": False}
    except Exception as err:  # report tool failures to the agent instead of crashing the server
        traceback.print_exc(file=sys.stderr)
        return {"content": [{"type": "text", "text": f"Error: {err}"}], "isError": True}


def _send(stdout, payload: dict) -> None:
    stdout.write(json.dumps(payload) + "\n")
    stdout.flush()
