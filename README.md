# Sports Illustrated Stadium events calendar

An **unofficial** iCalendar feed of events at Sports Illustrated Stadium
(Harrison, NJ), for anyone who wants warning about the traffic. Not affiliated
with, authorized by, or endorsed by the stadium, its operators, or any trademark
holder.

The stadium publishes no calendar you can subscribe to. This is that calendar.

## Subscribe

### **[→ Add to calendar](https://congee.github.io/sports-illustrated-stadium-events/)**

One click on macOS and iOS. Everywhere else, paste this URL into your calendar
app:

```
https://congee.github.io/sports-illustrated-stadium-events/calendar.ics
```

- **Apple Calendar** — the link above, or File → New Calendar Subscription.
- **Google Calendar** — Other calendars → **From URL**. Google has no
  `webcal://` handler, so the one-click link does not help here.
- **Outlook** — Add calendar → Subscribe from web.

Subscribe, don't import. An import is a one-time snapshot that never updates.

## What you get

- Every event the venue currently lists, plus everything it has listed since
  this feed started — the venue's own page only shows a rolling few weeks.
- An alert two hours ahead, enough warning to route around inbound traffic.
- Events marked free rather than busy, so they will not block your day.
- A weekly refresh. A rescheduled event moves in place instead of duplicating.

Start times are as published. **End times are estimated** from the event
category — soccer 120 minutes, concerts 180, and so on — because no available
source publishes one. Times may be wrong or change without notice.

## Development

Stdlib only at runtime. `uv sync` installs pytest into `.venv`, which is how an
editor's ty resolves the test imports.

```sh
SI_STADIUM_DIR=data python3 si_stadium_calendar.py --ics-out docs/calendar.ics
uvx ruff check && uvx --with pytest pytest
uvx --with pytest ty check
```

`--help` lists the flags.

`tests/test_uid.py` pins the UIDs already published. A failure there is a
migration hazard, not a test to update: subscribers key their existing calendar
entries off the UID, so a change turns every event into a duplicate plus an
orphan.
