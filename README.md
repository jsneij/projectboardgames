# Board Game Collection Tracker

A personal pipeline that pulls my entire [BoardGameGeek](https://boardgamegeek.com/) collection, scores every game with a custom analytical framework, maps each one to a 203-mechanism encyclopedia, and serves a live dashboard — updated every night, zero manual effort.

**[View the live dashboard](https://jsneij.github.io/projectboardgames/dashboard/dshb_bgg_collection.html)**

---

## What It Does

- **Fetches** my full BGG collection (owned, wishlisted, previously owned, preordered…) via the BGG XML API v2
- **Tracks play logs** using the plays API as the authoritative source — because BGG's collection endpoint lies about play counts for items with multiple subtypes
- **Runs incrementally** — only re-fetches games that changed since the last run, with a secondary plays-only check for logged sessions
- **Scores new games automatically** — when a game is added to the collection, the nightly pipeline calls the Claude API to generate Immersion Scores, descriptions, justifications, and comprehensive mechanism assignments
- **Maps every game to 203 mechanisms** from the *Building Blocks of Tabletop Game Design* encyclopedia by Engelstein & Shalev — structural, turn order, actions, resolution, victory, economy, movement, area control, cards, and more
- **Builds a single-file HTML dashboard** with sortable tables, filterable views, and interactive charts — no frameworks, no build step, just one `.html` file

## The Dashboard

Six tabs, zero dependencies:

| Tab | What's in it |
|-----|-------------|
| **Full Table** | Sortable table of every game — ratings, weight, player count, play history. Filter by Owned / Wishlist / All |
| **IS Framework** | Interactive breakdown of the Immersion Score formula with per-game variable radar charts and zone classification |
| **Insights** | Dynamic visualizations: Weight vs Enjoyment scatter, Owned vs Wishlist DNA radar, Mechanism Pairs co-occurrence with click-to-expand game lists, Priority Queue ranked by Immersion Score |
| **Mechanisms** | Full 203-mechanism encyclopedia browser — taxonomy grid, search, expandable entries with discussion, related mechanisms, sample games, and your collection's games per mechanism |
| **Stats** | Collection overview charts |
| **Games** | Card-based visual browse with expand panels showing descriptions, scores, and mechanism tags |

Everything is mobile-responsive. Tooltips become bottom sheets on small screens, tabs wrap instead of scrolling off-screen.

## The Immersion Score

Games are evaluated using a custom analytical framework:

```
GS = (M × T × G) − F
IS = GS × (Ar / 2)
```

Five variables scored 1–5: **Mechanical Depth (M)**, **Fiction-Mechanic Embodiment (T)** — the gatekeeper; if the theme is decorative, the score collapses — **Meaningful Agency (G)**, **Friction (F)**, and **Art/Production Quality (Ar)**.

The Priority Queue in the Insights tab uses IS with solo/co-op and low-friction multipliers to surface what to buy next from the wishlist.

## Mechanisms

Each game is mapped to mechanisms from the *Complete Encyclopedia of Mechanisms* (Engelstein & Shalev), using the format `CAT-## Name` across 13 categories:

| Prefix | Category |
|--------|----------|
| STR | Game Structure |
| TRN | Turn Order & Structure |
| ACT | Actions |
| RES | Resolution |
| VIC | Victory Points & Conditions |
| UNC | Uncertainty |
| ECO | Economy |
| AUC | Auctions |
| WPL | Worker Placement |
| MOV | Movement |
| ARC | Area Control |
| SET | Set Collection |
| CAR | Cards |

The full encyclopedia data — descriptions, design discussion, cross-references, and sample games for all 203 mechanisms — lives in `data/mechanisms.json`, parsed from the source markdown by `scripts/parse_encyclopedia.py`.

## Automation

Two GitHub Actions workflows:

1. **`fetch-bgg.yml`** — Runs nightly at 02:00 UTC:
   - Fetches the latest data from BGG
   - Syncs the scores file (adds new games, removes departed ones)
   - Auto-scores new games via Claude API (Immersion Score + mechanisms)
   - Commits any changes to `main`

2. **`deploy-pages.yml`** — Deploys to GitHub Pages on push or after a successful fetch run

The dashboard at the link above is always current.

## Running Locally

```bash
# Clone and set up
git clone https://github.com/jsneij/projectboardgames.git
cd projectboardgames
python3 -m venv venv
source venv/bin/activate
pip install -r scripts/requirements.txt

# Add credentials
echo "BGG_BEARER_TOKEN=your_token" > .env
echo "BGG_PASSWORD=your_password" >> .env
echo "ANTHROPIC_API_KEY=your_key" >> .env  # for auto-scoring

# Fetch data
python scripts/fetch_bgg_collection.py
python scripts/sync_scores.py
python scripts/score_new_games.py

# Serve the dashboard
python3 -m http.server 8000
# → open http://localhost:8000/dashboard/dshb_bgg_collection.html
```

## Project Structure

```
├── scripts/
│   ├── fetch_bgg_collection.py    ← BGG collection fetcher
│   ├── sync_scores.py             ← scores file sync (add/remove entries)
│   ├── score_new_games.py         ← auto-scoring via Claude API
│   └── parse_encyclopedia.py      ← encyclopedia markdown → mechanisms.json
├── dashboard/
│   └── dshb_bgg_collection.html   ← single-file dashboard
├── data/
│   ├── bgg_collection.json        ← raw BGG collection data
│   ├── bgg_collection_scores.json ← personal scores + mechanisms (source of truth)
│   ├── mechanisms.json            ← 203-mechanism encyclopedia
│   └── fetch_log.json             ← incremental fetch state
├── docs/
│   ├── immersion_score.md         ← IS framework + user profile
│   └── tabletop mechanics/        ← encyclopedia source markdown
├── .github/workflows/
│   ├── fetch-bgg.yml              ← nightly fetch + score + commit
│   └── deploy-pages.yml           ← GitHub Pages deploy
└── next steps/                    ← backlog
```

---

Built with Python, vanilla HTML/CSS/JS, the BGG XML API v2, and the Claude API.
