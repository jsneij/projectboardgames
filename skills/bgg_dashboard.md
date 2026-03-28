---
name: bgg-dashboard
description: Design spec and rules for building and extending the BGG collection HTML dashboard. Read this before making any changes to dashboard/dshb_bgg_collection.html.
---

# BGG Dashboard — Design Spec

## Serving

Start the HTTP server from the **project root**:

```bash
python3 -m http.server 8000
```

Open: `http://localhost:8000/dashboard/dshb_bgg_collection.html`

The server must run from the project root so relative paths `../data/` resolve correctly.

---

## Data Contract

### Source files (never copy, never embed)
| Path | Description |
|------|-------------|
| `../data/bgg_collection.json` | Full BGG collection: owned, wishlist, preordered, previously_owned, etc. |
| `../data/bgg_collection_scores.json` | Personal immersion scores: M, T, G, F, Ar, weight, feeling, mechs |

### Merge logic
- **Owned**: `collection.owned[i].name` → key in `scores.owned`
- **Wishlist**: `collection.wishlist.priority_N_*[i].name` → key in `scores.wishlist`
- **Priority** is taken from the bucket key (e.g. `priority_1_must_have` → `1`)

### Derived values (computed at runtime)
| Derived | Formula | Notes |
|---------|---------|-------|
| `GS` | `M × T × G − F` | Game Score |
| `IS` | `GS × (Ar / 2)` | Immersion Score — primary ranking metric |
| `IF` | `5 − F` | Inverted Friction — used as radar axis (higher = less friction) |

### BGG links
Every game name is hyperlinked:
```
https://boardgamegeek.com/boardgame/{bgg_id}
```

---

## Design System

| Token | Value | Use |
|-------|-------|-----|
| `--bg` | `#0f172a` | Page background |
| `--bg-card` | `#1e293b` | Card backgrounds |
| `--bg-card2` | `#0d1a2e` | Nested / alternate card |
| `--border` | `#334155` | Card borders |
| `--text` | `#f1f5f9` | Primary text |
| `--text2` | `#94a3b8` | Secondary text |
| `--text3` | `#64748b` | Muted / labels |
| `--red` | `#ef4444` | Loves it / T≥4 highlight |
| `--amber` | `#f59e0b` | Likes it / IS highlight |
| `--blue` | `#3b82f6` | Scored / links / accents |
| `--green` | `#22c55e` | Solo / G axis |
| `--purple` | `#a855f7` | Wishlist / Ar axis |
| `--indigo` | `#6366f1` | Preordered / mechs |

**Fonts:** `Outfit` (UI) + `JetBrains Mono` (numbers, scores, badges) via Google Fonts CDN.

---

## Radar Chart Specification

5-axis SVG spider diagram. Axes in order (clockwise from top):

| Axis | Meaning | Color |
|------|---------|-------|
| **M** | Mechanical Depth | `#3b82f6` blue |
| **T** | Fiction-Mechanic Embodiment | `#ef4444` red |
| **G** | Meaningful Agency | `#22c55e` green |
| **IF** | Inverted Friction = 5−F | `#f59e0b` amber |
| **Ar** | Art & Production Quality | `#a855f7` purple |

`viewBox="0 0 {size} {size}"`, center `(size/2, size/2)`, radius `size × 0.36`. Angles: `−π/2 + 2πi/5`. Grid rings at 1.25, 2.5, 3.75, 5.0. Fill `rgba(99,102,241,0.25)`, stroke `#6366f1`.

---

## Scatter Map — Bubble Size

```
radius = clamp(6, 22, 800 / bggRank)
```

Games without rank get minimum size (6px).

---

## Tab Structure

| # | ID | Content |
|---|----|---------|
| 1 | overview | Stat cards, IS Leaderboard, Collection DNA, Previously Owned |
| 2 | is-scores | Ranked IS bars + radar card grid (IS ≥ 50) |
| 3 | scatter | M×T scatter with wishlist toggle |
| 4 | table | Sortable table, click-to-expand radar + mechs |
| 5 | wishlist | Priority-grouped cards with thumbnails |
| 6 | patterns | T gatekeeper, mech histogram, IS bands, scatter panels, observations |

---

## Rules

1. Data is never copied or embedded — HTML fetches directly from `../data/`
2. No npm, no build step, no framework — pure HTML/CSS/JS, single file
3. Tabs are the extension point — add new `<div class="tab-pane">` + `<button class="tab-btn">`
4. All charts are pure SVG — no charting libraries
5. Every game name is a BGG hyperlink — never display a name as plain text
6. Do not modify `data/bgg_collection_scores.json` via dashboard code — scores are managed by `scripts/sync_scores.py`
