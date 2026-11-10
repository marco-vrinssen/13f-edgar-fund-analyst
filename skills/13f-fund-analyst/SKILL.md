---
name: 13f-fund-analyst
description: Track the SEC 13F holdings of ten growth and tech funds (BIT Capital, Andreessen Horowitz, Altimeter, ARK, Coatue, Tiger Global, Whale Rock, Light Street, Atreides, D1) and write a short English report on each fund's strategy, the likely reasons, what it means for sectors and markets, and what a private investor can adapt. Use when the user asks for a 13F report, what these funds bought or sold, a fund's holdings or changes, or when the weekly check found new filings.
---

# 13F fund analyst

All data comes from the `13f` CLI in the repo this skill lives in. It is on PATH after `scripts/install.sh`, otherwise run `../../bin/13f` from this skill's folder. The MCP server `13f mcp` offers the same read tools.

## Steps

1. Run `13f check`. It lists funds with filings newer than the last report. Exit code 1 means nothing new: stop unless the user asked for a report anyway.
2. Run `13f data` and read all of it. It prints coverage, per-fund facts, moves, sectors and cross-fund overlaps. The first run takes a few minutes while CUSIPs are mapped.
3. Write the report in the structure below, in English, using only numbers from the data pack.
4. Save it with a quoted heredoc, exactly in this form, so the command starts with `13f`:

   ```bash
   13f note <<'EOF'
   ...report...
   EOF
   ```

   It prints the note path and refuses to overwrite. Add `--force` only when the user wants the note replaced.
5. Run `13f mark-reported` so the weekly check waits for the next filings.

For one fund, `13f holdings <fund>` and `13f changes <fund>` give the full lists.

## Report structure

```markdown
---
tags:
  - invest
  - 13f
---
# 13F Report YYYY-MM-DD

One short paragraph, the only place for data limits: 13F shows long US-listed equity positions at quarter end only, arrives up to 45 days late, and leaves out shorts, cash, non-US holdings and private stakes. Weigh the conclusions accordingly. Name the quarters covered and say that Filing and Moves bullets are facts from the filings, while Interpretation and Implication bullets are readings of them.

## Summary
Three to five bullets: the largest moves across funds and what they point to.

## Funds
### Fund name, Q2 2026 vs Q1 2026
- Filing: book size and positions with the change, top-10 share, estimated buying and selling, the largest holdings.
- Moves: the new positions, additions, trims and exits that matter, with weights.
- Strategy: the pattern the moves show, such as concentration, rotation, theme or risk level.
- Interpretation: plausible reasons for the moves.
- Implication: what it suggests for the sector or market.

## Cross-fund themes
Three to six themes, each with the funds involved and the evidence: shared names, joint buying or selling, sector shifts.

## For a private investor
Patterns, sectors and themes a private investor at a neobroker can adapt.
```

## Rules

- Keep the order: summary, one section per fund, cross-fund themes, private investor. Keep each fund to five or six bullets.
- Facts come from the data pack. Every statement about why a fund moved starts with "Interpretation:" or sits in the Interpretation bullet.
- Strategy is read from changes between quarters, not from one snapshot.
- Name the quarter for each fund, since funds file on different days.
- Mention a failed data check, a confidential-treatment note or a possible split where it affects a conclusion.
- Use weights and share changes. 13F values reflect quarter-end prices, so a higher value alone is not buying.
- The private investor section covers adaptable patterns: themes with broad fund support, sector tilts, concentration versus diversification, holding periods, the 45-day lag, crowding risk, and how a neobroker's ETFs and savings plans give sector exposure. It names no single security to buy or sell, and it ends with: "This is not personalised financial advice."
- Write short sentences. No hype.
