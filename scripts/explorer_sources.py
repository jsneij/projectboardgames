"""
explorer_sources.py
Discovery sources for the Explorer tab. Each function returns a list of
candidates with the shape:

    {"bgg_id": int | None, "name": str | None, "source": str, "raw_title": str | None}

If bgg_id is None, the caller (explorer_resolve) will try to look it up via
BGG search. Each source is fail-soft: any exception logs a warning and the
function returns []. The pipeline never crashes on a single bad source.

BGG endpoints REQUIRE a Bearer token (BGG changed this — even public endpoints
like /hot return 401 without one). Pass token=… into the BGG functions.
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from typing import Any

import requests

BGG_API_BASE = "https://boardgamegeek.com/xmlapi2"
# Use a fully-browser-shaped UA. Some sources (Solitaire Times via Cloudflare,
# others behind WAF) reject UAs containing "bot" / "explorer" / "crawler" etc.
DEFAULT_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15"
HTTP_TIMEOUT = 30
MAX_RETRIES = 5
RETRY_DELAY_SECONDS = 5

# Atom feeds use a namespace. RSS 2.0 doesn't.
ATOM_NS = "{http://www.w3.org/2005/Atom}"


def _get(
    url: str,
    params: dict | None = None,
    bearer_token: str | None = None,
    accept: str = "application/xml",
    ua: str = DEFAULT_UA,
) -> requests.Response | None:
    """GET with retry + redirect following + BGG's 202-queued protocol."""
    headers = {"User-Agent": ua, "Accept": accept}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    for _ in range(MAX_RETRIES):
        try:
            resp = requests.get(
                url, params=params, headers=headers,
                timeout=HTTP_TIMEOUT, allow_redirects=True,
            )
        except requests.RequestException as e:
            print(f"  [explorer_sources] network error on {url}: {e}")
            return None
        if resp.status_code == 200:
            return resp
        if resp.status_code == 202:
            time.sleep(RETRY_DELAY_SECONDS)
            continue
        print(f"  [explorer_sources] HTTP {resp.status_code} from {url}")
        return None
    return None


# ─── BGG sources ────────────────────────────────────────────────────────────

def bgg_hot(bearer_token: str) -> list[dict[str, Any]]:
    """BGG hot list (top trending board games)."""
    try:
        resp = _get(f"{BGG_API_BASE}/hot", {"type": "boardgame"}, bearer_token=bearer_token)
        if resp is None:
            return []
        root = ET.fromstring(resp.text)
        out = []
        for item in root.findall("item"):
            bgg_id = item.get("id")
            name_el = item.find("name")
            if not bgg_id or name_el is None:
                continue
            out.append({
                "bgg_id": int(bgg_id),
                "name": name_el.get("value"),
                "source": "bgg_hot",
                "raw_title": None,
            })
        return out
    except Exception as e:
        print(f"  [bgg_hot] failed: {e}")
        return []


def bgg_geeklist(geeklist_id: int, bearer_token: str) -> list[dict[str, Any]]:
    """One BGG GeekList. Items with objecttype="thing" only (skip comments)."""
    try:
        resp = _get(f"{BGG_API_BASE}/geeklist/{geeklist_id}", bearer_token=bearer_token)
        if resp is None:
            return []
        root = ET.fromstring(resp.text)
        out = []
        for item in root.findall("item"):
            if item.get("objecttype") != "thing":
                continue
            obj_id = item.get("objectid")
            obj_name = item.get("objectname")
            if not obj_id:
                continue
            out.append({
                "bgg_id": int(obj_id),
                "name": obj_name,
                "source": f"bgg_geeklist:{geeklist_id}",
                "raw_title": None,
            })
        return out
    except Exception as e:
        print(f"  [bgg_geeklist:{geeklist_id}] failed: {e}")
        return []


def bgg_geeklists(geeklist_ids: list[int], bearer_token: str) -> list[dict[str, Any]]:
    """Fan-out across a list of GeekList IDs with small rate limit between calls."""
    out: list[dict[str, Any]] = []
    for i, gid in enumerate(geeklist_ids):
        out.extend(bgg_geeklist(gid, bearer_token))
        if i + 1 < len(geeklist_ids):
            time.sleep(1.0)
    return out


# ─── External (non-BGG) sources ─────────────────────────────────────────────

