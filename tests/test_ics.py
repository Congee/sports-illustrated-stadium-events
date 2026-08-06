"""RFC 5545 mechanics: folding, escaping, CRLF, and the rendered VEVENT."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from si_stadium_calendar import (
    FOLD_AT,
    LOCATION,
    MAX_OCTETS,
    Event,
    Listing,
    Window,
    fold,
    ics_escape,
    render_calendar,
)

NOW = datetime.fromisoformat("2026-08-01T12:00:00-04:00")


def make_event(
    title: str = "NYCFC vs. Santos Laguna",
    category: str = "LEAGUES CUP",
    start: str = "2026-08-06T19:30:00-04:00",
    *,
    time_known: bool = True,
) -> Event:
    listing = Listing(
        title=title,
        category=category,
        start=datetime.fromisoformat(start),
        time_known=time_known,
        url="https://example.com/e/1",
    )
    return Event(listing=listing, first_seen=NOW, last_seen=NOW)


# --- folding ---


def test_short_line_untouched() -> None:
    assert fold("SUMMARY:short") == "SUMMARY:short"


@pytest.mark.parametrize("length", [FOLD_AT, MAX_OCTETS])
def test_at_or_under_the_limit_is_never_split(length: int) -> None:
    """A line of exactly 75 octets is legal. LOCATION: is exactly 75."""
    line = "A" * length
    assert fold(line) == line


def test_one_octet_over_splits() -> None:
    folded = fold("A" * (MAX_OCTETS + 1))
    assert folded.count("\r\n") == 1
    head, tail = folded.split("\r\n")
    assert len(head.encode()) == FOLD_AT
    assert tail == " " + "A" * (MAX_OCTETS + 1 - FOLD_AT)


def test_real_location_line_is_not_folded() -> None:
    """Regression: this line is exactly at the limit and used to get split."""
    line = "LOCATION:" + ics_escape(LOCATION)
    assert len(line.encode()) == MAX_OCTETS
    assert "\r\n" not in fold(line)


@pytest.mark.parametrize("length", [80, 200, 1000])
def test_every_folded_line_fits(length: int) -> None:
    folded = fold("DESCRIPTION:" + "x" * length)
    for part in folded.split("\r\n"):
        assert len(part.encode()) <= MAX_OCTETS


def test_continuations_start_with_space() -> None:
    parts = fold("URL:" + "y" * 300).split("\r\n")
    assert all(part.startswith(" ") for part in parts[1:])


def test_unfolding_round_trips() -> None:
    original = "DESCRIPTION:" + "z" * 400
    unfolded = fold(original).replace("\r\n ", "")
    assert unfolded == original


def test_never_splits_a_multibyte_char() -> None:
    # Accents make the octet count exceed the character count.
    folded = fold("SUMMARY:" + "é" * 100)
    for part in folded.split("\r\n"):
        assert len(part.encode()) <= MAX_OCTETS
    assert folded.replace("\r\n ", "") == "SUMMARY:" + "é" * 100


# --- escaping ---


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a,b", r"a\,b"),
        ("a;b", r"a\;b"),
        ("a\nb", r"a\nb"),
        ("a\\b", "a\\\\b"),
        ("Harrison, NJ", r"Harrison\, NJ"),
    ],
)
def test_ics_escape(raw: str, expected: str) -> None:
    assert ics_escape(raw) == expected


def test_backslash_escaped_before_others() -> None:
    """A literal backslash must not be re-escaped by the later replacements."""
    assert ics_escape("a\\,b") == "a\\\\\\,b"


# --- calendar ---


def test_crlf_everywhere() -> None:
    ics = render_calendar([make_event()], NOW, Window())
    assert "\r\n" in ics
    assert ics.endswith("\r\n")
    for line in ics.split("\r\n")[:-1]:
        assert "\n" not in line, "a bare LF would break strict parsers"


def test_envelope() -> None:
    ics = render_calendar([make_event()], NOW, Window())
    assert ics.startswith("BEGIN:VCALENDAR\r\n")
    assert ics.rstrip("\r\n").endswith("END:VCALENDAR")
    assert "METHOD:PUBLISH" in ics
    assert ics.count("BEGIN:VEVENT") == ics.count("END:VEVENT") == 1


def test_duration_drives_dtend() -> None:
    ics = render_calendar([make_event()], NOW, Window())
    # Soccer: 120 minutes. 19:30 EDT is 23:30Z, so DTEND is 01:30Z the next day.
    assert "DTSTART:20260806T233000Z" in ics
    assert "DTEND:20260807T013000Z" in ics


def test_padding_widens_the_window() -> None:
    window = Window(pre=timedelta(minutes=90), post=timedelta(minutes=90))
    ics = render_calendar([make_event()], NOW, window)
    assert "DTSTART:20260806T220000Z" in ics
    assert "DTEND:20260807T030000Z" in ics
    assert "padded -90m / +90m" in ics


def test_no_padding_note_when_unpadded() -> None:
    assert "padded" not in render_calendar([make_event()], NOW, Window())


def test_alarm_optional() -> None:
    with_alarm = render_calendar([make_event()], NOW, Window(alarm=timedelta(minutes=120)))
    assert "BEGIN:VALARM" in with_alarm
    assert "TRIGGER:-PT120M" in with_alarm
    assert "BEGIN:VALARM" not in render_calendar([make_event()], NOW, Window())


def test_tbd_time_is_flagged_to_the_reader() -> None:
    ics = render_calendar([make_event(time_known=False)], NOW, Window())
    assert "not published yet" in ics


def test_events_are_transparent() -> None:
    """Traffic warnings should not mark the subscriber busy."""
    assert "TRANSP:TRANSPARENT" in render_calendar([make_event()], NOW, Window())


def test_summary_includes_category() -> None:
    ics = render_calendar([make_event()], NOW, Window())
    assert "SUMMARY:NYCFC vs. Santos Laguna (LEAGUES CUP)" in ics


def test_summary_without_category() -> None:
    ics = render_calendar([make_event(category="")], NOW, Window())
    assert "SUMMARY:NYCFC vs. Santos Laguna\r\n" in ics


def test_empty_calendar_is_still_valid() -> None:
    ics = render_calendar([], NOW, Window())
    assert "BEGIN:VEVENT" not in ics
    assert ics.startswith("BEGIN:VCALENDAR")
    assert ics.rstrip("\r\n").endswith("END:VCALENDAR")
