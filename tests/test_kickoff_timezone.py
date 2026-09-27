"""Kickoffs are Eastern; the process clock is whatever the machine says.

At 10:39 on a Sunday morning in New York a cloud container read 14:39 UTC,
every 1:00 PM kickoff parsed as ninety minutes past, and nine games' worth of
players silently left the pool. These pin the instant, not the digits.
"""

from __future__ import annotations

import pandas as pd

from seedman.projections import KICKOFF_TZ, _kicked_off, as_eastern

GAMES = pd.DataFrame(
    [
        {"gameday": "2026-09-24", "gametime": "20:15", "home_team": "GB", "away_team": "ATL"},
        {"gameday": "2026-09-27", "gametime": "13:00", "home_team": "BUF", "away_team": "LAC"},
        {"gameday": "2026-09-27", "gametime": "16:25", "home_team": "DAL", "away_team": "BAL"},
        {"gameday": "2026-09-28", "gametime": "20:15", "home_team": "CHI", "away_team": "PHI"},
    ]
)


def test_sunday_morning_in_new_york_locks_only_thursday():
    """The bug: 10:39 ET is 14:39 UTC, and 13:00 < 14:39 when nobody says which clock."""
    morning = pd.Timestamp("2026-09-27 10:39", tz=KICKOFF_TZ)
    assert _kicked_off(GAMES, morning) == {"GB", "ATL"}


def test_the_same_instant_expressed_in_utc_gives_the_same_answer():
    utc = pd.Timestamp("2026-09-27 14:39", tz="UTC")  # == 10:39 New York
    assert _kicked_off(GAMES, utc) == {"GB", "ATL"}


def test_a_naive_timestamp_is_taken_to_be_eastern():
    """Every hand-typed --as-of and every nflverse kickoff already means Eastern."""
    assert _kicked_off(GAMES, pd.Timestamp("2026-09-27 10:39")) == {"GB", "ATL"}
    assert _kicked_off(GAMES, pd.Timestamp("2026-09-27 13:00")) == {"GB", "ATL", "BUF", "LAC"}


def test_the_early_slate_locks_at_one_and_the_late_slate_does_not():
    at_two = pd.Timestamp("2026-09-27 14:00", tz=KICKOFF_TZ)
    assert _kicked_off(GAMES, at_two) == {"GB", "ATL", "BUF", "LAC"}
    assert "DAL" not in _kicked_off(GAMES, at_two)


def test_as_eastern_converts_an_aware_clock_and_localises_a_naive_one():
    aware = as_eastern(pd.Timestamp("2026-09-27 14:39", tz="UTC"))
    naive = as_eastern(pd.Timestamp("2026-09-27 10:39"))
    assert aware == naive
    assert str(aware.tz) == KICKOFF_TZ


def test_unparseable_kickoff_still_leaves_the_team_available():
    games = GAMES.copy()
    games.loc[1, "gametime"] = "TBD"
    assert "BUF" not in _kicked_off(games, pd.Timestamp("2026-09-27 23:00", tz=KICKOFF_TZ))
