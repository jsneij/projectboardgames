"""
parse_encyclopedia.py
Parses the Complete Encyclopedia of Mechanisms markdown into data/mechanisms.json.
Extracts description, discussion, related mechanisms, and sample games for all 203 mechanisms.
"""

import json
import re
from pathlib import Path

ENCYCLOPEDIA_PATH = "docs/tabletop mechanics/Complete Encyclopedia of Mechanisms.md"
OUTPUT_PATH = "data/mechanisms.json"

# Valid mechanism code prefixes
VALID_PREFIXES = {"STR", "TRN", "ACT", "RES", "VIC", "UNC", "ECO", "AUC", "WPL", "MOV", "ARC", "SET", "CAR"}
CODE_PATTERN = re.compile(r'\b([A-Z]{3}-\d{2})\b')


def parse_sample_games(text):
    """Parse sample games from bullet list text."""
    games = []
    # Handle multi-line entries (some games have designer on next line)
    lines = text.strip().split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line.startswith("- "):
            i += 1
            continue

        line = line[2:].strip()

        # Check if next line is a continuation (starts with "- " followed by year or parens)
        if i + 1 < len(lines):
            next_line = lines[i + 1].strip()
            if next_line.startswith("- ") and re.match(r'^- \(?[\d~∼]', next_line):
                # Continuation line like "- 2016)"
                line = line + " " + next_line[2:].strip()
                i += 1
            elif next_line.startswith("- (") or next_line.startswith("- and "):
                # Continuation like "- (Edwards, Goldberg, and Grady, 1981)"
                line = line + " " + next_line[2:].strip()
                i += 1

        # Try to parse "Title (Designer(s), Year)"
        # Handle various year formats: 1964, ∼1200, ~200 bce, 2016+
        match = re.match(r'^(.+?)\s*\((.+?),\s*([\d~∼]+.*?)\)\s*(?:,.*)?$', line)
        if match:
            title = match.group(1).strip().rstrip(",")
            designer = match.group(2).strip()
            year_str = match.group(3).strip()
            games.append({
                "title": title,
                "designer": designer,
                "year": year_str
            })
        else:
            # Fallback: just store the raw line
            # Some entries don't follow the standard format
            clean = line.rstrip(",").strip()
            if clean:
                games.append({"title": clean, "designer": "", "year": ""})

        i += 1

    return games


def extract_related_mechanisms(discussion, own_code):
    """Extract mechanism codes referenced in discussion text."""
    codes = set(CODE_PATTERN.findall(discussion))
    # Remove self-reference and validate prefix
    codes.discard(own_code)
    valid = sorted(c for c in codes if c.split("-")[0] in VALID_PREFIXES)
    return valid


def clean_text(text):
    """Clean up markdown text: normalize whitespace, join broken lines."""
    # Remove trailing whitespace per line, join paragraphs
    lines = text.strip().split("\n")
    paragraphs = []
    current = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current:
                paragraphs.append(" ".join(current))
                current = []
        else:
            current.append(stripped)

    if current:
        paragraphs.append(" ".join(current))

    return "\n\n".join(paragraphs)


def parse_encyclopedia(filepath):
    """Parse the complete encyclopedia markdown into structured data."""
    text = Path(filepath).read_text(encoding="utf-8")

    # Split into category sections by "## N — Category Name"
    cat_splits = re.split(r'^## (\d+) — (.+)$', text, flags=re.MULTILINE)

    # cat_splits: [preamble, num1, name1, content1, num2, name2, content2, ...]
    categories = []

    for i in range(1, len(cat_splits), 3):
        cat_num = int(cat_splits[i])
        cat_name = cat_splits[i + 1].strip()
        cat_content = cat_splits[i + 2]

        # Split into mechanism sections by "### CODE Name"
        mech_splits = re.split(r'^### ([A-Z]{3}-\d{2}) (.+)$', cat_content, flags=re.MULTILINE)

        prefix = None
        mechanisms = []

        for j in range(1, len(mech_splits), 3):
            code = mech_splits[j].strip()
            name = mech_splits[j + 1].strip()
            mech_content = mech_splits[j + 2]

            if prefix is None:
                prefix = code.split("-")[0]

            # Extract sections
            desc_match = re.search(
                r'#### Description\s*\n(.*?)(?=#### Discussion|#### Sample Games|\Z)',
                mech_content, re.DOTALL
            )
            disc_match = re.search(
                r'#### Discussion\s*\n(.*?)(?=#### Sample Games|\Z)',
                mech_content, re.DOTALL
            )
            sample_match = re.search(
                r'#### Sample Games\s*\n(.*?)(?=---|\Z)',
                mech_content, re.DOTALL
            )

            description = clean_text(desc_match.group(1)) if desc_match else ""
            discussion = clean_text(disc_match.group(1)) if disc_match else ""
            sample_text = sample_match.group(1) if sample_match else ""

            sample_games = parse_sample_games(sample_text)
            related = extract_related_mechanisms(discussion, code)

            mechanisms.append({
                "code": code,
                "name": name,
                "description": description,
                "discussion": discussion,
                "relatedMechanisms": related,
                "sampleGames": sample_games
            })

        categories.append({
            "name": cat_name,
            "prefix": prefix or "UNK",
            "mechanisms": mechanisms
        })

    return categories


def main():
    print("Parsing Complete Encyclopedia of Mechanisms...")
    categories = parse_encyclopedia(ENCYCLOPEDIA_PATH)

    total_mechs = sum(len(cat["mechanisms"]) for cat in categories)
    total_games = sum(
        len(m["sampleGames"])
        for cat in categories
        for m in cat["mechanisms"]
    )
    total_related = sum(
        len(m["relatedMechanisms"])
        for cat in categories
        for m in cat["mechanisms"]
    )

    print(f"\nCategories: {len(categories)}")
    for cat in categories:
        mechs = cat["mechanisms"]
        print(f"  {cat['prefix']} — {cat['name']}: {len(mechs)} mechanisms")

    print(f"\nTotal mechanisms: {total_mechs}")
    print(f"Total sample games: {total_games}")
    print(f"Total cross-references: {total_related}")

    # Spot checks
    print("\n--- Spot checks ---")
    for cat in categories:
        for m in cat["mechanisms"]:
            if m["code"] in ("STR-02", "ACT-12", "CAR-05"):
                print(f"\n{m['code']} {m['name']}:")
                print(f"  Description: {m['description'][:80]}...")
                print(f"  Discussion: {len(m['discussion'])} chars")
                print(f"  Related: {m['relatedMechanisms']}")
                print(f"  Sample games: {len(m['sampleGames'])}")
                if m["sampleGames"]:
                    print(f"    First: {m['sampleGames'][0]}")
                    print(f"    Last:  {m['sampleGames'][-1]}")

    # Write output
    output = {"categories": categories}
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    import os
    size_kb = os.path.getsize(OUTPUT_PATH) / 1024
    print(f"\nSaved to {OUTPUT_PATH} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
