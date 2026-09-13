import numpy as np
import pandas as pd
import pytest

from seedman.config import LeagueConfig
from seedman.projections import ProjectionModel, _mixture_moments

SCHEDULE = pd.DataFrame(
    [
        {"season": 2026, "week": 1, "home_team": "AAA", "away_team": "BBB",
         "home_score": 24, "away_score": 17, "spread_line": 3.0, "total_line": 44.0,
         "gameday": "2026-09-10"},
        {"season": 2026, "week": 2, "home_team": "AAA", "away_team": "CCC",
         "home_score": None, "away_score": None, "spread_line": 7.0, "total_line": 50.0,
         "gameday": "2026-09-17"},
        # BBB is on bye in week 2.
    ]
)


def _weekly(points_by_week: dict[int, float], position="WR", team="AAA") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "player_id": "00-0000001",
                "player_display_name": "Test Player",
                "position": position,
                "team": team,
                "season": 2026,
                "week": week,
                "receptions": 0,
                "receiving_yards": yards,
                "receiving_tds": 0,
            }
            for week, yards in points_by_week.items()
        ]
    )


@pytest.fixture
def model(config: LeagueConfig) -> ProjectionModel:
    return ProjectionModel(
        config,
        weekly_current=_weekly({1: 100.0}),
        weekly_prior=_weekly({w: 80.0 for w in range(1, 15)}),
        schedule=SCHEDULE,
        injuries=pd.DataFrame(),
        rosters=pd.DataFrame(),
    )


def test_implied_totals_follow_the_betting_line(model):
    """spread_line is from the home team's perspective; verified against
    2018-2025 results where implied totals track actual scoring nearly unbiased."""
    ctx = model.team_context(2).set_index("team")
    # total 50, spread +7 to the home side => 28.5 / 21.5
    assert ctx.loc["AAA", "implied_total"] == pytest.approx(28.5)
    assert ctx.loc["CCC", "implied_total"] == pytest.approx(21.5)


def test_teams_on_bye_are_absent_from_the_week(model):
    assert "BBB" not in set(model.team_context(2)["team"])


def test_players_on_bye_get_no_projection(config: LeagueConfig):
    bye_model = ProjectionModel(
        config,
        weekly_current=_weekly({1: 100.0}, team="BBB"),
        weekly_prior=_weekly({w: 80.0 for w in range(1, 15)}, team="BBB"),
        schedule=SCHEDULE,
        injuries=pd.DataFrame(),
        rosters=pd.DataFrame(),
    )
    projected = bye_model.project_weeks([2], as_of_week=2)
    assert projected.empty


def test_one_hot_game_barely_moves_a_shrunk_rate(model):
    """Early in a season the prior must dominate a single week of evidence."""
    rates = model._player_rates().set_index("player_id")
    rate = rates.loc["00-0000001", "rate"]
    # Current season says 10.0 ppg, prior season says 8.0, position prior is lower.
    assert 7.0 < rate < 9.0


def test_injury_report_cuts_the_projection(config: LeagueConfig):
    injuries = pd.DataFrame(
        [
            {"season": 2026, "week": 2, "team": "AAA", "gsis_id": "00-0000001",
             "report_status": "Doubtful", "practice_status": "Did Not Participate In Practice",
             "position": "WR"}
        ]
    )
    hurt = ProjectionModel(
        config,
        weekly_current=_weekly({1: 100.0}),
        weekly_prior=_weekly({w: 80.0 for w in range(1, 15)}),
        schedule=SCHEDULE,
        injuries=injuries,
        rosters=pd.DataFrame(),
    ).project_weeks([2], as_of_week=2)

    healthy = ProjectionModel(
        config,
        weekly_current=_weekly({1: 100.0}),
        weekly_prior=_weekly({w: 80.0 for w in range(1, 15)}),
        schedule=SCHEDULE,
        injuries=pd.DataFrame(),
        rosters=pd.DataFrame(),
    ).project_weeks([2], as_of_week=2)

    assert hurt.iloc[0]["availability"] < 0.1
    assert hurt.iloc[0]["mean"] < 0.2 * healthy.iloc[0]["mean"]


def test_mixture_variance_exceeds_on_field_variance():
    """A player who might not suit up at all is riskier than the same player who will."""
    certain_mean, certain_sd = _mixture_moments(1.0, 12.0, 6.0)
    doubtful_mean, doubtful_sd = _mixture_moments(0.6, 12.0, 6.0)

    assert certain_mean == 12.0 and certain_sd == 6.0
    assert doubtful_mean == pytest.approx(7.2)
    assert doubtful_sd > certain_sd


def test_never_playing_means_zero_points_and_zero_spread():
    assert _mixture_moments(0.0, 20.0, 8.0) == (0.0, 0.0)


def test_defense_projection_inverts_the_opponent_total(model):
    strong_spot = model._context_multiplier("DEF", own=20.0, opp=15.0)
    weak_spot = model._context_multiplier("DEF", own=20.0, opp=32.0)
    assert strong_spot > weak_spot


def test_offense_projection_follows_its_own_total(model):
    good = model._context_multiplier("WR", own=30.0, opp=20.0)
    bad = model._context_multiplier("WR", own=15.0, opp=20.0)
    assert good > bad
