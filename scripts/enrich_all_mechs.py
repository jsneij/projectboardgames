"""
enrich_all_mechs.py
One-time script: enriches ALL games in bgg_collection_scores.json with comprehensive
mechanism assignments from the full encyclopedia (data/mechanisms.json).

Calls Claude API for each game, providing the full mechanism catalog + game context.
Replaces the existing 3-5 mechs with a thorough list of all applicable mechanisms.

Requires ANTHROPIC_API_KEY in environment or .env file.
"""

import json
import os
import sys
import time
from pathlib import Path

SCORES_PATH = "data/bgg_collection_scores.json"
BGG_PATH = "data/bgg_collection.json"
MECHS_PATH = "data/mechanisms.json"
FRAMEWORK_PATH = "docs/immersion_score.md"

MODEL = "claude-sonnet-4-20250514"
BATCH_DELAY = 1.0  # seconds between API calls


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_env():
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())


def get_bgg_data(bgg_collection, game_name):
    """Find a game's BGG data from the collection JSON."""
    for section_key in ("owned", "preordered", "previously_owned",
                        "want_to_play", "want_to_buy", "for_trade"):
        for g in bgg_collection.get(section_key, []):
            if g["name"] == game_name:
                return g
    wl = bgg_collection.get("wishlist", {})
    if isinstance(wl, dict):
        for priority_games in wl.values():
            if isinstance(priority_games, list):
                for g in priority_games:
                    if g["name"] == game_name:
                        return g
    return None


def build_mechanism_catalog(mechs_data):
    """Format the full mechanism catalog for the prompt."""
    lines = []
    for cat in mechs_data["categories"]:
        lines.append(f"\n### {cat['name']} ({cat['prefix']})")
        for m in cat["mechanisms"]:
            lines.append(f"  {m['code']} {m['name']}: {m['description']}")
    return "\n".join(lines)


def build_prompt(game_name, game_data, bgg_data, mechanism_catalog):
    """Build the Claude prompt for enriching a single game's mechanisms."""
    bgg_info = ""
    if bgg_data:
        stats = bgg_data.get("stats", {})
        bgg_info = f"""
BGG data:
- Year: {bgg_data.get('year', 'unknown')}
- Players: {stats.get('min_players', '?')}-{stats.get('max_players', '?')}
- Playtime: {stats.get('min_playtime', '?')}-{stats.get('max_playtime', '?')} min
- Weight: {stats.get('avg_weight', 'N/A')}
"""

    current_mechs = ", ".join(game_data.get("mechs", []))

    return f"""You are assigning mechanisms to a board game from a standardized encyclopedia.

## Full Mechanism Catalog
{mechanism_catalog}

## Game
Name: {game_name}
Type: {game_data.get('type', 'unknown')}
Weight: {game_data.get('weight', 'unknown')}
Description: {game_data.get('description', 'N/A')}
Current mechanisms (incomplete): {current_mechs}
{bgg_info}

## Task
Assign ALL applicable mechanisms from the catalog above to this game.

Rules:
- Include STRUCTURAL mechanisms (STR-01 through STR-10) where they apply:
  - co-op games → STR-02
  - solo games or games with solo mode → STR-04
  - competitive games → STR-01
  - semi-coop → STR-05
  - team-based → STR-03
  - campaign/scenario games → STR-08
  - legacy games → STR-10
  - traitor element → STR-07
- Include ALL gameplay mechanisms that genuinely apply, not just the primary 3-5.
  Think about turn structure, actions, resolution, victory conditions, economy, movement, cards, etc.
- Do NOT include mechanisms that only tangentially relate.
- Use exact codes and names from the catalog (e.g., "ACT-01 Action Points", "STR-02 Cooperative Games").
- Typical games should have 6-15 mechanisms. Complex games may have more.

Return ONLY a JSON array of strings, no explanation:
["CODE-NN Mechanism Name", ...]"""


def enrich_game(client, game_name, game_data, bgg_data, mechanism_catalog):
    """Call Claude API to enrich a single game's mechanisms."""
    prompt = build_prompt(game_name, game_data, bgg_data, mechanism_catalog)

    response = client.messages.create(
        model=MODEL,
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0].strip()

    return json.loads(text)


def main():
    load_env()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: No ANTHROPIC_API_KEY found.")
        sys.exit(1)

    scores = load_json(SCORES_PATH)
    bgg_collection = load_json(BGG_PATH)
    mechs_data = load_json(MECHS_PATH)
    mechanism_catalog = build_mechanism_catalog(mechs_data)

    # Build valid codes set for validation
    valid_codes = set()
    for cat in mechs_data["categories"]:
        for m in cat["mechanisms"]:
            valid_codes.add(m["code"])

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    total_games = 0
    total_mechs_before = 0
    total_mechs_after = 0

    for section in ("owned", "wishlist"):
        games = scores.get(section, {})
        print(f"\n{'='*60}")
        print(f"  Processing {section}: {len(games)} games")
        print(f"{'='*60}")

        for game_name, game_data in games.items():
            total_games += 1
            old_count = len(game_data.get("mechs", []))
            total_mechs_before += old_count

            bgg_data = get_bgg_data(bgg_collection, game_name)

            try:
                new_mechs = enrich_game(client, game_name, game_data, bgg_data, mechanism_catalog)

                # Validate: only keep mechs with valid codes
                validated = []
                for m in new_mechs:
                    code = m.split(" ")[0]
                    if code in valid_codes:
                        validated.append(m)
                    else:
                        print(f"    WARNING: Invalid code '{code}' for {game_name}, skipping")

                game_data["mechs"] = validated
                new_count = len(validated)
                total_mechs_after += new_count

                delta = new_count - old_count
                marker = f"+{delta}" if delta > 0 else str(delta)
                print(f"  [{total_games:3d}/154] {game_name}: {old_count} → {new_count} ({marker})")

            except Exception as e:
                print(f"  [{total_games:3d}/154] ERROR {game_name}: {e}")
                total_mechs_after += old_count
                continue

            time.sleep(BATCH_DELAY)

    save_json(SCORES_PATH, scores)
    print(f"\n{'='*60}")
    print(f"  Done! {total_games} games enriched")
    print(f"  Mechs: {total_mechs_before} → {total_mechs_after} "
          f"(avg {total_mechs_after/total_games:.1f}/game)")
    print(f"  Saved to {SCORES_PATH}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
