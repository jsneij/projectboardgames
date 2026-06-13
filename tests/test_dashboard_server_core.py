import json
from pathlib import Path

from scripts.dashboard_server_core import validate_scores


def _load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def test_validate_scores_accepts_unchanged_payload(scores_path):
    existing = _load(scores_path)
    errors = validate_scores(existing, existing)
    assert errors == []
