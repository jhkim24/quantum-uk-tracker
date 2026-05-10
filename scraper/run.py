"""
UK Quantum Internship Tracker - Scraper

Fetches each tracked programme page, classifies its current status (open/closed/unknown),
detects content changes since the last run, and writes results to data/status.json
which the static site reads.

Run from repo root:
    python scraper/run.py
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
PROGRAMMES_PATH = REPO_ROOT / "data" / "programmes.json"
STATUS_PATH = REPO_ROOT / "data" / "status.json"
HISTORY_PATH = REPO_ROOT / "data" / "history.json"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
)

REQUEST_TIMEOUT = 30.0
REQUEST_DELAY = 2.0  # seconds between requests, to be polite

# ---------------------------------------------------------------------------
# Status classification patterns
# ---------------------------------------------------------------------------

# Match in order: more specific patterns first.
CLOSED_PATTERNS = [
    re.compile(r"applications?\s+(?:are\s+now\s+|have\s+now\s+)?closed", re.I),
    re.compile(r"applications?\s+for\s+.{1,80}?\s+are\s+(?:now\s+)?closed", re.I),
    re.compile(r"closed\s+to\s+applications", re.I),
    re.compile(r"not\s+(?:currently\s+)?accepting\s+applications", re.I),
    re.compile(r"recruitment\s+(?:is\s+)?closed", re.I),
    re.compile(r"applications?\s+(?:have\s+)?closed", re.I),
    re.compile(r"this\s+(?:vacancy|role|position)\s+is\s+closed", re.I),
    re.compile(r"are\s+now\s+closed", re.I),
]

OPEN_PATTERNS = [
    re.compile(r"applications?\s+(?:are\s+now\s+)?open", re.I),
    re.compile(r"now\s+accepting\s+applications", re.I),
    re.compile(r"apply\s+(?:now|here|today)", re.I),
    re.compile(r"applications?\s+open\s+(?:on|from)", re.I),
    re.compile(r"closing\s+date[:\s]", re.I),
    re.compile(r"deadline[:\s]", re.I),
]

DEADLINE_PATTERNS = [
    re.compile(
        r"(?:closing\s+date|deadline|applications?\s+close)"
        r"[:\s]+(?:is\s+)?"
        r"(\d{1,2}(?:st|nd|rd|th)?\s+(?:January|February|March|April|May|June|"
        r"July|August|September|October|November|December)\s+\d{4})",
        re.I,
    ),
    re.compile(
        r"(?:closing\s+date|deadline|applications?\s+close)"
        r"[:\s]+(?:is\s+)?"
        r"((?:January|February|March|April|May|June|"
        r"July|August|September|October|November|December)\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4})",
        re.I,
    ),
    re.compile(r"deadline[:\s]+(\d{1,2}/\d{1,2}/\d{2,4})", re.I),
]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scraper")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class FetchResult:
    """Result of fetching and analysing a single programme page."""

    id: str
    url: str
    fetched_at: str
    http_status: int | None
    error: str | None
    status: str  # "open" | "closed" | "unknown" | "error"
    deadline_text: str | None
    content_hash: str | None
    snippet: str | None  # short excerpt around the most relevant match
    changed: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Could not read %s: %s", path, exc)
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def extract_main_text(html: str, selector_hint: str = "main") -> str:
    """Extract the most relevant text region from the page.

    Strategy: try the selector hint, fall back to <main>, then <body>.
    Strip script/style. Collapse whitespace.
    """
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    candidates = []
    if selector_hint:
        candidates.extend(soup.select(selector_hint))
    candidates.extend(soup.find_all("main"))
    if soup.body:
        candidates.append(soup.body)

    for cand in candidates:
        text = cand.get_text(separator=" ", strip=True)
        if len(text) > 200:  # minimum to be considered useful
            return re.sub(r"\s+", " ", text)

    # Fallback: whole document
    text = soup.get_text(separator=" ", strip=True)
    return re.sub(r"\s+", " ", text)


def classify_status(text: str) -> tuple[str, str | None, str | None]:
    """Classify status of the programme based on extracted page text.

    Returns: (status, deadline_text, snippet)
    Closed signals dominate over open ones. If neither, status = "unknown".
    """
    for pat in CLOSED_PATTERNS:
        m = pat.search(text)
        if m:
            return "closed", None, _surrounding(text, m.start(), m.end())

    deadline = None
    for pat in DEADLINE_PATTERNS:
        m = pat.search(text)
        if m:
            deadline = m.group(1)
            break

    for pat in OPEN_PATTERNS:
        m = pat.search(text)
        if m:
            return "open", deadline, _surrounding(text, m.start(), m.end())

    return "unknown", deadline, None


def _surrounding(text: str, start: int, end: int, window: int = 140) -> str:
    """Return a short text snippet around a match, with ellipses."""
    a = max(0, start - window)
    b = min(len(text), end + window)
    snippet = text[a:b].strip()
    if a > 0:
        snippet = "..." + snippet
    if b < len(text):
        snippet = snippet + "..."
    return snippet


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def fetch_programme(
    client: httpx.Client,
    programme: dict,
    previous: dict | None,
) -> FetchResult:
    """Fetch one programme page and produce a FetchResult."""
    pid = programme["id"]
    url = programme["url"]
    selector_hint = programme.get("selector_hint", "main")

    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    log.info("→ %s  [%s]", pid, url)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    }

    response = None
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            response = client.get(url, headers=headers, follow_redirects=True)
            # Retry on transient 5xx
            if response.status_code >= 500 and attempt == 0:
                log.warning("  HTTP %d on attempt %d — retrying", response.status_code, attempt + 1)
                time.sleep(3)
                continue
            break
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt == 0:
                log.warning("  fetch error on attempt %d (%s) — retrying", attempt + 1, exc)
                time.sleep(3)
                continue

    if response is None:
        log.error("  fetch failed after retries: %s", last_exc)
        return FetchResult(
            id=pid,
            url=url,
            fetched_at=fetched_at,
            http_status=None,
            error=f"{type(last_exc).__name__}: {last_exc}" if last_exc else "unknown fetch error",
            status="error",
            deadline_text=None,
            content_hash=None,
            snippet=None,
            changed=False,
        )

    if response.status_code >= 400:
        log.warning("  HTTP %d", response.status_code)
        return FetchResult(
            id=pid,
            url=url,
            fetched_at=fetched_at,
            http_status=response.status_code,
            error=f"HTTP {response.status_code}",
            status="error",
            deadline_text=None,
            content_hash=None,
            snippet=None,
            changed=False,
        )

    text = extract_main_text(response.text, selector_hint=selector_hint)
    if not text:
        log.warning("  no extractable text")
        return FetchResult(
            id=pid,
            url=url,
            fetched_at=fetched_at,
            http_status=response.status_code,
            error="empty page text",
            status="unknown",
            deadline_text=None,
            content_hash=None,
            snippet=None,
            changed=False,
        )

    content_hash = hash_text(text)
    status, deadline, snippet = classify_status(text)

    changed = (
        previous is not None
        and previous.get("content_hash") is not None
        and previous["content_hash"] != content_hash
    )

    log.info(
        "  status=%s  hash=%s  changed=%s",
        status,
        content_hash,
        changed,
    )

    return FetchResult(
        id=pid,
        url=url,
        fetched_at=fetched_at,
        http_status=response.status_code,
        error=None,
        status=status,
        deadline_text=deadline,
        content_hash=content_hash,
        snippet=snippet,
        changed=changed,
    )


def run() -> int:
    log.info("Loading programme registry from %s", PROGRAMMES_PATH)
    programmes = load_json(PROGRAMMES_PATH, default=[])
    if not programmes:
        log.error("No programmes loaded; exiting")
        return 1

    previous_status = load_json(STATUS_PATH, default={})
    previous_by_id = {item["id"]: item for item in previous_status.get("results", [])}

    results: list[FetchResult] = []

    with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
        for i, programme in enumerate(programmes):
            previous = previous_by_id.get(programme["id"])
            result = fetch_programme(client, programme, previous)
            results.append(result)
            if i < len(programmes) - 1:
                time.sleep(REQUEST_DELAY)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(results),
        "results": [r.to_dict() for r in results],
    }

    save_json(STATUS_PATH, output)
    log.info("Wrote %s (%d entries)", STATUS_PATH, len(results))

    # Append run summary to history
    history = load_json(HISTORY_PATH, default={"runs": []})
    history["runs"].append(
        {
            "timestamp": output["generated_at"],
            "open": sum(1 for r in results if r.status == "open"),
            "closed": sum(1 for r in results if r.status == "closed"),
            "unknown": sum(1 for r in results if r.status == "unknown"),
            "errors": sum(1 for r in results if r.status == "error"),
            "changes": sum(1 for r in results if r.changed),
        }
    )
    # Keep last 100 runs only
    history["runs"] = history["runs"][-100:]
    save_json(HISTORY_PATH, history)

    # Update the .ics calendar feed
    try:
        try:
            from scraper.calendar import update_calendar
        except ImportError:
            # When run as `python scraper/run.py` rather than `python -m scraper.run`
            import sys as _sys
            _sys.path.insert(0, str(REPO_ROOT))
            from scraper.calendar import update_calendar
        update_calendar()
    except Exception as exc:  # noqa: BLE001 — calendar failures shouldn't fail the run
        log.warning("Calendar update failed: %s", exc)

    # Print summary
    summary = output_summary(results)
    log.info("Summary:\n%s", summary)

    # Always exit 0 — partial fetch failures shouldn't break the workflow
    return 0


def output_summary(results: list[FetchResult]) -> str:
    lines = []
    by_status: dict[str, list[FetchResult]] = {}
    for r in results:
        by_status.setdefault(r.status, []).append(r)
    for status in ("open", "closed", "unknown", "error"):
        lst = by_status.get(status, [])
        if not lst:
            continue
        lines.append(f"  {status.upper()} ({len(lst)}):")
        for r in lst:
            tag = " [CHANGED]" if r.changed else ""
            lines.append(f"    - {r.id}{tag}")
    return "\n".join(lines) if lines else "  (no results)"


if __name__ == "__main__":
    sys.exit(run())
