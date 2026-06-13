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


def validate_scores(payload: Any, existing: Any) -> list[dict]:
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

    # Defensive: a malformed on-disk scores file could give us None or a non-dict here;
    # treat it as "no games known yet" and let the per-game unknown-game rule report.
    if not isinstance(existing, dict):
        existing = {}

    for collection, games in payload.items():
        if collection not in COLLECTIONS:
            errors.append({"field": collection, "problem": "unknown collection"})
            continue
        if not isinstance(games, dict):
            errors.append({"field": collection, "problem": "must be an object"})
            continue
        existing_games = existing.get(collection) or {}
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


def atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON to `path` atomically: write to .tmp, fsync, rename over real file.

    Requires `path.parent` to exist. Cleans up the temp file if json.dump or
    fsync raises before the rename completes.
    """
    path = Path(path)
    tmp_path: Path | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
        tmp_path = Path(tmp_name)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise


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
      "committed_not_pushed"  — commit succeeded but push failed
      "error"                 — git add or git commit failed
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
