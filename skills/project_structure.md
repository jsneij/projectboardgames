---
name: project-structure
description: Standard folder structure, file naming conventions, and portability rules for data + dashboard projects. Apply this when organizing or reorganizing any project that combines Python scripts, local data files, and an HTML dashboard.
---

# Project Structure — Data + Dashboard Projects

## Directory Layout

```
ProjectRoot/
├── CLAUDE.md                        ← Claude Code instructions (always at root)
├── runproject.command               ← macOS double-click launcher (always at root)
├── .env                             ← credentials (never committed, never packaged)
├── .claude/
│   └── commands/                    ← Claude slash commands (/refresh, /dashboard, etc.)
├── scripts/                         ← all executable scripts (Python, bash, etc.)
├── dashboard/                       ← web dashboard files
│   └── dshb_<project>.html          ← single-file HTML dashboard
├── data/                            ← all data files (source of truth)
├── docs/                            ← reference documents and framework definitions
│   └── <topic>/                     ← sub-folders for large reference sets
├── skills/                          ← reusable Claude skill files (including this one)
└── next steps/                      ← backlog, ideas, open questions
```

## Rules

### Portability — always relative paths
- The dashboard fetches data using **relative paths** (`../data/file.json`), never absolute
- Scripts reference data using paths **relative to project root** (e.g. `"data/file.json"`) or relative to `__file__` with `Path(__file__).parent.parent / "data"`
- The project folder can be zipped, uploaded to Drive, downloaded anywhere, and work immediately with `python3 -m http.server 8000`

### `data/` — not `output/`
- Named `data/` because it contains files that are both generated (fetched JSON) and hand-edited (scores, config)
- `output/` implies disposable/regeneratable — wrong when any file is manually curated

### `docs/` — reference knowledge
- Framework definitions, encyclopedias, design specs, pattern documentation
- Large reference sets go in a named sub-folder inside `docs/` (e.g. `docs/tabletop mechanics/`)
- Not for runnable code, not for data

### `skills/` — reusable Claude instructions
- Each file has YAML frontmatter: `name`, `description`
- Describes *how to do something* — a pattern, a spec, a set of rules
- Portable: copy the `skills/` folder to another project and Claude picks them up

### `.claude/commands/` — project slash commands
- Each file is a slash command (`/filename` triggers it)
- Project-specific workflows: how to run the fetch pipeline, how to launch the dashboard
- Reference `docs/`, `data/`, `scripts/` by their correct paths

### `scripts/` — not at root
- Scripts live in `scripts/`, not scattered at the project root
- Keeps the root clean: only `CLAUDE.md`, `runproject.command`, `.env`, and top-level folders

### `venv/` — local only
- Never packaged, never committed
- Recreate with: `python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt`

---

## Dashboard Naming Convention

```
dashboard/dshb_<project_shortname>.html
```

- Prefix `dshb_` makes dashboard files instantly recognizable
- Single self-contained HTML file — no build step, no framework, no npm
- All charts are pure SVG
- Fetches data via relative path `../data/`

---

## runproject.command

Every project gets a `runproject.command` at the root. Double-click on macOS to:
1. Kill any existing process on port 8000
2. Start `python3 -m http.server 8000` from the project root
3. Open the dashboard in Chrome
4. Wait for keypress → stop the server cleanly

Template:

```bash
#!/bin/bash
cd "$(dirname "$0")"

lsof -ti :8000 | xargs kill -9 2>/dev/null

python3 -m http.server 8000 &
SERVER_PID=$!

sleep 1

open -a "Google Chrome" "http://localhost:8000/dashboard/dshb_<project>.html"

echo ""
echo "  Dashboard running at http://localhost:8000/dashboard/dshb_<project>.html"
echo "  Server PID: $SERVER_PID"
echo ""
echo "  Press any key to stop the server..."
read -n 1

kill $SERVER_PID 2>/dev/null
echo "  Server stopped."
```

Make executable after creating: `chmod +x runproject.command`

---

## What Goes Where — Quick Reference

| File type | Location |
|-----------|----------|
| Claude project instructions | `CLAUDE.md` (root) |
| Slash commands | `.claude/commands/*.md` |
| Python / bash scripts | `scripts/` |
| Generated or curated data | `data/` |
| HTML dashboard | `dashboard/dshb_<name>.html` |
| Framework definitions, specs | `docs/` |
| Large reference document sets | `docs/<topic>/` |
| Reusable Claude skill files | `skills/` |
| Backlog / open questions | `next steps/` |
| Credentials | `.env` (root, never committed) |
| macOS launcher | `runproject.command` (root) |
