"""
score_new_games.py
Detects unscored games in bgg_collection_scores.json (M=0) and calls Claude API
to generate IS scores, description, justification, type, and mechanisms.

Requires ANTHROPIC_API_KEY in environment or .env file.
"""

import json
import os
import sys
from pathlib import Path

SCORES_PATH = "data/bgg_collection_scores.json"
BGG_PATH = "data/bgg_collection.json"
MECHS_PATH = "data/mechanisms.json"
FRAMEWORK_PATH = "docs/immersion_score.md"

MODEL = "claude-sonnet-4-20250514"


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_env():
    """Load .env if present (for local runs)."""
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())


def find_unscored(scores):
    """Find games with M=0 (placeholders from sync_scores.py)."""
    unscored = []
    for section in ("owned", "wishlist"):
        for name, obj in scores.get(section, {}).items():
            if obj.get("M", 0) == 0 and obj.get("T", 0) == 0:
                unscored.append((section, name, obj))
    return unscored


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


def build_calibration_examples(scores):
    """Pick diverse calibration examples from existing scored games."""
    examples = []
    targets = [
        ("owned", "Spirit Island"),
        ("owned", "Arkham Horror: The Card Game"),
        ("owned", "Nemesis"),
        ("owned", "Sky Team"),
        ("owned", "The Bloody Inn"),
        ("owned", "Harmonies"),
        ("owned", "Under Falling Skies"),
        ("owned", "Cards Against Humanity"),
    ]
    for section, name in targets:
        obj = scores.get(section, {}).get(name)
        if obj:
            gs = obj["M"] * obj["T"] * obj["G"] - obj["F"]
            is_val = gs * (obj["Ar"] / 2)
            examples.append(
                f"- {obj.get('name', name)}: M={obj['M']} T={obj['T']} G={obj['G']} "
                f"F={obj['F']} Ar={obj['Ar']} IS={is_val} | type={obj.get('type','')} | "
                f"description: {obj.get('description', 'N/A')} | "
                f"justification: {obj.get('justification', 'N/A')}"
            )
    return "\n".join(examples)


def build_mechanism_catalog(mechs_data):
    """Format the full mechanism catalog for the prompt."""
    lines = []
    for cat in mechs_data["categories"]:
        lines.append(f"\n### {cat['name']} ({cat['prefix']})")
        for m in cat["mechanisms"]:
            lines.append(f"  {m['code']} {m['name']}")
    return "\n".join(lines)


def build_prompt(game_name, section, bgg_data, framework_text, calibration, mech_catalog):
    """Build the Claude prompt for scoring a single game."""
    bgg_info = ""
    if bgg_data:
        stats = bgg_data.get("stats", {})
        bgg_info = f"""
BGG data for this game:
- Name: {game_name}
- Year: {bgg_data.get('year', 'unknown')}
- Players: {stats.get('min_players', '?')}-{stats.get('max_players', '?')}
- Playtime: {stats.get('min_playtime', '?')}-{stats.get('max_playtime', '?')} min
- BGG Weight: {stats.get('avg_weight', 'N/A')}
- BGG Average Rating: {stats.get('average', 'N/A')}
- BGG Rank: {next((r['value'] for r in stats.get('ranks', []) if r['name'] == 'boardgame'), 'N/A')}
"""

    return f"""You are scoring a board game using the Immersion Score framework for a specific user.

## Framework
{framework_text}

## Mechanism Catalog (use exact codes and names)
{mech_catalog}

## Calibration examples (already scored games for this user)
{calibration}

## Task
Score the following game that was just added to the user's {section}:

{bgg_info}

Return ONLY a JSON object with these fields:
{{
  "M": <int 1-5>,
  "T": <int 1-5>,
  "G": <int 1-5>,
  "F": <int 1-5>,
  "Ar": <int 1-5>,
  "type": "<solo|co-op|competitive|semi-coop|party>",
  "weight": <float from BGG or estimate>,
  "mechs": ["<CODE> <Mechanism Name>", ...],
  "description": "<one evocative sentence describing the game — a pitch, not a review>",
  "justification": "<M(n): 8-15 word reason. T(n): reason. G(n): reason. F(n): reason. Ar(n): reason.>"
}}

Rules:
- T is the gatekeeper. Score it for mechanics GENERATING the theme, not referencing it.
- Apply the solo-first lens: if this game has no solo mode or uses weak automa, lower T and G.
- F hard filter: unpainted miniatures in large quantities raise F by 1.
- Ar filter: goblin/Gearloc aesthetic scores lower. Prefer elegant, atmospheric art styles.
- mechs: use the format "CODE-NN Mechanism Name" (e.g., "ACT-01 Action Points", "CAR-08 Multi-Use Cards").
  Include ALL applicable mechanisms, not just primary ones. Include structural (STR-01 through STR-10),
  turn order, actions, resolution, victory, economy, movement, area control, cards, etc.
  Typical games have 6-15 mechanisms. Complex games may have more.
- description: one sentence, evocative, like a movie tagline.
- justification: cover all 5 variables, ~8-15 words each, in the style of the calibration examples.

Return ONLY the JSON object, no markdown fences, no explanation."""


def score_game(client, game_name, section, bgg_data, framework_text, calibration, mech_catalog):
    """Call Claude API to score a single game."""
    prompt = build_prompt(game_name, section, bgg_data, framework_text, calibration, mech_catalog)

    response = client.messages.create(
        model=MODEL,
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]

    return json.loads(text)


def main():
    load_env()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("  No ANTHROPIC_API_KEY found — skipping auto-scoring.")
        return

    scores = load_json(SCORES_PATH)
    unscored = find_unscored(scores)

    if not unscored:
        print("  All games already scored — nothing to do.")
        return

    print(f"  Found {len(unscored)} unscored game(s):")
    for section, name, _ in unscored:
        print(f"    - [{section}] {name}")

    # Load context
    bgg_collection = load_json(BGG_PATH)
    framework_text = Path(FRAMEWORK_PATH).read_text(encoding="utf-8")
    calibration = build_calibration_examples(scores)
    mechs_data = load_json(MECHS_PATH)
    mech_catalog = build_mechanism_catalog(mechs_data)

    # Import anthropic here so script doesn't crash if package missing but no games to score
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    for section, name, obj in unscored:
        print(f"\n  Scoring: {name} ({section})...")
        bgg_data = get_bgg_data(bgg_collection, name)

        try:
            result = score_game(client, name, section, bgg_data, framework_text, calibration, mech_catalog)

            # Merge result into existing entry (preserve name)
            display_name = obj.get("name", name)
            obj.update(result)
            obj["name"] = result.get("name", display_name)

            print(f"    M={obj['M']} T={obj['T']} G={obj['G']} F={obj['F']} Ar={obj['Ar']}")
            gs = obj["M"] * obj["T"] * obj["G"] - obj["F"]
            is_val = gs * (obj["Ar"] / 2)

            # Auto-populate feeling from IS zones
            if is_val >= 150:
                obj["feeling"] = "Total"
            elif is_val >= 87:
                obj["feeling"] = "Immersive"
            elif is_val >= 30:
                obj["feeling"] = "Engaging"
            else:
                obj["feeling"] = "On Shelf"

            print(f"    IS={is_val} → {obj['feeling']}")
            print(f"    {obj.get('description', '')[:80]}")

        except Exception as e:
            print(f"    ERROR scoring {name}: {e}")
            continue

    save_json(SCORES_PATH, scores)
    print(f"\n  Scores saved to {SCORES_PATH}")


if __name__ == "__main__":
    main()
