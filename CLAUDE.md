This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is a Python utility that fetches a personal board game collection from BoardGameGeek (BGG) and outputs structured JSON, plus a pure HTML dashboard for visualizing it.

## Project Structure

```
ProjectBoardGames/
├── CLAUDE.md                          ← you are here
├── .claude/commands/                  ← slash commands
│   ├── dashboard_BGG.md               ← /dashboard_BGG
│   └── refresh_BGG.md                 ← /refresh_BGG
├── scripts/
│   ├── fetch_bgg_collection.py        ← BGG fetcher
│   └── sync_scores.py                 ← scores sync
├── dashboard/
│   └── dshb_bgg_collection.html       ← HTML dashboard
├── data/                              ← all data files
│   ├── bgg_collection.json
│   ├── bgg_collection_scores.json     ← personal scores (do not overwrite)
│   ├── bgg_collection_compact.json
│   └── fetch_log.json
├── docs/
│   └── immersion_score.md             ← scoring framework + user profile
├── skills/
│   ├── bgg_dashboard.md               ← dashboard design spec
│   └── efficient_json_fetch.md        ← fetch pattern reference
├── docs/tabletop mechanics/                ← mechanism encyclopedias
├── next steps/                        ← backlog
└── venv/                              ← local only, not in package
```

## Running the Scripts

```bash
source venv/bin/activate
python scripts/fetch_bgg_collection.py
python scripts/sync_scores.py
```

Use `/refresh_BGG` to run the full pipeline via Claude. Use `/dashboard_BGG` to launch the dashboard.

## Architecture

**`scripts/fetch_bgg_collection.py`** handles everything end-to-end:

1. **Auth** — Loads `BGG_BEARER_TOKEN` and `BGG_PASSWORD` from `.env`, logs into BGG to access private collection data
2. **Check** — On subsequent runs, calls BGG API with `modifiedsince=<last_fetch_date>`; if 0 items changed, exits immediately with no files written
3. **Fetch** — If changes exist (or first run), fetches only those items; handles HTTP 202 queue responses with retry logic (up to 5 retries, 5s delay)
4. **Parse** — Converts XML → Python dicts with ownership status, ratings, rankings, play logs, and private acquisition data
5. **Plays** — Only fetches play logs for changed games where `num_plays` increased; reuses cached plays for everything else (100/page, 1.5s delay between requests)
6. **Merge** — Patches updated games into the full existing dataset loaded from `data/bgg_collection.json`
7. **Change report** — Prints a diff vs the previous fetch; saves lightweight `data/fetch_log.json` for next run
8. **Categorize** — Groups games into: owned, wishlist (5 priority tiers), preordered, previously_owned, want_to_play, want_to_buy, for_trade
9. **Output** — Writes JSON to `data/` with metadata counts + categorized game arrays

## Key Configuration (hardcoded in script)

- `BGG_USERNAME = "jsneij"` — the BGG account to fetch
- `BGG_API_BASE = "https://boardgamegeek.com/xmlapi2"`
- `MAX_RETRIES = 5`, `RETRY_DELAY_SECONDS = 5` — queue polling
- `PLAYS_REQUEST_DELAY = 1.5` — rate limit between per-game play log requests

Credentials live in `.env` (not committed).

## Planned Enhancements (see `next steps/to_explore.txt`)

- Enrich with `/thing` API endpoint (designers, mechanics, categories, expansions)
- Normalized multi-dataset schema for SQL/Pandas analysis



## Insights Integration (Claude.ai ↔ Claude Code)

This project uses `insights/` as a bridge for analytical work done in Claude.ai.

### Consuming insights
- Insight files live in `insights/ins_<slug>_<date>.json`
- Each file is self-contained with `type`, `chart_hint`, `columns`, `data`, and `summary`
- Read `skills/insights_exchange.md` for the full schema and integration guide

### Slash command
- `/integrate_insight` — scan `insights/` for new files and add them to the dashboard

### Rules
- Never modify files in `insights/` — they are Claude.ai outputs
- Dashboard reads insights via relative path `../insights/`
- Use `chart_hint` and `columns` metadata to generate the right visualization
- Show `title` as heading, `summary` as narrative, `generated_at` as timestamp
- Keep the same design system as the main dashboard (colors, fonts, card style)


