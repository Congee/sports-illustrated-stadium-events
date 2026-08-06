"""Category-to-duration estimates, including the pattern precedence."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from si_stadium_calendar import DEFAULT_DURATION, Listing


def listing(category: str, title: str = "A vs B") -> Listing:
    return Listing(
        title=title,
        category=category,
        start=datetime.fromisoformat("2026-08-06T19:30:00-04:00"),
        time_known=True,
        url="",
    )


@pytest.mark.parametrize(
    ("category", "title", "minutes"),
    [
        ("MLS 2026", "New York vs. Philadelphia", 120),
        ("NWSL 2026", "Gotham FC vs Thorns", 120),
        ("LEAGUES CUP", "NYCFC vs. Necaxa", 120),
        ("CONCACAF", "Team vs Team", 120),
        ("CONCERT", "Some Band Live", 180),
        ("", "Summer Music Festival", 180),
        ("RUGBY", "Ireland vs USA", 110),
        ("NFL", "Preseason Game", 200),
    ],
)
def test_known_categories(category: str, title: str, minutes: int) -> None:
    assert listing(category, title).duration == timedelta(minutes=minutes)


@pytest.mark.parametrize(
    ("category", "title"),
    [("", "Monster Truck Rally"), ("", ""), ("EXPO", "Trade Show")],
)
def test_unknown_falls_back(category: str, title: str) -> None:
    assert listing(category, title).duration == DEFAULT_DURATION


def test_rugby_beats_the_soccer_catch_all() -> None:
    """'Ireland vs USA' also matches the generic 'vs' soccer pattern."""
    assert listing("RUGBY", "Ireland vs USA").duration == timedelta(minutes=110)


def test_concert_beats_the_soccer_catch_all() -> None:
    assert listing("CONCERT", "Band A vs. Band B").duration == timedelta(minutes=180)


def test_matching_is_case_insensitive() -> None:
    assert listing("mls 2026").duration == listing("MLS 2026").duration


def test_year_suffix_does_not_defeat_matching() -> None:
    """The venue writes 'MLS 2026', not 'MLS', so matching must be a substring."""
    assert listing("MLS 2030", "X vs Y").duration == timedelta(minutes=120)
