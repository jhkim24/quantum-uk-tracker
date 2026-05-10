"""
UK Quantum Internship Tracker - Discovery

Performs broad searches for UK undergraduate quantum-computing internships and
flags candidate URLs not already in the programme registry for manual review.

Uses DuckDuckGo's HTML endpoint (no API key required). Results are written to
data/discoveries.json. Candidates are NEVER auto-added to the registry —
the maintainer reviews them and edits programmes.json by hand.

Run from repo root:
    python scraper/discover.py
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

import httpx
from bs4 import BeautifulSoup

REPO_ROOT = Path(__file__).resolve().parent.parent
PROGRAMMES_PATH = REPO_ROOT / "data" / "programmes.json"
DISCOVERIES_PATH = REPO_ROOT / "data" / "discoveries.json"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Search queries — short and specific. Each one returns ~10-30 results.
QUERIES = [
    "UK quantum computing undergraduate summer internship 2027",
    "UK quantum technology undergraduate research placement",
    "quantum computing summer placement undergraduate UK",
    "UK quantum hardware internship undergraduate",
    "quantum information vacation bursary UK undergraduate",
]

# Domains we trust to be relevant signals.
RELEVANT_DOMAIN_HINTS = [
    ".ac.uk",
    "nqcc.ac.uk",
    "stfc",
    "ukri.org",
    "epsrc",
    "riverlane",
    "phasecraft",
    "quantinuum",
    "oxinst",
    "pqshield",
    "quantum-dice",
    "quantummotion",
    "oxfordquantumcircuits",
    "npl.co.uk",
    "qworld.net",
]

# Domains we never want to surface (job aggregators, low-signal mirrors).
DOMAIN_BLOCKLIST = [
    "indeed.com",
    "glassdoor",
    "linkedin.com",
    "simplyhired",
    "lensa",
    "prosple",
    "jobs.ac.uk",
    "totaljobs",
    "monster",
    "reed.co.uk",
    "jooble",
    "jobsora",
    "trovit",
    "ukscholarships.uk",
    "scholarshipdb",
    "youtube.com",
    "reddit.com",
    "twitter.com",
    "x.com",
    "facebook.com",
]

# Keywords required somewhere in the result snippet.
RELEVANCE_KEYWORDS = ["quantum", "qubit", "QC"]

REQUEST_DELAY = 3.0
REQUEST_TIMEOUT = 25.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("discover")


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Could not read %s: %s", path, exc)
        return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def normalise_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def is_blocked(url: str) -> bool:
    domain = normalise_domain(url)
    return any(b in domain for b in DOMAIN_BLOCKLIST)


def looks_relevant(title: str, snippet: str) -> bool:
    text = f"{title} {snippet}".lower()
    if not any(kw.lower() in text for kw in RELEVANCE_KEYWORDS):
        return False
    # Must mention internship/placement/bursary/UROP/research
    role_terms = ["internship", "placement", "bursary", "urop", "vacation", "summer research"]
    return any(t in text for t in role_terms)


def is_uk_signal(url: str, title: str, snippet: str) -> bool:
    """Heuristic for UK-relevance. Conservative: prefer false negatives."""
    domain = normalise_domain(url)
    text = f"{title} {snippet}".lower()
    if domain.endswith(".ac.uk") or domain.endswith(".uk") or domain.endswith(".gov.uk"):
        return True
    if any(h in domain for h in RELEVANT_DOMAIN_HINTS):
        return True
    uk_terms = [" uk ", "united kingdom", "britain", "england", "scotland", "wales",
                "oxford", "cambridge", "london", "bristol", "manchester", "edinburgh",
                "harwell", "imperial college"]
    return any(t in text for t in uk_terms)


def search_duckduckgo(client: httpx.Client, query: str) -> list[dict]:
    """Search DuckDuckGo HTML endpoint. Returns list of {title, url, snippet}."""
    log.info("Search: %r", query)
    try:
        response = client.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "en-GB,en;q=0.9",
            },
        )
    except httpx.HTTPError as exc:
        log.error("  search failed: %s", exc)
        return []

    if response.status_code != 200:
        log.warning("  HTTP %d", response.status_code)
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    out = []
    for result in soup.select("div.result")[:30]:
        title_el = result.select_one("a.result__a")
        snippet_el = result.select_one("a.result__snippet, div.result__snippet")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        href = title_el.get("href", "")
        snippet = snippet_el.get_text(strip=True) if snippet_el else ""
        # DuckDuckGo wraps URLs with a redirect — extract the real one.
        real_url = unwrap_ddg_url(href)
        if real_url:
            out.append({"title": title, "url": real_url, "snippet": snippet})
    log.info("  parsed %d results", len(out))
    return out


def unwrap_ddg_url(href: str) -> str | None:
    """DuckDuckGo HTML results use //duckduckgo.com/l/?uddg=<encoded url>."""
    if not href:
        return None
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        qs = parse_qs(parsed.query)
        if "uddg" in qs:
            return unquote(qs["uddg"][0])
    if href.startswith(("http://", "https://")):
        return href
    return None


