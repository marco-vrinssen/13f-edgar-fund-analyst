# 13F EDGAR Fund Analyst

Tracks the US equity holdings of ten growth and tech funds through their Form 13F-HR filings at the SEC, shows what each fund bought and sold, and turns it into a short report: each fund's strategy, plausible reasons, what it implies for sectors, and what a private investor can adapt.

One core library serves three entry points:

- `13f`, a command line tool that works without any AI agent.
- `13f mcp`, an MCP server with read-only tools for agents.
- The `13f-fund-analyst` skill, which has Claude write the report from the data and save it as an Obsidian note.

A weekly job checks EDGAR and writes a new report only when a tracked fund has filed.

It is pure Python standard library and runs on the Python 3.9 that ships with macOS. Nothing to install.

## Funds

Set in [funds.json](funds.json). Every fund is confirmed to file 13F-HR reports with holdings: run `13f verify` after changing the list.

| Fund | Filer | CIK | Manager |
| --- | --- | --- | --- |
| BIT Capital | BIT Capital GmbH | 2053305 | Jan Beckers |
| Andreessen Horowitz | a16z Capital Management, L.L.C. | 1540358 | Marc Andreessen, Ben Horowitz |
| Altimeter Capital | Altimeter Capital Management, LP | 1541617 | Brad Gerstner |
| ARK Invest | ARK Investment Management LLC | 1697748 | Cathie Wood |
| Coatue | Coatue Management LLC | 1135730 | Philippe Laffont |
| Tiger Global | Tiger Global Management LLC | 1167483 | Chase Coleman |
| Whale Rock | Whale Rock Capital Management LLC | 1387322 | Alex Sacerdote |
| Light Street | Light Street Capital Management, LLC | 1569049 | Glen Kacher |
| Atreides | Atreides Management, LP | 1777813 | Gavin Baker |
| D1 Capital | D1 Capital Partners L.P. | 1747057 | Dan Sundheim |

## Setup

1. Clone the repo.
2. Copy `config.example.json` to `config.local.json` and fill in `user_agent`. SEC requires a name and a reachable contact email in every automated request and blocks GitHub noreply addresses. Add `vault_dir` to write notes into an Obsidian vault.
3. Optional: store an [OpenFIGI](https://www.openfigi.com/api) key in the keychain for faster CUSIP mapping:

   ```bash
   security add-generic-password -s openfigi-api-key -a "$USER" -w
   ```

4. Run `scripts/install.sh` to link `13f` into `~/.local/bin`, link the skill into `~/.claude/skills` and load the weekly LaunchAgent.
5. Register the MCP server with Claude Code:

   ```bash
   claude mcp add --scope user 13f-edgar -- "$PWD/bin/13f" mcp
   ```

Every setting can also come from an environment variable: `EDGAR13F_USER_AGENT`, `EDGAR13F_VAULT_DIR`, `EDGAR13F_NOTE_FOLDER`, `EDGAR13F_NOTE_TITLE`, `EDGAR13F_DATA_DIR`, `EDGAR13F_CONFIG`, `EDGAR13F_FUNDS`, `OPENFIGI_API_KEY`.

## Commands

| Command | Does |
| --- | --- |
| `13f funds` | List the tracked funds |
| `13f verify` | Confirm every fund has a recent 13F-HR with holdings |
| `13f filings <fund>` | List a fund's 13F filings with EDGAR links |
| `13f holdings <fund> [--period YYYY-MM-DD]` | All long equity positions with ticker, sector and weight |
| `13f changes <fund> [--period YYYY-MM-DD]` | New positions, additions, trims and exits against the quarter before |
| `13f check` | New filings since the last report. Exit 0 if any, 1 if none |
| `13f data` | The full data pack for all funds as Markdown |
| `13f note [--title T] [--force]` | Write Markdown from stdin as a new note in the vault |
| `13f report [--force]` | Write a data-only note without interpretation |
| `13f note-path` | Print the path today's note gets |
| `13f mark-reported` | Record the latest filings as reported |
| `13f mcp` | Run the MCP server on stdio |

`<fund>` is an id from `funds.json`, a CIK or a unique part of the name.

## MCP tools

All read-only, all return plain text: `list_funds`, `list_filings`, `get_holdings`, `get_changes`, `check_new_filings`, `get_report_data`.

## Weekly check

`scripts/weekly-check.sh` runs every Saturday at 09:00 local time through launchd, after the week's filings are in. It exits quietly when nothing is new. When a fund has filed, it runs Claude Code headless with the skill to write the report. If Claude is unavailable or writes nothing, it writes a data-only note instead, so a filing is never missed. Then it marks the filings as reported. The log is at `~/Library/Logs/13f-edgar-fund-analyst.log`. Change the time with `WEEKDAY=2 HOUR=8 scripts/install.sh`.

## How it works

1. The fund's filing list comes from `data.sec.gov/submissions`. For each quarter it takes the latest original 13F-HR and applies later amendments: a restatement replaces it, new holdings add to it.
2. Holdings come from the filing's XML information table. Parsed row counts and value totals are checked against the filing's summary page.
3. CUSIPs map to tickers through OpenFIGI, then to company names and SIC codes through SEC's ticker file and submissions API. Sectors are grouped from SIC codes.
4. Positions are compared with the previous quarter by ticker. Trade size is estimated as share change times the quarter-end price implied by the filing.
5. Across funds, positions are grouped by issuer to find shared holdings and joint buying or selling.

## Limits

13F covers long positions in US-listed equities and listed options at quarter end. It arrives up to 45 days after quarter end and leaves out shorts, cash, bonds, non-US listings and private stakes. Share counts are not split-adjusted: changes that look like splits are flagged and left out of flows. SIC-based sectors are approximate. Reports mark reasons as interpretation and contain no buy or sell recommendations. Nothing here is financial advice.

## Fair access

Requests declare the configured User-Agent and stay below 10 per second, as SEC asks. Filings never change, so they are cached on disk for good in `~/.local/share/edgar13f/cache`. Filing lists are cached for an hour, the SEC ticker file for a week, CUSIP mappings for good.

## Develop

```bash
python3 -m unittest
```

Offline tests cover parsing, amendments, comparison, split detection, sectors and the MCP protocol.

## License

MIT
