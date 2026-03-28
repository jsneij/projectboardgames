"""
sync_scores.py
Keeps output/bgg_collection_scores.json in sync with output/bgg_collection.json.

Rules:
  owned    — all owned games (base games and expansions). Adds placeholders for new
             games; removes entries for games no longer in the collection.
  wishlist — all wishlist items. Adds placeholders for new items; removes stale ones.
"""

import json
import sys

BGG_PATH    = "data/bgg_collection.json"
SCORES_PATH = "data/bgg_collection_scores.json"

GREEN  = "\033[32m"
RED    = "\033[31m"
RESET  = "\033[0m"

WISHLIST_PRIORITY_KEYS = [
    "priority_1_must_have",
    "priority_2_love_to_have",
    "priority_3_like_to_have",
    "priority_4_thinking_about",
    "priority_5_dont_buy_this",
]


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def sync():
    bgg    = load(BGG_PATH)
    scores = load(SCORES_PATH)

    owned_scores = scores.get("owned", {})
    wl_scores    = scores.get("wishlist", {})

    changes = []

    # ── OWNED ─────────────────────────────────────────────────────────────────
    # All owned games in collection (base games and expansions)
    current_owned = {
        g["name"]: g
        for g in bgg.get("owned", [])
    }

    # Add missing
    new_owned = []
    for bgg_name, g in current_owned.items():
        if bgg_name not in owned_scores:
            owned_scores[bgg_name] = {
                "name":    g["name"],
                "type":    "co-op",
                "weight":  0,
                "M": 0, "T": 0, "G": 0, "F": 0, "Ar": 0,
                "feeling": None,
                "mechs":   [],
            }
            new_owned.append(bgg_name)
            changes.append(f"{GREEN}  ★ SCORES ADDED (new owned game): {bgg_name}{RESET}")

    # Remove stale
    removed_owned = []
    for bgg_name in list(owned_scores.keys()):
        if bgg_name not in current_owned:
            del owned_scores[bgg_name]
            removed_owned.append(bgg_name)
            changes.append(f"{RED}  ✕ REMOVED from owned scores: {bgg_name}{RESET}")

    scores["owned"] = owned_scores

    # ── WISHLIST ───────────────────────────────────────────────────────────────
    current_wl = {}
    for key in WISHLIST_PRIORITY_KEYS:
        for g in bgg.get("wishlist", {}).get(key, []):
            current_wl[g["name"]] = g

    # Add missing
    new_wishlist = []
    for bgg_name in current_wl:
        if bgg_name not in wl_scores:
            wl_scores[bgg_name] = {
                "name":    bgg_name,
                "type":    "competitive",
                "weight":  0,
                "M": 0, "T": 0, "G": 0, "F": 0, "Ar": 0,
                "feeling": None,
                "mechs":   [],
            }
            new_wishlist.append(bgg_name)
            changes.append(f"{GREEN}  ★ SCORES ADDED (new wishlist): {bgg_name}{RESET}")

    # Remove stale
    removed_wishlist = []
    for bgg_name in list(wl_scores.keys()):
        if bgg_name not in current_wl:
            del wl_scores[bgg_name]
            removed_wishlist.append(bgg_name)
            changes.append(f"{RED}  ✕ REMOVED from wishlist scores: {bgg_name}{RESET}")

    scores["wishlist"] = wl_scores

    # ── SAVE ──────────────────────────────────────────────────────────────────
    save(SCORES_PATH, scores)

    if changes:
        print("Scores synced:")
        for line in changes:
            print(line)
        if new_owned:
            print(f"\n{GREEN}  → {len(new_owned)} new owned game(s) added: {', '.join(new_owned)}{RESET}")
        if new_wishlist:
            print(f"{GREEN}  → {len(new_wishlist)} new wishlist game(s) added: {', '.join(new_wishlist)}{RESET}")
        if removed_owned:
            print(f"{RED}  → {len(removed_owned)} owned game(s) removed: {', '.join(removed_owned)}{RESET}")
        if removed_wishlist:
            print(f"{RED}  → {len(removed_wishlist)} wishlist game(s) removed: {', '.join(removed_wishlist)}{RESET}")
    else:
        print("Scores already in sync — no changes.")

    return {
        "changes": changes,
        "new_owned": new_owned,
        "new_wishlist": new_wishlist,
        "removed_owned": removed_owned,
        "removed_wishlist": removed_wishlist,
    }


if __name__ == "__main__":
    sync()
