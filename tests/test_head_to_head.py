"""Head-to-head format: make a bracket, then win two games.

The distinction these tests protect is the one that makes the whole league
different from a survivor pool. In survivor, banking a star for week 17 can get
you knocked out in September. Here nobody is eliminated weekly, so the playoff
weeks really are worth more -- but only up to the point where the record stops
being good enough to reach them.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from seedman.config import ConfigError, LeagueConfig, ScoringRules, Slot, SurvivalFormat
from seedman.optimize.solver import SurvivorOptimizer
from seedman.survival import (
    FieldModel,
    berth_sensitivity,
    playoff_cut_wins,
    playoff_opponent_moments,
    playoff_probability,
    poisson_binomial_pmf,
)


# ----------------------------------------------------------------------
# the arithmetic underneath
# ----------------------------------------------------------------------
def test_poisson_binomial_reduces_to_binomial_when_odds_are_equal():
    from scipy.stats import binom

    pmf = poisson_binomial_pmf([0.5] * 8)
    assert pmf == pytest.approx(binom.pmf(range(9), 8, 0.5), abs=1e-12)


def test_poisson_binomial_is_a_distribution():
    pmf = poisson_binomial_pmf([0.1, 0.9, 0.44, 0.77])
    assert pmf.sum() == pytest.approx(1.0)
    assert (pmf >= 0).all()


def test_certain_wins_and_losses_collapse_to_a_point_mass():
    assert poisson_binomial_pmf([1.0, 1.0, 1.0])[3] == pytest.approx(1.0)
    assert poisson_binomial_pmf([0.0, 0.0])[0] == pytest.approx(1.0)


def test_a_banked_win_reduces_what_is_left_to_earn():
    weeks = [0.5] * 10
    assert playoff_probability(weeks, 2, 7) > playoff_probability(weeks, 0, 7)


def test_playoff_probability_is_monotone_in_weekly_odds():
    weak = playoff_probability([0.4] * 12, 0, 7)
    strong = playoff_probability([0.6] * 12, 0, 7)
    assert strong > weak


def test_clinched_and_eliminated_are_certainties():
    assert playoff_probability([0.5] * 4, 9, 8) == 1.0
    assert playoff_probability([0.5] * 2, 0, 9) == 0.0


def test_more_berths_means_a_lower_bar():
    tight = playoff_cut_wins(14, 2, 12)
    loose = playoff_cut_wins(14, 8, 12)
    assert tight > loose


def test_berth_sensitivity_peaks_on_the_bubble_and_vanishes_once_settled():
    weeks = [0.5] * 12
    bubble = max(berth_sensitivity(weeks, 0, 6))
    locked = max(berth_sensitivity(weeks, 11, 1))     # already in
    hopeless = max(berth_sensitivity(weeks, 0, 13))   # cannot get there
    assert bubble > locked
    assert bubble > hopeless
    assert locked == pytest.approx(0.0, abs=1e-9)


def test_berth_sensitivity_is_the_exact_derivative():
    """P is linear in any one week's odds, so compare against a finite difference."""
    weeks = [0.45, 0.6, 0.5, 0.7, 0.35]
    analytic = berth_sensitivity(weeks, 1, 4)[2]
    step = 1e-6
    bumped = list(weeks)
    bumped[2] += step
    numeric = (
        playoff_probability(bumped, 1, 4) - playoff_probability(weeks, 1, 4)
    ) / step
    assert analytic == pytest.approx(numeric, rel=1e-4)


def test_playoff_opponent_is_better_than_average_but_not_absurdly_so():
    mean, sd = playoff_opponent_moments(80.0, 22.0, 6, 12)
    assert mean > 80.0
    # Selection acts on skill, not on one week's luck. Conditioning the whole
    # weekly spread would put this near 97 and make any amount of hoarding look
    # justified; only the between-team slice may be truncated.
    assert mean < 90.0
    assert sd < 22.0


def test_playoff_opponent_uplift_grows_with_the_skill_share():
    low, _ = playoff_opponent_moments(80.0, 22.0, 6, 12, between_team_share=0.05)
    high, _ = playoff_opponent_moments(80.0, 22.0, 6, 12, between_team_share=0.50)
    assert high > low


def test_everyone_qualifying_means_no_uplift():
    assert playoff_opponent_moments(80.0, 22.0, 12, 12) == (80.0, 22.0)


# ----------------------------------------------------------------------
# config
# ----------------------------------------------------------------------
def test_head_to_head_faces_one_opponent_and_never_shrinks_the_field():
    fmt = SurvivalFormat(mode="head_to_head", teams_remaining=12, as_of_week=1)
    assert fmt.opponents_at(2) == 1
    assert fmt.opponents_at(14) == 1
    assert fmt.teams_alive_at(14) == 12


def test_survivor_still_faces_everyone_left():
    fmt = SurvivalFormat(mode="survivor", teams_remaining=12, as_of_week=1)
    assert fmt.opponents_at(1) == 11
    assert fmt.teams_alive_at(5) == 8


def test_playoff_weeks_are_only_after_the_regular_season():
    fmt = SurvivalFormat(mode="head_to_head", regular_season_final_week=15, final_week=17)
    assert not fmt.is_playoff_week(15)
    assert fmt.is_playoff_week(16)
    assert fmt.is_playoff_week(17)


def test_a_survivor_league_has_no_playoff_weeks():
    fmt = SurvivalFormat(mode="survivor", regular_season_final_week=15)
    assert not fmt.is_playoff_week(17)


def test_bad_mode_is_rejected():
    with pytest.raises(ConfigError):
        SurvivalFormat(mode="ladder").validate()


def test_impossible_berth_count_is_rejected():
    with pytest.raises(ConfigError):
        SurvivalFormat(mode="head_to_head", teams_remaining=12, playoff_berths=12).validate()


