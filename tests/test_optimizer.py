import pandas as pd
import pytest

from seedman.config import LeagueConfig
from seedman.optimize import SurvivorOptimizer
from seedman.survival import FieldModel

FIELD = FieldModel(mean=45.0, sd=10.0)


def solve(config, projections, **kwargs):
    optimizer = SurvivorOptimizer(config, projections, field_model=FIELD, **kwargs)
    return optimizer.solve()


def test_every_slot_is_filled_every_week(config: LeagueConfig, projections):
    plan = solve(config, projections)
    assert plan.status == "Optimal"
    assert len(plan.weeks) == 4
    for week in plan.weeks:
        assert sorted(p["slot"] for p in week.picks) == ["QB", "RB", "WR"]


def test_no_player_is_started_twice(config: LeagueConfig, projections):
    """The defining constraint of a survivor league."""
    plan = solve(config, projections)
    used = [pick["player_id"] for week in plan.weeks for pick in week.picks]
    assert len(used) == len(set(used))


def test_reuse_limit_above_one_permits_repeats(config: LeagueConfig, projections):
    from dataclasses import replace

    relaxed = replace(config, survival=replace(config.survival, player_reuse_limit=2))
    plan = solve(relaxed, projections)
    counts = pd.Series(
        [pick["player_id"] for week in plan.weeks for pick in week.picks]
    ).value_counts()
    assert counts.max() == 2


def test_already_burned_players_never_reappear(config: LeagueConfig, projections):
    burned = {"QB0", "QB1", "RB0"}
    plan = solve(config, projections, used_players=burned)
    chosen = {pick["player_id"] for week in plan.weeks for pick in week.picks}
    assert not (chosen & burned)


def test_best_players_are_used_when_only_one_week_is_planned(config: LeagueConfig, projections):
    """With no future to save for, the optimum is simply the best available."""
    single_week = projections[projections["week"] == 1]
    plan = solve(config, single_week)
    assert {p["player_id"] for p in plan.weeks[0].picks} == {"QB0", "RB0", "WR0"}


def test_team_cap_prevents_stacking(config: LeagueConfig):
    """A lineup that could take three players from one team must not."""
    rows = []
    for position, base in (("QB", 30.0), ("RB", 30.0), ("WR", 30.0)):
        # The best option at every slot sits on one team.
        for rank in range(4):
            rows.append(
                {
                    "player_id": f"{position}{rank}",
                    "name": f"{position}{rank}",
                    "position": position,
                    "team": "AAA" if rank == 0 else f"T{rank}",
                    "opponent": "ZZZ",
                    "week": 1,
                    "mean": base - rank,
                    "sd": 5.0,
                    "availability": 1.0,
                    "conditional_mean": base - rank,
                    "implied_team_total": 23.0,
                    "report_status": "",
                }
            )
    frame = pd.DataFrame(rows)

    capped = solve(config, frame, max_per_team=2, max_per_game=6)
    teams = [p["team"] for p in capped.weeks[0].picks]
    assert teams.count("AAA") <= 2

    uncapped = solve(config, frame, max_per_team=0, max_per_game=0)
    assert [p["team"] for p in uncapped.weeks[0].picks].count("AAA") == 3


def test_survival_weighting_never_loses_to_flat_weights(config: LeagueConfig, projections):
    """The guarantee that makes the iterative reweighting safe to ship.

    Maximising a linearisation of a concave objective over an integer feasible
    set overshoots and can oscillate between two lineups. The solver evaluates
    every iterate against the true survival objective and returns the best, and
    since the first iterate uses flat weights, the answer can never be worse
    than plain expected-points maximisation.
    """
    survival = solve(config, projections, use_survival_weights=True)
    points = solve(config, projections, use_survival_weights=False)
    assert survival.survival_objective >= points.survival_objective - 1e-9
    assert survival.cumulative_survival >= points.cumulative_survival - 1e-9


def test_point_weight_rises_as_the_league_shrinks(config: LeagueConfig, projections):
    """Fewer opponents means a higher cut line, so late points matter more."""
    plan = solve(config, projections)
    weights = [w.point_weight for w in plan.weeks]
    assert weights[-1] > weights[0]


