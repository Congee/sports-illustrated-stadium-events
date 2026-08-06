"""Merge semantics and on-disk round-tripping."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from si_stadium_calendar import Event, Listing, State

NOW = datetime.fromisoformat("2026-08-01T12:00:00-04:00")
LATER = NOW + timedelta(days=7)


def listing(
    title: str = "New York vs. Chicago",
    start: str = "2026-08-22T19:30:00-04:00",
    category: str = "MLS 2026",
    url: str = "https://example.com/a",
    *,
    time_known: bool = True,
) -> Listing:
    return Listing(
        title=title,
        category=category,
        start=datetime.fromisoformat(start),
        time_known=time_known,
        url=url,
    )


def empty_state() -> State:
    return State(path=Path("unused.json"), events={})


def test_first_sighting_is_added() -> None:
    state, delta = empty_state().merge([listing()], NOW)

    assert len(delta.added) == 1
    assert not delta.rescheduled
    assert not delta.delisted
    assert state.events[listing().uid].first_seen == NOW


def test_merge_leaves_the_receiver_untouched() -> None:
    """State is immutable: the caller decides whether to keep the new one."""
    original = empty_state()
    updated, _ = original.merge([listing()], NOW)

    assert original.events == {}
    assert len(updated.events) == 1


def test_second_sighting_is_not_added_again() -> None:
    state, _ = empty_state().merge([listing()], NOW)
    state, delta = state.merge([listing()], LATER)

    assert not delta.added
    assert len(state.events) == 1


def test_last_seen_advances_but_first_seen_does_not() -> None:
    state, _ = empty_state().merge([listing()], NOW)
    state, _ = state.merge([listing()], LATER)

    event = state.events[listing().uid]
    assert event.first_seen == NOW
    assert event.last_seen == LATER


def test_time_change_is_rescheduled_not_duplicated() -> None:
    state, _ = empty_state().merge([listing(start="2026-08-22T19:30:00-04:00")], NOW)
    state, delta = state.merge([listing(start="2026-08-22T16:00:00-04:00")], LATER)

    assert len(state.events) == 1, "same day means same UID"
    assert len(delta.rescheduled) == 1
    was, event = delta.rescheduled[0]
    assert was.hour == 19, "the start subscribers still have"
    assert event.listing.start.hour == 16, "the start they should move to"


def test_blank_fields_do_not_erase_known_values() -> None:
    """A thinner source must not wipe metadata the venue page gave us."""
    state, _ = empty_state().merge([listing(category="MLS 2026", url="https://example.com/a")], NOW)
    state, _ = state.merge([listing(category="", url="")], LATER)

    event = state.events[listing().uid]
    assert event.listing.category == "MLS 2026"
    assert event.listing.url == "https://example.com/a"


def test_disappearing_future_event_is_delisted() -> None:
    state, _ = empty_state().merge(
        [listing(), listing(title="Other", start="2026-08-23T19:30:00-04:00")], NOW
    )
    _, delta = state.merge([listing()], LATER)

    assert [ev.listing.title for ev in delta.delisted] == ["Other"]


def test_past_events_are_never_delisted() -> None:
    """The venue page drops events once played; that is not a cancellation."""
    state, _ = empty_state().merge([listing(start="2026-07-01T19:30:00-04:00")], NOW)
    _, delta = state.merge([], LATER)

    assert not delta.delisted


def test_brand_new_event_is_not_also_delisted() -> None:
    _, delta = empty_state().merge([listing()], NOW)

    assert len(delta.added) == 1
    assert not delta.delisted


def test_upcoming_excludes_the_past_and_sorts() -> None:
    state, _ = empty_state().merge(
        [
            listing(title="C", start="2026-08-29T19:30:00-04:00"),
            listing(title="A", start="2026-07-01T19:30:00-04:00"),
            listing(title="B", start="2026-08-22T19:30:00-04:00"),
        ],
        NOW,
    )
    assert [ev.listing.title for ev in state.upcoming(NOW)] == ["B", "C"]


def test_upcoming_honours_the_week_horizon() -> None:
    state, _ = empty_state().merge(
        [
            listing(title="soon", start="2026-08-03T19:30:00-04:00"),
            listing(title="later", start="2026-09-30T19:30:00-04:00"),
        ],
        NOW,
    )
    assert [ev.listing.title for ev in state.upcoming(NOW, weeks=2)] == ["soon"]


def test_all_sorted_includes_the_past() -> None:
    state, _ = empty_state().merge(
        [
            listing(title="past", start="2026-07-01T19:30:00-04:00"),
            listing(title="future", start="2026-08-22T19:30:00-04:00"),
        ],
        NOW,
    )
    assert [ev.listing.title for ev in state.all_sorted()] == ["past", "future"]


# --- persistence ---


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "events.json"
    original, _ = State(path=path, events={}).merge(
        [listing(), listing(title="Other", time_known=False)], NOW
    )
    original.save()

    reloaded = State.load(path)
    assert reloaded.events.keys() == original.events.keys()
    for uid, event in original.events.items():
        assert reloaded.events[uid] == event


def test_missing_file_loads_empty(tmp_path: Path) -> None:
    assert State.load(tmp_path / "absent.json").events == {}


def test_on_disk_schema_is_flat(tmp_path: Path) -> None:
    """Field names are a compatibility surface; existing state must keep loading."""
    path = tmp_path / "events.json"
    state, _ = State(path=path, events={}).merge([listing()], NOW)
    state.save()

    raw = json.loads(path.read_text())
    (record,) = raw.values()
    assert set(record) == {
        "uid",
        "title",
        "category",
        "start",
        "time_known",
        "url",
        "first_seen",
        "last_seen",
    }


def test_legacy_record_still_loads(tmp_path: Path) -> None:
    path = tmp_path / "events.json"
    path.write_text(
        json.dumps(
            {
                "0c2001e4ef264570@si-stadium": {
                    "uid": "0c2001e4ef264570@si-stadium",
                    "title": "NYCFC vs. Santos Laguna",
                    "category": "LEAGUES CUP",
                    "start": "2026-08-06T19:30:00-04:00",
                    "time_known": True,
                    "url": "https://example.com/e",
                    "first_seen": "2026-08-06T17:07:57-04:00",
                    "last_seen": "2026-08-06T17:07:57-04:00",
                }
            }
        )
    )
    (event,) = State.load(path).events.values()
    assert isinstance(event, Event)
    assert event.listing.title == "NYCFC vs. Santos Laguna"
    assert event.listing.start.hour == 19
