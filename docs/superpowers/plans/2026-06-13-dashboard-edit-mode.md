# Dashboard Edit Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an in-browser "Edit mode" to the BGG dashboard that lets the user mutate any field in `data/bgg_collection_scores.json`, and when toggled off, atomically writes the file, commits, and pushes to `origin`.

**Architecture:** A new stdlib-only Python server (`scripts/dashboard_server.py`) serves the existing single-file dashboard plus `data/` as static files and exposes `GET /api/status` + `POST /api/save`. The dashboard (extended HTML/vanilla JS) probes `/api/status` to decide whether to show the toggle, accumulates edits in memory + `localStorage`, and POSTs the full updated JSON when Edit mode is switched off. `/api/save` validates, atomically writes the file, then runs `git add`/`commit`/`push` via `subprocess`.

**Tech Stack:** Python 3 stdlib (`http.server`, `json`, `subprocess`, `tempfile`, `pathlib`), pytest (server tests), vanilla HTML/CSS/JS (dashboard).

**Spec:** `docs/superpowers/specs/2026-06-13-dashboard-edit-mode-design.md`

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `scripts/dashboard_server.py` | NEW | HTTP server entrypoint + handler; thin wrapper around the three helpers below |
| `scripts/dashboard_server_core.py` | NEW | Pure functions: `validate_scores`, `atomic_write_json`, `commit_and_push`. No HTTP, easy to test. |
| `tests/__init__.py` | NEW | Marks `tests/` as a package |
| `tests/test_dashboard_server_core.py` | NEW | pytest suite for the core helpers (validation, atomic write, git command sequencing) |
| `dashboard/dshb_bgg_collection.html` | MODIFY | Adds edit toggle, input rendering for editable fields, mechanism picker, save flow |
| `.claude/commands/dashboard_BGG.md` | MODIFY | Launch `python scripts/dashboard_server.py` and point at `http://localhost:8765` |

---

### Task 1: Test scaffold + repo-shaped tmp fixture

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Create empty `tests/__init__.py`**

Create: `tests/__init__.py`

```python
```

- [ ] **Step 2: Create `tests/conftest.py` with a fixture that builds a temporary git repo containing a minimal `data/bgg_collection_scores.json`**

Create: `tests/conftest.py`

```python
import json
import shutil
import subprocess
from pathlib import Path

import pytest


SAMPLE_SCORES = {
    "owned": {
        "Burgle Bros.": {
            "name": "Burgle Bros.",
            "type": "co-op",
            "weight": 2.31,
            "M": 3, "T": 4, "G": 3, "F": 1, "Ar": 4,
            "feeling": "Engaging",
            "mechs": ["STR-02 Cooperative Games"],
            "description": "",
            "justification": "",
        }
    },
    "wishlist": {},
    "preordered": {},
}


def _run(args, cwd):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A temp dir initialized as a git repo with a remote bare repo as 'origin'."""
    work = tmp_path / "work"
    remote = tmp_path / "remote.git"

    work.mkdir()
    (work / "data").mkdir()
    (work / "data" / "bgg_collection_scores.json").write_text(
        json.dumps(SAMPLE_SCORES, indent=2), encoding="utf-8"
    )

    _run(["git", "init", "-q", "-b", "main"], cwd=work)
    _run(["git", "config", "user.email", "test@example.com"], cwd=work)
    _run(["git", "config", "user.name", "Test"], cwd=work)
    _run(["git", "add", "."], cwd=work)
    _run(["git", "commit", "-q", "-m", "initial"], cwd=work)

    _run(["git", "init", "-q", "--bare", str(remote)], cwd=tmp_path)
    _run(["git", "remote", "add", "origin", str(remote)], cwd=work)
    _run(["git", "push", "-q", "-u", "origin", "main"], cwd=work)

    return work


@pytest.fixture
def scores_path(repo: Path) -> Path:
    return repo / "data" / "bgg_collection_scores.json"
```

- [ ] **Step 3: Verify pytest collects the conftest**

Run: `cd /Users/jsneij/Desktop/Claude/ProjectBoardGames && source venv/bin/activate && python -m pytest tests/ -q --collect-only`

Expected: `no tests ran` (no test files yet) — but no errors about the conftest.

If pytest is not installed: `pip install pytest`.

- [ ] **Step 4: Commit**

```bash
git add tests/__init__.py tests/conftest.py
git commit -m "test: add repo fixture for dashboard server tests"
```

---

### Task 2: `validate_scores` — happy path

**Files:**
- Create: `scripts/dashboard_server_core.py`
- Create: `tests/test_dashboard_server_core.py`

- [ ] **Step 1: Write the failing test**

Create: `tests/test_dashboard_server_core.py`

```python
import json
from pathlib import Path

from scripts.dashboard_server_core import validate_scores


def _load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def test_validate_scores_accepts_unchanged_payload(scores_path):
    existing = _load(scores_path)
    errors = validate_scores(existing, existing)
    assert errors == []
```

- [ ] **Step 2: Run test — expect failure (module missing)**

Run: `python -m pytest tests/test_dashboard_server_core.py::test_validate_scores_accepts_unchanged_payload -v`

Expected: `ModuleNotFoundError: No module named 'scripts.dashboard_server_core'`.

- [ ] **Step 3: Minimal core module — `validate_scores` skeleton**

Create: `scripts/dashboard_server_core.py`

