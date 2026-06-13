# Dashboard Edit Mode — Design Spec

**Date:** 2026-06-13
**Status:** Approved for implementation planning
**Topic:** In-browser editing of personal scores, mechanism tags, and free-text data on the BGG dashboard

## Goal

Let the user edit their personal-scoring data (everything in `data/bgg_collection_scores.json` that does not come from BoardGameGeek) directly in the dashboard UI. Saving must be one action that persists locally **and** ships the change to the live GitHub Pages site.

## User-Facing Behavior

1. User runs `/dashboard_BGG`. Dashboard opens in the browser as today, with one new control in the header: an **"Edit mode"** pill toggle.
2. The toggle is visible only when the dashboard detects it is being served by the local editor server (not the static GitHub Pages deployment).
3. Toggling **Edit mode ON** makes every supported field editable in place inside the existing Collection table. A floating banner shows `"Edit mode active — N changes pending"`.
4. The user makes changes across one or many rows. Changes accumulate in memory; nothing is written to disk until the toggle goes OFF.
5. Toggling **Edit mode OFF**:
   - If there are no pending changes → silently return to view mode with a toast `"No changes"`.
   - If there are pending changes → POST the updated scores JSON to the local server, which writes the file, commits, and pushes. A toast reports the outcome (`"Saved & pushed — N games updated"`, `"Committed locally; push failed: …"`, or a validation error).
6. After a successful save, the dashboard reloads its in-memory scores from the just-written JSON so the view stays consistent.

## Editable Fields

All fields stored in `bgg_collection_scores.json`, for every collection (`owned`, `wishlist`, `preordered`):

| Field | Type | UI |
|------|------|-----|
| `M`, `T`, `G`, `F`, `Ar` | int 0–5 | numeric input, clamped |
| `weight` | float | numeric input |
| `type` | string (e.g. `"co-op"`, `"competitive"`) | dropdown, options derived from existing values |
| `feeling` | string (e.g. `"Total"`, `"Engaging"`) | dropdown, options derived from existing values |
| `mechs` | array of strings | tag-pill UI: click pill to remove; `+ add mechanism` opens a searchable dropdown populated from `data/mechanisms.json` |
| `description` | string | auto-expanding `<textarea>` |
| `justification` | string | auto-expanding `<textarea>` |
| `name` | string (short display) | text input |

BGG-sourced fields (rank, BGG rating, play counts, image, designer, etc.) remain read-only and unaffected — they are not stored in this JSON.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│ Browser (existing single-file HTML dashboard, extended)      │
│  - Renders rows as inputs when Edit mode = ON                │
│  - Tracks pendingChanges in memory + localStorage backup     │
│  - Calls GET /api/status on load to decide if edit toggle is │
│    visible                                                   │
│  - Calls POST /api/save on Edit-mode-OFF                     │
└────────────────────────┬─────────────────────────────────────┘
                         │ http://127.0.0.1:8765
                         ▼
┌──────────────────────────────────────────────────────────────┐
│ scripts/dashboard_server.py (stdlib http.server, ~150 lines) │
│  - Serves dashboard/ and data/ as static files               │
│  - GET  /api/status    → {"mode":"edit"}                     │
│  - POST /api/save      → validate, write, commit, push       │
│  - Binds 127.0.0.1 only                                      │
└────────────────────────┬─────────────────────────────────────┘
                         │ subprocess
                         ▼
                  git (in repo root)
