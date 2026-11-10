# 13F EDGAR Fund Analyst

Tracks Form 13F-HR holdings of ten growth and tech funds from SEC EDGAR, compares each filing with the one before and feeds a report skill. Public repo: keep personal data, contact details, vault paths and keys out of every committed file.

## Setup

- Python 3.9+ standard library only. No install step, no dependencies.
- `config.local.json` (git-ignored, template in `config.example.json`) or env vars hold machine settings: `user_agent`, `vault_dir`, `note_folder`, `note_title`, `data_dir`.
- Optional OpenFIGI key in the macOS keychain, service `openfigi-api-key`, or `OPENFIGI_API_KEY`.
- `scripts/install.sh` links `bin/13f` into `~/.local/bin`, links the skill into `~/.claude/skills` and loads the LaunchAgent `13f-edgar-fund-analyst.weekly`.
- MCP: `claude mcp add --scope user 13f-edgar -- <repo>/bin/13f mcp`.

## Layout

- `funds.json`: the tracked funds. Change funds here, then run `13f verify`.
- `edgar13f/net.py`: HTTP with User-Agent, per-host rate limits, retries and the file cache.
- `edgar13f/edgar.py`: filing lists, cover pages, information tables, amendments, summary-total checks.
- `edgar13f/securities.py`: CUSIP to ticker, name, SIC sector and listing date.
- `edgar13f/analysis.py`: positions, quarter-over-quarter changes, split and listing flags, cross-fund overlaps.
- `edgar13f/app.py`: the shared core used by CLI and MCP: views, check, mark-reported, note writing.
- `edgar13f/report.py`: plain-text and Markdown rendering, including the data pack.
- `edgar13f/cli.py`, `edgar13f/mcp.py`: the two entry points.
- `skills/13f-fund-analyst/SKILL.md`: how an agent writes the report.
- `scripts/weekly-check.sh`: the scheduled job. `scripts/install.sh`: local wiring.

## Checks

- `python3 -m unittest` after every change. Tests are offline.
- `bin/13f verify` after changing `funds.json`.
- `bin/13f data` end to end against EDGAR. Every fund's data check must say `ok`.

## Facts

- SEC rejects requests without a contact email in the User-Agent and also rejects GitHub noreply and github.com addresses (403). Other domains pass.
- SEC limit is 10 requests per second; the client keeps 0.12 s between requests per SEC host.
- Filing values are whole USD for filings from 2023-01-03 on, thousands of USD before.
- The information table file name varies. `edgar.documents` reads every XML in the filing folder and picks the one whose root is `informationTable`.
- Amendments: `RESTATEMENT` replaces the original, `NEW HOLDINGS` adds to it. Only amendments filed after the latest original count.
- CUSIPs starting with a letter are CINS codes and need `ID_CINS` at OpenFIGI. OpenFIGI without a key allows 25 requests a minute with 10 jobs each.
- When OpenFIGI has no answer, the issuer name is matched against SEC's ticker file, exactly or by unique prefix, since filings cut names at about 28 characters. Warrants, rights and units never take the common stock's ticker from a name match.
- Filers sometimes change a security's CUSIP between quarters, so positions are matched by ticker or name, not CUSIP.
- Share counts in 13F are not split-adjusted. A split-like change counts as a split only if the company's `dei:EntityCommonStockSharesOutstanding` moved by the same ratio. Without that data it stays a possible split. Both are left out of flows.
- A listing is an 8-A12B or CERT filing within a year after an S-1, F-1 or Form 10. An 8-A12B alone also marks bond listings of established companies.
- Sectors come from SIC codes, so some are odd: SpaceX is SIC 7370 (software and internet), IREN is 6199 (financials).

## Decisions

- Standard library only, so it runs on the stock macOS Python and outside Claude. The MCP server is plain JSON-RPC over stdio instead of the SDK, which needs Python 3.10+.
- The CLI produces facts; interpretation is the skill's job. Without Claude the weekly job still writes a data-only note, so no filing goes unreported.
- MCP tools are read-only. Writing notes and marking filings as reported stay in the CLI.
- New positions in companies that listed during the quarter are kept out of buying, since they are often private stakes that became reportable.
- Trade size is share change times the quarter-end price implied by the filing, because 13F values move with prices.
- Cross-fund grouping is by issuer (CIK, else CUSIP issuer code), so share classes count once.
- The weekly job runs Saturday 09:00 local time, after the week's EDGAR filings are in.
- Funds were chosen for a growth and tech focus and confirmed as active 13F-HR filers with holdings. Dragoneer was dropped because no 13F filer could be confirmed.