```python
"""Pure helpers for the dashboard editor server: validation, atomic write, git ops."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any


COLLECTIONS = ("owned", "wishlist", "preordered")
SCORE_FIELDS = ("M", "T", "G", "F", "Ar")
TEXT_FIELDS = ("name", "type", "feeling", "description", "justification")


def validate_scores(payload: Any, existing: dict) -> list[dict]:
    """Return a list of {field, problem} errors. Empty list = valid.

    Rules:
      - top-level keys must be a subset of COLLECTIONS
      - every game name in the payload must already exist in `existing`
        (this feature only updates; adds/removes go through sync_scores.py)
      - scores M/T/G/F/Ar must be ints in 0..5 when present
      - weight must be numeric when present
      - mechs must be a list of strings when present
      - text fields must be strings when present
    """
    errors: list[dict] = []

    if not isinstance(payload, dict):
        return [{"field": "<root>", "problem": "must be an object"}]

    for collection, games in payload.items():
        if collection not in COLLECTIONS:
            errors.append({"field": collection, "problem": "unknown collection"})
            continue
        if not isinstance(games, dict):
            errors.append({"field": collection, "problem": "must be an object"})
            continue
        existing_games = existing.get(collection, {})
        for name, entry in games.items():
            prefix = f"{collection}.{name}"
            if name not in existing_games:
                errors.append({
                    "field": prefix,
                    "problem": "unknown game; add/remove entries via sync_scores.py",
                })
                continue
            if not isinstance(entry, dict):
                errors.append({"field": prefix, "problem": "must be an object"})
                continue
            for f in SCORE_FIELDS:
                if f in entry:
                    v = entry[f]
                    if not isinstance(v, int) or isinstance(v, bool) or not (0 <= v <= 5):
                        errors.append({"field": f"{prefix}.{f}", "problem": "must be int 0..5"})
            if "weight" in entry:
                w = entry["weight"]
                if isinstance(w, bool) or not isinstance(w, (int, float)):
                    errors.append({"field": f"{prefix}.weight", "problem": "must be numeric"})
            if "mechs" in entry:
                mechs = entry["mechs"]
                if not isinstance(mechs, list) or any(not isinstance(m, str) for m in mechs):
                    errors.append({"field": f"{prefix}.mechs", "problem": "must be array of strings"})
            for tf in TEXT_FIELDS:
                if tf in entry and not isinstance(entry[tf], str):
                    errors.append({"field": f"{prefix}.{tf}", "problem": "must be a string"})

    return errors
```

- [ ] **Step 4: Make `scripts/` importable from tests**

Create: `scripts/__init__.py`

```python
```

(Touch the file — empty, just makes `scripts` a package.)

Also add a `conftest.py` next to the `tests/` dir to put the repo on `sys.path`:

Create: `conftest.py` (at the **project root**)

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
```

- [ ] **Step 5: Run test — expect pass**

Run: `python -m pytest tests/test_dashboard_server_core.py::test_validate_scores_accepts_unchanged_payload -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/__init__.py scripts/dashboard_server_core.py tests/test_dashboard_server_core.py conftest.py
git commit -m "feat: add validate_scores with happy-path test"
```

---

### Task 3: `validate_scores` — rejects bad inputs

**Files:**
- Modify: `tests/test_dashboard_server_core.py`

- [ ] **Step 1: Add rejection tests**

Append to `tests/test_dashboard_server_core.py`:

```python
def test_validate_scores_rejects_non_object_root():
    errors = validate_scores("not a dict", {})
    assert errors == [{"field": "<root>", "problem": "must be an object"}]


def test_validate_scores_rejects_unknown_collection(scores_path):
    existing = _load(scores_path)
    errors = validate_scores({"junk": {}}, existing)
    assert {"field": "junk", "problem": "unknown collection"} in errors


def test_validate_scores_rejects_unknown_game(scores_path):
    existing = _load(scores_path)
    payload = {"owned": {"Made Up Game": {"M": 3}}}
    errors = validate_scores(payload, existing)
    assert errors == [{
        "field": "owned.Made Up Game",
        "problem": "unknown game; add/remove entries via sync_scores.py",
    }]


def test_validate_scores_rejects_out_of_range_score(scores_path):
    existing = _load(scores_path)
    payload = {"owned": {"Burgle Bros.": {"M": 9}}}
    errors = validate_scores(payload, existing)
    assert {"field": "owned.Burgle Bros..M", "problem": "must be int 0..5"} in errors


def test_validate_scores_rejects_non_int_score(scores_path):
    existing = _load(scores_path)
    payload = {"owned": {"Burgle Bros.": {"T": "five"}}}
    errors = validate_scores(payload, existing)
    assert {"field": "owned.Burgle Bros..T", "problem": "must be int 0..5"} in errors


def test_validate_scores_rejects_mechs_not_array_of_strings(scores_path):
    existing = _load(scores_path)
    payload = {"owned": {"Burgle Bros.": {"mechs": ["ok", 42]}}}
    errors = validate_scores(payload, existing)
    assert {"field": "owned.Burgle Bros..mechs", "problem": "must be array of strings"} in errors
```

- [ ] **Step 2: Run new tests — expect all pass**

Run: `python -m pytest tests/test_dashboard_server_core.py -v`

Expected: 6 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_dashboard_server_core.py
git commit -m "test: cover validate_scores rejection paths"
```

---

### Task 4: `atomic_write_json`

**Files:**
- Modify: `scripts/dashboard_server_core.py`
- Modify: `tests/test_dashboard_server_core.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dashboard_server_core.py`:

```python
from scripts.dashboard_server_core import atomic_write_json


def test_atomic_write_json_replaces_file_and_leaves_no_tmp(tmp_path):
    target = tmp_path / "out.json"
    target.write_text('{"old": true}', encoding="utf-8")
    atomic_write_json(target, {"new": 42})
    assert json.loads(target.read_text(encoding="utf-8")) == {"new": 42}
    leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []
```

- [ ] **Step 2: Run test — expect failure (function missing)**

Run: `python -m pytest tests/test_dashboard_server_core.py::test_atomic_write_json_replaces_file_and_leaves_no_tmp -v`

Expected: `ImportError: cannot import name 'atomic_write_json'`.

- [ ] **Step 3: Implement `atomic_write_json`**

Append to `scripts/dashboard_server_core.py`:

```python
def atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON to `path` atomically: write to .tmp, fsync, rename over real file."""
    path = Path(path)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise
```

- [ ] **Step 4: Run test — expect pass**

Run: `python -m pytest tests/test_dashboard_server_core.py -v`

Expected: all tests pass (7 total).

- [ ] **Step 5: Commit**

```bash
git add scripts/dashboard_server_core.py tests/test_dashboard_server_core.py
git commit -m "feat: add atomic_write_json"
```

---

### Task 5: `commit_and_push` — happy path

**Files:**
- Modify: `scripts/dashboard_server_core.py`
- Modify: `tests/test_dashboard_server_core.py`

- [ ] **Step 1: Write the failing test using the `repo` fixture**

Append to `tests/test_dashboard_server_core.py`:

```python
from scripts.dashboard_server_core import commit_and_push


def test_commit_and_push_happy_path(repo, scores_path):
    # Mutate the scores file so there's something to commit
    data = _load(scores_path)
    data["owned"]["Burgle Bros."]["M"] = 5
    atomic_write_json(scores_path, data)

    result = commit_and_push(["Burgle Bros."], scores_path, repo)

    assert result["status"] == "ok"
    assert result["pushed"] is True
    assert len(result["commit"]) == 40  # full sha
    # The commit landed
    log = subprocess.run(
        ["git", "log", "-1", "--format=%s"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert log == "Update collection scores: Burgle Bros."
```

- [ ] **Step 2: Run test — expect failure**

