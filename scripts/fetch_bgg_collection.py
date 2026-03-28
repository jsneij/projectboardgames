#!/usr/bin/env python3
"""
BGG Collection Fetcher
======================
Fetches your BoardGameGeek collection via XML API v2, parses it,
and outputs a clean JSON file ready for Claude project knowledge.

Includes: stats, private info (acquisition dates, prices), and
detailed play logs with dates and comments.

On subsequent runs, play logs are only re-fetched for games whose
play count has changed since the last run (incremental update).

Usage:
    python fetch_bgg_collection.py

Requirements:
    - Python 3.8+
    - requests library (pip install requests)
    - A .env file with BGG_BEARER_TOKEN and BGG_PASSWORD

Output:
    bgg_collection.json in the output directory (default: ./output/)
"""

import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: 'requests' library not found. Install with: pip install requests")
    sys.exit(1)

# --- Configuration ---

BGG_USERNAME = "jsneij"
BGG_API_BASE = "https://boardgamegeek.com/xmlapi2"
OUTPUT_DIR = Path(__file__).parent.parent / "data"
OUTPUT_FILE = OUTPUT_DIR / "bgg_collection.json"

# Fetch parameters
COLLECTION_PARAMS = {
    "username": BGG_USERNAME,
    "stats": "1",
    "showprivate": "1",
    "subtype": "boardgame",
}

# Retry config (BGG queues requests and returns 202 on first attempt)
MAX_RETRIES = 5
RETRY_DELAY_SECONDS = 5

# Rate limiting: pause between play log requests to avoid hitting BGG limits
PLAYS_REQUEST_DELAY = 1.5  # seconds between each game's play log fetch

# /thing API batch size (BGG allows up to 20 IDs per request)
THING_BATCH_SIZE = 20
THING_REQUEST_DELAY = 1.0  # seconds between /thing batch requests


# =============================================================================
# Authentication & Environment
# =============================================================================

def load_env() -> dict:
    """Load configuration from .env file or environment variables."""
    config = {}

    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    config[key.strip()] = val.strip().strip('"').strip("'")

    # Environment variables override .env
    for key in ["BGG_BEARER_TOKEN", "BGG_PASSWORD"]:
        env_val = os.environ.get(key)
        if env_val:
            config[key] = env_val

    if not config.get("BGG_BEARER_TOKEN"):
        print("ERROR: No BGG_BEARER_TOKEN found.")
        print("Add to .env file: BGG_BEARER_TOKEN=your-token-here")
        sys.exit(1)

    if not config.get("BGG_PASSWORD"):
        print("WARNING: No BGG_PASSWORD found — private info will not be available.")
        print("Add to .env file: BGG_PASSWORD=your-bgg-password")

    return config


def bgg_login(password: str) -> requests.Session:
    """Log into BGG to get session cookies for private data access."""
    session = requests.Session()

    print("  Logging into BGG...")
    login_url = "https://boardgamegeek.com/login/api/v1"
    login_data = {
        "credentials": {
            "username": BGG_USERNAME,
            "password": password,
        }
    }

    response = session.post(
        login_url,
        json=login_data,
        headers={"Content-Type": "application/json"},
        timeout=30,
    )

    if response.status_code in (200, 202, 204):
        print("  ✓ Logged in successfully")
        return session
    else:
        print(f"  WARNING: Login returned HTTP {response.status_code}")
        print(f"  Response: {response.text[:300]}")
        print("  Continuing without session — private info may be missing.")
        return session


# =============================================================================
# BGG API Fetchers
# =============================================================================

def fetch_collection(token: str, session: requests.Session = None, extra_params: dict = None) -> ET.Element:
    """Fetch collection XML from BGG API with retry logic for 202 queue responses."""
    params = {**COLLECTION_PARAMS}
    if extra_params:
        params.update(extra_params)

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/xml",
    }

    url = f"{BGG_API_BASE}/collection"
    http = session if session else requests

    for attempt in range(1, MAX_RETRIES + 1):
        print(f"  Fetching collection (attempt {attempt}/{MAX_RETRIES})...")
        response = http.get(url, params=params, headers=headers, timeout=30)

        if response.status_code == 200:
            return ET.fromstring(response.text)
        elif response.status_code == 202:
            print(f"  BGG is queuing request... waiting {RETRY_DELAY_SECONDS}s")
            time.sleep(RETRY_DELAY_SECONDS)
        else:
            print(f"  ERROR: HTTP {response.status_code}")
            print(f"  Response: {response.text[:500]}")
            sys.exit(1)

    print(f"ERROR: BGG did not return data after {MAX_RETRIES} attempts.")
    sys.exit(1)


