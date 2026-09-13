"""Tests for the backtest harness itself.

A backtest that is wrong is worse than no backtest: it produces confident
numbers that justify shipping a broken model.
"""

import numpy as np
import pandas as pd
import pytest

from seedman.backtest import (
    _greedy_lineup,
    apply_rate_model,
    baseline_projections,
    start_the_best_table,
    survival_outcomes,
)
from seedman.config import LeagueConfig


def test_baselines_never_use_the_week_being_predicted():
    """A baseline that peeks would make the model look worse than it is."""
    actuals = pd.DataFrame(
        {
            "player_id": ["a"] * 4,
            "week": [1, 2, 3, 4],
            "actual": [10.0, 20.0, 30.0, 999.0],
        }
    )
    out = baseline_projections(actuals, week=4, prior_actuals=pd.DataFrame())
    row = out[out["player_id"] == "a"].iloc[0]
    assert row["season_to_date"] == pytest.approx(20.0)  # mean of 10/20/30
    assert row["last_week"] == pytest.approx(30.0)  # week 3, not week 4


def test_survival_outcome_eliminates_the_lowest_score():
    strategy = np.array([50.0, 50.0, 50.0])
    field = np.array([[60.0, 70.0], [60.0, 70.0], [60.0, 70.0]])
    weeks, won = survival_outcomes(strategy, field)
    assert weeks == 0 and not won


def test_survival_outcome_survives_when_never_last():
    """Surviving week 2 needs a score above the *remaining* rival, not the cut
    one -- the bar rises every week as the field thins."""
    strategy = np.array([65.0, 75.0])
    field = np.array([[60.0, 70.0], [60.0, 70.0]])
    weeks, won = survival_outcomes(strategy, field)
    assert weeks == 2


def test_eliminated_opponents_stop_competing():
    """Once the weak rival is gone, the bar rises -- that is the whole format."""
    strategy = np.array([65.0, 65.0])
    field = np.array([[10.0, 70.0], [10.0, 70.0]])
    weeks, won = survival_outcomes(strategy, field)
    # Week 1 the 10-scorer is cut; week 2 only the 70 remains and we lose.
    assert weeks == 1


def test_outlasting_everyone_counts_as_a_win():
    strategy = np.array([100.0, 100.0])
    field = np.array([[10.0], [10.0]])
    weeks, won = survival_outcomes(strategy, field)
    assert won


def test_winning_is_not_scored_as_a_short_season():
    """Winning in week 1 must not read as worse than being cut in week 2."""
    field = np.array([[10.0], [10.0], [10.0]])
    weeks, won = survival_outcomes(np.array([100.0, 100.0, 100.0]), field)
    assert won and weeks == 3


def test_greedy_respects_the_used_ledger(config: LeagueConfig, projections):
    week_one = projections[projections["week"] == 1]
    lineup = _greedy_lineup(config, week_one, used={"QB0", "RB0"})
    assert not {"QB0", "RB0"} & set(lineup["player_id"])
    assert len(lineup) == len(config.slots)


def test_greedy_respects_the_team_cap(config: LeagueConfig):
    rows = [
        {"player_id": f"{pos}{i}", "name": f"{pos}{i}", "position": pos,
         "team": "AAA" if i == 0 else f"T{i}", "opponent": "ZZZ", "week": 1,
         "mean": 30.0 - i, "sd": 5.0, "availability": 1.0, "conditional_mean": 30.0 - i,
         "implied_team_total": 23.0, "report_status": ""}
        for pos in ("QB", "RB", "WR") for i in range(4)
    ]
    lineup = _greedy_lineup(config, pd.DataFrame(rows), used=set(), max_per_team=2)
    assert list(lineup["team"]).count("AAA") <= 2


def test_rate_model_is_monotone_in_current_form():
    """More points per game, with everything else equal, must project higher."""
    rates = pd.DataFrame(
        {
            "position": ["WR", "WR"],
            "games_cur": [4.0, 4.0],
            "mean_cur": [5.0, 15.0],
            "games_pri": [10.0, 10.0],
            "mean_pri": [8.0, 8.0],
        }
    )
    params = {"prior_season_weight": 2.0, "replacement_weight": 3.0, "replacement_quantile": 0.2}
    out = apply_rate_model(rates, {"WR": pd.Series([1.0, 2.0, 3.0, 8.0, 20.0])}, params)
    assert out.iloc[1] > out.iloc[0]
    assert out.dtype == float


def test_shrinkage_pulls_a_thin_record_toward_replacement():
    rates = pd.DataFrame(
        {"position": ["WR"], "games_cur": [1.0], "mean_cur": [30.0],
         "games_pri": [0.0], "mean_pri": [0.0]}
    )
    params = {"prior_season_weight": 2.0, "replacement_weight": 3.0, "replacement_quantile": 0.2}
    out = apply_rate_model(rates, {"WR": pd.Series([1.0, 2.0, 3.0, 8.0, 20.0])}, params)
    assert out.iloc[0] < 15.0, "one enormous game must not project as a 30-point player"


def test_decision_metric_prefers_a_better_ranking():
    weeks = 6
    rows = pd.DataFrame(
        {
            "season": [2024] * (10 * weeks),
            "week": sum(([w] * 10 for w in range(1, weeks + 1)), []),
            "position": ["WR"] * (10 * weeks),
            "actual": list(range(10)) * weeks,
            "good": list(range(10)) * weeks,
            "bad": list(range(9, -1, -1)) * weeks,
        }
    )
    table = start_the_best_table(rows, ["good", "bad"])
    by_method = table.set_index("method")["avg_points_of_pick"]
    assert by_method["good"] > by_method["bad"]