Run: `python -m pytest tests/test_dashboard_server_core.py::test_commit_and_push_happy_path -v`

Expected: `ImportError` for `commit_and_push`.

- [ ] **Step 3: Implement `commit_and_push`**

Append to `scripts/dashboard_server_core.py`:

```python
def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


def _build_commit_message(changed_games: list[str]) -> str:
    shown = changed_games[:5]
    extra = f" (+{len(changed_games) - 5} more)" if len(changed_games) > 5 else ""
    return f"Update collection scores: {', '.join(shown)}{extra}"


def commit_and_push(changed_games: list[str], scores_path: Path, repo_root: Path) -> dict:
    """Stage scores_path, commit, push. Returns a status dict.

    status values:
      "noop"                  — nothing staged, nothing to do
      "ok"                    — commit + push both succeeded
      "committed_not_pushed"  — commit succeeded but push failed (commit is safe locally)
      "error"                 — git add or git commit failed (no commit exists)
    """
    rel = Path(scores_path).resolve().relative_to(Path(repo_root).resolve())

    add = _run_git(["add", str(rel)], repo_root)
    if add.returncode != 0:
        return {"status": "error", "message": f"git add failed: {add.stderr.strip()}"}

    diff = _run_git(["diff", "--cached", "--quiet"], repo_root)
    if diff.returncode == 0:
        return {"status": "noop", "message": "No changes to commit.", "pushed": False, "commit": None}

    msg = _build_commit_message(changed_games)
    commit = _run_git(["commit", "-m", msg], repo_root)
    if commit.returncode != 0:
        return {"status": "error", "message": f"git commit failed: {commit.stderr.strip()}"}

    sha = _run_git(["rev-parse", "HEAD"], repo_root).stdout.strip()

    push = _run_git(["push"], repo_root)
    if push.returncode != 0:
        return {
            "status": "committed_not_pushed",
            "commit": sha,
            "pushed": False,
            "message": f"Saved & committed locally. Push failed: {push.stderr.strip()}",
        }

    return {
        "status": "ok",
        "commit": sha,
        "pushed": True,
        "message": f"Saved & pushed {len(changed_games)} game(s).",
    }
```

- [ ] **Step 4: Run test — expect pass**

Run: `python -m pytest tests/test_dashboard_server_core.py::test_commit_and_push_happy_path -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/dashboard_server_core.py tests/test_dashboard_server_core.py
git commit -m "feat: add commit_and_push happy path"
```

---

### Task 6: `commit_and_push` — noop, push failure, commit message truncation

**Files:**
- Modify: `tests/test_dashboard_server_core.py`

- [ ] **Step 1: Write the additional tests**

Append to `tests/test_dashboard_server_core.py`:

```python
def test_commit_and_push_returns_noop_when_no_diff(repo, scores_path):
    result = commit_and_push(["Burgle Bros."], scores_path, repo)
    assert result["status"] == "noop"
    # Repo is clean
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert status == ""


def test_commit_and_push_returns_committed_not_pushed_when_remote_is_gone(repo, scores_path, tmp_path):
    # Mutate the file
    data = _load(scores_path)
    data["owned"]["Burgle Bros."]["G"] = 5
    atomic_write_json(scores_path, data)
    # Point origin at a non-existent path so push fails
    subprocess.run(["git", "remote", "set-url", "origin", str(tmp_path / "does-not-exist")],
                   cwd=repo, check=True)

    result = commit_and_push(["Burgle Bros."], scores_path, repo)

    assert result["status"] == "committed_not_pushed"
    assert result["pushed"] is False
    assert len(result["commit"]) == 40
    # The commit is still in the local log
    log = subprocess.run(
        ["git", "log", "-1", "--format=%s"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert log.startswith("Update collection scores:")


def test_commit_message_truncates_after_five_games():
    from scripts.dashboard_server_core import _build_commit_message
    names = [f"Game {i}" for i in range(8)]
    msg = _build_commit_message(names)
    assert msg == "Update collection scores: Game 0, Game 1, Game 2, Game 3, Game 4 (+3 more)"
```

- [ ] **Step 2: Run all tests — expect 10 passed**

Run: `python -m pytest tests/test_dashboard_server_core.py -v`

Expected: 10 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_dashboard_server_core.py
git commit -m "test: cover commit_and_push noop, push failure, message truncation"
```

---

### Task 7: HTTP server entrypoint + `/api/status`

**Files:**
- Create: `scripts/dashboard_server.py`

- [ ] **Step 1: Create the server file**

Create: `scripts/dashboard_server.py`

```python
"""Local HTTP server for the BGG dashboard editor.

Serves the existing single-file dashboard plus `data/` as static assets and
exposes two endpoints:

  GET  /api/status   → {"mode": "edit"}
  POST /api/save     → validate + write + git add/commit/push

Bind 127.0.0.1 only — single-user local-only tool.
"""

from __future__ import annotations

import http.server
import json
import os
import sys
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent

# Allow the test suite or other tools to import the core helpers directly
sys.path.insert(0, str(REPO_ROOT))
from scripts.dashboard_server_core import (  # noqa: E402
    atomic_write_json,
    commit_and_push,
    validate_scores,
)

SCORES_PATH = REPO_ROOT / "data" / "bgg_collection_scores.json"
DASHBOARD_HTML = REPO_ROOT / "dashboard" / "dshb_bgg_collection.html"
ALLOWED_DIRS = ("dashboard", "data")
HOST = "127.0.0.1"
PORT = int(os.environ.get("DASHBOARD_PORT", "8765"))


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(REPO_ROOT), **kwargs)

    # ── Logging: keep server output tidy ────────────────────────────────
    def log_message(self, fmt, *args):
        sys.stderr.write("[dashboard_server] " + fmt % args + "\n")

    # ── Helpers ─────────────────────────────────────────────────────────
    def _send_json(self, code: int, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _safe_static_path(self, raw_path: str) -> bool:
        path = urlparse(raw_path).path.lstrip("/")
        if not path:
            return False
        if ".." in path.split("/"):
            return False
        top = path.split("/", 1)[0]
        return top in ALLOWED_DIRS

    # ── Routes ──────────────────────────────────────────────────────────
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path in ("/", ""):
            self._send_dashboard()
            return
        if parsed.path == "/api/status":
            self._send_json(200, {"mode": "edit"})
            return
        if self._safe_static_path(self.path):
            super().do_GET()
            return
        self.send_error(404, "Not Found")

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/save":
            self.send_error(404, "Not Found")
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send_json(400, {
                "status": "validation_error",
                "errors": [{"field": "<root>", "problem": "invalid JSON"}],
                "message": "Invalid JSON.",
            })
            return

        scores = body.get("scores")
        changed_games = body.get("changed_games") or []

        with open(SCORES_PATH, encoding="utf-8") as f:
            existing = json.load(f)

        errors = validate_scores(scores, existing)
        if errors:
            self._send_json(400, {
                "status": "validation_error",
                "errors": errors,
                "message": "Validation failed.",
            })
            return

        atomic_write_json(SCORES_PATH, scores)
        result = commit_and_push(list(changed_games), SCORES_PATH, REPO_ROOT)
        code = 200 if result["status"] in ("ok", "committed_not_pushed", "noop") else 500
        self._send_json(code, result)

    def _send_dashboard(self) -> None:
        if not DASHBOARD_HTML.exists():
            self.send_error(404, "dashboard HTML missing")
            return
        data = DASHBOARD_HTML.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    httpd = http.server.ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}/"
    print(f"[dashboard_server] Serving on {url}  (Ctrl+C to stop)")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[dashboard_server] shutting down")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Manual smoke test — `/api/status`**

