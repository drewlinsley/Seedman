"""Forcing a player into this week, and paying for it.

The mirror of `--hold`. Both exist for the same reason: you know something the
model does not, and the honest interface is to let you say so and then be told
the price -- not to argue, and not to silently ignore you.
"""

from __future__ import annotations

import pandas as pd
import pytest

from seedman.config import LeagueConfig, ScoringRules, Slot, SurvivalFormat
from seedman.optimize.solver import SurvivorOptimizer
from seedman.survival import FieldModel


def _config() -> LeagueConfig:
    return LeagueConfig(
        name="Force Test",
        season=2026,
        slots=(Slot(name="QB", eligible=("QB",)), Slot(name="RB", eligible=("RB",))),
        scoring=ScoringRules(per_stat={"passing_yards": 0.04}),
        survival=SurvivalFormat(
            mode="head_to_head", player_reuse_limit=1, teams_remaining=12,
            as_of_week=2, regular_season_final_week=5, final_week=7, playoff_berths=6,
        ),
    )


def _projections(depth=20) -> pd.DataFrame:
    rows = []
    for week in range(2, 8):
        for position in ("QB", "RB"):
            for rank in range(depth):
                rows.append({
                    "player_id": f"{position}{rank}", "name": f"{position} {rank}",
                    "position": position, "team": f"T{rank}", "opponent": f"O{rank}",
                    "week": week, "mean": 25.0 - 1.2 * rank, "sd": 6.0,
                    "availability": 0.95, "implied_team_total": 23.0, "report_status": "",
                })
    return pd.DataFrame(rows)


def _solve(**kwargs):
    return SurvivorOptimizer(
        _config(), _projections(), field_model=FieldModel(mean=20.0, sd=8.0), **kwargs
    ).solve()


def _week(plan, week):
    return next(wp for wp in plan.weeks if wp.week == week)


def test_a_forced_player_actually_starts():
    plan = _solve(start_players={"QB9"})
    assert "QB9" in {p["player_id"] for p in _week(plan, 2).picks}


def test_forcing_a_weak_player_costs_something():
    """Otherwise the flag is just theatre."""
    free = _solve()
    forced = _solve(start_players={"QB19"})
    assert _week(forced, 2).mean < _week(free, 2).mean


def test_forcing_someone_the_plan_already_wanted_costs_nothing():
    free = _solve()
    already = _week(free, 2).picks[0]["player_id"]
    forced = _solve(start_players={already})
    assert _week(forced, 2).mean == pytest.approx(_week(free, 2).mean, abs=1e-6)


def test_several_players_can_be_forced_at_once():
    plan = _solve(start_players={"QB7", "RB11"})
    assert {"QB7", "RB11"} <= {p["player_id"] for p in _week(plan, 2).picks}


def test_a_forced_player_is_not_burned_in_later_weeks_too():
    """He is started once, this week, and then he is spent -- like anyone else."""
    plan = _solve(start_players={"QB9"})
    later = [p["player_id"] for wp in plan.weeks[1:] for p in wp.picks]
    assert "QB9" not in later


def test_the_polish_cannot_undo_a_forced_start():
    """The local search reshuffles weeks; it must treat this as a constraint."""
    plan = _solve(start_players={"QB15"})
    assert "QB15" in {p["player_id"] for p in _week(plan, 2).picks}


def test_forcing_and_holding_the_same_player_is_an_error():
    """Contradictory instructions must not be silently resolved either way.

    Left alone the two constraints make the MILP infeasible, which surfaces as an
    empty plan and a status nobody reads. Picking a winner is worse: it could
    start a player you meant to sit, and you would not find out until kickoff.
    """
    with pytest.raises(ValueError, match="both held and forced"):
        _solve(hold_players={"QB0"}, start_players={"QB0"})


def test_forcing_still_respects_the_one_use_rule():
    plan = _solve(start_players={"QB3", "RB3"})
    started = [p["player_id"] for wp in plan.weeks for p in wp.picks]
    assert len(started) == len(set(started))


# ----------------------------------------------------------------------
# the polish must not overrule you
# ----------------------------------------------------------------------
def test_the_polish_cannot_reinstate_a_held_player():
    """The bug this test exists for: it did exactly that, silently.

    The MILP honoured the hold, and then the local search -- which reshuffles
    players between weeks on its own judgement -- swapped him straight back in.
    The printed lineup contradicted the "holding..." line three rows above it.
    """
    plan = _solve(hold_players={"QB0", "RB0"})
    week2 = {p["player_id"] for p in _week(plan, 2).picks}
    assert not ({"QB0", "RB0"} & week2)


def test_a_held_player_is_still_used_in_a_later_week():
    """A hold is one week, not a ban; the polish must not turn it into one."""
    plan = _solve(hold_players={"QB0"})
    later = {p["player_id"] for wp in plan.weeks[1:] for p in wp.picks}
    assert "QB0" in later


def test_holds_and_starts_both_survive_together():
    plan = _solve(hold_players={"QB0"}, start_players={"QB5"})
    week2 = {p["player_id"] for p in _week(plan, 2).picks}
    assert "QB5" in week2 and "QB0" not in week2
