"""Barring early games for one week.

Starting a Thursday player locks the roster at his kickoff, which costs three
days of injury news on the other five. That is a real option worth pricing, and
it is a *this week only* constraint -- the same player is perfectly fine in week
10, so it must not leak into the rest of the plan the way `used_players` does.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import pytest

from seedman.cli import _teams_kicking_off_before


class _Client:
    def __init__(self, games: pd.DataFrame) -> None:
        self._games = games

    def schedule(self) -> pd.DataFrame:
        return self._games


def _schedule() -> pd.DataFrame:
    return pd.DataFrame(
        [
            # Thursday night
            {"season": 2026, "week": 2, "gameday": "2026-09-17", "weekday": "Thursday",
             "gametime": "20:15", "away_team": "DET", "home_team": "BUF"},
            # Sunday early
            {"season": 2026, "week": 2, "gameday": "2026-09-20", "weekday": "Sunday",
             "gametime": "13:00", "away_team": "NO", "home_team": "BAL"},
            # Sunday late
            {"season": 2026, "week": 2, "gameday": "2026-09-20", "weekday": "Sunday",
             "gametime": "16:25", "away_team": "MIA", "home_team": "SF"},
            # Monday night
            {"season": 2026, "week": 2, "gameday": "2026-09-21", "weekday": "Monday",
             "gametime": "20:15", "away_team": "NYG", "home_team": "LA"},
            # A different week, which must never be touched
            {"season": 2026, "week": 3, "gameday": "2026-09-24", "weekday": "Thursday",
             "gametime": "20:15", "away_team": "SEA", "home_team": "ARI"},
        ]
    )


def test_sunday_bars_exactly_the_thursday_game():
    teams = _teams_kicking_off_before(_Client(_schedule()), 2026, 2, "sunday")
    assert teams == {"DET", "BUF"}


def test_the_cutoff_only_looks_at_the_week_being_planned():
    """Week 3's Thursday game is somebody else's problem."""
    teams = _teams_kicking_off_before(_Client(_schedule()), 2026, 2, "sunday")
    assert "SEA" not in teams and "ARI" not in teams


def test_monday_bars_everything_before_it():
    teams = _teams_kicking_off_before(_Client(_schedule()), 2026, 2, "monday")
    assert teams == {"DET", "BUF", "NO", "BAL", "MIA", "SF"}


def test_the_monday_game_itself_is_never_barred_by_its_own_day():
    teams = _teams_kicking_off_before(_Client(_schedule()), 2026, 2, "monday")
    assert "NYG" not in teams and "LA" not in teams


def test_an_explicit_timestamp_splits_the_sunday_slate():
    teams = _teams_kicking_off_before(
        _Client(_schedule()), 2026, 2, "2026-09-20 16:00"
    )
    assert teams == {"DET", "BUF", "NO", "BAL"}


def test_thursday_bars_nothing_because_it_is_the_first_game():
    assert _teams_kicking_off_before(_Client(_schedule()), 2026, 2, "thursday") == set()


def test_a_weekday_with_no_games_bars_nothing():
    assert _teams_kicking_off_before(_Client(_schedule()), 2026, 2, "saturday") == set()


def test_a_week_with_no_games_is_not_an_error():
    assert _teams_kicking_off_before(_Client(_schedule()), 2026, 9, "sunday") == set()
