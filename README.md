# Board Game Collection Tracker

A personal pipeline that pulls my entire [BoardGameGeek](https://boardgamegeek.com/) collection, crunches the numbers, and serves a live dashboard — updated every night, zero manual effort.

**[View the live dashboard](https://jsneij.github.io/projectboardgames/dashboard/dshb_bgg_collection.html)**

---

## What It Does

- **Fetches** my full BGG collection (owned, wishlisted, previously owned, preordered…) via the BGG XML API v2
- **Tracks play logs** using the plays API as the authoritative source — because BGG's collection endpoint lies about play counts for items with multiple subtypes
- **Runs incrementally** — only re-fetches games that changed since the last run, with a secondary plays-only check for logged sessions
- **Builds a single-file HTML dashboard** with sortable tables, filterable views, and interactive charts — no frameworks, no build step, just one `.html` file

## The Dashboard

Three tabs, zero dependencies:

| Tab | What's in it |
|-----|-------------|
| **Collection** | Full sortable table of every game — ratings, weight, player count, play history. Filter by Owned / Wishlist / All |
| **Stats** | Collection overview charts |
| **Insights** | Dynamic visualizations computed from live data: Weight vs Enjoyment scatter, Owned vs Wishlist DNA radar, Mechanism Pairs co-occurrence with click-to-expand game lists, and a Priority Queue ranked by Immersion Score |

Everything is mobile-responsive. Tooltips become bottom sheets on small screens, tabs wrap instead of scrolling off-screen.

## The Immersion Score

Games are evaluated using a custom analytical framework:

```
IS = ((M × T × G) − F) × (Ar / 2)
```

Five variables scored 1–5: **Mechanical Depth**, **Fiction-Mechanic Embodiment** (the gatekeeper — if the theme is decorative, the score collapses), **Meaningful Agency**, **Friction**, and **Art/Production Quality**.

The Priority Queue in the Insights tab uses IS with solo/co-op and low-friction multipliers to surface what to play next from the wishlist.

## Automation

A GitHub Actions workflow runs nightly at 02:00 UTC:

1. Fetches the latest data from BGG
2. Commits any changes to `main`
3. Triggers a GitHub Pages deployment

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

# Fetch data
python scripts/fetch_bgg_collection.py

# Serve the dashboard
python3 -m http.server 8000
# → open http://localhost:8000/dashboard/dshb_bgg_collection.html
```

## Project Structure

```
├── scripts/
│   ├── fetch_bgg_collection.py    ← the pipeline
│   └── sync_scores.py             ← personal scores sync
├── dashboard/
│   └── dshb_bgg_collection.html   ← single-file dashboard
├── data/                          ← JSON outputs (committed)
├── docs/                          ← scoring framework, mechanics reference
├── .github/workflows/             ← nightly fetch + Pages deploy
└── next steps/                    ← backlog
```

---

Built with Python, vanilla HTML/CSS/JS, and the BGG XML API v2.
