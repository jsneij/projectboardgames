import json
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

    _run(["git", "init", "-q"], cwd=work)
    _run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=work)
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
