"""
explorer_discover.py
End-to-end pipeline that discovers solo board games likely to score
Total or Immersive on the user's Immersion Score framework, drops games
already in the collection, and writes data/explorer_candidates.json.

Cost is bounded by a BGG-id-keyed cache (data/explorer_cache.json) so each
run only spends Claude tokens on never-before-seen games.

Usage:
    python scripts/explorer_discover.py [--limit N] [--no-scrapers] [--dry-run]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

# Allow `python scripts/explorer_discover.py` to import sibling modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts import explorer_sources, explorer_resolve  # noqa: E402
from scripts.explorer_sources import (  # noqa: E402
    BGG_API_BASE, DEFAULT_UA, HTTP_TIMEOUT, MAX_RETRIES, RETRY_DELAY_SECONDS,
)
from scripts.score_new_games import (  # noqa: E402
    build_calibration_examples,
    build_mechanism_catalog,
    load_env,
    score_game,
)

ROOT = Path(__file__).resolve().parent.parent
SOURCES_CONFIG = ROOT / "data" / "explorer_sources.json"
COLLECTION_PATH = ROOT / "data" / "bgg_collection.json"
SCORES_PATH = ROOT / "data" / "bgg_collection_scores.json"
MECHS_PATH = ROOT / "data" / "mechanisms.json"
FRAMEWORK_PATH = ROOT / "docs" / "immersion_score.md"
OUTPUT_PATH = ROOT / "data" / "explorer_candidates.json"
CACHE_PATH = ROOT / "data" / "explorer_cache.json"

# BGG mechanic ID for "Solo / Solitaire Game"
BGG_SOLO_MECHANIC_ID = 2023

# Default thresholds — match scripts/score_new_games.py
FEELING_TOTAL_MIN = 150
FEELING_IMMERSIVE_MIN = 87


# ─── /thing fetcher (richer than the existing one in fetch_bgg_collection.py) ─

def _get_thing_xml(ids_chunk: list[int], bearer_token: str) -> ET.Element | None:
    headers = {
        "User-Agent": DEFAULT_UA,
        "Accept": "application/xml",
        "Authorization": f"Bearer {bearer_token}",
    }
    params = {"id": ",".join(str(i) for i in ids_chunk), "stats": "1"}
    backoff_429 = 30  # /thing batches are heavier — start at 30s
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(f"{BGG_API_BASE}/thing", params=params,
                                headers=headers, timeout=HTTP_TIMEOUT)
        except requests.RequestException as e:
            print(f"  [thing] network error: {e}")
            return None
        if resp.status_code == 200:
            try:
                return ET.fromstring(resp.text)
            except ET.ParseError as e:
                print(f"  [thing] parse: {e}")
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
            print(f"  [thing] HTTP 429 — backing off {wait}s (attempt {attempt + 1}/{MAX_RETRIES})")
            time.sleep(wait)
            backoff_429 = min(backoff_429 * 2, 240)
            continue
        print(f"  [thing] HTTP {resp.status_code}")
        return None
    return None


def _int_attr(el: ET.Element | None, attr: str = "value") -> int | None:
    if el is None: return None
    v = el.get(attr)
    if not v: return None
    try: return int(v)
    except ValueError: return None


def _float_attr(el: ET.Element | None, attr: str = "value", ndigits: int = 3) -> float | None:
    if el is None: return None
    v = el.get(attr)
    if not v: return None
    try: return round(float(v), ndigits)
    except ValueError: return None


def _parse_thing_item(item: ET.Element) -> dict:
    """Extract the fields the Explorer needs from a single <item>."""
    bgg_id = _int_attr(item, "id") or 0
    out: dict = {"bgg_id": bgg_id}

    # Primary name (type="primary")
    name = None
    for n in item.findall("name"):
        if n.get("type") == "primary":
            name = n.get("value")
            break
    if name is None:
        first = item.find("name")
        if first is not None:
            name = first.get("value")
    out["name"] = name or ""

    out["year"] = _int_attr(item.find("yearpublished"))

    img = item.find("image")
    out["image"] = img.text if img is not None else None
    thumb = item.find("thumbnail")
    out["thumbnail"] = thumb.text if thumb is not None else None

    out["min_players"]  = _int_attr(item.find("minplayers"))
    out["max_players"]  = _int_attr(item.find("maxplayers"))
    out["min_playtime"] = _int_attr(item.find("minplaytime"))
    out["max_playtime"] = _int_attr(item.find("maxplaytime"))

    designers = []
    mechanic_ids = set()
    for link in item.findall("link"):
        ltype = link.get("type")
        if ltype == "boardgamedesigner":
            v = link.get("value")
            if v:
                designers.append(v)
        elif ltype == "boardgamemechanic":
            mid = link.get("id")
            if mid:
                mechanic_ids.add(int(mid))
    out["designers"] = designers
    out["mechanic_ids"] = sorted(mechanic_ids)

    out["bayes_rating"] = None
    out["average_rating"] = None
    out["weight"] = None
    out["num_voters"] = None
    stats = item.find("statistics")
    if stats is not None:
        ratings = stats.find("ratings")
        if ratings is not None:
            out["bayes_rating"]   = _float_attr(ratings.find("bayesaverage"))
            out["average_rating"] = _float_attr(ratings.find("average"))
            out["weight"]         = _float_attr(ratings.find("averageweight"), ndigits=2)
            out["num_voters"]     = _int_attr(ratings.find("usersrated"))

    # Solo recommendation: parse the suggested_numplayers poll for "1"
    out["solo_recommended"] = False
    for poll in item.findall("poll"):
        if poll.get("name") != "suggested_numplayers":
            continue
        for ng in poll.findall("results"):
            if ng.get("numplayers") != "1":
                continue
            best, recommended, not_rec = 0, 0, 0
            for r in ng.findall("result"):
                v = r.get("value", "").lower()
                n = int(r.get("numvotes", 0))
                if v == "best": best = n
                elif v == "recommended": recommended = n
                elif v == "not recommended": not_rec = n
            out["solo_recommended"] = (best + recommended) > not_rec
            break
        break

    return out


def fetch_thing_data(bgg_ids: list[int], bearer_token: str, batch_size: int = 20,
                     delay: float = 1.0) -> dict[int, dict]:
    """Batched /thing fetch returning richer data than fetch_bgg_collection.py."""
    out: dict[int, dict] = {}
    for i in range(0, len(bgg_ids), batch_size):
        batch = bgg_ids[i:i + batch_size]
        print(f"  [thing] batch {i // batch_size + 1}: {len(batch)} ids")
        root = _get_thing_xml(batch, bearer_token)
        if root is None:
            continue
        for item in root.findall("item"):
            parsed = _parse_thing_item(item)
            out[parsed["bgg_id"]] = parsed
        if i + batch_size < len(bgg_ids):
            time.sleep(delay)
    return out


# ─── Filters ────────────────────────────────────────────────────────────────

def collection_bgg_ids(collection: dict) -> set[int]:
    """Every BGG id we already own / preordered / wishlisted / want / etc."""
    ids: set[int] = set()
    for key in ("owned", "preordered", "previously_owned", "want_to_play",
                "want_to_buy", "for_trade"):
        for g in collection.get(key, []):
            bid = g.get("bgg_id")
            if isinstance(bid, int):
                ids.add(bid)
    wl = collection.get("wishlist", {})
    if isinstance(wl, dict):
        for priority_games in wl.values():
            if isinstance(priority_games, list):
                for g in priority_games:
                    bid = g.get("bgg_id")
                    if isinstance(bid, int):
                        ids.add(bid)
    return ids


def is_solo_capable(thing: dict) -> bool:
    """Two signals: BGG solo mechanic 2023, or minplayers==1 + positive poll."""
    if BGG_SOLO_MECHANIC_ID in thing.get("mechanic_ids", []):
        return True
    if thing.get("min_players") == 1 and thing.get("solo_recommended"):
        return True
    return False


# ─── Cache + output ─────────────────────────────────────────────────────────

def load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            print("  [cache] corrupt, starting fresh")
            return {}
    return {}


def save_cache(cache: dict) -> None:
    CACHE_PATH.write_text(json.dumps(cache, indent=2, ensure_ascii=False),
                          encoding="utf-8")


def compute_is(scored: dict) -> tuple[float, str]:
    gs = scored["M"] * scored["T"] * scored["G"] - scored["F"]
    is_val = gs * (scored["Ar"] / 2)
    if is_val >= FEELING_TOTAL_MIN:
        feeling = "Total"
    elif is_val >= FEELING_IMMERSIVE_MIN:
        feeling = "Immersive"
    elif is_val >= 30:
        feeling = "Engaging"
    else:
        feeling = "On Shelf"
    return is_val, feeling


# ─── Main orchestration ─────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0,
                        help="Cap newly-scored games per run (0 = no cap)")
    parser.add_argument("--no-scrapers", action="store_true",
                        help="Skip external scrapers (BGG-only)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Do not write output files; just print summary")
    parser.add_argument("--mock-scoring", action="store_true",
                        help="Skip the LLM; assign deterministic placeholder scores. "
                             "For pipeline plumbing tests when no ANTHROPIC_API_KEY is available.")
    args = parser.parse_args(argv)

    load_env()
    bgg_token = os.environ.get("BGG_BEARER_TOKEN")
    if not bgg_token:
        print("ERROR: BGG_BEARER_TOKEN not set")
        return 2
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if not anthropic_key and not args.mock_scoring:
        print("ERROR: ANTHROPIC_API_KEY not set (or pass --mock-scoring for dev)")
        return 2

    # Config
    config = json.loads(SOURCES_CONFIG.read_text(encoding="utf-8"))
    enabled = config.get("enabled", {})
    geeklist_ids = config.get("bgg_geeklists", [])
    custom_url_list = [u for u in config.get("custom_urls", []) if isinstance(u, str)]

    # ── 1. Discover from sources ───────────────────────────────────────────
    print("\n[1/8] Discovery")
    candidates: list[dict] = []
    if enabled.get("bgg_hot", True):
        c = explorer_sources.bgg_hot(bgg_token)
        print(f"  bgg_hot                  → {len(c)}")
        candidates.extend(c)
    if enabled.get("bgg_geeklists", True) and geeklist_ids:
        c = explorer_sources.bgg_geeklists(geeklist_ids, bgg_token)
        print(f"  bgg_geeklists ({len(geeklist_ids)})        → {len(c)}")
        candidates.extend(c)
    if not args.no_scrapers:
        if enabled.get("polyhedron_collider", True):
            c = explorer_sources.polyhedron_collider()
            print(f"  polyhedron_collider      → {len(c)}")
            candidates.extend(c)
        if enabled.get("solitaire_times", True):
            c = explorer_sources.solitaire_times()
            print(f"  solitaire_times          → {len(c)}")
            candidates.extend(c)
        if enabled.get("custom_urls", True) and custom_url_list:
            c = explorer_sources.custom_urls(custom_url_list)
            print(f"  custom_urls ({len(custom_url_list)})         → {len(c)}")
            candidates.extend(c)

    # ── 2. Resolve names → BGG ids ──────────────────────────────────────────
    print("\n[2/8] Resolve names → BGG ids")
    unresolved_names = [c["name"] for c in candidates if c.get("bgg_id") is None and c.get("name")]
    resolved_count = 0
    if unresolved_names:
        unique_names = list({n: None for n in unresolved_names}.keys())
        print(f"  Resolving {len(unique_names)} unique names…")
        for n in unique_names:
            bid = explorer_resolve.resolve_name_to_bgg_id(n, bgg_token)
            if bid is not None:
                resolved_count += 1
            for c in candidates:
                if c.get("name") == n and c.get("bgg_id") is None:
                    c["bgg_id"] = bid
        print(f"  Resolved {resolved_count}/{len(unique_names)}")

    # ── 3. Dedup by bgg_id; merge sources ──────────────────────────────────
    print("\n[3/8] Dedup")
    by_id: dict[int, dict] = {}
    for c in candidates:
        bid = c.get("bgg_id")
        if bid is None:
            continue
        if bid in by_id:
            srcs = set(by_id[bid].get("sources", []))
            srcs.add(c.get("source", ""))
            by_id[bid]["sources"] = sorted(srcs)
        else:
            by_id[bid] = {"bgg_id": bid, "name": c.get("name"),
                          "sources": [c.get("source", "")]}
    print(f"  Distinct BGG ids: {len(by_id)}")

    # ── 4. Drop ids already in collection ──────────────────────────────────
    print("\n[4/8] Drop collection overlap")
    collection = json.loads(COLLECTION_PATH.read_text(encoding="utf-8"))
    own_ids = collection_bgg_ids(collection)
    print(f"  Collection size: {len(own_ids)}")
    by_id = {bid: v for bid, v in by_id.items() if bid not in own_ids}
    print(f"  Remaining: {len(by_id)}")

    # ── 5. Split cache vs new ─────────────────────────────────────────────
    # Treat _unavailable entries as new — they failed on a transient error
    # (HTTP 429, network blip) and should be retried.
    print("\n[5/8] Cache lookup")
    cache = load_cache()
    def _needs_rescore(bid):
        c = cache.get(str(bid))
        if c is None:
            return True
        return bool(c.get("_unavailable"))
    new_ids = [bid for bid in by_id if _needs_rescore(bid)]
    cached_ids = [bid for bid in by_id if not _needs_rescore(bid)]
    print(f"  Cached:  {len(cached_ids)}   New/retry: {len(new_ids)}")

    # ── 6. Enrich the NEW ones via /thing ──────────────────────────────────
    new_things: dict[int, dict] = {}
    if new_ids:
        print(f"\n[6/8] Fetch /thing for {len(new_ids)} new ids")
        new_things = fetch_thing_data(new_ids, bgg_token)

    # ── 7. Filter solo + score new ones ────────────────────────────────────
    print("\n[7/8] Filter solo + LLM score new candidates")
    framework_text = FRAMEWORK_PATH.read_text(encoding="utf-8")
    scores_data = json.loads(SCORES_PATH.read_text(encoding="utf-8"))
    calibration = build_calibration_examples(scores_data)
    mechs_data = json.loads(MECHS_PATH.read_text(encoding="utf-8"))
    mech_catalog = build_mechanism_catalog(mechs_data)

    client = None
    if not args.mock_scoring:
        import anthropic
        client = anthropic.Anthropic(api_key=anthropic_key)

    scored_count = 0
    skipped_non_solo = 0
    for bid in new_ids:
        thing = new_things.get(bid)
        if thing is None:
            cache[str(bid)] = {"bgg_id": bid, "_unavailable": True}
            continue
        if not is_solo_capable(thing):
            skipped_non_solo += 1
            cache[str(bid)] = {"bgg_id": bid, "_solo": False,
                               "name": thing.get("name")}
            continue
        if args.limit and scored_count >= args.limit:
            print(f"  Reached --limit {args.limit}; stopping scoring loop")
            break

        # Shape the bgg_data to what score_game expects (mirrors the parsed
        # /thing structure used by score_new_games.py).
        bgg_data_for_scoring = {
            "year": thing.get("year"),
            "stats": {
                "min_players": thing.get("min_players"),
                "max_players": thing.get("max_players"),
                "min_playtime": thing.get("min_playtime"),
                "max_playtime": thing.get("max_playtime"),
                "avg_weight": thing.get("weight"),
                "average": thing.get("average_rating"),
                "ranks": [],  # not parsed here
            },
        }

        print(f"  Scoring [{scored_count+1}] {thing.get('name')!r} (id={bid})…")
        if args.mock_scoring:
            # Deterministic placeholder so we can exercise pipeline plumbing
            # without spending Claude tokens. NOT for production.
            w = thing.get("weight") or 2.5
            scored = {
                "M": min(5, max(1, round(w))),
                "T": 4, "G": 4, "F": 2, "Ar": 4,
                "type": "solo" if thing.get("min_players") == 1 else "co-op",
                "weight": w,
                "mechs": [],
                "description": f"Mock entry for {thing.get('name')}.",
                "justification": "M(?): mock. T(?): mock. G(?): mock. F(?): mock. Ar(?): mock.",
            }
        else:
            try:
                scored = score_game(client, thing.get("name") or by_id[bid]["name"],
                                    "candidate", bgg_data_for_scoring,
                                    framework_text, calibration, mech_catalog)
            except Exception as e:
                print(f"    ERROR: {e}")
                continue
        is_val, feeling = compute_is(scored)

        entry = {
            "bgg_id": bid,
            "name": thing.get("name"),
            "year": thing.get("year"),
            "image": thing.get("image"),
            "thumbnail": thing.get("thumbnail"),
            "designers": thing.get("designers", []),
            "weight": thing.get("weight"),
            "bayes_rating": thing.get("bayes_rating"),
            "min_players": thing.get("min_players"),
            "max_players": thing.get("max_players"),
            **scored,
            "GS": scored["M"] * scored["T"] * scored["G"] - scored["F"],
            "IS": is_val,
            "feeling": feeling,
        }
        cache[str(bid)] = entry
        scored_count += 1
        print(f"    IS={is_val} → {feeling}")
        time.sleep(0.5)  # be polite to the Anthropic API

    # ── 8. Build candidates list (cached + new), filter, write ─────────────
    print("\n[8/8] Build output")
    visible: list[dict] = []
    for bid, base in by_id.items():
        c = cache.get(str(bid))
        if not c or c.get("_unavailable") or c.get("_solo") is False:
            continue
        if c.get("feeling") not in ("Total", "Immersive"):
            continue
        out = dict(c)
        out["sources"] = base.get("sources", [])
        visible.append(out)

    visible.sort(key=lambda x: x.get("IS", 0), reverse=True)

    output = {
        "metadata": {
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "sources_used": sorted({s for v in visible for s in v["sources"]}),
            "total_evaluated": len(by_id),
            "newly_scored": scored_count,
            "skipped_non_solo": skipped_non_solo,
            "kept_immersive": sum(1 for v in visible if v["feeling"] == "Immersive"),
            "kept_total":     sum(1 for v in visible if v["feeling"] == "Total"),
        },
        "candidates": visible,
    }

    print(f"  Candidates kept: {len(visible)} "
          f"(Total={output['metadata']['kept_total']}, "
          f"Immersive={output['metadata']['kept_immersive']})")

    if args.dry_run:
        print("\nDRY RUN — no files written.")
        return 0

    OUTPUT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False),
                           encoding="utf-8")
    save_cache(cache)
    print(f"\nWrote {OUTPUT_PATH}")
    print(f"Wrote {CACHE_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
