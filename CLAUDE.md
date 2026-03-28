This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A Python pipeline that fetches a personal board game collection from BoardGameGeek (BGG), outputs structured JSON, and serves a single-file HTML dashboard with dynamic visualizations and insights — all deployed automatically via GitHub Pages.

## Project Structure

```
ProjectBoardGames/
├── CLAUDE.md                          ← you are here
├── .claude/commands/                  ← slash commands
│   ├── dashboard_BGG.md               ← /dashboard_BGG
│   └── refresh_BGG.md                 ← /refresh_BGG
├── .github/workflows/
│   ├── fetch-bgg.yml                  ← nightly BGG fetch (02:00 UTC)
│   └── deploy-pages.yml               ← GitHub Pages deploy
├── scripts/
│   ├── fetch_bgg_collection.py        ← BGG fetcher
│   └── sync_scores.py                 ← scores sync
├── dashboard/
│   └── dshb_bgg_collection.html       ← HTML dashboard (single file)
├── data/                              ← all data files
│   ├── bgg_collection.json
│   ├── bgg_collection_scores.json     ← personal scores (do not overwrite)
│   ├── bgg_collection_compact.json
│   └── fetch_log.json
├── docs/
│   ├── immersion_score.md             ← scoring framework + user profile
│   └── tabletop mechanics/            ← mechanism encyclopedias
├── skills/
│   ├── bgg_dashboard.md               ← dashboard design spec
│   └── efficient_json_fetch.md        ← fetch pattern reference
├── insights/                          ← reserved folder (currently unused)
├── next steps/                        ← backlog
└── venv/                              ← local only, not committed
```

## Running Locally

```bash
source venv/bin/activate
python scripts/fetch_bgg_collection.py
python scripts/sync_scores.py
```

Use `/refresh_BGG` to run the full pipeline via Claude. Use `/dashboard_BGG` to launch the dashboard locally.

## Architecture

### Fetch Pipeline — `scripts/fetch_bgg_collection.py`

Handles everything end-to-end:

1. **Auth** — Loads `BGG_BEARER_TOKEN` and `BGG_PASSWORD` from `.env`, logs into BGG to access private collection data
2. **Check** — On subsequent runs, calls BGG API with `modifiedsince=<last_fetch_date>`; if 0 items changed, falls through to a plays-only check
3. **Plays check** — Queries the plays API (`/xmlapi2/plays`) for authoritative play counts (the collection API's `num_plays` is unreliable for multi-subtype items). If counts differ from cached data, fetches updated play logs and patches the existing dataset
4. **Fetch** — If collection changes exist (or first run), fetches those items; handles HTTP 202 queue responses with retry logic (up to 5 retries, 5s delay)
5. **Parse** — Converts XML to Python dicts with ownership status, ratings, rankings, play logs, and private acquisition data
6. **Plays** — Fetches play logs for changed games where `num_plays` increased; reuses cached plays for everything else (100/page, 1.5s delay between requests)
7. **Merge** — Patches updated games into the full existing dataset loaded from `data/bgg_collection.json`
8. **Change report** — Prints a diff vs the previous fetch; saves lightweight `data/fetch_log.json` for next run
9. **Categorize** — Groups games into: owned, wishlist (5 priority tiers), preordered, previously_owned, want_to_play, want_to_buy, for_trade
10. **Output** — Writes JSON to `data/` with metadata counts + categorized game arrays

### Dashboard — `dashboard/dshb_bgg_collection.html`

Single self-contained HTML file with inline CSS and JS. No build step, no framework.

- **Collection tab** — Full sortable/filterable table of all games with stats
- **Stats tab** — Collection overview with charts
- **Insights tab** — Dynamic visualizations computed at runtime from the live dataset:
  - Weight vs Enjoyment scatter plot
  - Owned vs Wishlist DNA comparison (radar)
  - Mechanism Pairs co-occurrence (bar chart with click-to-expand game lists)
  - Priority Queue (ranked wishlist with Immersion Score weighting)
- Mobile-responsive: flex-wrap tabs, bottom-sheet tooltips, tap-to-toggle interactions

### GitHub Actions

- **`fetch-bgg.yml`** — Runs nightly at 02:00 UTC, fetches latest BGG data, commits changes
- **`deploy-pages.yml`** — Deploys to GitHub Pages on push or after successful fetch workflow (uses `workflow_run` trigger since bot pushes don't fire `on: push`)

## Key Configuration (hardcoded in script)

- `BGG_USERNAME = "jsneij"` — the BGG account to fetch
- `BGG_API_BASE = "https://boardgamegeek.com/xmlapi2"`
- `MAX_RETRIES = 5`, `RETRY_DELAY_SECONDS = 5` — queue polling
- `PLAYS_REQUEST_DELAY = 1.5` — rate limit between per-game play log requests

Credentials live in `.env` (not committed). GitHub Secrets hold the same values for CI.

## Planned Enhancements (see `next steps/to_explore.txt`)

- Enrich with `/thing` API endpoint (designers, mechanics, categories, expansions)
- Normalized multi-dataset schema for SQL/Pandas analysis
