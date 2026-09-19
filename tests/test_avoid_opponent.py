"""Barring players by who they FACE, not by who they are.

A matchup read the model does not have -- a defensive front that looks harder
than the season-long numbers say -- is still a real read. `--avoid-opponent`
spends it for one week without writing the player off for the season, which is
the distinction that matters in a format where every player starts once.
"""

from __future__ import annotations

import pandas as pd
import pytest

from seedman.cli import _teams_facing


class _Client:
    def __init__(self, games):
        self._games = pd.DataFrame(
            games, columns=["season", "week", "home_team", "away_team"]
        )

    def schedule(self):
        return self._games


WEEK2 = _Client(
    [
        (2026, 2, "HOU", "CIN"),
        (2026, 2, "NE", "PIT"),
        (2026, 2, "CHI", "MIN"),
        (2026, 1, "CIN", "CLE"),  # a different week must not leak in
    ]
)


def test_bars_the_team_that_draws_the_named_front():
    assert _teams_facing(WEEK2, 2026, 2, ["CIN"]) == {"HOU"}


def test_works_from_either_side_of_the_fixture():
    """CIN is the away team here; NE's opponent PIT is the home one."""
    assert _teams_facing(WEEK2, 2026, 2, ["PIT"]) == {"NE"}


def test_the_named_team_itself_is_not_barred():
    """You are avoiding their defense, not their offense."""
    assert "CIN" not in _teams_facing(WEEK2, 2026, 2, ["CIN"])


def test_several_opponents_at_once():
    assert _teams_facing(WEEK2, 2026, 2, ["CIN", "MIN"]) == {"HOU", "CHI"}


def test_lower_case_and_stray_whitespace_resolve():
    assert _teams_facing(WEEK2, 2026, 2, [" cin "]) == {"HOU"}


def test_only_the_week_being_planned_counts():
    """CIN plays CLE in week 1; planning week 2 must not bar Cleveland."""
    assert _teams_facing(WEEK2, 2026, 2, ["CIN"]) == {"HOU"}


def test_a_team_on_bye_raises_rather_than_barring_nobody():
    """Silence would hand back the very lineup you were trying to change."""
    with pytest.raises(SystemExit, match="DAL"):
        _teams_facing(WEEK2, 2026, 2, ["DAL"])


def test_a_misspelled_team_code_raises():
    with pytest.raises(SystemExit, match="CINCINNATI"):
        _teams_facing(WEEK2, 2026, 2, ["Cincinnati"])


def test_the_error_names_every_unknown_team_not_just_the_first():
    with pytest.raises(SystemExit) as err:
        _teams_facing(WEEK2, 2026, 2, ["DAL", "SEA"])
    assert "DAL" in str(err.value) and "SEA" in str(err.value)


def test_a_valid_team_alongside_an_unknown_one_still_raises():
    """Partial success is the dangerous case: half the bar, silently applied."""
    with pytest.raises(SystemExit, match="DAL"):
        _teams_facing(WEEK2, 2026, 2, ["CIN", "DAL"])


def test_blank_entries_are_ignored_rather_than_treated_as_a_team():
    assert _teams_facing(WEEK2, 2026, 2, ["CIN", "", "  "]) == {"HOU"}