Run:
```bash
cd /Users/jsneij/Desktop/Claude/ProjectBoardGames && source venv/bin/activate
python scripts/dashboard_server.py &
SERVER_PID=$!
sleep 1
curl -s http://127.0.0.1:8765/api/status
echo
kill $SERVER_PID
```

Expected: `{"mode": "edit"}`.

- [ ] **Step 3: Manual smoke test — static serving + 404**

Run:
```bash
python scripts/dashboard_server.py &
SERVER_PID=$!
sleep 1
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8765/                                      # 200
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8765/data/bgg_collection_scores.json       # 200
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8765/../etc/passwd                         # 404
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8765/scripts/dashboard_server.py           # 404
kill $SERVER_PID
```

Expected: `200`, `200`, `404`, `404`.

- [ ] **Step 4: Commit**

```bash
git add scripts/dashboard_server.py
git commit -m "feat: add HTTP server with /api/status and /api/save"
```

---

### Task 8: Dashboard — detect local server and add Edit-mode toggle

**Files:**
- Modify: `dashboard/dshb_bgg_collection.html`

Goal: add the toggle to the header, visible **only** when the local server is reachable. No editing behavior yet — just the toggle and a banner.

- [ ] **Step 1: Locate the header and add the toggle markup**

Find the dashboard's top header area (search the file for the `<h1>` or the existing tab-bar markup). Add this block as the **last child** of the header / toolbar area:

```html
<div id="edit-mode-wrap" style="display:none;align-items:center;gap:8px;margin-left:auto">
  <label class="edit-toggle" title="Toggle edit mode">
    <input type="checkbox" id="edit-toggle-checkbox">
    <span class="edit-toggle-pill"></span>
    <span class="edit-toggle-label">Edit mode</span>
  </label>
</div>

<div id="edit-banner" style="display:none;position:sticky;top:0;z-index:50;
     background:linear-gradient(90deg,#ffdc78,#ffa94d);color:#1a1a1a;
     padding:8px 14px;font-size:13px;font-weight:600;text-align:center">
  Edit mode active — <span id="edit-pending-count">0</span> changes pending
</div>
```

- [ ] **Step 2: Add the toggle CSS**

In the dashboard's existing `<style>` block, add:

```css
.edit-toggle { display:inline-flex; align-items:center; gap:6px; cursor:pointer; user-select:none; }
.edit-toggle input { display:none; }
.edit-toggle-pill {
  width: 34px; height: 18px; background: #444; border-radius: 999px; position: relative;
  transition: background .15s ease;
}
.edit-toggle-pill::after {
  content: ""; width: 14px; height: 14px; background: #fff; border-radius: 50%;
  position: absolute; top: 2px; left: 2px; transition: left .15s ease;
}
.edit-toggle input:checked + .edit-toggle-pill { background: #ffa94d; }
.edit-toggle input:checked + .edit-toggle-pill::after { left: 18px; }
.edit-toggle-label { font-size: 12px; opacity: .85; }
```

- [ ] **Step 3: Add the status probe + toggle wiring at the bottom of the dashboard's main script**

In the dashboard's existing `<script>` block, add (near the top of the script, before any code that uses `editMode`):

```javascript
const Edit = {
  available: false,
  on: false,
  pending: {},        // { collection: { gameName: { field: value } } }
  original: null,     // snapshot of scores at load time
};

async function detectEditServer() {
  try {
    const r = await fetch('/api/status', { cache: 'no-store' });
    if (!r.ok) return false;
    const j = await r.json();
    return j.mode === 'edit';
  } catch {
    return false;
  }
}

function setEditMode(on) {
  Edit.on = on;
  document.getElementById('edit-banner').style.display = on ? 'block' : 'none';
  document.body.classList.toggle('edit-mode', on);
  renderPendingCount();
  // Re-render the collection table (function added in later tasks)
  if (typeof renderCollection === 'function') renderCollection();
}

function renderPendingCount() {
  let n = 0;
  for (const c of Object.values(Edit.pending)) n += Object.keys(c).length;
  const el = document.getElementById('edit-pending-count');
  if (el) el.textContent = String(n);
}

(async () => {
  Edit.available = await detectEditServer();
  if (!Edit.available) return;
  document.getElementById('edit-mode-wrap').style.display = 'flex';
  document.getElementById('edit-toggle-checkbox').addEventListener('change', (e) => {
    setEditMode(e.target.checked);
  });
})();
```

- [ ] **Step 4: Manual smoke test**

```bash
python scripts/dashboard_server.py
# Browser opens at http://127.0.0.1:8765/
```

In the browser:
1. Confirm the **Edit mode** pill toggle is visible in the header.
2. Toggle it on — the orange banner appears at the top with "0 changes pending".
3. Toggle it off — the banner disappears.
4. Stop the server, open the file directly (e.g. `file:///…/dshb_bgg_collection.html`) — the toggle should be **hidden**.

- [ ] **Step 5: Commit**

```bash
git add dashboard/dshb_bgg_collection.html
git commit -m "feat(dashboard): add Edit-mode toggle visible only when local server is running"
```

---

### Task 9: Dashboard — render editable score cells when Edit mode is on

**Files:**
- Modify: `dashboard/dshb_bgg_collection.html`

Goal: when `Edit.on === true`, each row in the Collection table renders M/T/G/F/Ar as numeric `<input>` fields. Each change updates `Edit.pending`.

- [ ] **Step 1: Snapshot the original scores at load time**

