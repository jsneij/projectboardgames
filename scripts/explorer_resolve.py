"""
explorer_resolve.py
Resolve game NAMES → BGG IDs via the BGG search API.

External sources (Polyhedron Collider, etc.) yield candidate game names without
BGG IDs. This module looks each name up. Strategy:
  1. Exact-match search (`exact=1`).
  2. Fall back to fuzzy search; pick the top result by string similarity.
  3. In-memory cache of name → id within a single run so the same name
     pulled from multiple sources only hits the API once.
"""

from __future__ import annotations

import difflib
import re
import time
import xml.etree.ElementTree as ET
from typing import Iterable

import requests

from scripts.explorer_sources import (
    BGG_API_BASE,
    DEFAULT_UA,
    HTTP_TIMEOUT,
    MAX_RETRIES,
    RETRY_DELAY_SECONDS,
)

# Names that should not be resolved (false positives from scraping)
_SKIP_NAMES = {
    "the table", "the news", "podcast", "video", "interview",
    "review", "preview", "expo", "kickstarter", "the rpg",
}

# Tokens we strip from incoming names before searching (subtitles, suffixes)
_STRIP_PATTERNS = [
    re.compile(r"\s+rpg$", re.IGNORECASE),
    re.compile(r"\s+\(2[0-9]{3}\)$"),
]

_search_cache: dict[str, int | None] = {}


def _clean_name(name: str) -> str:
    name = name.strip().strip("'\"")
    for pat in _STRIP_PATTERNS:
        name = pat.sub("", name)
    return name.strip()


def _get_xml(url: str, params: dict, bearer_token: str) -> ET.Element | None:
    headers = {
        "User-Agent": DEFAULT_UA,
        "Accept": "application/xml",
        "Authorization": f"Bearer {bearer_token}",
    }
    backoff_429 = 15
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, params=params, headers=headers,
                                timeout=HTTP_TIMEOUT, allow_redirects=True)
        except requests.RequestException as e:
            print(f"  [resolve] network error: {e}")
            return None
        if resp.status_code == 200:
            try:
                return ET.fromstring(resp.text)
            except ET.ParseError as e:
                print(f"  [resolve] XML parse: {e}")
                return None
        if resp.status_code == 202:
            time.sleep(RETRY_DELAY_SECONDS)
            continue
        if resp.status_code == 429:
            ra = resp.headers.get("Retry-After", "")
            try:
                wait = int(ra) if ra.isdigit() else backoff_429
            except Exception:
                wait = backoff_429
            print(f"  [resolve] HTTP 429 for {params.get('query')!r} — backing off {wait}s "
                  f"(attempt {attempt + 1}/{MAX_RETRIES})")
            time.sleep(wait)
            backoff_429 = min(backoff_429 * 2, 120)
            continue
        print(f"  [resolve] HTTP {resp.status_code} for {params.get('query')!r}")
        return None
    return None


def _candidates_from_xml(root: ET.Element) -> list[tuple[int, str]]:
    out = []
    for item in root.findall("item"):
        if item.get("type") != "boardgame":
            continue
        bgg_id = item.get("id")
        name_el = item.find("name")
        if not bgg_id or name_el is None:
            continue
        out.append((int(bgg_id), name_el.get("value") or ""))
    return out


def resolve_name_to_bgg_id(name: str, bearer_token: str) -> int | None:
    """Return a BGG ID for `name`, or None if no confident match."""
    cleaned = _clean_name(name)
    if not cleaned or cleaned.lower() in _SKIP_NAMES or len(cleaned) < 3:
        return None

    cached = _search_cache.get(cleaned.lower())
    if cached is not None:
        return cached

    # 1. Exact match (fast path)
    root = _get_xml(f"{BGG_API_BASE}/search",
                    {"type": "boardgame", "exact": "1", "query": cleaned},
                    bearer_token)
    if root is not None:
        cands = _candidates_from_xml(root)
        if cands:
            best_id = cands[0][0]
            _search_cache[cleaned.lower()] = best_id
            # Brief politeness pause; BGG is sensitive to bursts.
            time.sleep(0.3)
            return best_id

    # 2. Fuzzy fallback — top-1 from non-exact search
    time.sleep(0.5)
    root = _get_xml(f"{BGG_API_BASE}/search",
                    {"type": "boardgame", "query": cleaned},
                    bearer_token)
    if root is None:
        _search_cache[cleaned.lower()] = None
        return None
    cands = _candidates_from_xml(root)
    if not cands:
        _search_cache[cleaned.lower()] = None
        return None

    names = [c[1] for c in cands]
    matches = difflib.get_close_matches(cleaned, names, n=1, cutoff=0.6)
    if not matches:
        _search_cache[cleaned.lower()] = None
        return None
    chosen_name = matches[0]
    chosen_id = next(cid for cid, cname in cands if cname == chosen_name)
    _search_cache[cleaned.lower()] = chosen_id
    time.sleep(0.3)
    return chosen_id


def resolve_many(names: Iterable[str], bearer_token: str) -> dict[str, int | None]:
    """Bulk resolve. Returns {input_name: bgg_id_or_None}."""
    out: dict[str, int | None] = {}
    for n in names:
        out[n] = resolve_name_to_bgg_id(n, bearer_token)
    return out


if __name__ == "__main__":
    import os
    import sys
    from pathlib import Path

    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    token = os.environ.get("BGG_BEARER_TOKEN", "")
    if not token:
        print("BGG_BEARER_TOKEN not set", file=sys.stderr)
        sys.exit(1)

    test_names = sys.argv[1:] or [
        "Spirit Island",
        "Arkham Horror The Card Game",  # near match, no subtitle
        "Final Girl",
        "Gloomhaven Jaws of the Lion",
        "Made Up Game Title 9999",  # should not resolve
    ]
    for n in test_names:
        bid = resolve_name_to_bgg_id(n, token)
        print(f"  {n!r:50} → {bid}")