def _parse_feed_titles(xml_text: str) -> list[str]:
    """Pull item titles from either RSS 2.0 or Atom feeds."""
    titles: list[str] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"  [feed] XML parse failed: {e}")
        return []

    # RSS 2.0: <rss><channel><item><title>...</title></item></channel></rss>
    for item in root.iter("item"):
        title_el = item.find("title")
        if title_el is not None and title_el.text:
            titles.append(title_el.text.strip())

    # Atom: <feed><entry><title>...</title></entry></feed>
    if not titles:
        for entry in root.iter(f"{ATOM_NS}entry"):
            title_el = entry.find(f"{ATOM_NS}title")
            if title_el is not None and title_el.text:
                titles.append(title_el.text.strip())

    return titles


def _fetch_feed_titles(url: str) -> list[str]:
    resp = _get(url, accept="application/rss+xml, application/atom+xml, application/xml")
    if resp is None:
        return []
    return _parse_feed_titles(resp.text)


def _extract_game_names_from_titles(titles: list[str]) -> list[str]:
    """Best-effort heuristic to recover game names from review-post titles."""
    names = []
    SUFFIX_RE = re.compile(r"\s*[-—:|]\s*", re.UNICODE)
    NOISE_PREFIXES = re.compile(
        r"^(review|first impressions?|solo spotlight|spotlight|playthrough|"
        r"impressions?|preview|let'?s play|now playing|on the table|"
        r"unboxing|spoilers?|gameplay|video)\s*[-—:|]?\s*",
        re.IGNORECASE,
    )
    NOISE_SUFFIXES = re.compile(
        r"\s*[-—:|]\s*(review|impressions?|playthrough|preview|first impressions?|"
        r"unboxing|gameplay|video)\s*$",
        re.IGNORECASE,
    )
    for t in titles:
        cleaned = NOISE_PREFIXES.sub("", t)
        cleaned = NOISE_SUFFIXES.sub("", cleaned)
        parts = SUFFIX_RE.split(cleaned)
        candidate = parts[0].strip() if parts else cleaned.strip()
        if len(candidate) < 3 or candidate.lower() in {"news", "blog", "podcast", "video"}:
            continue
        candidate = candidate.strip("'\"")
        names.append(candidate)
    seen = set()
    deduped = []
    for n in names:
        key = n.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(n)
    return deduped


_PC_EPISODE_RE = re.compile(
    r"^(?:polyhedron collider\s+)?episode\s+\d+\s*[-–—:]\s*(.+)$",
    re.IGNORECASE,
)
# Phrases that indicate a non-list episode title (expo previews, mail bag, etc.)
_PC_SKIP_PATTERNS = re.compile(
    r"\b(uk games expo|gen ?con|essen|preview(?!s)|recap|mail bag|"
    r"questions from|best of|kickstarter (round|update)|news|"
    r"interview)\b",
    re.IGNORECASE,
)


def _extract_pc_games_from_episode_title(title: str) -> list[str]:
    """Polyhedron Collider episode titles list 4-6 games after the dash."""
    m = _PC_EPISODE_RE.match(title.strip())
    if not m:
        return []
    rest = m.group(1).strip()
    if _PC_SKIP_PATTERNS.search(rest):
        return []
    # HTML entities show up in feeds (Ada&#39;s Dream); cheap decode.
    rest = rest.replace("&#39;", "'").replace("&amp;", "&")
    # Replace " and " with "," to unify split; trailing "and" before last item.
    rest = re.sub(r"\s+and\s+", ", ", rest, flags=re.IGNORECASE)
    parts = [p.strip(" ,") for p in rest.split(",")]
    parts = [p for p in parts if len(p) >= 3]
    return parts


def polyhedron_collider() -> list[dict[str, Any]]:
    """Polyhedron Collider — Blogger podcast. Each episode title is a CSV of
    games covered. We parse those CSVs into per-game candidates."""
    titles = _fetch_feed_titles("https://www.polyhedroncollider.com/feeds/posts/default?alt=rss")
    out = []
    seen = set()
    for t in titles:
        for name in _extract_pc_games_from_episode_title(t):
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "bgg_id": None, "name": name, "source": "polyhedron_collider",
                "raw_title": t,
            })
    return out


def solitaire_times() -> list[dict[str, Any]]:
    """Solitaire Times — solo-focused newsletter, WordPress RSS feed."""
    titles = _fetch_feed_titles("https://solitairetimes.com/feed/")
    names = _extract_game_names_from_titles(titles)
    return [
        {"bgg_id": None, "name": n, "source": "solitaire_times",
         "raw_title": titles[i] if i < len(titles) else None}
        for i, n in enumerate(names)
    ]


# ─── Custom URLs (user-defined) ────────────────────────────────────────────

_FEED_AUTODISCOVERY_RE = re.compile(
    r'<link[^>]+rel=["\']alternate["\'][^>]*type=["\']application/'
    r'(?:rss\+xml|atom\+xml|xml)["\'][^>]*>',
    re.IGNORECASE,
)
_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
_HEADING_RE = re.compile(
    r'<h[1-3][^>]*>(?:<a[^>]*>)?\s*([^<]{4,200}?)\s*(?:</a>)?</h[1-3]>',
    re.IGNORECASE,
)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")