In the dashboard's existing data-load function (wherever `bgg_collection_scores.json` is fetched and stored), add this immediately after the JSON is parsed:

```javascript
// Deep clone so mutations during edit mode don't pollute `original`
Edit.original = JSON.parse(JSON.stringify(scores));
```

Replace `scores` with whatever the existing local variable is called.

- [ ] **Step 2: Add helpers to render an editable score input + track changes**

In the script block, add:

```javascript
const SCORE_FIELDS = ['M', 'T', 'G', 'F', 'Ar'];

function findGame(name) {
  for (const collection of ['owned', 'wishlist', 'preordered']) {
    if (Edit.original?.[collection]?.[name]) return { collection, original: Edit.original[collection][name] };
  }
  return null;
}

function recordEdit(collection, name, field, value) {
  if (!Edit.pending[collection]) Edit.pending[collection] = {};
  if (!Edit.pending[collection][name]) Edit.pending[collection][name] = {};
  const original = Edit.original[collection][name][field];
  if (value === original) {
    delete Edit.pending[collection][name][field];
    if (Object.keys(Edit.pending[collection][name]).length === 0) {
      delete Edit.pending[collection][name];
    }
    if (Object.keys(Edit.pending[collection]).length === 0) {
      delete Edit.pending[collection];
    }
  } else {
    Edit.pending[collection][name][field] = value;
  }
  renderPendingCount();
}

function renderScoreCell(gameName, field, currentValue) {
  if (!Edit.on) return String(currentValue ?? 0);
  const v = currentValue ?? 0;
  return `<input type="number" min="0" max="5" step="1" value="${v}"
    class="edit-score" data-game="${gameName.replace(/"/g, '&quot;')}" data-field="${field}"
    style="width:36px;padding:2px 4px;background:#1a1a1a;color:#fff;border:1px solid #555;border-radius:3px;text-align:center">`;
}
```

- [ ] **Step 3: Update the existing row rendering to use `renderScoreCell` for M/T/G/F/Ar**

Find the function that builds rows for the Collection tab (search for where M/T/G/F/Ar values are emitted into the table). Replace each direct emission with `renderScoreCell(game.name, 'M', game.M)`, etc.

- [ ] **Step 4: Wire score-cell `change` events**

After the table is rendered (the existing rendering function should run after data loads), attach a delegated listener once. In the same script block:

```javascript
document.addEventListener('change', (e) => {
  const el = e.target;
  if (!el.classList?.contains('edit-score')) return;
  const game = el.getAttribute('data-game');
  const field = el.getAttribute('data-field');
  let value = parseInt(el.value, 10);
  if (Number.isNaN(value)) value = 0;
  if (value < 0) value = 0;
  if (value > 5) value = 5;
  el.value = value;
  const found = findGame(game);
  if (!found) return;
  recordEdit(found.collection, game, field, value);
});
```

- [ ] **Step 5: Manual smoke test**

```bash
python scripts/dashboard_server.py
```

In the browser:
1. Toggle Edit mode ON.
2. The score columns in the Collection tab now show numeric inputs.
3. Change a score → the banner counter increments.
4. Change it back to the original value → the counter decrements.
5. Type a value out of range (e.g. 9) → it clamps to 5 on blur.

- [ ] **Step 6: Commit**

```bash
git add dashboard/dshb_bgg_collection.html
git commit -m "feat(dashboard): inline-editable score cells in Edit mode"
```

---

### Task 10: Dashboard — editable `type` / `feeling` / `weight` / `name`

**Files:**
- Modify: `dashboard/dshb_bgg_collection.html`

- [ ] **Step 1: Add helpers**

In the script block, add:

```javascript
function uniqueValuesFor(field) {
  const set = new Set();
  for (const c of ['owned', 'wishlist', 'preordered']) {
    const games = Edit.original?.[c] || {};
    for (const g of Object.values(games)) {
      if (g[field] != null && g[field] !== '') set.add(g[field]);
    }
  }
  return [...set].sort();
}

function renderSelectCell(gameName, field, currentValue) {
  if (!Edit.on) return String(currentValue ?? '');
  const opts = uniqueValuesFor(field).map(v => {
    const sel = v === currentValue ? ' selected' : '';
    return `<option value="${String(v).replace(/"/g, '&quot;')}"${sel}>${v}</option>`;
  }).join('');
  // include an empty option + an "add new" sentinel
  return `<select class="edit-select" data-game="${gameName.replace(/"/g, '&quot;')}" data-field="${field}"
    style="background:#1a1a1a;color:#fff;border:1px solid #555;border-radius:3px;padding:2px 4px">
    <option value=""${currentValue ? '' : ' selected'}>—</option>${opts}
  </select>`;
}

function renderTextInputCell(gameName, field, currentValue, opts = {}) {
  if (!Edit.on) return String(currentValue ?? '');
  const widthAttr = opts.width ? `style="width:${opts.width}"` : '';
  return `<input type="${opts.type || 'text'}" value="${(currentValue ?? '').toString().replace(/"/g, '&quot;')}"
    class="edit-text" data-game="${gameName.replace(/"/g, '&quot;')}" data-field="${field}"
    ${widthAttr}
    >`;
}
```

- [ ] **Step 2: Update the existing row rendering**

Wherever `type`, `feeling`, `weight`, and `name` are emitted, swap in:

- `type`     → `renderSelectCell(game.name, 'type', game.type)`
- `feeling`  → `renderSelectCell(game.name, 'feeling', game.feeling)`
- `weight`   → `renderTextInputCell(game.name, 'weight', game.weight, { type: 'number', width: '56px' })`
- `name`     → `renderTextInputCell(game.name, 'name', game.name, { width: '160px' })`

(If `name` is the row key used in the table — be careful not to break sort/filter logic. If sort/filter uses `game.name` directly, the column showing the short display name is fine to make editable; the row key remains the BGG canonical name from `bgg_collection.json`.)

- [ ] **Step 3: Delegated listeners for selects and text inputs**

Append to the script block:

```javascript
document.addEventListener('change', (e) => {
  const el = e.target;
  if (el.classList?.contains('edit-select')) {
    const game = el.getAttribute('data-game');
    const field = el.getAttribute('data-field');
    const found = findGame(game);
    if (found) recordEdit(found.collection, game, field, el.value);
  }
});

