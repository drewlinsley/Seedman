"""Wiring: data in, season plan out.

Keeps the moving parts (nflverse fetch, scoring, projection, field calibration,
optimisation) behind one call so the CLI and the tests exercise the same path.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .config import LeagueConfig
from .data import NflverseClient, current_week
from .injury import AvailabilityModel
from .league.base import LeagueState
from .optimize import SeasonPlan, SurvivorOptimizer
from .projections import ProjectionModel
from .survival import FieldModel

log = logging.getLogger(__name__)

# Spread between managers' lineup-building skill and luck, on top of the
# on-field randomness already in each player's projection. Without it the field
# looks unrealistically predictable and every survival number is overconfident.
MANAGER_SPREAD_POINTS = 10.0

# Minimum real weekly scores needed before we trust them over the model.
MIN_OBSERVED_SCORES = 4


@dataclass
class PipelineResult:
    plan: SeasonPlan
    projections: pd.DataFrame
    state: LeagueState
    field: FieldModel
    config: LeagueConfig
    weeks: list[int]


def build_projections(
    config: LeagueConfig,
    client: NflverseClient,
    *,
    as_of_week: int,
    horizon: int,
) -> tuple[pd.DataFrame, list[int]]:
    """Project every eligible player from `as_of_week` out to the horizon."""
    season = config.season
    schedule = client.schedule()
    weekly_current = client.weekly_stats(season)
    weekly_prior = client.weekly_stats(season - 1)
    injuries = client.injuries(season)
    rosters = client.rosters(season)

    last_week = min(config.survival.final_week, as_of_week + horizon - 1)
    weeks = list(range(as_of_week, last_week + 1))

    model = ProjectionModel(
        config,
        weekly_current=weekly_current,
        weekly_prior=weekly_prior,
        schedule=schedule,
        injuries=injuries,
        rosters=rosters,
        availability_model=AvailabilityModel(),
    )
    projections = model.project_weeks(weeks, as_of_week=as_of_week)
    return projections, weeks


def calibrate_field(
    config: LeagueConfig,
    projections: pd.DataFrame,
    state: LeagueState,
    weeks: list[int],
) -> FieldModel:
    """Estimate what a typical surviving opponent scores each week.

    Real observed scores win when there are enough of them.  Otherwise we model
    the median manager directly: with `T` teams alive, the typical manager is not
    starting the best option at each slot, he is starting something around the
    `T/2`-th best.  And because this is a one-use format, by week `w` he has
    already burned his own top `w - 1` choices at that slot, so his typical pick
    slides steadily further down the board.  That gives a per-week field decay
    grounded in the same projections we use for ourselves, rather than a guessed
    constant.
    """
    if len(state.observed_field_scores) >= MIN_OBSERVED_SCORES:
        scores = np.asarray(state.observed_field_scores, dtype=float)
        log.info("calibrating field from %d observed scores", len(scores))
        return FieldModel(mean=float(scores.mean()), sd=float(scores.std(ddof=1)))

    by_week: dict[int, tuple[float, float]] = {}
    teams = max(2, state.teams_remaining)

    for week in weeks:
        frame = projections[projections["week"] == week]
        if frame.empty:
            continue

        depth_used = max(0, week - config.survival.as_of_week)
        lineup_mean = 0.0
        lineup_var = 0.0
        for slot in config.slots:
            pool = frame[frame["position"].isin(slot.eligible)].sort_values(
                "mean", ascending=False
            )
            if pool.empty:
                continue
            rank = min(len(pool) - 1, teams // 2 + depth_used)
            row = pool.iloc[rank]
            lineup_mean += float(row["mean"])
            lineup_var += float(row["sd"]) ** 2

        by_week[week] = (
            lineup_mean,
            float(np.sqrt(lineup_var + MANAGER_SPREAD_POINTS**2)),
        )

    if not by_week:
        return FieldModel()

    means = [m for m, _ in by_week.values()]
    sds = [s for _, s in by_week.values()]
    return FieldModel(
        mean=float(np.mean(means)), sd=float(np.mean(sds)), by_week=by_week
    )


def run(
    config: LeagueConfig,
    state: LeagueState,
    *,
    cache_dir: Path | str = ".cache/nflverse",
    horizon: int = 8,
    use_survival_weights: bool = True,
    offline: bool = False,
    candidates: int = 45,
    max_per_team: int = 2,
    max_per_game: int = 3,
) -> PipelineResult:
    """Fetch, project, calibrate and optimise in one go."""
    client = NflverseClient(cache_dir=Path(cache_dir), offline=offline)

    projections, weeks = build_projections(
        config, client, as_of_week=state.current_week, horizon=horizon
    )
    if projections.empty:
        raise RuntimeError(
            "no projections produced -- check that the season/week in your league "
            "config matches an actual NFL schedule"
        )

    if state.available_players:
        projections = projections[
            projections["player_id"].isin(state.available_players)
        ].copy()

    field = calibrate_field(config, projections, state, weeks)

    optimizer = SurvivorOptimizer(
        config,
        projections,
        field_model=field,
        used_players=state.used_players,
        use_survival_weights=use_survival_weights,
        candidates_per_slot_week=candidates,
        max_per_team=max_per_team,
        max_per_game=max_per_game,
    )
    plan = optimizer.solve()

    return PipelineResult(
        plan=plan,
        projections=projections,
        state=state,
        field=field,
        config=config,
        weeks=weeks,
    )


def contest_end_week(config: LeagueConfig, state: LeagueState) -> int:
    """The last week that can still matter.

    With one elimination a week, a 12-team league is down to a winner long
    before week 17, and planning past that point wastes players on weeks that
    will never be played.
    """
    survival = config.survival
    weeks_until_two_left = max(
        0, (state.teams_remaining - 2) // max(1, survival.eliminations_per_week)
    )
    return min(survival.final_week, state.current_week + weeks_until_two_left)


def infer_current_week(config: LeagueConfig, cache_dir: Path | str = ".cache/nflverse") -> int:
    client = NflverseClient(cache_dir=Path(cache_dir))
    return current_week(client.schedule(), config.season)