def fetch_plays_for_game(bgg_id: int, token: str, session: requests.Session = None) -> list:
    """Fetch all play log entries for a specific game from BGG."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/xml",
    }

    http = session if session else requests
    all_plays = []
    page = 1

    while True:
        params = {
            "username": BGG_USERNAME,
            "id": str(bgg_id),
            "page": str(page),
        }

        for attempt in range(1, MAX_RETRIES + 1):
            response = http.get(
                f"{BGG_API_BASE}/plays",
                params=params,
                headers=headers,
                timeout=30,
            )

            if response.status_code == 200:
                break
            elif response.status_code == 202:
                time.sleep(RETRY_DELAY_SECONDS)
            else:
                return all_plays
        else:
            return all_plays

        root = ET.fromstring(response.text)
        play_elements = root.findall("play")

        if not play_elements:
            break

        for play_el in play_elements:
            play = {
                "play_id": _int_or_none(play_el.get("id")),
                "date": play_el.get("date", None) or None,
                "quantity": _int_or_none(play_el.get("quantity")) or 1,
                "length_minutes": _int_or_none(play_el.get("length")) or None,
                "incomplete": play_el.get("incomplete") == "1",
                "no_win_stats": play_el.get("nowinstats") == "1",
                "location": play_el.get("location", None) or None,
            }

            # Comments
            comments_el = play_el.find("comments")
            play["comments"] = comments_el.text if comments_el is not None else None

            # Players
            players_el = play_el.find("players")
            if players_el is not None:
                play["players"] = []
                for player_el in players_el.findall("player"):
                    play["players"].append({
                        "username": player_el.get("username", None) or None,
                        "name": player_el.get("name", None) or None,
                        "start_position": player_el.get("startposition", None) or None,
                        "color": player_el.get("color", None) or None,
                        "score": player_el.get("score", None) or None,
                        "new": player_el.get("new") == "1",
                        "rating": _float_or_none(player_el.get("rating")),
                        "win": player_el.get("win") == "1",
                    })
            else:
                play["players"] = []

            all_plays.append(play)

        # BGG returns max 100 plays per page
        total_plays = _int_or_none(root.get("total")) or 0
        if page * 100 >= total_plays:
            break
        page += 1
        time.sleep(PLAYS_REQUEST_DELAY)

    return all_plays


def fetch_thing_data(bgg_ids: list, token: str, session: requests.Session = None) -> dict:
    """Fetch weight and other data from /thing endpoint in batches.

    The collection API does not return averageweight — this endpoint does.
    BGG allows up to 20 IDs per /thing request.
    Returns dict of {bgg_id: {"avg_weight": float, ...}}.
    """
    http = session if session else requests
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/xml",
    }
    results = {}

    for i in range(0, len(bgg_ids), THING_BATCH_SIZE):
        batch = bgg_ids[i:i + THING_BATCH_SIZE]
        ids_str = ",".join(str(bid) for bid in batch)

        for attempt in range(1, MAX_RETRIES + 1):
            resp = http.get(
                f"{BGG_API_BASE}/thing",
                params={"id": ids_str, "stats": "1"},
                headers=headers,
                timeout=30,
            )
            if resp.status_code == 200:
                break
            elif resp.status_code == 202:
                time.sleep(RETRY_DELAY_SECONDS)
            else:
                break
        else:
            continue

        if resp.status_code != 200:
            continue

        root = ET.fromstring(resp.text)
        for item in root.findall("item"):
            bgg_id = int(item.get("id", 0))
            data = {}

            stats_el = item.find("statistics")
            if stats_el is not None:
                ratings_el = stats_el.find("ratings")
                if ratings_el is not None:
                    wt_el = ratings_el.find("averageweight")
                    if wt_el is not None:
                        val = wt_el.get("value")
                        data["avg_weight"] = round(float(val), 2) if val and float(val) != 0 else None

            results[bgg_id] = data

        if i + THING_BATCH_SIZE < len(bgg_ids):
            time.sleep(THING_REQUEST_DELAY)

    return results


# =============================================================================
# Parsing
# =============================================================================

def parse_item(item: ET.Element) -> dict:
    """Parse a single <item> element from the BGG collection XML."""
    game = {}

    # Core identifiers
    game["bgg_id"] = int(item.get("objectid", 0))
    game["type"] = item.get("subtype", "boardgame")
    game["collection_id"] = int(item.get("collid", 0))

    # Name and year
    name_el = item.find("name")
    game["name"] = name_el.text if name_el is not None else "Unknown"

    year_el = item.find("yearpublished")
    game["year"] = int(year_el.text) if year_el is not None and year_el.text else None

    # Images
    image_el = item.find("image")
    game["image"] = image_el.text if image_el is not None else None

    thumb_el = item.find("thumbnail")
    game["thumbnail"] = thumb_el.text if thumb_el is not None else None

    # Status flags
    status_el = item.find("status")
    if status_el is not None:
        game["status"] = {
            "own": status_el.get("own") == "1",
            "previously_owned": status_el.get("prevowned") == "1",
            "for_trade": status_el.get("fortrade") == "1",
            "want": status_el.get("want") == "1",
            "want_to_play": status_el.get("wanttoplay") == "1",
            "want_to_buy": status_el.get("wanttobuy") == "1",
            "wishlist": status_el.get("wishlist") == "1",
            "preordered": status_el.get("preordered") == "1",
            "last_modified": status_el.get("lastmodified", ""),
        }
        wp = item.find("wishlistpriority")
        if wp is not None and wp.text:
            game["wishlist_priority"] = int(wp.text)
        elif game["status"]["wishlist"]:
            wlp = status_el.get("wishlistpriority")
            game["wishlist_priority"] = int(wlp) if wlp else None
        else:
            game["wishlist_priority"] = None
    else:
        game["status"] = {}
        game["wishlist_priority"] = None

    # Play count
    plays_el = item.find("numplays")
    game["num_plays"] = int(plays_el.text) if plays_el is not None and plays_el.text else 0

    # User comment
    comment_el = item.find("comment")
    game["comment"] = comment_el.text if comment_el is not None else None

    # Stats
    stats_el = item.find("stats")
    if stats_el is not None:
        game["stats"] = {
            "min_players": _int_or_none(stats_el.get("minplayers")),
            "max_players": _int_or_none(stats_el.get("maxplayers")),
            "min_playtime": _int_or_none(stats_el.get("minplaytime")),
            "max_playtime": _int_or_none(stats_el.get("maxplaytime")),
            "playing_time": _int_or_none(stats_el.get("playingtime")),
        }

        rating_el = stats_el.find("rating")
        if rating_el is not None:
            user_rating = rating_el.get("value")
            game["stats"]["user_rating"] = float(user_rating) if user_rating and user_rating != "N/A" else None

            for tag in ["usersrated", "average", "bayesaverage", "stddev", "median"]:
                el = rating_el.find(tag)
                if el is not None:
                    val = el.get("value")
                    game["stats"][tag] = float(val) if val else None

            weight_el = rating_el.find("averageweight")
            if weight_el is not None:
                val = weight_el.get("value")
                game["stats"]["avg_weight"] = float(val) if val and val != "0" else None
            else:
                game["stats"]["avg_weight"] = None

            ranks_el = rating_el.find("ranks")
            if ranks_el is not None:
                game["stats"]["ranks"] = []
                for rank_el in ranks_el.findall("rank"):
                    rank_val = rank_el.get("value")
                    game["stats"]["ranks"].append({
                        "type": rank_el.get("type", ""),
                        "name": rank_el.get("name", ""),
                        "friendly_name": rank_el.get("friendlyname", ""),
                        "value": int(rank_val) if rank_val and rank_val != "Not Ranked" else None,
                    })
    else:
        game["stats"] = {}

    # Private info (acquisition date, price paid, etc.)
    private_el = item.find("privateinfo")
    if private_el is not None:
        game["private_info"] = {
            "acquisition_date": private_el.get("acquisitiondate", None) or None,
            "price_paid": private_el.get("pp_currency", "") + private_el.get("pricepaid", "") or None,
            "current_value": private_el.get("cv_currency", "") + private_el.get("currvalue", "") or None,
            "quantity": _int_or_none(private_el.get("quantity")),
        }
        priv_comment_el = private_el.find("privatecomment")
        game["private_info"]["comment"] = priv_comment_el.text if priv_comment_el is not None else None
    else:
        game["private_info"] = None

    # Play log placeholder — populated later
    game["plays"] = []

    return game


def _int_or_none(val):
    """Convert string to int, or return None."""
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _float_or_none(val):
    """Convert string to float, or return None."""
    if val is None:
        return None
    try:
        f = float(val)
        return f if f != 0 else None
    except (ValueError, TypeError):
        return None


# =============================================================================
# Collection Organization
# =============================================================================

def categorize_collection(games: list) -> dict:
    """Organize games into categories for easy consumption."""
    collection = {
        "metadata": {
            "username": BGG_USERNAME,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "total_items": len(games),
        },
        "owned": [],
        "wishlist": {
            "priority_1_must_have": [],
            "priority_2_love_to_have": [],
            "priority_3_like_to_have": [],
            "priority_4_thinking_about": [],
            "priority_5_dont_buy_this": [],
            "unset": [],
        },
        "preordered": [],
        "previously_owned": [],
        "want_to_play": [],
        "want_to_buy": [],
        "for_trade": [],
    }

    for g in games:
        s = g.get("status", {})

        if s.get("own"):
            collection["owned"].append(g)

        if s.get("wishlist"):
            wp = g.get("wishlist_priority")
            if wp == 1:
                collection["wishlist"]["priority_1_must_have"].append(g)
            elif wp == 2:
                collection["wishlist"]["priority_2_love_to_have"].append(g)
            elif wp == 3:
                collection["wishlist"]["priority_3_like_to_have"].append(g)
            elif wp == 4:
                collection["wishlist"]["priority_4_thinking_about"].append(g)
            elif wp == 5:
                collection["wishlist"]["priority_5_dont_buy_this"].append(g)
            else:
                collection["wishlist"]["unset"].append(g)

        if s.get("preordered"):
            collection["preordered"].append(g)

        if s.get("previously_owned"):
            collection["previously_owned"].append(g)

        if s.get("want_to_play"):
            collection["want_to_play"].append(g)

        if s.get("want_to_buy"):
            collection["want_to_buy"].append(g)

        if s.get("for_trade"):
            collection["for_trade"].append(g)

    # Counts
    collection["metadata"]["counts"] = {
        "owned": len(collection["owned"]),
        "wishlist_total": sum(len(v) for v in collection["wishlist"].values()),
        "wishlist_p1": len(collection["wishlist"]["priority_1_must_have"]),
        "wishlist_p2": len(collection["wishlist"]["priority_2_love_to_have"]),
        "wishlist_p3": len(collection["wishlist"]["priority_3_like_to_have"]),
        "wishlist_p4": len(collection["wishlist"]["priority_4_thinking_about"]),
        "wishlist_p5": len(collection["wishlist"]["priority_5_dont_buy_this"]),
        "preordered": len(collection["preordered"]),
        "previously_owned": len(collection["previously_owned"]),
    }

    return collection


# =============================================================================
# Fetch Log & Incremental Update Helpers
# =============================================================================

FETCH_LOG_FILE = OUTPUT_DIR / "fetch_log.json"

# Terminal colors
YELLOW = "\033[33m"
GREEN  = "\033[32m"
RESET  = "\033[0m"

# Maps status flag -> display label (priority order for primary category)
_STATUS_LABELS = [
    ("own",              "owned"),
    ("preordered",       "preordered"),
    ("previously_owned", "previously_owned"),
    ("want_to_play",     "want_to_play"),
    ("want_to_buy",      "want_to_buy"),
    ("for_trade",        "for_trade"),
    ("wishlist",         "wishlist"),
]


def _primary_category(game: dict) -> str:
    s = game.get("status", {})
    for flag, label in _STATUS_LABELS:
        if s.get(flag):
            return label
    return "other"


def load_fetch_log() -> dict:
    """Load the lightweight fetch log. Returns {} if none exists."""
    if not FETCH_LOG_FILE.exists():
        return {}
    try:
        with open(FETCH_LOG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_fetch_log(games: list, counts: dict, fetched_at: str):
    """Save a lightweight snapshot used for next-run comparison."""
    def _snapshot(g):
        stats = g.get("stats", {}) or {}
        private = g.get("private_info") or {}
        return {
            "name": g["name"],
            "num_plays": g["num_plays"],
            "category": _primary_category(g),
            "wishlist_priority": g.get("wishlist_priority"),
            "user_rating": stats.get("user_rating"),
            "comment": g.get("comment"),
            "acquisition_date": private.get("acquisition_date"),
            "play_comments": {
                str(p["play_id"]): p.get("comments")
                for p in g.get("plays", [])
                if p.get("play_id") is not None and p.get("comments")
            },
        }

    log = {
        "fetched_at": fetched_at,
        "counts": counts,
        "games": {str(g["bgg_id"]): _snapshot(g) for g in games},
    }
    with open(FETCH_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)


def print_previous_state(log: dict):
    """Print a summary of the last fetch."""
    counts = log.get("counts", {})
    print(f"  Last fetched: {log.get('fetched_at', 'unknown')}")
    print(f"    Owned:            {counts.get('owned', '?')}")
    print(f"    Wishlist (total): {counts.get('wishlist_total', '?')}")
    print(f"      P1 Must Have:   {counts.get('wishlist_p1', '?')}")
    print(f"      P2 Love to Have:{counts.get('wishlist_p2', '?')}")
    print(f"      P3 Like to Have:{counts.get('wishlist_p3', '?')}")
    print(f"      P4 Thinking:    {counts.get('wishlist_p4', '?')}")
    print(f"      P5 Don't Buy:   {counts.get('wishlist_p5', '?')}")
    print(f"    Preordered:       {counts.get('preordered', '?')}")
    print(f"    Previously Owned: {counts.get('previously_owned', '?')}")


def _fmt_rating(val) -> str:
    return f"{val:.1f}" if val is not None else "—"


def print_changes(old_log: dict, games: list):
    """Compare current games against the previous fetch log and print what changed."""
    old_games = old_log.get("games", {})

    new_snapshots = {}
    for g in games:
        stats = g.get("stats", {}) or {}
        private = g.get("private_info") or {}
        new_snapshots[str(g["bgg_id"])] = {
            "name": g["name"],
            "num_plays": g["num_plays"],
            "category": _primary_category(g),
            "wishlist_priority": g.get("wishlist_priority"),
            "user_rating": stats.get("user_rating"),
            "comment": g.get("comment"),
            "acquisition_date": private.get("acquisition_date"),
            "play_comments": {
                str(p["play_id"]): p.get("comments")
                for p in g.get("plays", [])
                if p.get("play_id") is not None and p.get("comments")
            },
        }

    added = []
    removed = []
    updates = {}  # bgg_id -> list of change strings

    for bgg_id, new in new_snapshots.items():
        name = new["name"]
        if bgg_id not in old_games:
            added.append(new)
            continue

        old = old_games[bgg_id]
        changes = []

        if new["category"] != old.get("category", ""):
            changes.append(f"status: {old.get('category', '?')} → {new['category']}")

        if new["num_plays"] != old.get("num_plays", 0):
            changes.append(f"plays: {old.get('num_plays', 0)} → {new['num_plays']}")

        old_rating = old.get("user_rating")
        new_rating = new["user_rating"]
        if new_rating != old_rating:
            changes.append(f"your rating: {_fmt_rating(old_rating)} → {_fmt_rating(new_rating)}")

        old_wp = old.get("wishlist_priority")
        new_wp = new["wishlist_priority"]
        if new_wp != old_wp and (old_wp is not None or new_wp is not None):
            changes.append(f"wishlist priority: {old_wp or '—'} → {new_wp or '—'}")

        old_comment = old.get("comment")
        new_comment = new["comment"]
        if new_comment != old_comment:
            if new_comment and old_comment:
                changes.append("collection comment: updated")
            elif new_comment:
                changes.append("collection comment: added")
            else:
                changes.append("collection comment: removed")

        old_acq = old.get("acquisition_date")
        new_acq = new["acquisition_date"]
        if new_acq != old_acq:
            changes.append(f"acquisition date: {old_acq or '—'} → {new_acq or '—'}")

        old_pc = old.get("play_comments", {})
        new_pc = new["play_comments"]
        for play_id, new_text in new_pc.items():
            old_text = old_pc.get(play_id)
            if old_text is None:
                changes.append(f"play #{play_id}: comment added")
            elif new_text != old_text:
                changes.append(f"play #{play_id}: comment updated")
        for play_id in old_pc:
            if play_id not in new_pc:
                changes.append(f"play #{play_id}: comment removed")

        if changes:
            updates[bgg_id] = (name, changes)

    for bgg_id in old_games:
        if bgg_id not in new_snapshots:
            removed.append(old_games[bgg_id]["name"])

    if not any([added, removed, updates]):
        print(f"  {GREEN}No changes detected.{RESET}")
        return

    if added:
        print(f"  New games ({len(added)}):")
        for g in added:
            plays = f", {g['num_plays']} plays" if g["num_plays"] else ""
            print(f"    {YELLOW}+ {g['name']} [{g['category']}]{plays}{RESET}")

    if removed:
        print(f"  Removed from collection ({len(removed)}):")
        for name in removed:
            print(f"    {YELLOW}- {name}{RESET}")

    if updates:
        print(f"  Updated ({len(updates)}):")
        for name, changes in updates.values():
            print(f"    {YELLOW}~ {name}{RESET}")
            for c in changes:
                print(f"        {YELLOW}{c}{RESET}")


def load_existing_games_index() -> dict:
    """Load existing output and return a dict of bgg_id -> full game dict (including plays)."""
    if not OUTPUT_FILE.exists():
        return {}

    try:
        with open(OUTPUT_FILE, encoding="utf-8") as f:
            existing = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

    index = {}
    categories = ["owned", "preordered", "previously_owned", "want_to_play", "want_to_buy", "for_trade"]
    for key in categories:
        for game in existing.get(key, []):
            index[game["bgg_id"]] = game
    for games in existing.get("wishlist", {}).values():
        for game in games:
            index[game["bgg_id"]] = game

    return index


def fetch_user_play_counts(token: str, session=None) -> dict:
    """Fetch play counts from the user's plays API.

    BGG's collection API num_plays is unreliable for items with multiple
    subtypes (e.g. boardgame + boardgameexpansion) — it reports 0 when
    queried under the wrong subtype. The plays API is authoritative.
    Returns dict of {bgg_id: play_count}.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/xml",
    }
    http = session if session else requests
    counts = {}
    page = 1
    while True:
        url = f"{BGG_API_BASE}/plays"
        params = {"username": BGG_USERNAME, "page": str(page)}
        resp = http.get(url, params=params, headers=headers, timeout=30)
        if resp.status_code != 200:
            break
        root = ET.fromstring(resp.text)
        plays = root.findall("play")
        if not plays:
            break
        for play in plays:
            item = play.find("item")
            if item is not None:
                bgg_id = int(item.get("objectid"))
                qty = int(play.get("quantity", 1))
                counts[bgg_id] = counts.get(bgg_id, 0) + qty
        total = int(root.get("total", 0))
        if page * 100 >= total:
            break
        page += 1
        time.sleep(PLAYS_REQUEST_DELAY)
    return counts