def test_stars_are_saved_for_the_week_that_actually_needs_them(config: LeagueConfig, projections):
    """Point the danger at one specific week and the best players should go there."""
    dangerous_week = 3
    field = FieldModel(
        mean=30.0,
        sd=10.0,
        # Week 3's cut line sits right on top of a typical lineup; the other
        # weeks are a stroll.
        by_week={w: (75.0 if w == dangerous_week else 25.0, 10.0) for w in (1, 2, 3, 4)},
    )
    optimizer = SurvivorOptimizer(config, projections, field_model=field)
    plan = optimizer.solve()

    picks = {w.week: {p["player_id"] for p in w.picks} for w in plan.weeks}
    assert {"QB0", "RB0", "WR0"} <= picks[dangerous_week]

    by_week = {w.week: w.point_weight for w in plan.weeks}
    assert by_week[dangerous_week] == max(by_week.values())


def test_unavailable_players_are_not_candidates(config: LeagueConfig, projections):
    frame = projections.copy()
    frame.loc[frame["player_id"] == "QB0", "availability"] = 0.0
    plan = solve(config, frame)
    chosen = {p["player_id"] for w in plan.weeks for p in w.picks}
    assert "QB0" not in chosen


def test_empty_projections_report_cleanly(config: LeagueConfig):
    plan = solve(config, pd.DataFrame(columns=["week", "position", "player_id"]))
    assert plan.status == "no-projections"
    assert plan.weeks == []


def test_infeasible_pool_is_reported_not_crashed(config: LeagueConfig, projections):
    """Four weeks, one-use-only, but only two quarterbacks exist."""
    thin = projections[~projections["player_id"].isin({"QB2", "QB3", "QB4", "QB5"})]
    plan = solve(config, thin)
    assert plan.status != "Optimal"


def test_correlated_lineups_report_a_wider_spread(config: LeagueConfig):
    """Two same-team pass catchers must not look as safe as two independent ones."""
    from seedman.correlation import lineup_sd

    stacked = pd.DataFrame(
        [
            {"position": "QB", "team": "AAA", "opponent": "BBB", "sd": 8.0},
            {"position": "WR", "team": "AAA", "opponent": "BBB", "sd": 8.0},
        ]
    )
    split = pd.DataFrame(
        [
            {"position": "QB", "team": "AAA", "opponent": "BBB", "sd": 8.0},
            {"position": "WR", "team": "CCC", "opponent": "DDD", "sd": 8.0},
        ]
    )
    assert lineup_sd(stacked) > lineup_sd(split)


def test_plan_frames_render(config: LeagueConfig, projections):
    plan = solve(config, projections)
    assert len(plan.to_frame()) == 12
    summary = plan.summary_frame()
    assert list(summary.columns) == [
        "week", "projected", "sd", "cut_line", "survive_pct", "teams_alive", "point_weight",
    ]
    assert 0.0 <= plan.cumulative_survival <= 1.0


def test_hold_bars_a_player_this_week_but_not_later(config: LeagueConfig, projections):
    """Holding is not the same as burning: the player must still be usable later."""
    plan = solve(config, projections, hold_players={"QB0"})
    first = plan.weeks[0]
    assert "QB0" not in {p["player_id"] for p in first.picks}
    later = {p["player_id"] for w in plan.weeks[1:] for p in w.picks}
    assert "QB0" in later


def test_holding_every_option_at_a_slot_is_infeasible(config: LeagueConfig, projections):
    qbs = {f"QB{i}" for i in range(6)}
    plan = solve(config, projections, hold_players=qbs)
    assert plan.status != "Optimal"


def test_hold_and_used_are_different(config: LeagueConfig, projections):
    """`used` is permanent, `hold` is one week."""
    held = solve(config, projections, hold_players={"RB0"})
    burned = solve(config, projections, used_players={"RB0"})
    assert "RB0" in {p["player_id"] for w in held.weeks for p in w.picks}
    assert "RB0" not in {p["player_id"] for w in burned.weeks for p in w.picks}
