"""Following the venue's Webflow pagination; page 1 is only the nearest events."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

import si_stadium_calendar as sic
from si_stadium_calendar import EVENTS_URL, collect, venue_pages

NEXT = (
    '<div role="navigation" aria-label="List" class="w-pagination-wrapper">'
    '<a href="{href}" aria-label="Next Page" class="w-pagination-next">'
    '<div class="w-inline-block">Next</div></a>'
    '<link rel="prerender" href="{href}"/></div>'
)


def item(when: str, title: str, category: str = "MLS 2026") -> str:
    return (
        '<div role="listitem" class="w-dyn-item">'
        f'<h5 class="heading-21">{when}</h5>'
        f'<h1 class="heading-events-main">{category}</h1>'
        f'<h3 class="heading-22">{title}</h3>'
        '<a href="https://sitickets.com/event/x" class="w-button">Get TICKETS</a>'
        "</div>"
    )


def serve(pages: Mapping[str, str], monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stub fetch out of the module and record the URLs it was asked for."""
    asked: list[str] = []

    def fake_fetch(url: str, timeout: int = 30) -> str:
        asked.append(url)
        return pages[url]

    monkeypatch.setattr(sic, "fetch", fake_fetch)
    return asked


def test_follows_next_until_it_runs_out(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = {
        EVENTS_URL: item("Oct 30, 2026 5:15 PM", "WXV") + NEXT.format(href="?p=2"),
        f"{EVENTS_URL}?p=2": item("Nov 1, 2026 5:00 PM", "Utah Royals"),
    }
    asked = serve(pages, monkeypatch)

    assert len(list(venue_pages(EVENTS_URL))) == 2
    assert asked == [EVENTS_URL, f"{EVENTS_URL}?p=2"]


def test_relative_next_resolves_against_the_current_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = {
        EVENTS_URL: NEXT.format(href="?bfb23ded_page=2"),
        f"{EVENTS_URL}?bfb23ded_page=2": "",
    }
    asked = serve(pages, monkeypatch)

    list(venue_pages(EVENTS_URL))
    assert asked[1] == f"{EVENTS_URL}?bfb23ded_page=2"


def test_entity_escaped_next_is_unescaped(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = {
        EVENTS_URL: NEXT.format(href="?a=1&amp;page=2"),
        f"{EVENTS_URL}?a=1&page=2": "",
    }
    asked = serve(pages, monkeypatch)

    list(venue_pages(EVENTS_URL))
    assert asked[1] == f"{EVENTS_URL}?a=1&page=2"


def test_a_page_pointing_at_itself_does_not_spin(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = {EVENTS_URL: NEXT.format(href=EVENTS_URL)}
    asked = serve(pages, monkeypatch)

    assert len(list(venue_pages(EVENTS_URL))) == 1
    assert asked == [EVENTS_URL]


def test_an_endless_chain_stops_at_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    class Endless(dict[str, str]):
        """Every next link is one no page has used, so only the cap can stop it."""

        def __missing__(self, key: str) -> str:
            return NEXT.format(href=f"{key}&p")

    serve(Endless(), monkeypatch)

    assert len(list(venue_pages(EVENTS_URL))) == sic.MAX_PAGES


def test_prerender_link_alone_is_not_a_next_page(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = {EVENTS_URL: '<link rel="prerender" href="?p=2" class="w-pagination-next"/>'}
    asked = serve(pages, monkeypatch)

    assert len(list(venue_pages(EVENTS_URL))) == 1
    assert asked == [EVENTS_URL]


def test_collect_gathers_every_page(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = {
        EVENTS_URL: item("Oct 30, 2026 5:15 PM", "WXV") + NEXT.format(href="?p=2"),
        f"{EVENTS_URL}?p=2": (
            item("Nov 1, 2026 5:00 PM", "Gotham FC vs Utah Royals", "NWSL 2026")
            + item("Nov 7, 2026 4:00 PM", "New York vs. Atlanta")
        ),
    }
    serve(pages, monkeypatch)

    titles = [listing.title for listing in collect(None)]
    assert titles == ["WXV", "Gotham FC vs Utah Royals", "New York vs. Atlanta"]