def _absolutize(base: str, href: str) -> str:
    """Resolve a possibly-relative URL against base."""
    if href.startswith(("http://", "https://")):
        return href
    if href.startswith("//"):
        scheme = "https" if base.startswith("https") else "http"
        return f"{scheme}:{href}"
    try:
        from urllib.parse import urljoin
        return urljoin(base, href)
    except Exception:
        return href


def _autodiscover_feed_url(html_text: str, base_url: str) -> str | None:
    m = _FEED_AUTODISCOVERY_RE.search(html_text)
    if not m:
        return None
    href = _HREF_RE.search(m.group(0))
    if not href:
        return None
    return _absolutize(base_url, href.group(1))


def _scrape_html_headings(html_text: str) -> list[str]:
    out = []
    for m in _HEADING_RE.finditer(html_text):
        text = _TAG_STRIP_RE.sub("", m.group(1)).strip()
        # cheap entity decode for the common ones
        text = (text.replace("&amp;", "&").replace("&#39;", "'")
                    .replace("&quot;", '"').replace("&nbsp;", " "))
        if text and len(text) >= 4:
            out.append(text)
    # Dedup while preserving order
    seen, deduped = set(), []
    for t in out:
        if t.lower() in seen:
            continue
        seen.add(t.lower())
        deduped.append(t)
    return deduped


def _host_label(url: str) -> str:
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        return host or url
    except Exception:
        return url


def custom_url(url: str) -> list[dict[str, Any]]:
    """Scrape one user-provided URL. Tries feed → autodiscovery → HTML headings."""
    if not url or not url.startswith(("http://", "https://")):
        return []

    # 1. Try direct feed parse
    titles = _fetch_feed_titles(url)

    # 2. If no titles, fetch as HTML and look for autodiscovery link
    if not titles:
        try:
            resp = _get(url, accept="text/html, application/rss+xml, */*")
            if resp is not None:
                feed_url = _autodiscover_feed_url(resp.text, url)
                if feed_url:
                    titles = _fetch_feed_titles(feed_url)
                if not titles:
                    # 3. Last-resort: scrape <h1>/<h2>/<h3> headings
                    titles = _scrape_html_headings(resp.text)
        except Exception as e:
            print(f"  [custom_url] {url}: {e}")
            return []

    if not titles:
        return []

    names = _extract_game_names_from_titles(titles)
    host = _host_label(url)
    label = f"custom:{host}"
    return [
        {"bgg_id": None, "name": n, "source": label,
         "raw_title": titles[i] if i < len(titles) else None}
        for i, n in enumerate(names)
    ]


def custom_urls(urls: list[str]) -> list[dict[str, Any]]:
    """Run custom_url on each user-provided URL with a small politeness gap."""
    out: list[dict[str, Any]] = []
    for i, url in enumerate(urls or []):
        out.extend(custom_url(url))
        if i + 1 < len(urls):
            time.sleep(0.5)
    return out


# NOTE: r/soloboardgaming top-of-year RSS was investigated as a source but its
# titles are dominated by discussion posts, memes, photos of collections, and
# "what did you play this week" threads — game-name signal is too low to be
# useful and would dilute the candidate pool. The 1 Player Guild's curated
# BGG GeekLists capture community-favorite solo games with far higher S/N.
# If you want Reddit back, the OAuth flow is documented at
# https://www.reddit.com/dev/api/oauth — uncomment a function here that
# follows that pattern.


# ─── Self-test ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import os
    from pathlib import Path

    # Load .env so we have BGG_BEARER_TOKEN
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())

    token = os.environ.get("BGG_BEARER_TOKEN", "")
    if not token:
        print("WARNING: BGG_BEARER_TOKEN not set — BGG calls will 401")

    print("== bgg_hot ==")
    hot = bgg_hot(token)
    print(f"  {len(hot)} candidates")
    for c in hot[:5]:
        print(f"    {c['bgg_id']:>7} {c['name']}")

    print("\n== polyhedron_collider ==")
    pc = polyhedron_collider()
    print(f"  {len(pc)} candidates")
    for c in pc[:5]:
        print(f"    {c['name']!r:50} ← {c['raw_title']!r}")

    print("\n== solitaire_times ==")
    st = solitaire_times()
    print(f"  {len(st)} candidates")
    for c in st[:5]:
        print(f"    {c['name']!r:50} ← {c['raw_title']!r}")

    # Reddit dropped — see note above for rationale.
