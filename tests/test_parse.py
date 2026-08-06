"""Parsing the venue's date strings, including the DST-sensitive parts."""

from __future__ import annotations

from datetime import datetime

import pytest

from si_stadium_calendar import (
    DEFAULT_START_HOUR,
    EVENTS_URL,
    parse_when,
    scrape_venue,
    strip_tags,
)


def test_time_published() -> None:
    parsed = parse_when("Aug 6, 2026 7:30 PM")
    assert parsed is not None
    start, time_known = parsed
    assert time_known
    assert (start.year, start.month, start.day, start.hour, start.minute) == (
        2026,
        8,
        6,
        19,
        30,
    )


def test_date_only_assumes_evening() -> None:
    parsed = parse_when("Aug 6, 2026")
    assert parsed is not None
    start, time_known = parsed
    assert not time_known
    assert start.hour == DEFAULT_START_HOUR
    assert start.minute == 0


@pytest.mark.parametrize(
    ("raw", "hour"),
    [
        ("Aug 6, 2026 12:00 AM", 0),
        ("Aug 6, 2026 12:00 PM", 12),
        ("Aug 6, 2026 1:05 pm", 13),
        ("Aug 6, 2026 11:59 P.M.", 23),
    ],
)
def test_meridiem(raw: str, hour: int) -> None:
    parsed = parse_when(raw)
    assert parsed is not None
    assert parsed[0].hour == hour


@pytest.mark.parametrize(
    "raw", ["September 3, 2026 8:00 PM", "Sept. 3, 2026 8:00 PM", "Sep 3 2026 8:00 PM"]
)
def test_month_spellings(raw: str) -> None:
    parsed = parse_when(raw)
    assert parsed is not None
    assert parsed[0].month == 9


@pytest.mark.parametrize("raw", ["", "not a date", "Foo 6, 2026", "2026-08-06"])
def test_unparseable(raw: str) -> None:
    assert parse_when(raw) is None


@pytest.mark.parametrize(
    ("raw", "offset_hours"),
    [("Aug 6, 2026 7:30 PM", -4), ("Jan 6, 2026 7:30 PM", -5)],
)
def test_dst_offset(raw: str, offset_hours: int) -> None:
    """Venue-local means EDT in summer and EST in winter."""
    parsed = parse_when(raw)
    assert parsed is not None
    offset = parsed[0].utcoffset()
    assert offset is not None
    assert offset.total_seconds() / 3600 == offset_hours


def test_utc_conversion_matches_published_feed() -> None:
    parsed = parse_when("Aug 6, 2026 7:30 PM")
    assert parsed is not None
    assert parsed[0].astimezone(tz=None).utcoffset() is not None
    assert parsed[0].timestamp() == datetime.fromisoformat("2026-08-06T23:30:00+00:00").timestamp()


def test_strip_tags_unescapes() -> None:
    assert strip_tags("<h5>Gotham &amp; Co&#x27;s</h5>") == "Gotham & Co's"
    assert strip_tags("<b>a</b>\n\n  <i>b</i>") == "a b"


VENUE_HTML = """
<div role="list">
<div role="listitem"><div class="container-13">
  <h5 class="heading-21">Aug 6, 2026 7:30 PM</h5>
  <h1 class="heading-events-main">LEAGUES CUP</h1>
  <h3 class="heading-22">NYCFC vs. Santos Laguna</h3>
  <a href="https://www.ticketmaster.com/foo/event/ABC">Get TICKETS</a>
</div></div>
<div role="listitem"><div class="container-13">
  <h5 class="heading-21">Aug 22, 2026</h5>
  <h3 class="heading-22">New York vs. Chicago</h3>
</div></div>
<div role="listitem"><div class="container-13">
  <h5 class="heading-21">garbage</h5>
  <h3 class="heading-22">Dropped</h3>
</div></div>
</div>
"""


def test_scrape_venue() -> None:
    listings = scrape_venue(VENUE_HTML)
    assert len(listings) == 2, "the unparseable date must be skipped, not crash"

    first, second = listings
    assert first.title == "NYCFC vs. Santos Laguna"
    assert first.category == "LEAGUES CUP"
    assert first.time_known
    assert first.url.endswith("/event/ABC")

    assert second.title == "New York vs. Chicago"
    assert second.category == "", "no category element means empty, not a crash"
    assert not second.time_known
    assert second.url == EVENTS_URL, "no ticket link falls back to the events page"


def test_scrape_empty_html() -> None:
    assert scrape_venue("<html><body>redesigned</body></html>") == []