```

### New / changed files

| File | Change |
|------|--------|
| `scripts/dashboard_server.py` | **NEW** — local editor server |
| `dashboard/dshb_bgg_collection.html` | **EDITED** — edit toggle, input rendering, mechanism picker, save logic |
| `.claude/commands/dashboard_BGG.md` | **EDITED** — invoke the server instead of opening the bare HTML file |

Untouched: `sync_scores.py`, `fetch_bgg_collection.py`, GitHub Actions workflows, the `data/` JSON schema.

## Server Endpoints

### `GET /api/status`

Response: `200 {"mode": "edit"}`

Used by the dashboard to detect "I am being served locally, edit toggle is allowed."

### `POST /api/save`

Request body:
```json
{
  "scores": { ...complete updated bgg_collection_scores.json content... },
  "changed_games": ["Burgle Bros.", "Catan", ...]   // names whose entries differ from on-disk
}
```

Server flow:
1. Validate payload:
   - Top-level keys must be a subset of `{owned, wishlist, preordered}`.
   - Each game entry must have the existing schema keys; integer scores in 0..5; `mechs` is `array<string>`; numeric `weight`; strings for text fields.
   - Reject any game name in the payload that is **not already present** in the on-disk `bgg_collection_scores.json`. This feature only **updates** existing entries — adding or removing entries remains the exclusive job of `sync_scores.py`, consistent with the existing project rule.
2. Atomic write: write to `data/bgg_collection_scores.json.tmp`, fsync, rename over the real file.
3. `git add data/bgg_collection_scores.json`
4. `git diff --cached --quiet` — if exit 0 (no staged diff), return `{"status":"noop"}`.
5. `git commit -m "Update collection scores: <comma-joined changed_games, truncated to 5 + count>"`
6. `git push` — best-effort; capture stderr.
7. Return:
   ```json
   {
     "status": "ok" | "committed_not_pushed" | "validation_error" | "noop",
     "commit": "<sha or null>",
     "pushed": true/false,
     "message": "<human-readable summary>",
     "errors": [ {"field": "owned.Catan.M", "problem": "must be 0..5"} ]   // only on validation_error
   }
   ```

### Static file serving

The server serves the repo's `dashboard/` and `data/` directories. No directory listing, no path-traversal (limit `..` segments).

## Dashboard Side

### State

- `originalScores` — the JSON snapshot loaded on page load.
- `pendingChanges` — `{collection: {gameName: {field: newValue}}}`. Updated on every input event.
- `editMode` — boolean, controlled by the header toggle.

### localStorage backup

On every change, `pendingChanges` is persisted to `localStorage["bgg_pending_edits"]` keyed by a session timestamp. On page load with the local server present:
- If a non-empty `pendingChanges` exists for the most recent session, prompt: *"Unsaved edits found from {time}. Restore?"* — Yes restores, No discards.

### Mechanism picker UX

- Each currently-tagged mechanism renders as a removable pill.
- `+ add mechanism` opens a searchable dropdown (typeahead) sourced from `data/mechanisms.json`.
- Mechanisms are grouped by category prefix (`STR-`, `ACT-`, etc.) in the dropdown.
- Selecting an item adds the pill; the dropdown stays open for rapid multi-add until the user clicks outside.

### Field validation in the browser

Inputs enforce client-side constraints (0..5 clamps, required fields) but the server is the source of truth — it re-validates everything.

## Error Handling

| Failure | Behavior |
|---------|----------|
| Validation error from server | Toast with field-level errors; edit mode stays **ON**; nothing is lost. |
| `git commit` succeeds, `git push` fails (network/auth) | Toast: `"Saved & committed locally. Push failed — see terminal. Run \`git push\` when ready."` Edit mode returns to view; data is safe. |
| Server unreachable when toggling OFF | Toast: `"Can't reach local server — your edits are kept in this browser. Restart \`/dashboard_BGG\` and try again."` Edit mode stays ON. |
| Page reload mid-edit | `localStorage` restore prompt as described above. |
| Bad JSON on disk at server startup | Server refuses to start, prints a clear error, exits non-zero. |
| Concurrent BGG fetch (nightly Action) writes to `bgg_collection.json` while edit mode is open | Not a conflict — different file. Scores JSON is owned entirely by this server during a local session. |

## Out of Scope (Intentional)

- Multi-user or concurrent editing — single-user assumption.
- Authentication on the local server — `127.0.0.1`-only bind is the only barrier.
- Editing through the deployed GitHub Pages site — read-only there by design; the `/api/status` probe simply 404s and the toggle stays hidden.
- Schema changes to `bgg_collection_scores.json` — same shape, this feature only mutates existing fields.
- A history / undo of past saves — git is the history.

## Testing Strategy

- **Server unit tests** (`tests/test_dashboard_server.py`): payload validation, atomic write, git command sequencing (mock `subprocess`), noop short-circuit, error paths.
- **Manual smoke test**: run `/dashboard_BGG`, toggle Edit ON, tweak a score, toggle OFF, verify (a) JSON file mutated, (b) git log shows a commit, (c) `git status` clean, (d) push reached `origin/main`.
- **Live-site regression check**: visit the GitHub Pages URL after a save; confirm the change appears within a minute of `deploy-pages.yml` completing.

## Success Criteria

1. `/dashboard_BGG` launches the dashboard with edit capability.
2. Toggling Edit OFF after changes results in `bgg_collection_scores.json` updated on disk, a commit landed on the local branch, and (on success) the commit pushed to `origin`.
3. Toggling Edit OFF with no changes is silent and does nothing destructive.
4. The deployed GitHub Pages dashboard remains functional and read-only (no edit toggle visible).
5. `sync_scores.py`, the BGG fetch pipeline, and the GitHub Actions workflows continue to work unchanged.