document.addEventListener('input', (e) => {
  const el = e.target;
  if (!el.classList?.contains('edit-text')) return;
  const game = el.getAttribute('data-game');
  const field = el.getAttribute('data-field');
  let value = el.value;
  if (field === 'weight') {
    const n = parseFloat(value);
    value = Number.isFinite(n) ? n : 0;
  }
  const found = findGame(game);
  if (found) recordEdit(found.collection, game, field, value);
});
```

- [ ] **Step 4: Manual smoke test**

```bash
python scripts/dashboard_server.py
```

1. Toggle Edit ON → `type` and `feeling` now show dropdowns with options drawn from existing values; `weight` and `name` are text inputs.
2. Pick a different `type` → the pending counter goes up.
3. Set it back → the counter goes down.

- [ ] **Step 5: Commit**

```bash
git add dashboard/dshb_bgg_collection.html
git commit -m "feat(dashboard): editable type/feeling/weight/name cells"
```

---

### Task 11: Dashboard — mechanism picker (pills + searchable add)

**Files:**
- Modify: `dashboard/dshb_bgg_collection.html`

- [ ] **Step 1: Load `mechanisms.json` at startup (alongside the existing scores load)**

Inside the existing data-load code, fetch the mechanisms file and store a flat list with category labels:

```javascript
let MECH_OPTIONS = []; // [{code, name, label, prefix}]
try {
  const r = await fetch('data/mechanisms.json', { cache: 'no-store' });
  const j = await r.json();
  for (const cat of j.categories || []) {
    for (const m of cat.mechanisms || []) {
      MECH_OPTIONS.push({
        code: m.code,
        name: m.name,
        label: `${m.code} ${m.name}`,
        prefix: cat.prefix,
        category: cat.name,
      });
    }
  }
} catch {
  MECH_OPTIONS = [];
}
```

- [ ] **Step 2: Add mechanism picker CSS**

In the `<style>` block:

```css
.mech-pill {
  display:inline-flex; align-items:center; gap:4px;
  background:rgba(120,200,250,.18); color:#cce8ff;
  border:1px solid rgba(120,200,250,.4); border-radius:999px;
  padding:2px 8px; margin:2px 4px 2px 0; font-size:11px; cursor:default;
}
.mech-pill button {
  background:none; border:0; color:#cce8ff; cursor:pointer; font-size:12px; line-height:1; padding:0 0 0 2px;
}
.mech-add { font-size:11px; padding:2px 8px; border:1px dashed #888; background:transparent;
            color:#ccc; border-radius:999px; cursor:pointer; }
.mech-picker {
  position:absolute; background:#1a1a1a; border:1px solid #555; border-radius:6px;
  padding:6px; z-index:100; max-height:280px; overflow:auto; min-width:260px;
  box-shadow:0 8px 24px rgba(0,0,0,.5);
}
.mech-picker input { width:100%; margin-bottom:6px; padding:4px 6px; background:#0f0f0f; color:#fff;
                      border:1px solid #444; border-radius:3px; }
.mech-picker .mech-option { padding:3px 6px; cursor:pointer; font-size:11px; border-radius:3px; }
.mech-picker .mech-option:hover { background:rgba(120,200,250,.18); }
.mech-picker .mech-cat { font-size:10px; opacity:.6; text-transform:uppercase; padding:6px 6px 2px; }
```

- [ ] **Step 3: Render mechanism cell**

```javascript
function renderMechCell(gameName, mechs) {
  if (!Edit.on) return (mechs || []).map(m => `<span class="mech-pill">${m}</span>`).join('');
  const safe = gameName.replace(/"/g, '&quot;');
  const pills = (mechs || []).map(m =>
    `<span class="mech-pill" data-mech="${m.replace(/"/g, '&quot;')}">${m}
       <button class="mech-remove" data-game="${safe}" data-mech="${m.replace(/"/g, '&quot;')}">×</button>
     </span>`
  ).join('');
  return `<div class="mech-cell" data-game="${safe}">${pills}
            <button class="mech-add" data-game="${safe}">+ add mechanism</button>
          </div>`;
}
```

Update the row rendering to call `renderMechCell(game.name, currentMechs(game))` where `currentMechs` is:

```javascript
function currentMechs(game) {
  const pending = Edit.pending?.[findGame(game.name)?.collection]?.[game.name]?.mechs;
  return pending ?? game.mechs ?? [];
}
```

- [ ] **Step 4: Hook the remove button + add button**

```javascript
document.addEventListener('click', (e) => {
  // Remove pill
  const rm = e.target.closest('.mech-remove');
  if (rm) {
    const game = rm.getAttribute('data-game');
    const mech = rm.getAttribute('data-mech');
    const found = findGame(game);
    if (!found) return;
    const current = [...currentMechs({ name: game, mechs: found.original.mechs })];
    const next = current.filter(m => m !== mech);
    recordEdit(found.collection, game, 'mechs', next);
    renderCollection();
    return;
  }
  // Open picker
  const add = e.target.closest('.mech-add');
  if (add) {
    openMechPicker(add);
    return;
  }
  // Close picker when clicking outside
  if (!e.target.closest('.mech-picker')) {
    document.querySelectorAll('.mech-picker').forEach(p => p.remove());
  }
});

function openMechPicker(addBtn) {
  document.querySelectorAll('.mech-picker').forEach(p => p.remove());
  const game = addBtn.getAttribute('data-game');
  const found = findGame(game);
  if (!found) return;
  const current = new Set(currentMechs({ name: game, mechs: found.original.mechs }));

  const picker = document.createElement('div');
  picker.className = 'mech-picker';
  picker.innerHTML = `<input type="text" placeholder="Search mechanisms…" autofocus><div class="mech-list"></div>`;
  document.body.appendChild(picker);
  const rect = addBtn.getBoundingClientRect();
  picker.style.left = `${rect.left + window.scrollX}px`;
  picker.style.top  = `${rect.bottom + window.scrollY + 4}px`;

  const list = picker.querySelector('.mech-list');
  const input = picker.querySelector('input');

  function render(query) {
    const q = query.trim().toLowerCase();
    const filtered = MECH_OPTIONS.filter(o => !current.has(o.label) &&
        (q === '' || o.label.toLowerCase().includes(q)));
    // group by category
    const byCat = {};
    for (const o of filtered) {
      (byCat[o.category] ||= []).push(o);
    }
    list.innerHTML = Object.entries(byCat).map(([cat, items]) =>
      `<div class="mech-cat">${cat}</div>` +
      items.map(o => `<div class="mech-option" data-label="${o.label.replace(/"/g, '&quot;')}">${o.label}</div>`).join('')
    ).join('') || `<div class="mech-cat">No matches</div>`;
  }
  render('');
  input.addEventListener('input', (e) => render(e.target.value));
  list.addEventListener('click', (e) => {
    const opt = e.target.closest('.mech-option');
    if (!opt) return;
    const label = opt.getAttribute('data-label');
    const next = [...currentMechs({ name: game, mechs: found.original.mechs }), label];
    current.add(label);
    recordEdit(found.collection, game, 'mechs', next);
    renderCollection();
    // Keep picker open for rapid multi-add
    render(input.value);
    input.focus();
  });
  input.focus();
}
```

- [ ] **Step 5: Manual smoke test**

1. Toggle Edit ON. Mechanisms now show as pills with × buttons.
2. Click × on a pill → it disappears, pending counter updates.
3. Click `+ add mechanism` → picker opens.
4. Type "deck" → only matching mechanisms show.
5. Click one → it adds, picker stays open for the next add.
6. Click outside → picker closes.

- [ ] **Step 6: Commit**

```bash
git add dashboard/dshb_bgg_collection.html
git commit -m "feat(dashboard): mechanism picker (pills + searchable add)"
```

---

### Task 12: Dashboard — description + justification textareas

**Files:**
- Modify: `dashboard/dshb_bgg_collection.html`

- [ ] **Step 1: Add an auto-expanding textarea helper**

```javascript
function renderTextareaCell(gameName, field, currentValue) {
  if (!Edit.on) return (currentValue || '').replace(/\n/g, '<br>');
  const safe = gameName.replace(/"/g, '&quot;');
  const val = (currentValue || '').replace(/</g, '&lt;');
  return `<textarea class="edit-textarea" data-game="${safe}" data-field="${field}"
    style="width:100%;min-height:48px;background:#0f0f0f;color:#fff;border:1px solid #444;
           border-radius:3px;padding:4px 6px;font-family:inherit;font-size:11px;resize:none"
    oninput="this.style.height='auto';this.style.height=this.scrollHeight+'px'">${val}</textarea>`;
}
```

- [ ] **Step 2: Use it for `description` and `justification`**

In the row-rendering function (likely the same one updated in Task 9), replace any place that emits these two fields with `renderTextareaCell(game.name, 'description', game.description)` and `renderTextareaCell(game.name, 'justification', game.justification)`.

- [ ] **Step 3: Wire the listener**

```javascript
document.addEventListener('input', (e) => {
  const el = e.target;
  if (!el.classList?.contains('edit-textarea')) return;
  const game = el.getAttribute('data-game');
  const field = el.getAttribute('data-field');
  const found = findGame(game);
  if (found) recordEdit(found.collection, game, field, el.value);
});
```

- [ ] **Step 4: Manual smoke test**

1. Toggle Edit ON → description/justification become textareas.
2. Type into one → counter updates.
3. Textarea auto-expands as content grows.

- [ ] **Step 5: Commit**

```bash
git add dashboard/dshb_bgg_collection.html
git commit -m "feat(dashboard): editable description and justification textareas"
```

---

### Task 13: Dashboard — save flow on toggle OFF + toast + localStorage backup

**Files:**
- Modify: `dashboard/dshb_bgg_collection.html`

- [ ] **Step 1: Add toast helpers + localStorage key constants**

```javascript
const PENDING_KEY = 'bgg_pending_edits_v1';

function showToast(text, kind = 'info', ms = 4000) {
  const div = document.createElement('div');
  div.textContent = text;
  div.style.cssText = `position:fixed;bottom:24px;left:50%;transform:translateX(-50%);
    background:${kind==='error'?'#7a2222':kind==='warn'?'#7a5b22':'#1f4a2a'};
    color:#fff;padding:10px 16px;border-radius:6px;font-size:13px;
    box-shadow:0 8px 24px rgba(0,0,0,.5);z-index:200`;
  document.body.appendChild(div);
  setTimeout(() => div.remove(), ms);
}

function pendingChangeCount() {
  let n = 0;
  for (const c of Object.values(Edit.pending)) n += Object.keys(c).length;
  return n;
}

function applyPendingToOriginal() {
  // Deep copy of original, with pending merged in
  const out = JSON.parse(JSON.stringify(Edit.original));
  for (const [collection, games] of Object.entries(Edit.pending)) {
    for (const [name, fields] of Object.entries(games)) {
      Object.assign(out[collection][name], fields);
    }
  }
  return out;
}
```

- [ ] **Step 2: Persist pending edits to localStorage on every change**

Update `recordEdit` to also call:

```javascript
try { localStorage.setItem(PENDING_KEY, JSON.stringify({ ts: Date.now(), pending: Edit.pending })); }
catch {}
```

(Add at the end of `recordEdit`, after the existing `renderPendingCount()`.)

- [ ] **Step 3: Restore pending edits on page load**

In the startup code (right after `Edit.original` is set and before the first render):

```javascript
if (Edit.available) {
  try {
    const stored = JSON.parse(localStorage.getItem(PENDING_KEY) || 'null');
    if (stored && stored.pending && Object.keys(stored.pending).length) {
      const ago = Math.round((Date.now() - stored.ts) / 60000);
      const restore = window.confirm(`Unsaved edits found from ${ago} min ago. Restore?`);
      if (restore) {
        Edit.pending = stored.pending;
      } else {
        localStorage.removeItem(PENDING_KEY);
      }
    }
  } catch {}
}
```

- [ ] **Step 4: Save flow when Edit mode goes OFF**

Replace the existing `setEditMode` body with:

```javascript
async function setEditMode(on) {
  if (Edit.on && !on) {
    // Turning OFF — save if there are pending changes
    if (pendingChangeCount() === 0) {
      Edit.on = false;
      document.body.classList.remove('edit-mode');
      document.getElementById('edit-banner').style.display = 'none';
      showToast('No changes', 'info', 1500);
      if (typeof renderCollection === 'function') renderCollection();
      return;
    }
    const updated = applyPendingToOriginal();
    const changed_games = Object.values(Edit.pending).flatMap(c => Object.keys(c));
    showToast(`Saving ${changed_games.length} game(s)…`, 'info', 1500);
    try {
      const r = await fetch('/api/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scores: updated, changed_games }),
      });
      const j = await r.json();
      if (j.status === 'ok') {
        Edit.original = updated;
        Edit.pending = {};
        localStorage.removeItem(PENDING_KEY);
        Edit.on = false;
        document.body.classList.remove('edit-mode');
        document.getElementById('edit-banner').style.display = 'none';
        renderPendingCount();
        if (typeof renderCollection === 'function') renderCollection();
        showToast(j.message || `Saved & pushed ${changed_games.length} game(s)`, 'info');
      } else if (j.status === 'committed_not_pushed') {
        Edit.original = updated;
        Edit.pending = {};
        localStorage.removeItem(PENDING_KEY);
        Edit.on = false;
        document.body.classList.remove('edit-mode');
        document.getElementById('edit-banner').style.display = 'none';
        renderPendingCount();
        if (typeof renderCollection === 'function') renderCollection();
        showToast(j.message || 'Committed locally; push failed', 'warn', 6000);
      } else if (j.status === 'validation_error') {
        document.getElementById('edit-toggle-checkbox').checked = true;
        Edit.on = true;
        showToast(`Validation error: ${j.errors?.[0]?.field || ''} ${j.errors?.[0]?.problem || ''}`, 'error', 6000);
      } else {
        document.getElementById('edit-toggle-checkbox').checked = true;
        Edit.on = true;
        showToast(`Save failed: ${j.message || 'unknown error'}`, 'error', 6000);
      }
    } catch (err) {
      // Server unreachable — leave Edit mode ON so the data isn't lost
      document.getElementById('edit-toggle-checkbox').checked = true;
      Edit.on = true;
      showToast(`Can't reach local server. Your edits are safe in this browser. Restart /dashboard_BGG and try again.`, 'error', 8000);
    }
    return;
  }
  // Turning ON
  Edit.on = on;
  document.body.classList.toggle('edit-mode', on);
  document.getElementById('edit-banner').style.display = on ? 'block' : 'none';
  renderPendingCount();
  if (typeof renderCollection === 'function') renderCollection();
}
```

- [ ] **Step 5: Manual smoke test — full happy path**

```bash
python scripts/dashboard_server.py
```

1. Toggle Edit ON.
2. Change Burgle Bros. M from 3 → 5; type a word into description; add a mechanism.
3. Toggle Edit OFF.
4. Toast: `Saved & pushed 1 game(s)`.
5. In another terminal: `git log -1 --stat` shows a commit touching `data/bgg_collection_scores.json` with the message `Update collection scores: Burgle Bros.`.
6. Reload the dashboard — the changes persist (loaded from the file).

- [ ] **Step 6: Manual smoke test — no-op**

1. Toggle Edit ON without changing anything.
2. Toggle Edit OFF.
3. Toast: `No changes`. No new commit in `git log`.

- [ ] **Step 7: Manual smoke test — push-fail recovery**

1. `git remote set-url origin /tmp/nonexistent` (temporarily).
2. In the browser: edit one score, toggle OFF.
3. Toast: warn-coloured `Saved & committed locally. Push failed: …`.
4. `git log -1` shows the commit. `git status` is clean.
5. Restore the original remote: `git remote set-url origin <real url>`. Run `git push` manually — succeeds.

- [ ] **Step 8: Commit**

```bash
git add dashboard/dshb_bgg_collection.html
git commit -m "feat(dashboard): save flow + toast + localStorage backup"
```

---

### Task 14: Update the `/dashboard_BGG` slash command

**Files:**
- Modify: `.claude/commands/dashboard_BGG.md`

- [ ] **Step 1: Replace the file with the editor-aware version**

Replace the contents of `.claude/commands/dashboard_BGG.md` with:

```markdown
Launch the BGG dashboard in the local editor server.

## Step 1 — Start the server

Run this from the **project root**:

```bash
cd /Users/jsneij/Desktop/Claude/ProjectBoardGames && source venv/bin/activate && python scripts/dashboard_server.py
```

The server binds `127.0.0.1:8765`, serves the dashboard, and opens it in the default browser automatically. Ctrl+C stops it.

## Step 2 — Tell the user

> Dashboard is live at **http://localhost:8765/** — toggle "Edit mode" in the header to edit scores. Toggling it off writes the JSON, commits, and pushes.

## Notes

- Edits to personal scoring data are made in the UI (Edit mode toggle). Toggling Edit mode OFF triggers save → commit → push.
- The deployed GitHub Pages dashboard remains read-only — the `/api/status` probe 404s there and the toggle stays hidden.
- BGG-sourced fields (rank, play counts, designer, etc.) are never editable in this UI — those still come from the nightly `fetch_bgg_collection.py` pipeline.
- `sync_scores.py` is still the only path for adding or removing game entries.
- If you change `DASHBOARD_PORT` in the environment, the server uses that port (default 8765).
- Spec: `docs/superpowers/specs/2026-06-13-dashboard-edit-mode-design.md`
```

- [ ] **Step 2: Commit**

```bash
git add .claude/commands/dashboard_BGG.md
git commit -m "docs: /dashboard_BGG now launches the editor server"
```

---

### Task 15: End-to-end verification + handoff

**Files:** none.

- [ ] **Step 1: Full pytest run**

Run: `cd /Users/jsneij/Desktop/Claude/ProjectBoardGames && source venv/bin/activate && python -m pytest tests/ -v`

Expected: all tests pass (10 total).

- [ ] **Step 2: Full manual flow**

1. Run `/dashboard_BGG`.
2. In the browser, toggle Edit ON.
3. Make changes across three different games — score, mechanism add, mechanism remove, description, justification.
4. Toggle Edit OFF.
5. Confirm: toast `Saved & pushed 3 game(s)`, `git log -1` shows commit, `git status` clean, GitHub Pages site updates within ~1 min after the `deploy-pages.yml` workflow finishes (check the Actions tab).

- [ ] **Step 3: Confirm read-only on the live site**

Open the deployed GitHub Pages URL. The Edit-mode toggle must be **hidden** (because `/api/status` 404s on Pages).

- [ ] **Step 4: Confirm `/refresh_BGG` still works alongside this**

Run `/refresh_BGG` in a fresh session. It should run as before — the BGG fetch pipeline doesn't touch `bgg_collection_scores.json`.

- [ ] **Step 5: Final commit-free verification — leave the tree clean**

Run: `git status`

Expected: `nothing to commit, working tree clean`.

---

## Self-Review Notes (for the implementer)

- Each task is independently committable. If something blows up mid-task, you can `git reset --hard HEAD` and the previous commit is still good.
- The dashboard tasks (8–13) all edit the **same** HTML file. Order matters — earlier tasks set up helpers that later tasks use.
- If `python scripts/dashboard_server.py` fails to start because port 8765 is in use, `export DASHBOARD_PORT=8766` and retry.
- The test suite assumes pytest is installed in `venv/`. If not: `pip install pytest`.
- `git push` in `commit_and_push` uses the configured remote `origin` and current branch's upstream. If your local checkout has detached HEAD or no upstream, push will fail with `committed_not_pushed` — fix the branch state, then push manually.
