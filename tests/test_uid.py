"""UID stability. This is the one thing that must never change.

Subscribers key existing calendar entries off the UID. If the algorithm drifts,
every event turns into a duplicate plus an orphan that never clears. The table
below is the real UID set as published; treat a failure here as a migration
hazard, not a test to update.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from si_stadium_calendar import Listing

PUBLISHED: list[tuple[str, str, str]] = [
    ("NYCFC vs. Santos Laguna", "2026-08-06T19:30:00-04:00", "0c2001e4ef264570@si-stadium"),
    ("Gotham FC vs San Diego Wave", "2026-08-07T20:00:00-04:00", "9c34267849572a1c@si-stadium"),
    ("Cruz Azul vs. NYCFC", "2026-08-09T19:30:00-04:00", "826c9004c8f99fcb@si-stadium"),
    ("NYCFC vs. Necaxa", "2026-08-13T19:30:00-04:00", "dad1b13395423af7@si-stadium"),
    ("Gotham FC vs Current", "2026-08-14T20:00:00-04:00", "b66ad0947bc1060e@si-stadium"),
    ("New York vs. Nashville", "2026-08-19T19:30:00-04:00", "824bdef85e205217@si-stadium"),
    ("New York vs. Chicago", "2026-08-22T19:30:00-04:00", "90d6c5bdd1524664@si-stadium"),
    ("Gotham FC vs Thorns", "2026-08-28T20:00:00-04:00", "05447ab6b15fe20b@si-stadium"),
    ("New York vs. Philadelphia", "2026-08-29T19:30:00-04:00", "29e3acbb5700bbfb@si-stadium"),
]


def listing(title: str, start: str) -> Listing:
    return Listing(
        title=title,
        category="",
        start=datetime.fromisoformat(start),
        time_known=True,
        url="",
    )


@pytest.mark.parametrize(("title", "start", "expected"), PUBLISHED)
def test_uid_matches_published_feed(title: str, start: str, expected: str) -> None:
    assert listing(title, start).uid == expected


def test_uids_are_unique() -> None:
    uids = {listing(t, s).uid for t, s, _ in PUBLISHED}
    assert len(uids) == len(PUBLISHED)


def test_uid_ignores_category_and_url() -> None:
    """Only title and date feed the hash, so metadata edits are non-breaking."""
    base = listing("New York vs. Chicago", "2026-08-22T19:30:00-04:00")
    assert base.uid == replace_meta(base).uid


def replace_meta(base: Listing) -> Listing:
    return Listing(
        title=base.title,
        category="CHANGED",
        start=base.start,
        time_known=False,
        url="https://changed.example",
    )


def test_uid_ignores_time_of_day() -> None:
    """A kickoff moved within the same day updates the entry, not duplicates it."""
    evening = listing("New York vs. Chicago", "2026-08-22T19:30:00-04:00")
    afternoon = listing("New York vs. Chicago", "2026-08-22T15:00:00-04:00")
    assert evening.uid == afternoon.uid


def test_uid_changes_with_the_date() -> None:
    same_day = listing("New York vs. Chicago", "2026-08-22T19:30:00-04:00")
    next_day = listing("New York vs. Chicago", "2026-08-23T19:30:00-04:00")
    assert same_day.uid != next_day.uid


def test_uid_normalizes_punctuation_and_case() -> None:
    """Cosmetic retitling on the venue page must not orphan an entry."""
    plain = listing("NYCFC vs. Necaxa", "2026-08-13T19:30:00-04:00")
    noisy = listing("nycfc  VS.   necaxa!", "2026-08-13T19:30:00-04:00")
    assert plain.uid == noisy.uid


def test_uid_shape() -> None:
    uid = listing("Anything", "2026-08-22T19:30:00-04:00").uid
    digest, _, domain = uid.partition("@")
    assert len(digest) == 16
    assert domain == "si-stadium"
    assert all(char in "0123456789abcdef" for char in digest)
