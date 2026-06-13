"""
fetch_bgg_awards.py
Build a structured board game awards database from Wikipedia tables, then
enrich each entry with BGG metadata (cover image, year published, etc.).

Why Wikipedia: BGG's awards page (/browse/boardgamehonor) is behind Cloudflare
bot protection, and the BGG XML API doesn't expose game-to-honor mappings.
Wikipedia keeps clean structured tables for every major award, accessible
via plain HTTP.

Output: data/bgg_awards.json
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from html import unescape
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = ROOT / "data" / "bgg_awards.json"
CACHE_PATH = ROOT / "data" / "bgg_awards_cache.json"

BGG_API_BASE = "https://boardgamegeek.com/xmlapi2"
DEFAULT_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15"
HTTP_TIMEOUT = 30

# Award programs to scrape. Each entry: (program_name, wikipedia_url).
# The parser per program may need to be customized via PARSERS below.
AWARD_PROGRAMS = [
    ("Spiel des Jahres",       "https://en.wikipedia.org/wiki/Spiel_des_Jahres"),
    ("Kennerspiel des Jahres", "https://en.wikipedia.org/wiki/Kennerspiel_des_Jahres"),
    ("Kinderspiel des Jahres", "https://en.wikipedia.org/wiki/Kinderspiel_des_Jahres"),
    ("Golden Geek Awards",     "https://en.wikipedia.org/wiki/Golden_Geek_Awards"),
    ("Origins Awards",         "https://en.wikipedia.org/wiki/Origins_Award"),
    ("As d'Or",                "https://en.wikipedia.org/wiki/As_d%27Or"),
]


# ─── Wikipedia table parsing ───────────────────────────────────────────────

TABLE_RE = re.compile(
    r'<table[^>]*class="[^"]*wikitable[^"]*"[^>]*>(.*?)</table>',
    re.DOTALL | re.IGNORECASE,
)
ROW_RE = re.compile(r'<tr[^>]*>(.*?)</tr>', re.DOTALL | re.IGNORECASE)
CELL_RE = re.compile(r'<t[hd][^>]*>(.*?)</t[hd]>', re.DOTALL | re.IGNORECASE)
TAG_RE = re.compile(r'<[^>]+>')
YEAR_RE = re.compile(r'\b((?:19|20)\d{2})\b')
WHITESPACE_RE = re.compile(r'\s+')
WIKI_LINK_RE = re.compile(r'<a[^>]*href="/wiki/([^"#]+)"[^>]*>([^<]+)</a>', re.IGNORECASE)
H_RE = re.compile(r'<h([23])[^>]*>(.*?)</h\1>', re.DOTALL | re.IGNORECASE)


def _clean_cell(text: str) -> str:
    """Strip HTML tags + decode entities + collapse whitespace."""
    # Replace <br> with separator first
    t = re.sub(r'<br\s*/?>', ' / ', text, flags=re.IGNORECASE)
    t = TAG_RE.sub("", t)
    t = unescape(t)
    t = WHITESPACE_RE.sub(" ", t).strip()
    # Strip footnote markers like [1], [a]
    t = re.sub(r'\s*\[[^\]]{1,4}\]', '', t)
    return t


def _find_year(text: str) -> int | None:
    m = YEAR_RE.search(text)
    return int(m.group(1)) if m else None


def _http_get(url: str) -> str | None:
    try:
        r = requests.get(url, headers={"User-Agent": DEFAULT_UA},
                         timeout=HTTP_TIMEOUT, allow_redirects=True)
    except requests.RequestException as e:
        print(f"  [http] {url}: {e}")
        return None
    if r.status_code != 200:
        print(f"  [http] {r.status_code} {url}")
        return None
    return r.text


def parse_wikipedia_award(program: str, url: str) -> list[dict]:
    """Generic Wikipedia award-table parser.

    Returns a flat list of entries:
        {"program": str, "year": int, "status": "Winner"|"Nominee"|"Recommended", "name": str}

    Strategy:
      1. Walk through the HTML keeping track of the most recent <h2>/<h3>
         heading (often a year or category context).
      2. For each <table class="wikitable">, parse its header row to find
         columns like 'Year', 'Game', 'Status'/'Category'/'Result'.
      3. For each data row, extract the game name + winner/nominee status.
    """
    print(f"\n=== {program} ===")
    html = _http_get(url)
    if not html:
        return []

    # Split into "sections" by h2/h3 to keep heading context as we walk tables
    entries: list[dict] = []
    chunks: list[tuple[str, str]] = []  # (heading_text, body_text)
    last_idx = 0
    last_heading = ""
    for m in H_RE.finditer(html):
        chunks.append((last_heading, html[last_idx:m.start()]))
        last_heading = _clean_cell(m.group(2))
        last_idx = m.end()
    chunks.append((last_heading, html[last_idx:]))

    # Names of OTHER programs in the SdJ family — skip their dedicated sections
    # when scraping a sibling page (each page tends to include summary tables
    # for all three).
    SDJ_FAMILY = ["Spiel des Jahres", "Kennerspiel des Jahres", "Kinderspiel des Jahres"]
    sibling_names = [s for s in SDJ_FAMILY if s != program and s.lower() != program.lower()]

    for heading, body in chunks:
        heading_year = _find_year(heading)
        # Skip sections whose heading explicitly belongs to a sibling award
        h_lower = heading.lower()
        if any(s.lower() in h_lower for s in sibling_names):
            continue
        for tm in TABLE_RE.finditer(body):
            table = tm.group(0)
            rows = ROW_RE.findall(table)
            if not rows:
                continue
            # Header row → column map
            header_cells = [_clean_cell(c).lower() for c in CELL_RE.findall(rows[0])]
            if not header_cells:
                continue

            def find_col(*names):
                for i, h in enumerate(header_cells):
                    for n in names:
                        if n in h:
                            return i
                return None

            game_col   = find_col("game", "title")
            year_col   = find_col("year")
            status_col = find_col("status", "result", "category", "outcome")

            # Fallback: first column is usually the game name
            if game_col is None and len(header_cells) >= 1:
                # Skip if first col is clearly a year
                if header_cells[0] not in {"year", "winner"}:
                    game_col = 0
                elif header_cells[0] == "winner":
                    # 'Winner' as a column header → row IS the winner
                    game_col = 0
                    status_col = -1  # synthetic: all rows are winners

            if game_col is None:
                continue

            for raw in rows[1:]:
                cells_raw = CELL_RE.findall(raw)
                if not cells_raw:
                    continue
                cells = [_clean_cell(c) for c in cells_raw]
                if game_col >= len(cells):
                    continue
                name = cells[game_col]
                if not name or len(name) < 2:
                    continue

                # Determine year
                if year_col is not None and year_col < len(cells):
                    yr = _find_year(cells[year_col])
                else:
                    yr = heading_year
                if not yr:
                    continue

                # Determine status
                if status_col == -1:
                    status = "Winner"
                elif status_col is not None and status_col < len(cells):
                    raw_status = cells[status_col].lower()
                    if "won" in raw_status or "winner" in raw_status:
                        status = "Winner"
                    elif "recommend" in raw_status:
                        status = "Recommended"
                    elif "nominat" in raw_status:
                        status = "Nominee"
                    else:
                        # Use as-is if non-empty and short
                        status = cells[status_col][:40] or "Nominee"
                else:
                    status = "Nominee"

                # If first column is the winner column with a year header,
                # the heading tells us the year.
                entries.append({
                    "program": program,
                    "year": yr,
                    "status": status,
                    "name": name,
                })

    # Dedup by (program, year, name) — prefer "Winner" status if duplicated
    by_key: dict[tuple, dict] = {}
    rank = {"Winner": 0, "Recommended": 1, "Nominee": 2}
    for e in entries:
        key = (e["program"], e["year"], e["name"].lower())
        cur = by_key.get(key)
        if cur is None or rank.get(e["status"], 9) < rank.get(cur["status"], 9):
            by_key[key] = e
    deduped = list(by_key.values())
    print(f"  {len(deduped)} entries (years {min((e['year'] for e in deduped), default='—')}"
          f"–{max((e['year'] for e in deduped), default='—')})")
    return deduped


# ─── BGG ID resolution + enrichment ────────────────────────────────────────

class BGGClient:
    def __init__(self, token: str, cache: dict):
        self.token = token
        self.cache = cache  # mutable shared cache
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": DEFAULT_UA,
            "Accept": "application/xml",
            "Authorization": f"Bearer {token}",
        })

    def _get(self, url: str, params: dict, backoff_429: int = 15) -> ET.Element | None:
        for attempt in range(3):
            try:
                r = self.session.get(url, params=params, timeout=HTTP_TIMEOUT)
            except requests.RequestException as e:
                print(f"    [bgg] {e}")
                return None
            if r.status_code == 200:
                try:
                    return ET.fromstring(r.text)
                except ET.ParseError as e:
                    print(f"    [bgg] parse: {e}")
                    return None
            if r.status_code == 202:
                time.sleep(5)
                continue
            if r.status_code == 429:
                wait = min(int(r.headers.get("Retry-After", backoff_429) or backoff_429), 60)
                print(f"    [bgg] HTTP 429 — sleep {wait}s (attempt {attempt + 1}/3)")
                time.sleep(wait)
                backoff_429 = min(backoff_429 * 2, 60)
                continue
            print(f"    [bgg] HTTP {r.status_code} ({params})")
            return None
        return None

    def resolve(self, name: str) -> int | None:
        """name → bgg_id, cached."""
        key = f"resolve:{name.lower()}"
        if key in self.cache:
            return self.cache[key]
        root = self._get(f"{BGG_API_BASE}/search",
                         {"type": "boardgame", "exact": "1", "query": name})
        if root is None:
            return None
        cands = [(int(i.get("id")), (i.find("name").get("value") or ""))
                 for i in root.findall("item")
                 if i.get("type") == "boardgame" and i.get("id")]
        if not cands:
            # Fuzzy fallback
            time.sleep(0.3)
            root = self._get(f"{BGG_API_BASE}/search",
                             {"type": "boardgame", "query": name})
            if root is None:
                self.cache[key] = None
                return None
            cands = [(int(i.get("id")), (i.find("name").get("value") or ""))
                     for i in root.findall("item")
                     if i.get("type") == "boardgame" and i.get("id")]
        if not cands:
            self.cache[key] = None
            return None
        bid = cands[0][0]
        self.cache[key] = bid
        time.sleep(0.3)
        return bid

    def fetch_things(self, ids: list[int]) -> dict[int, dict]:
        """Batch /thing fetch — cover, year, rating."""
        out: dict[int, dict] = {}
        # Check cache first
        to_fetch = [i for i in ids if f"thing:{i}" not in self.cache]
        for i in ids:
            cached = self.cache.get(f"thing:{i}")
            if cached:
                out[i] = cached

        # Batches of 20
        for start in range(0, len(to_fetch), 20):
            batch = to_fetch[start:start + 20]
            root = self._get(f"{BGG_API_BASE}/thing",
                             {"id": ",".join(str(b) for b in batch), "stats": "1"})
            if root is None:
                continue
            for item in root.findall("item"):
                bid = int(item.get("id", 0))
                primary = next(
                    (n.get("value") for n in item.findall("name") if n.get("type") == "primary"),
                    None
                )
                if primary is None:
                    n0 = item.find("name")
                    primary = n0.get("value") if n0 is not None else ""
                img_el = item.find("image")
                thumb_el = item.find("thumbnail")
                yp_el = item.find("yearpublished")
                stats = item.find("statistics")
                bayes_rating = None
                if stats is not None:
                    r = stats.find("ratings")
                    if r is not None:
                        b = r.find("bayesaverage")
                        if b is not None and b.get("value"):
                            try:
                                bayes_rating = round(float(b.get("value")), 3)
                            except ValueError:
                                pass
                data = {
                    "bgg_id": bid,
                    "name": primary,
                    "image": img_el.text if img_el is not None else None,
                    "thumbnail": thumb_el.text if thumb_el is not None else None,
                    "year": int(yp_el.get("value")) if yp_el is not None and yp_el.get("value", "").lstrip("-").isdigit() else None,
                    "bayes_rating": bayes_rating,
                }
                self.cache[f"thing:{bid}"] = data
                out[bid] = data
            time.sleep(1.0)
        return out


# ─── Main pipeline ──────────────────────────────────────────────────────────

def load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-year", type=int, default=2015,
                    help="Drop awards before this year (default 2015)")
    ap.add_argument("--programs", nargs="*",
                    help="Only scrape these programs (substring match on name)")
    args = ap.parse_args()

    load_env()
    token = os.environ.get("BGG_BEARER_TOKEN")
    if not token:
        print("ERROR: BGG_BEARER_TOKEN not set")
        return 2

    # Cache survives across runs to avoid re-hitting BGG for known games.
    cache: dict = {}
    if CACHE_PATH.exists():
        try:
            cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            cache = {}

    client = BGGClient(token, cache)

    # 1. Scrape Wikipedia for all entries
    programs_to_scrape = AWARD_PROGRAMS
    if args.programs:
        programs_to_scrape = [(p, u) for p, u in AWARD_PROGRAMS
                              if any(s.lower() in p.lower() for s in args.programs)]
    all_entries: list[dict] = []
    for program, url in programs_to_scrape:
        all_entries.extend(parse_wikipedia_award(program, url))
    if args.min_year:
        before = len(all_entries)
        all_entries = [e for e in all_entries if e["year"] >= args.min_year]
        print(f"\nFiltered to year >= {args.min_year}: {len(all_entries)}/{before}")

    # 2. Resolve unique game names to BGG IDs
    print(f"\n[Resolve] {len(set(e['name'] for e in all_entries))} unique games")
    name_to_bgg = {}
    unique_names = sorted(set(e["name"] for e in all_entries))
    for i, name in enumerate(unique_names):
        bid = client.resolve(name)
        name_to_bgg[name] = bid
        if i % 25 == 0:
            print(f"  {i}/{len(unique_names)} resolved (cache size {len(cache)})")
    resolved_count = sum(1 for v in name_to_bgg.values() if v)
    print(f"  Total: {resolved_count}/{len(unique_names)}")

    # 3. Fetch /thing details for resolved games
    resolved_ids = sorted({v for v in name_to_bgg.values() if v})
    print(f"\n[Enrich] /thing for {len(resolved_ids)} games")
    things = client.fetch_things(resolved_ids)

    # 4. Restructure for the dashboard
    # Shape: list of award entries grouped by program & year, with winner + nominees
    grouped: dict[tuple[str, int], dict] = {}
    for e in all_entries:
        key = (e["program"], e["year"])
        bid = name_to_bgg.get(e["name"])
        game_data = things.get(bid) if bid else None
        entry = {
            "bgg_id": bid,
            "name": e["name"],
            "image": game_data.get("image") if game_data else None,
            "thumbnail": game_data.get("thumbnail") if game_data else None,
            "year_published": game_data.get("year") if game_data else None,
            "bayes_rating": game_data.get("bayes_rating") if game_data else None,
        }
        group = grouped.setdefault(key, {
            "program": e["program"], "year": e["year"],
            "winners": [], "nominees": [], "recommended": [],
        })
        if e["status"] == "Winner":
            group["winners"].append(entry)
        elif e["status"] == "Recommended":
            group["recommended"].append(entry)
        else:
            group["nominees"].append(entry)

    awards = sorted(grouped.values(), key=lambda g: (g["program"], -g["year"]))

    import datetime as dt
    output = {
        "metadata": {
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "programs": [p for p, _ in AWARD_PROGRAMS],
            "total_awards": len(awards),
            "total_entries": len(all_entries),
            "resolved_games": resolved_count,
        },
        "awards": awards,
    }

    OUTPUT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False),
                           encoding="utf-8")
    CACHE_PATH.write_text(json.dumps(cache, indent=2, ensure_ascii=False),
                          encoding="utf-8")
    print(f"\nWrote {OUTPUT_PATH}")
    print(f"Wrote {CACHE_PATH}  ({len(cache)} cached entries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
