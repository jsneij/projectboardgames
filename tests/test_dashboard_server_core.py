import json
from pathlib import Path

from scripts.dashboard_server_core import validate_scores


def _load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def test_validate_scores_accepts_unchanged_payload(scores_path):
    existing = _load(scores_path)
    errors = validate_scores(existing, existing)
    assert errors == []


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


def test_validate_scores_rejects_bool_as_score(scores_path):
    # Python quirk: isinstance(True, int) is True. The explicit bool guard
    # in validate_scores prevents True/False from passing as a score. This
    # test pins that guard so refactors can't silently regress it.
    existing = _load(scores_path)
    payload = {"owned": {"Burgle Bros.": {"M": True}}}
    errors = validate_scores(payload, existing)
    assert {"field": "owned.Burgle Bros..M", "problem": "must be int 0..5"} in errors


def test_validate_scores_rejects_non_numeric_weight(scores_path):
    existing = _load(scores_path)
    payload = {"owned": {"Burgle Bros.": {"weight": "heavy"}}}
    errors = validate_scores(payload, existing)
    assert {"field": "owned.Burgle Bros..weight", "problem": "must be numeric"} in errors


def test_validate_scores_rejects_non_string_text_field(scores_path):
    existing = _load(scores_path)
    payload = {"owned": {"Burgle Bros.": {"feeling": 99}}}
    errors = validate_scores(payload, existing)
    assert {"field": "owned.Burgle Bros..feeling", "problem": "must be a string"} in errors


from scripts.dashboard_server_core import atomic_write_json


def test_atomic_write_json_replaces_file_and_leaves_no_tmp(tmp_path):
    target = tmp_path / "out.json"
    target.write_text('{"old": true}', encoding="utf-8")
    atomic_write_json(target, {"new": 42})
    assert json.loads(target.read_text(encoding="utf-8")) == {"new": 42}
    leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []
