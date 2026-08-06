#!/usr/bin/env python3
"""Scrape Sports Illustrated Stadium events into an .ics feed.

The venue publishes no iCal feed, and its /events page only lists a rolling few
weeks, so state accumulates on disk across runs. Meant to be run weekly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, NamedTuple, TypedDict
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

EVENTS_URL: Final = "https://www.sportsillustratedstadium.com/events"
VENUE_TZ: Final = ZoneInfo("America/New_York")
LOCATION: Final = "Sports Illustrated Stadium, 600 Cape May St, Harrison, NJ 07029"
CALENDAR_NAME: Final = "Sports Illustrated Stadium"
TM_VENUE_ID: Final = "1269"
DEFAULT_STATE_DIR: Final = Path.home() / ".local/share/si-stadium-calendar"

# Assumed when the venue lists a date but no time.
DEFAULT_START_HOUR: Final = 19

# The venue publishes a start time only, never an end, so the length is an
# estimate. First match against "<category> <title>", lowercased, wins.
DURATIONS: Final[tuple[tuple[str, int], ...]] = (
    (r"concert|festival|tour\b|music", 180),
    (r"nfl|american football", 200),
    (r"rugby", 110),
    # 90 regulation + 15 halftime + stoppage; knockouts can add penalties.
    (r"mls|nwsl|leagues cup|concacaf|usl|soccer|friendly|\bfc\b|\bvs\.? ", 120),
)
DEFAULT_DURATION: Final = timedelta(minutes=150)

# RFC 5545 caps a content line at 75 octets. Fold one short, so the
# continuation's mandatory leading space still fits.
MAX_OCTETS: Final = 75
FOLD_AT: Final = MAX_OCTETS - 1


# --- model -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Listing:
    """One event as a source publishes it."""

    title: str
    category: str
    start: datetime
    time_known: bool
    url: str

    @property
    def uid(self) -> str:
        """Stable across runs, so a time change updates rather than duplicates."""
        slug = re.sub(r"[^a-z0-9]+", "", self.title.lower())
        key = f"{slug}|{self.start:%Y-%m-%d}"
        return hashlib.sha1(key.encode()).hexdigest()[:16] + "@si-stadium"

    @property
    def summary(self) -> str:
        return f"{self.title} ({self.category})" if self.category else self.title

    @property
    def duration(self) -> timedelta:
        haystack = f"{self.category} {self.title}".lower()
        for pattern, minutes in DURATIONS:
            if re.search(pattern, haystack):
                return timedelta(minutes=minutes)
        return DEFAULT_DURATION


class Record(TypedDict):
    """One event as it is stored in events.json. Changing a key breaks old state."""

    uid: str
    title: str
    category: str
    start: str
    time_known: bool
    url: str
    first_seen: str
    last_seen: str


@dataclass(frozen=True, slots=True)
class Event:
    """A listing, plus when we have seen it."""

    listing: Listing
    first_seen: datetime
    last_seen: datetime

    def to_json(self) -> Record:
        return {
            "uid": self.listing.uid,
            "title": self.listing.title,
            "category": self.listing.category,
            "start": self.listing.start.isoformat(),
            "time_known": self.listing.time_known,
            "url": self.listing.url,
            "first_seen": self.first_seen.isoformat(timespec="seconds"),
            "last_seen": self.last_seen.isoformat(timespec="seconds"),
        }

    @classmethod
    def from_json(cls, raw: Record) -> Event:
        return cls(
            listing=Listing(
                title=raw["title"],
                category=raw["category"],
                start=datetime.fromisoformat(raw["start"]),
                time_known=raw["time_known"],
                url=raw["url"],
            ),
            first_seen=datetime.fromisoformat(raw["first_seen"]),
            last_seen=datetime.fromisoformat(raw["last_seen"]),
        )


@dataclass(frozen=True, slots=True)
class Window:
    """How much of the surrounding traffic to include, and when to warn."""

    pre: timedelta = timedelta()
    post: timedelta = timedelta()
    alarm: timedelta | None = None

    def bounds(self, listing: Listing) -> tuple[datetime, datetime]:
        return listing.start - self.pre, listing.start + listing.duration + self.post


class Reschedule(NamedTuple):
    """The start a subscriber still has, and the event as it now stands."""

    was: datetime
    event: Event


@dataclass(frozen=True, slots=True)
class Delta:
    """What one merge changed."""

    added: tuple[Event, ...] = ()
    rescheduled: tuple[Reschedule, ...] = ()
    delisted: tuple[Event, ...] = ()


# --- state -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class State:
    """Accumulated events, keyed by UID, backed by a JSON file."""

    path: Path
    events: Mapping[str, Event]

    @classmethod
    def load(cls, path: Path) -> State:
        if not path.exists():
            return cls(path=path, events={})
        raw: dict[str, Record] = json.loads(path.read_text())
        return cls(path=path, events={k: Event.from_json(v) for k, v in raw.items()})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {uid: ev.to_json() for uid, ev in self.events.items()}
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True))

    def merge(self, listings: Sequence[Listing], now: datetime) -> tuple[State, Delta]:
        """Fold listings in and report what changed. Leaves this State untouched."""
        seen_at = now.replace(microsecond=0)
        events = dict(self.events)
        added: list[Event] = []
        rescheduled: list[Reschedule] = []

        for fresh in listings:
            previous = events.get(fresh.uid)
            if previous is None:
                event = Event(listing=fresh, first_seen=seen_at, last_seen=seen_at)
                events[fresh.uid] = event
                added.append(event)
                continue

            moved = previous.listing.start != fresh.start
            current = (
                replace(previous.listing, start=fresh.start, time_known=fresh.time_known)
                if moved
                else previous.listing
            )
            event = replace(
                previous,
                # A source that omits a field should not blank what we already have.
                listing=replace(
                    current,
                    url=fresh.url or current.url,
                    category=fresh.category or current.category,
                ),
                last_seen=seen_at,
            )
            events[fresh.uid] = event
            if moved:
                # The updated event, so a report can show both sides of the move.
                rescheduled.append(Reschedule(previous.listing.start, event))

        delisted = tuple(
            ev
            for uid, ev in events.items()
            if ev.listing.start > now and uid in self.events and ev.last_seen != seen_at
        )
        return replace(self, events=events), Delta(tuple(added), tuple(rescheduled), delisted)

    def upcoming(self, now: datetime, weeks: int = 0) -> list[Event]:
        horizon = now + timedelta(weeks=weeks) if weeks else None
        return sorted(
            (
                ev
                for ev in self.events.values()
                if ev.listing.start > now and (horizon is None or ev.listing.start <= horizon)
            ),
            key=lambda ev: ev.listing.start,
        )

    def all_sorted(self) -> list[Event]:
        return sorted(self.events.values(), key=lambda ev: ev.listing.start)


# --- scraping ----------------------------------------------------------------

# Webflow CMS markup on the venue page. This is the fragile part: if the page is
# restyled these stop matching, the scrape yields nothing, and main() exits 1.
_ITEM = re.compile(r'<div role="listitem"')
_WHEN = re.compile(r'class="heading-21"[^>]*>(.*?)</h5>', re.S)
_TITLE = re.compile(r'class="heading-22"[^>]*>(.*?)</h3>', re.S)
_CATEGORY = re.compile(r'class="heading-events-main"[^>]*>(.*?)</h1>', re.S)
_LINK = re.compile(r'href="(https?://[^"]*(?:ticketmaster|sitickets)[^"]*)"')

_WHEN_TEXT = re.compile(
    r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})"
    r"(?:\s+(\d{1,2}):(\d{2})\s*([AaPp])\.?[Mm]\.?)?"
)
_TAG = re.compile(r"<[^>]+>")
_ENTITIES: Final = (("&amp;", "&"), ("&nbsp;", " "), ("&#x27;", "'"), ("&quot;", '"'))


def fetch(url: str, timeout: int = 30) -> str:
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    with urlopen(Request(url, headers=headers), timeout=timeout) as response:
        body: bytes = response.read()
    return body.decode("utf-8", "replace")


def strip_tags(html: str) -> str:
    text = _TAG.sub(" ", html)
    for entity, char in _ENTITIES:
        text = text.replace(entity, char)
    return re.sub(r"\s+", " ", text).strip()


class Moment(NamedTuple):
    """A parsed start, and whether the source actually published a time."""

    start: datetime
    time_known: bool


def parse_when(raw: str) -> Moment | None:
    """Parse 'Aug 6, 2026 7:30 PM' or 'Aug 6, 2026', venue-local."""
    match = _WHEN_TEXT.match(raw.strip())
    if not match:
        return None
    month_name, day, year, hour, minute, meridiem = match.groups()
    try:
        month = datetime.strptime(month_name[:3], "%b").month
    except ValueError:
        return None

    if hour is None:
        start = datetime(int(year), month, int(day), DEFAULT_START_HOUR, tzinfo=VENUE_TZ)
        return Moment(start, time_known=False)

    hour24 = int(hour) % 12 + (12 if meridiem.lower() == "p" else 0)
    start = datetime(int(year), month, int(day), hour24, int(minute), tzinfo=VENUE_TZ)
    return Moment(start, time_known=True)


def scrape_venue(html: str) -> list[Listing]:
    """Pull listings out of the Webflow collection items."""
    listings: list[Listing] = []
    for block in _ITEM.split(html)[1:]:
        when, title = _WHEN.search(block), _TITLE.search(block)
        if not (when and title):
            continue
        parsed = parse_when(strip_tags(when.group(1)))
        if not parsed:
            continue
        start, time_known = parsed
        category = _CATEGORY.search(block)
        link = _LINK.search(block)
        listings.append(
            Listing(
                title=strip_tags(title.group(1)),
                category=strip_tags(category.group(1)) if category else "",
                start=start,
                time_known=time_known,
                url=link.group(1) if link else EVENTS_URL,
            )
        )
    return listings


def scrape_ticketmaster(api_key: str) -> list[Listing]:
    """Look further ahead than the venue page's rolling window."""
    url = (
        "https://app.ticketmaster.com/discovery/v2/events.json"
        f"?venueId={TM_VENUE_ID}&size=200&sort=date,asc&apikey={api_key}"
    )
    try:
        # Any: a deep foreign payload we only dip into; modelling it buys nothing.
        payload: dict[str, Any] = json.loads(fetch(url))
    except Exception as exc:  # best-effort enrichment; never fatal
        print(f"  ticketmaster: skipped ({exc})", file=sys.stderr)
        return []

    listings: list[Listing] = []
    for event in payload.get("_embedded", {}).get("events", []):
        dates = event.get("dates", {}).get("start", {})
        local_date = dates.get("localDate")
        if not local_date:
            continue
        local_time = dates.get("localTime")
        time_known = bool(local_time) and not (dates.get("timeTBA") or dates.get("dateTBD"))
        clock = local_time or f"{DEFAULT_START_HOUR:02d}:00:00"
        genres = event.get("classifications") or [{}]
        listings.append(
            Listing(
                title=event.get("name", "Event"),
                category=(genres[0].get("genre") or {}).get("name", ""),
                start=datetime.fromisoformat(f"{local_date}T{clock[:8]}").replace(tzinfo=VENUE_TZ),
                time_known=time_known,
                url=event.get("url", EVENTS_URL),
            )
        )
    return listings


# --- ics ---------------------------------------------------------------------


def ics_escape(text: str) -> str:
    for char, escaped in (("\\", "\\\\"), (";", r"\;"), (",", r"\,"), ("\n", r"\n")):
        text = text.replace(char, escaped)
    return text


def fold(line: str) -> str:
    """Wrap a content line onto continuations, never splitting a character.

    Folds only once a line exceeds the limit, but cuts one octet short so the
    continuation's mandatory leading space still fits.
    """
    parts: list[str] = []
    rest = line
    while len(rest.encode()) > MAX_OCTETS:
        head = rest.encode()[:FOLD_AT].decode(errors="ignore")
        parts.append(head)
        rest = " " + rest[len(head) :]
    parts.append(rest)
    return "\r\n".join(parts)


def as_utc(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def describe(listing: Listing, window: Window) -> str:
    minutes = int(listing.duration.total_seconds() // 60)
    lines = [
        listing.summary,
        f"Event start: {listing.start:%a %b %-d, %-I:%M %p} ET",
    ]
    if not listing.time_known:
        lines.append("Start time not published yet - assumed.")
    lines.append(f"Assumed {minutes}min long; the venue publishes no end time.")
    if window.pre or window.post:
        pre = int(window.pre.total_seconds() // 60)
        post = int(window.post.total_seconds() // 60)
        lines.append(f"Window padded -{pre}m / +{post}m for traffic.")
    lines.append(listing.url)
    return "\n".join(lines)


def render_event(event: Event, stamp: datetime, window: Window) -> list[str]:
    listing = event.listing
    start, end = window.bounds(listing)
    lines = [
        "BEGIN:VEVENT",
        f"UID:{listing.uid}",
        f"DTSTAMP:{as_utc(stamp)}",
        f"DTSTART:{as_utc(start)}",
        f"DTEND:{as_utc(end)}",
        fold("SUMMARY:" + ics_escape(listing.summary)),
        fold("LOCATION:" + ics_escape(LOCATION)),
        fold("DESCRIPTION:" + ics_escape(describe(listing, window))),
        fold("URL:" + listing.url),
        "TRANSP:TRANSPARENT",
        "SEQUENCE:0",
    ]
    if window.alarm is not None:
        lines += [
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"TRIGGER:-PT{int(window.alarm.total_seconds() // 60)}M",
            "DESCRIPTION:Stadium event - expect traffic in Harrison",
            "END:VALARM",
        ]
    lines.append("END:VEVENT")
    return lines


def render_calendar(events: Sequence[Event], stamp: datetime, window: Window) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//si-stadium-calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{CALENDAR_NAME}",
        "X-WR-TIMEZONE:America/New_York",
        fold(
            "X-WR-CALDESC:Events at Sports Illustrated Stadium, Harrison NJ. "
            "End times are estimated. Unofficial - scraped from the venue site."
        ),
        # Hints only; clients poll on their own schedule.
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "X-PUBLISHED-TTL:PT12H",
    ]
    for event in events:
        lines += render_event(event, stamp, window)
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def _without_stamps(ics: str) -> str:
    return re.sub(r"DTSTAMP:[0-9TZ]+\n", "", ics.replace("\r\n", "\n"))


def write_if_changed(path: Path, ics: str) -> bool:
    """Write only on a real change, so DTSTAMP churn does not touch the file."""
    previous = path.read_text() if path.exists() else ""
    if _without_stamps(previous) == _without_stamps(ics):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ics)
    return True


# --- reporting ---------------------------------------------------------------


def render_report(delta: Delta, upcoming: Sequence[Event], weeks: int = 0) -> str:
    def when(moment: datetime) -> str:
        return f"{moment:%a %Y-%m-%d %-I:%M %p}"

    lines = [
        "",
        f"{len(delta.added)} new, {len(delta.rescheduled)} rescheduled, "
        f"{len(delta.delisted)} no longer listed",
    ]
    for event in sorted(delta.added, key=lambda ev: ev.listing.start):
        tbd = "" if event.listing.time_known else "  (time TBD)"
        lines.append(f"  NEW          {when(event.listing.start)}  {event.listing.summary}{tbd}")
    for was, event in delta.rescheduled:
        lines.append(
            f"  RESCHEDULED  {was:%Y-%m-%d %-I:%M %p} -> "
            f"{when(event.listing.start)}  {event.listing.summary}"
        )
    for event in sorted(delta.delisted, key=lambda ev: ev.listing.start):
        seen = event.last_seen.date()
        lines.append(
            f"  DELISTED?    {when(event.listing.start)}  {event.listing.summary}"
            f"  (last seen {seen})"
        )

    horizon = f" within {weeks} week(s)" if weeks else ""
    lines += ["", f"{len(upcoming)} upcoming event(s){horizon}"]
    return "\n".join(lines)


# --- main --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Options:
    """Everything argv decides. Keeps argparse's untyped Namespace out of main."""

    dir: Path
    ics_out: Path | None
    tm_key: str | None
    pre_min: int
    post_min: int
    alarm_min: int
    weeks: int
    dry_run: bool

    @property
    def window(self) -> Window:
        return Window(
            pre=timedelta(minutes=self.pre_min),
            post=timedelta(minutes=self.post_min),
            alarm=timedelta(minutes=self.alarm_min) if self.alarm_min > 0 else None,
        )


def parse_args(argv: Sequence[str] | None = None) -> Options:
    parser = argparse.ArgumentParser(
        description="Pull Sports Illustrated Stadium events into a calendar."
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=Path(os.environ.get("SI_STADIUM_DIR", DEFAULT_STATE_DIR)),
        help="where events.json lives (env: SI_STADIUM_DIR)",
    )
    parser.add_argument(
        "--ics-out",
        metavar="PATH",
        type=Path,
        help="write the .ics here instead of the state dir, e.g. a static site dir",
    )
    parser.add_argument(
        "--tm-key",
        default=os.environ.get("TICKETMASTER_API_KEY"),
        help="Ticketmaster Discovery API key, to see past the venue page's window",
    )
    parser.add_argument("--pre-min", type=int, default=0, help="pad earlier, for inbound traffic")
    parser.add_argument("--post-min", type=int, default=0, help="pad later, for outbound traffic")
    parser.add_argument(
        "--alarm-min", type=int, default=120, help="alert N minutes ahead; 0 disables"
    )
    parser.add_argument("--weeks", type=int, default=0, help="only act on events within N weeks")
    parser.add_argument("--dry-run", action="store_true", help="report what changed, write nothing")

    ns = parser.parse_args(argv)
    # The single place argparse's Any leaks in; everything downstream is checked.
    return Options(
        dir=ns.dir,
        ics_out=ns.ics_out,
        tm_key=ns.tm_key,
        pre_min=ns.pre_min,
        post_min=ns.post_min,
        alarm_min=ns.alarm_min,
        weeks=ns.weeks,
        dry_run=ns.dry_run,
    )


def collect(tm_key: str | None) -> list[Listing]:
    print(f"Fetching {EVENTS_URL} ...")
    listings = scrape_venue(fetch(EVENTS_URL))
    print(f"  venue page: {len(listings)} events")
    if tm_key:
        extra = scrape_ticketmaster(tm_key)
        print(f"  ticketmaster: {len(extra)} events")
        listings += extra
    return listings


def main(argv: Sequence[str] | None = None) -> int:
    opts = parse_args(argv)
    now = datetime.now(VENUE_TZ)

    listings = collect(opts.tm_key)
    if not listings:
        print(
            "No events parsed - the venue page layout probably changed.",
            file=sys.stderr,
        )
        return 1

    state, delta = State.load(opts.dir / "events.json").merge(listings, now)
    upcoming = state.upcoming(now, opts.weeks)
    print(render_report(delta, upcoming, opts.weeks))

    if opts.dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    state.save()
    ics_path = opts.ics_out or opts.dir / "si-stadium.ics"
    changed = write_if_changed(ics_path, render_calendar(state.all_sorted(), now, opts.window))
    print(
        f"\n{'Wrote' if changed else 'Unchanged'} {ics_path} "
        f"({len(state.events)} events, past included)"
    )
    print(f"State: {state.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