def run() -> int:
    programmes = load_json(PROGRAMMES_PATH, default=[])
    known_domains = {normalise_domain(p["url"]) for p in programmes}
    known_urls = {p["url"].rstrip("/") for p in programmes}
    log.info("Registry has %d programmes across %d domains", len(programmes), len(known_domains))

    discoveries: dict[str, dict] = {}

    with httpx.Client(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
        for i, query in enumerate(QUERIES):
            results = search_duckduckgo(client, query)
            for r in results:
                url = r["url"]
                if not url.startswith(("http://", "https://")):
                    continue
                if is_blocked(url):
                    continue
                domain = normalise_domain(url)
                if domain in known_domains:
                    continue
                if url.rstrip("/") in known_urls:
                    continue
                if not looks_relevant(r["title"], r["snippet"]):
                    continue
                if not is_uk_signal(url, r["title"], r["snippet"]):
                    continue

                # Dedupe; keep first match
                key = url.rstrip("/")
                if key in discoveries:
                    discoveries[key]["matched_queries"].append(query)
                    continue
                discoveries[key] = {
                    "url": url,
                    "title": r["title"],
                    "snippet": r["snippet"],
                    "domain": domain,
                    "matched_queries": [query],
                    "first_seen": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }

            if i < len(QUERIES) - 1:
                time.sleep(REQUEST_DELAY)

    # Merge with existing discoveries (preserving first_seen for already-seen items)
    existing = load_json(DISCOVERIES_PATH, default={"items": []})
    existing_by_key = {item["url"].rstrip("/"): item for item in existing.get("items", [])}

    final_items = []
    for key, new_item in discoveries.items():
        if key in existing_by_key:
            old = existing_by_key[key]
            new_item["first_seen"] = old.get("first_seen", new_item["first_seen"])
            new_item["seen_count"] = old.get("seen_count", 0) + 1
        else:
            new_item["seen_count"] = 1
        final_items.append(new_item)

    # Also retain previously-seen items not surfaced this run, with a bumped timestamp.
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for key, old in existing_by_key.items():
        if key not in discoveries:
            old["last_missing"] = now
            final_items.append(old)

    # Sort by seen_count descending, then alphabetically
    final_items.sort(key=lambda x: (-x.get("seen_count", 0), x["domain"]))

    save_json(
        DISCOVERIES_PATH,
        {
            "generated_at": now,
            "count": len(final_items),
            "items": final_items,
        },
    )

    log.info("Wrote %s (%d candidates)", DISCOVERIES_PATH, len(final_items))
    new_count = sum(1 for item in final_items if item.get("seen_count", 0) == 1 and "last_missing" not in item)
    log.info("New candidates this run: %d", new_count)
    return 0


if __name__ == "__main__":
    sys.exit(run())
