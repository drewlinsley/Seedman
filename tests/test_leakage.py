"""Guards against the failure mode that would make every backtest number a lie."""

import numpy as np
import pandas as pd
import pytest

from seedman.config import LeagueConfig
from seedman.projections import ProjectionModel

SCHEDULE = pd.DataFrame(
    [
        {"season": 2026, "week": w, "home_team": "AAA", "away_team": "BBB",
         "home_score": 30.0, "away_score": 10.0, "result": 20.0, "total": 40.0,
         "spread_line": 3.0, "total_line": 44.0, "gameday": f"2026-09-{w:02d}"}
        for w in range(1, 11)
    ]
)


def _stats(weeks, yards) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"player_id": "00-0000001", "player_display_name": "P", "position": "WR",
             "team": "AAA", "season": 2026, "week": w, "receptions": 5,
             "receiving_yards": yards, "receiving_tds": 0}
            for w in weeks
        ]
    )


def _model(config, through_week, current):
    return ProjectionModel(
        config,
        weekly_current=current,
        weekly_prior=pd.DataFrame(),
        schedule=SCHEDULE,
        injuries=pd.DataFrame(),
        rosters=pd.DataFrame(),
        through_week=through_week,
    )


def test_future_weeks_cannot_change_a_projection(config: LeagueConfig):
    """The decisive test: a monster week 6 must not alter the week-4 projection."""
    history_only = _stats([1, 2, 3], 50)
    with_future = pd.concat([history_only, _stats([6, 7, 8], 400)], ignore_index=True)

    a = _model(config, 3, history_only).project_weeks([4], as_of_week=4)
    b = _model(config, 3, with_future).project_weeks([4], as_of_week=4)

    assert not a.empty
    assert a["mean"].iloc[0] == pytest.approx(b["mean"].iloc[0])


def test_betting_lines_beyond_the_posting_window_are_hidden(config: LeagueConfig):
    model = _model(config, 2, _stats([1, 2], 50))
    # Books post ~4 weeks out, so week 9 has no line and falls back to the average.
    near = model.team_context(4)
    far = model.team_context(9)
    # Week 4 has a line: total 44, home favoured by 3 => 22 + 1.5.
    assert near.loc[near["team"] == "AAA", "implied_total"].iloc[0] == pytest.approx(23.5)
    assert near.loc[near["team"] == "BBB", "implied_total"].iloc[0] == pytest.approx(20.5)
    # Week 9 has none, so both sides collapse to the league average.
    assert far.loc[far["team"] == "AAA", "implied_total"].iloc[0] == pytest.approx(22.0)
    assert far.loc[far["team"] == "BBB", "implied_total"].iloc[0] == pytest.approx(22.0)


def test_scores_are_hidden_earlier_than_lines(config: LeagueConfig):
    """A line four weeks out is public; that game's final score is not."""
    model = _model(config, 2, _stats([1, 2], 50))
    played = model.schedule[
        (model.schedule["season"] == 2026) & (model.schedule["week"] <= 2)
    ]
    unplayed = model.schedule[
        (model.schedule["season"] == 2026) & (model.schedule["week"] > 2)
    ]
    assert played["home_score"].notna().all()
    assert unplayed["home_score"].isna().all()
    # ...but week 5's line is still visible.
    assert unplayed[unplayed["week"] == 5]["total_line"].notna().all()


def test_position_priors_do_not_fit_on_withheld_data(config: LeagueConfig):
    """With no prior season and a cutoff in force, fall back to constants."""
    from seedman.projections import FALLBACK_POSITION_MEAN

    model = _model(config, 3, _stats([1, 2, 3], 500))
    assert model.position_mean["WR"] == FALLBACK_POSITION_MEAN["WR"]


def test_no_cutoff_means_no_masking(config: LeagueConfig):
    """Live use must be unaffected by the backtest machinery."""
    model = _model(config, None, _stats([1, 2, 3], 50))
    assert model.schedule["home_score"].notna().all()
    assert model.schedule["total_line"].notna().all()


# ----------------------------------------------------------------------
# kickoff lock
# ----------------------------------------------------------------------
KICKOFF_SCHEDULE = pd.DataFrame(
    [
        {"season": 2026, "week": 1, "home_team": "EARLY", "away_team": "ALSOEARLY",
         "home_score": 20.0, "away_score": 17.0, "result": 3.0, "total": 37.0,
         "spread_line": 2.0, "total_line": 44.0,
         "gameday": "2026-09-10", "gametime": "20:15"},
        {"season": 2026, "week": 1, "home_team": "LATE", "away_team": "ALSOLATE",
         "home_score": None, "away_score": None, "result": None, "total": None,
         "spread_line": 1.0, "total_line": 44.0,
         "gameday": "2026-09-13", "gametime": "16:25"},
    ]
)


def _kickoff_model(config, as_of):
    return ProjectionModel(
        config,
        weekly_current=pd.DataFrame(),
        weekly_prior=pd.DataFrame(),
        schedule=KICKOFF_SCHEDULE,
        injuries=pd.DataFrame(),
        rosters=pd.DataFrame(),
        as_of=as_of,
    )


def test_teams_whose_game_started_are_unpickable(config: LeagueConfig):
    """A Thursday-night player cannot go in a Sunday-morning lineup.

    Missing this recommended a player whose game had already finished.
    """
    model = _kickoff_model(config, pd.Timestamp("2026-09-13 09:52"))
    teams = set(model.team_context(1)["team"])
    assert teams == {"LATE", "ALSOLATE"}


def test_nothing_is_locked_before_the_first_kickoff(config: LeagueConfig):
    model = _kickoff_model(config, pd.Timestamp("2026-09-09 08:00"))
    assert len(model.team_context(1)) == 4


def test_everything_locks_once_the_last_game_starts(config: LeagueConfig):
    model = _kickoff_model(config, pd.Timestamp("2026-09-13 23:00"))
    assert model.team_context(1).empty


def test_no_as_of_means_no_kickoff_filtering(config: LeagueConfig):
    """Backtests replay whole weeks and must not have players silently removed."""
    model = _kickoff_model(config, None)
    assert len(model.team_context(1)) == 4


def test_unparseable_kickoff_time_leaves_the_team_available(config: LeagueConfig):
    """Wrongly dropping an available player is worse than offering a locked one."""
    schedule = KICKOFF_SCHEDULE.copy()
    schedule.loc[0, "gametime"] = "not a time"
    model = ProjectionModel(
        config,
        weekly_current=pd.DataFrame(),
        weekly_prior=pd.DataFrame(),
        schedule=schedule,
        injuries=pd.DataFrame(),
        rosters=pd.DataFrame(),
        as_of=pd.Timestamp("2026-09-13 09:52"),
    )
    assert "EARLY" in set(model.team_context(1)["team"])