def patch_play_counts(games: list, play_counts: dict):
    """Update num_plays for games where the plays API reports a different count."""
    patched = 0
    for game in games:
        api_plays = play_counts.get(game["bgg_id"], 0)
        if api_plays != game.get("num_plays", 0):
            game["num_plays"] = api_plays
            patched += 1
    return patched


# =============================================================================
# Main
# =============================================================================

def main():
    print("=" * 60)
    print("BGG Collection Fetcher")
    print("=" * 60)

    # Show previous fetch state
    fetch_log = load_fetch_log()
    if fetch_log:
        print("\n[Previous fetch]")
        print_previous_state(fetch_log)
    else:
        print("\n  No previous fetch log — this will be a full fetch.")

    # Load config and authenticate
    config = load_env()
    token = config["BGG_BEARER_TOKEN"]
    print(f"\n✓ Token loaded  (username: {BGG_USERNAME})")

    session = None
    password = config.get("BGG_PASSWORD")
    if password:
        print("\n[0/4] Authenticating for private data...")
        session = bgg_login(password)
    else:
        print("\n  Skipping login — no BGG_PASSWORD in .env")

    # -------------------------------------------------------------------------
    # Incremental path: use modifiedsince to check for changes first
    # -------------------------------------------------------------------------
    if fetch_log:
        last_date = datetime.strptime(fetch_log["fetched_at"][:10], "%Y-%m-%d")
        since_date = last_date.strftime("%Y-%m-%d")
        print(f"\n[1/4] Checking BGG for changes since {since_date}...")
        root = fetch_collection(token, session=session, extra_params={"modifiedsince": since_date})
        changed_count = int(root.get("totalitems", 0))

        if changed_count == 0:
            # modifiedsince doesn't catch play log changes — check num_plays
            print(f"  ✓ No collection edits. Checking play counts...")
            # Collection API num_plays is unreliable for multi-subtype items;
            # use the plays API as authoritative source for play counts
            play_counts = fetch_user_play_counts(token, session=session)
            existing_index = load_existing_games_index()
            play_changed = []
            for bgg_id, game in existing_index.items():
                api_plays = play_counts.get(bgg_id, 0)
                cached_plays = game.get("num_plays", 0)
                if api_plays != cached_plays:
                    play_changed.append((bgg_id, game, api_plays))

            # Check for missing weights even if nothing else changed
            missing_wt = [bid for bid, g in existing_index.items()
                          if not g.get("stats", {}).get("avg_weight")]

            if not play_changed and not missing_wt:
                print(f"  {GREEN}✓ Nothing has changed since last fetch.{RESET}")
                print(f"\n{'=' * 60}")
                print("Up to date. No files written.")
                print(f"{'=' * 60}")
                return

            if not play_changed and missing_wt:
                # Only weights need backfill — skip play log work
                print(f"  ✓ No play changes, but {len(missing_wt)} game(s) missing weight")
            elif play_changed:
                # Patch play counts and fetch new play logs
                print(f"  ✓ {len(play_changed)} game(s) have new plays")
                for i, (bgg_id, prev_game, new_count) in enumerate(play_changed, 1):
                    prev_game["num_plays"] = new_count
                    print(f"  [{i}/{len(play_changed)}] {prev_game['name']} ({new_count} plays)...", end="", flush=True)
                    if new_count > 0:
                        plays = fetch_plays_for_game(bgg_id, token, session=session)
                        prev_game["plays"] = plays
                        print(f" ✓ {len(plays)} entries")
                    else:
                        prev_game["plays"] = []
                        print(" ✓ cleared")
                    if i < len(play_changed):
                        time.sleep(PLAYS_REQUEST_DELAY)

            games = list(existing_index.values())

        else:
            # Also check expansions for changes
            exp_root = fetch_collection(token, session=session,
                                        extra_params={"subtype": "boardgameexpansion",
                                                      "modifiedsince": since_date})
            exp_changed = int(exp_root.get("totalitems", 0))
            total_changed = changed_count + exp_changed
            print(f"  ✓ {changed_count} boardgame(s) + {exp_changed} expansion(s) changed")

            print(f"\n[2/4] Parsing {total_changed} changed item(s)...")
            seen_ids = set()
            changed_games = []
            for item in root.findall("item"):
                g = parse_item(item)
                seen_ids.add(g["bgg_id"])
                changed_games.append(g)
            for item in exp_root.findall("item"):
                g = parse_item(item)
                if g["bgg_id"] not in seen_ids:
                    seen_ids.add(g["bgg_id"])
                    changed_games.append(g)
            print(f"  ✓ Parsed {len(changed_games)} games")

            # Patch play counts from plays API (collection API is unreliable for expansions)
            user_plays = fetch_user_play_counts(token, session=session)
            patch_play_counts(changed_games, user_plays)

            # Load full existing data to merge into
            existing_index = load_existing_games_index()

            # Determine play log fetches needed (only for changed games)
            games_to_fetch = []
            for game in changed_games:
                prev = existing_index.get(game["bgg_id"])
                prev_plays = prev.get("plays", []) if prev else []
                prev_count = prev.get("num_plays", 0) if prev else 0
                if game["num_plays"] == prev_count and prev_plays:
                    game["plays"] = prev_plays  # reuse cached plays
                elif game["num_plays"] > 0:
                    games_to_fetch.append(game)

            print(f"\n[3/4] Fetching play logs for {len(games_to_fetch)} updated game(s)...")
            if not games_to_fetch:
                print("  ✓ No play log changes needed.")
            for i, game in enumerate(games_to_fetch, 1):
                print(f"  [{i}/{len(games_to_fetch)}] {game['name']} ({game['num_plays']} plays)...", end="", flush=True)
                plays = fetch_plays_for_game(game["bgg_id"], token, session=session)
                game["plays"] = plays
                print(f" ✓ {len(plays)} entries")
                if i < len(games_to_fetch):
                    time.sleep(PLAYS_REQUEST_DELAY)

            # Merge changed games into the full existing set
            for game in changed_games:
                existing_index[game["bgg_id"]] = game
            games = list(existing_index.values())

    # -------------------------------------------------------------------------
    # Full fetch path: first run, no fetch log
    # -------------------------------------------------------------------------
    else:
        print("\n[1/4] Fetching full collection from BGG...")
        root = fetch_collection(token, session=session)
        total_items = root.get("totalitems", "?")
        print(f"  ✓ Received {total_items} boardgames")

        # Also fetch expansions (they may not appear under subtype=boardgame)
        print("  Fetching expansions...")
        exp_root = fetch_collection(token, session=session,
                                    extra_params={"subtype": "boardgameexpansion"})
        exp_count = exp_root.get("totalitems", "0")
        print(f"  ✓ Received {exp_count} expansions")

        print("\n[2/4] Parsing collection data...")
        seen_ids = set()
        games = []
        for item in root.findall("item"):
            g = parse_item(item)
            seen_ids.add(g["bgg_id"])
            games.append(g)
        # Add expansions not already in the boardgame results
        exp_added = 0
        for item in exp_root.findall("item"):
            g = parse_item(item)
            if g["bgg_id"] not in seen_ids:
                seen_ids.add(g["bgg_id"])
                games.append(g)
                exp_added += 1
        print(f"  ✓ Parsed {len(games)} games ({exp_added} expansion-only items added)")

        # Patch play counts from plays API (collection API is unreliable for expansions)
        user_plays = fetch_user_play_counts(token, session=session)
        patched = patch_play_counts(games, user_plays)
        if patched:
            print(f"  ✓ Patched play counts for {patched} game(s)")

        games_to_fetch = [g for g in games if g["num_plays"] > 0]
        print(f"\n[3/4] Fetching play logs for {len(games_to_fetch)} games...")
        for i, game in enumerate(games_to_fetch, 1):
            print(f"  [{i}/{len(games_to_fetch)}] {game['name']} ({game['num_plays']} plays)...", end="", flush=True)
            plays = fetch_plays_for_game(game["bgg_id"], token, session=session)
            game["plays"] = plays
            print(f" ✓ {len(plays)} entries")
            if i < len(games_to_fetch):
                time.sleep(PLAYS_REQUEST_DELAY)

    # -------------------------------------------------------------------------
    # Common: fetch weights from /thing API, categorize, write output
    # -------------------------------------------------------------------------

    # Fetch weight from /thing endpoint (collection API doesn't include it)
    missing_weight = [g["bgg_id"] for g in games
                      if not g.get("stats", {}).get("avg_weight")]
    if missing_weight:
        print(f"\n  Fetching weight data for {len(missing_weight)} games "
              f"({len(missing_weight) // THING_BATCH_SIZE + 1} batches)...")
        thing_data = fetch_thing_data(missing_weight, token, session=session)
        patched_wt = 0
        for g in games:
            td = thing_data.get(g["bgg_id"])
            if td and td.get("avg_weight") is not None:
                g.setdefault("stats", {})["avg_weight"] = td["avg_weight"]
                patched_wt += 1
        print(f"  ✓ Updated weight for {patched_wt} game(s)")

    total_play_entries = sum(len(g["plays"]) for g in games)

    collection = categorize_collection(games)
    counts = collection["metadata"]["counts"]
    fetched_at = collection["metadata"]["fetched_at"]

    print(f"\n[Changes since last fetch]")
    if fetch_log:
        print_changes(fetch_log, games)
    else:
        print("  (First run — no previous data to compare.)")

    print(f"\n[Current summary]")
    print(f"    Owned:            {counts['owned']}")
    print(f"    Wishlist (total): {counts['wishlist_total']}")
    print(f"      P1 Must Have:   {counts['wishlist_p1']}")
    print(f"      P2 Love to Have:{counts['wishlist_p2']}")
    print(f"      P3 Like to Have:{counts['wishlist_p3']}")
    print(f"      P4 Thinking:    {counts['wishlist_p4']}")
    print(f"      P5 Don't Buy:   {counts['wishlist_p5']}")
    print(f"    Preordered:       {counts['preordered']}")
    print(f"    Previously Owned: {counts['previously_owned']}")
    print(f"    Play log entries: {total_play_entries}")

    print(f"\n[4/4] Writing output...")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(collection, f, indent=2, ensure_ascii=False)
    print(f"  ✓ Written to: {OUTPUT_FILE} ({OUTPUT_FILE.stat().st_size / 1024:.1f} KB)")

    compact_file = OUTPUT_DIR / "bgg_collection_compact.json"
    with open(compact_file, "w", encoding="utf-8") as f:
        json.dump(collection, f, ensure_ascii=False)
    print(f"  ✓ Compact: {compact_file} ({compact_file.stat().st_size / 1024:.1f} KB)")

    save_fetch_log(games, counts, fetched_at)
    print(f"  ✓ Fetch log updated")

    print(f"\n{'=' * 60}")
    print(f"Done! Upload bgg_collection.json to your Claude project.")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