# ----------------------------------------------------------------------
# end to end through the solver
# ----------------------------------------------------------------------
def _config(mode: str) -> LeagueConfig:
    return LeagueConfig(
        name="H2H Test",
        season=2026,
        slots=(Slot(name="QB", eligible=("QB",)), Slot(name="RB", eligible=("RB",))),
        scoring=ScoringRules(per_stat={"passing_yards": 0.04}),
        survival=SurvivalFormat(
            mode=mode,
            player_reuse_limit=1,
            teams_remaining=12,
            as_of_week=2,
            regular_season_final_week=5,
            final_week=7,
            playoff_berths=6,
        ),
    )


def _projections(weeks=range(2, 8), depth=40) -> pd.DataFrame:
    """A flat board: identical every week, so only *allocation* can differ."""
    rows = []
    for week in weeks:
        for position in ("QB", "RB"):
            for rank in range(depth):
                rows.append(
                    {
                        "player_id": f"{position}{rank}",
                        "name": f"{position} {rank}",
                        "position": position,
                        "team": f"T{rank}",
                        "opponent": f"O{rank}",
                        "week": week,
                        "mean": 25.0 - 1.5 * rank,
                        "sd": 6.0,
                        "availability": 0.95,
                        "implied_team_total": 23.0,
                        "report_status": "",
                    }
                )
    return pd.DataFrame(rows)


def _solve(mode: str, **kwargs):
    config = _config(mode)
    return SurvivorOptimizer(
        config,
        _projections(),
        field_model=FieldModel(mean=20.0, sd=8.0),
        **kwargs,
    ).solve()


def test_playoff_weeks_get_the_better_lineups():
    """The whole point: on a board that never changes, spend late, not early."""
    plan = _solve("head_to_head")
    by_week = {wp.week: wp.mean for wp in plan.weeks}
    playoffs = [by_week[6], by_week[7]]
    regular = [by_week[w] for w in (2, 3, 4, 5)]
    assert min(playoffs) > max(regular)


def test_head_to_head_back_loads_harder_than_survivor_does():
    """The formats disagree about the last week, and that is the whole point.

    Survivor does back-load somewhat on this fixture, because a flat field makes
    the late cut line (lowest of 6) a higher bar than the early one (lowest of
    11). What it will not do is concentrate the way head-to-head does, where the
    last two weeks are the only ones you must win outright.
    """
    def tilt(mode: str) -> float:
        by_week = {wp.week: wp.mean for wp in _solve(mode).weeks}
        late = np.mean([by_week[6], by_week[7]])
        early = np.mean([by_week[2], by_week[3]])
        return late - early

    assert tilt("head_to_head") > tilt("survivor")


def test_the_best_player_on_the_board_is_saved_for_the_bracket():
    plan = _solve("head_to_head")
    playoff_picks = {
        p["player_id"] for wp in plan.weeks if wp.phase == "PLAYOFF" for p in wp.picks
    }
    assert {"QB0", "RB0"} <= playoff_picks


def test_marginal_value_equalises_once_the_plan_is_optimal():
    """Not "playoffs are worth more" -- that is the input, not the output.

    Playoff weeks start out worth more per point, which is why they attract the
    stars. By the time the allocation is settled those weeks are comfortable and
    the regular-season games are close, so the marginal values converge. A large
    residual gap would mean points are still sitting in the wrong weeks.
    """
    plan = _solve("head_to_head")
    weights = [wp.point_weight for wp in plan.weeks]
    assert max(weights) / max(min(weights), 1e-9) < 4.0


def test_weeks_are_labelled_with_their_phase():
    plan = _solve("head_to_head")
    assert {wp.week: wp.phase for wp in plan.weeks}[7] == "PLAYOFF"
    assert {wp.week: wp.phase for wp in plan.weeks}[3] == "regular"


def test_head_to_head_reports_bracket_odds_not_survival():
    plan = _solve("head_to_head", wins_so_far=1, losses_so_far=0)
    assert plan.head_to_head
    assert 0.0 <= plan.playoff_probability <= 1.0
    assert plan.title_probability <= plan.playoff_probability
    assert plan.expected_wins >= 1.0
    assert "win_pct" in plan.summary_frame().columns
    assert "survive_pct" not in plan.summary_frame().columns


def test_wins_already_banked_raise_the_odds_of_qualifying():
    behind = _solve("head_to_head", wins_so_far=0, losses_so_far=1)
    ahead = _solve("head_to_head", wins_so_far=1, losses_so_far=0)
    assert ahead.playoff_probability > behind.playoff_probability


def test_the_one_use_rule_still_holds_across_the_whole_plan():
    plan = _solve("head_to_head")
    started = [p["player_id"] for wp in plan.weeks for p in wp.picks]
    assert len(started) == len(set(started))


def test_polish_never_makes_the_plan_worse():
    """The local search evaluates the real objective, so it can only improve."""
    config = _config("head_to_head")
    opt = SurvivorOptimizer(
        config, _projections(), field_model=FieldModel(mean=20.0, sd=8.0)
    )
    polished = opt.solve()
    unpolished = SurvivorOptimizer(
        config,
        _projections(),
        field_model=FieldModel(mean=20.0, sd=8.0),
        use_survival_weights=False,
    ).solve()
    assert opt._objective(polished) >= opt._objective(unpolished) - 1e-9


def test_polish_respects_the_stacking_caps():
    config = _config("head_to_head")
    opt = SurvivorOptimizer(
        config,
        _projections(),
        field_model=FieldModel(mean=20.0, sd=8.0),
        max_per_team=1,
    )
    plan = opt.solve()
    for wp in plan.weeks:
        teams = [p["team"] for p in wp.picks]
        assert len(teams) == len(set(teams))
