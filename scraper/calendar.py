"""
UK Quantum Internship Tracker - Calendar feed generator

Produces an iCalendar (.ics) feed from the scraper's status.json plus the
curated programmes.json. Each event represents either:

  - DEADLINE: an applications-close date detected on the page
  - OPENING:  a status flip from "closed"/"unknown" to "open" since last run

Each event has VALARM components for reminders. By default:
  - DEADLINE: 1 week before, on the day
  - OPENING:  on the day

The output is a static .ics file at data/calendar.ics. When deployed alongside
the site, calendar apps subscribe to the URL once and pick up new events on
their own refresh cadence.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
PROGRAMMES_PATH = REPO_ROOT / "data" / "programmes.json"
STATUS_PATH = REPO_ROOT / "data" / "status.json"
HISTORY_PATH = REPO_ROOT / "data" / "history.json"
CAL_PATH = REPO_ROOT / "data" / "calendar.ics"
EVENTS_PATH = REPO_ROOT / "data" / "events.json"  # canonical event store

# A stable namespace UUID lets us regenerate identical UIDs across runs;
# this is what allows calendar apps to *update* an event when its date
# shifts, rather than creating a duplicate.
NAMESPACE = uuid.UUID("a3f3c1ad-8d62-49a2-b4d6-2b53cb1bdb3a")

PRODID = "-//Quantum UK Tracker//calendar v1//EN"

log = logging.getLogger("calendar")


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------

MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"], start=1)}

# "8th March 2026", "8 March 2026", "March 8 2026", "March 8th, 2026"
_RX_DAY_MONTH_YEAR = re.compile(
    r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})", re.I
)
_RX_MONTH_DAY_YEAR = re.compile(
    r"([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", re.I
)
# "08/03/2026", "08-03-26"
_RX_NUMERIC = re.compile(r"(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{2,4})")


def parse_date_text(text: str) -> date | None:
    """Parse a human-written date string into a date object. Returns None on failure."""
    if not text:
        return None
    s = text.strip()

    m = _RX_DAY_MONTH_YEAR.search(s)
    if m:
        d, month_name, y = m.group(1), m.group(2).lower(), m.group(3)
        if month_name in MONTHS:
            try:
                return date(int(y), MONTHS[month_name], int(d))
            except ValueError:
                pass

    m = _RX_MONTH_DAY_YEAR.search(s)
    if m:
        month_name, d, y = m.group(1).lower(), m.group(2), m.group(3)
        if month_name in MONTHS:
            try:
                return date(int(y), MONTHS[month_name], int(d))
            except ValueError:
                pass

    m = _RX_NUMERIC.search(s)
    if m:
        d_, m_, y_ = m.group(1), m.group(2), m.group(3)
        if len(y_) == 2:
            y_ = "20" + y_
        # Assume DD/MM/YYYY (UK convention)
        try:
            return date(int(y_), int(m_), int(d_))
        except ValueError:
            pass

    return None


# ---------------------------------------------------------------------------
# Event model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Event:
    uid: str
    summary: str
    description: str
    url: str
    event_date: date
    kind: str  # "deadline" | "opening"
    programme_id: str

    @property
    def reminders(self) -> list[timedelta]:
        """Lead times before the event for VALARM components."""
        if self.kind == "deadline":
            return [timedelta(days=7), timedelta(0)]
        return [timedelta(0)]  # opening: just the day

    def to_dict(self) -> dict:
        return {
            "uid": self.uid,
            "summary": self.summary,
            "description": self.description,
            "url": self.url,
            "date": self.event_date.isoformat(),
            "kind": self.kind,
            "programme_id": self.programme_id,
        }


def make_uid(programme_id: str, kind: str, event_date: date) -> str:
    """Stable UID — calendars dedupe on this, so it must be deterministic."""
    name = f"{programme_id}|{kind}|{event_date.isoformat()}"
    return f"{uuid.uuid5(NAMESPACE, name)}@quantum-uk-tracker"


# ---------------------------------------------------------------------------
# Event derivation from scraper output
# ---------------------------------------------------------------------------


def derive_events(
    programmes: list[dict],
    status_results: list[dict],
    previous_events: list[dict],
) -> list[Event]:
    """Build the canonical event list from current scrape state.

    Two sources:
      1. DEADLINE: status.deadline_text parsed to a date.
      2. OPENING: status.status == "open" AND we have no previous OPEN-event
         for this programme cycle (or the previous record was non-open).
    """
    today = datetime.now(timezone.utc).date()
    programmes_by_id = {p["id"]: p for p in programmes}
    status_by_id = {s["id"]: s for s in status_results}

    # Build a quick lookup of previously-emitted opening events so we don't
    # re-emit them every run while the programme stays open.
    prev_open_by_pid: dict[str, str] = {}
    for ev in previous_events:
        if ev.get("kind") == "opening":
            prev_open_by_pid[ev["programme_id"]] = ev["date"]

    events: list[Event] = []

    for pid, status in status_by_id.items():
        programme = programmes_by_id.get(pid)
        if not programme:
            continue

        # --- DEADLINE event ---------------------------------------------
        if status.get("deadline_text"):
            d = parse_date_text(status["deadline_text"])
            if d and d >= today - timedelta(days=1):
                events.append(Event(
                    uid=make_uid(pid, "deadline", d),
                    summary=f"⏰ Deadline: {programme['name']}",
                    description=(
                        f"Application deadline for {programme['name']} ({programme['org']}).\n\n"
                        f"Eligibility: {programme.get('eligibility', '—')}\n"
                        f"Pay: {programme.get('pay', '—')}\n"
                        f"Apply via: {programme.get('apply_route', '—')}\n\n"
                        f"Source: {programme['url']}\n\n"
                        f"Detected from page text: \"{status['deadline_text']}\""
                    ),
                    url=programme["url"],
                    event_date=d,
                    kind="deadline",
                    programme_id=pid,
                ))

        # --- OPENING event ---------------------------------------------
        if status.get("status") == "open":
            # Use today as the opening date if we don't already have one stored
            existing_open_date_str = prev_open_by_pid.get(pid)
            if existing_open_date_str:
                # Reuse stored date — keep the event stable
                try:
                    open_date = date.fromisoformat(existing_open_date_str)
                except ValueError:
                    open_date = today
            else:
                open_date = today

            events.append(Event(
                uid=make_uid(pid, "opening", open_date),
                summary=f"🟢 Now open: {programme['name']}",
                description=(
                    f"Applications appear to be OPEN for {programme['name']} ({programme['org']}).\n\n"
                    f"Apply early — these schemes often fill on a rolling basis.\n\n"
                    f"Eligibility: {programme.get('eligibility', '—')}\n"
                    f"Apply via: {programme.get('apply_route', '—')}\n\n"
                    f"Source: {programme['url']}"
                ),
                url=programme["url"],
                event_date=open_date,
                kind="opening",
                programme_id=pid,
            ))

    return events


# ---------------------------------------------------------------------------
# .ics serialisation
# ---------------------------------------------------------------------------


def _escape_text(text: str) -> str:
    """Escape per RFC 5545 §3.3.11."""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
        .replace("\r", "")
    )


def _fold_line(line: str, limit: int = 73) -> list[str]:
    """RFC 5545 line folding: max 75 octets per line, continuation = leading space.
    We use 73 to give a safety margin for multi-byte chars."""
    if len(line.encode("utf-8")) <= 75:
        return [line]
    parts: list[str] = []
    encoded = line.encode("utf-8")
    while len(encoded) > limit:
        # Find a safe split that doesn't break a multi-byte char
        cut = limit
        while (encoded[cut] & 0xC0) == 0x80:  # continuation byte
            cut -= 1
        parts.append(encoded[:cut].decode("utf-8"))
        encoded = b" " + encoded[cut:]
    parts.append(encoded.decode("utf-8"))
    return parts


def _format_date(d: date) -> str:
    """All-day events use YYYYMMDD (DATE value type)."""
    return d.strftime("%Y%m%d")


def _format_dt(dt: datetime) -> str:
    """UTC datetime as YYYYMMDDTHHMMSSZ."""
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def render_ics(events: Iterable[Event], generated_at: datetime) -> str:
    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:UK Quantum Internships",
        "X-WR-CALDESC:Auto-generated deadlines and openings for UK quantum internships",
        "X-WR-TIMEZONE:Europe/London",
        # Refresh hint for clients that support it (Apple/Outlook)
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "X-PUBLISHED-TTL:PT12H",
    ]

    dtstamp = _format_dt(generated_at)

    for ev in events:
        end_date = ev.event_date + timedelta(days=1)  # DTEND is exclusive for all-day

        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{ev.uid}")
        lines.append(f"DTSTAMP:{dtstamp}")
        lines.append(f"DTSTART;VALUE=DATE:{_format_date(ev.event_date)}")
        lines.append(f"DTEND;VALUE=DATE:{_format_date(end_date)}")
        lines.append(f"SUMMARY:{_escape_text(ev.summary)}")
        lines.append(f"DESCRIPTION:{_escape_text(ev.description)}")
        lines.append(f"URL:{ev.url}")
        lines.append("STATUS:CONFIRMED")
        lines.append("TRANSP:TRANSPARENT")  # don't block free/busy time
        lines.append(f"CATEGORIES:{ev.kind.upper()}")

        # VALARM components — reminders
        for lead in ev.reminders:
            total_minutes = int(lead.total_seconds() // 60)
            if total_minutes == 0:
                trigger = "-PT0M"  # at start of event
            else:
                trigger = f"-PT{total_minutes}M"
            lines.append("BEGIN:VALARM")
            lines.append("ACTION:DISPLAY")
            lines.append(f"DESCRIPTION:{_escape_text(ev.summary)}")
            lines.append(f"TRIGGER:{trigger}")
            lines.append("END:VALARM")

        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")

    # Apply RFC 5545 line folding & CRLF
    folded: list[str] = []
    for line in lines:
        folded.extend(_fold_line(line))
    return "\r\n".join(folded) + "\r\n"


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Could not read %s: %s", path, exc)
        return default


def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def update_calendar() -> int:
    """Re-derive events from the latest status, write events.json + calendar.ics."""
    programmes = _load_json(PROGRAMMES_PATH, default=[])
    status = _load_json(STATUS_PATH, default={"results": []})
    prev_events_blob = _load_json(EVENTS_PATH, default={"events": []})
    prev_events = prev_events_blob.get("events", [])

    events = derive_events(
        programmes=programmes,
        status_results=status.get("results", []),
        previous_events=prev_events,
    )

    # Persist
    generated_at = datetime.now(timezone.utc)
    _save_json(EVENTS_PATH, {
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "count": len(events),
        "events": [e.to_dict() for e in events],
    })

    ics = render_ics(events, generated_at)
    CAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CAL_PATH.open("w", encoding="utf-8", newline="") as f:
        f.write(ics)

    log.info("Wrote %s (%d events)", CAL_PATH, len(events))
    log.info("Breakdown: %d deadlines, %d openings",
             sum(1 for e in events if e.kind == "deadline"),
             sum(1 for e in events if e.kind == "opening"))
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
    )
    import sys
    sys.exit(update_calendar())
