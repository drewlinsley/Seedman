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

from . import fitted
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
    as_of: pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, list[int]]:
    """Project every eligible player from `as_of_week` out to the horizon."""
    season = config.season
    schedule = client.schedule()
    weekly_current = client.weekly_stats(season)
    weekly_prior = client.weekly_stats(season - 1)
    injuries = client.injuries(season)
    injuries_prior = client.injuries(season - 1)
    rosters = client.rosters(season)

    last_week = min(config.survival.final_week, as_of_week + horizon - 1)
    weeks = list(range(as_of_week, last_week + 1))

    curves = availability_curves(config, client, season, as_of_week, horizon)

    model = ProjectionModel(
        config,
        weekly_current=weekly_current,
        weekly_prior=weekly_prior,
        schedule=schedule,
        injuries=injuries,
        injuries_prior=injuries_prior,
        rosters=rosters,
        availability_model=AvailabilityModel(),
        availability_curves=curves,
        as_of=as_of,
    )
    projections = model.project_weeks(weeks, as_of_week=as_of_week)
    return projections, weeks


def availability_curves(
    config: LeagueConfig,
    client: NflverseClient,
    season: int,
    as_of_week: int,
    horizon: int,
) -> pd.DataFrame | None:
    """Fitted multi-week availability for every player, as known this week.

    Returns None when no hazard has been fitted, in which case the projection
    model falls back to its prior. Building the panel costs a few seconds; the
    alternative was an AR(1) with no discriminative power at all, so it is worth
    the wait.
    """
    from .availability import FittedHazard, build_panel

    hazard = FittedHazard.from_dict(fitted.get().raw.get("availability_hazard") or {})
    if hazard is None:
        return None

    # In week 1 the current season has no completed games, so the player's state
    # has to come from the end of last season. That is real information: a back
    # who finished the previous year on injured reserve is a materially worse
    # bet for the opener than one who played week 18.
    seasons = [season] if as_of_week > 1 else [season - 1, season]
    try:
        panel = build_panel(seasons, client.cache_dir, config)
    except FileNotFoundError:
        log.warning("no cached data for %s; falling back to the prior", seasons)
        return None
    if panel.empty:
        return None

    current = panel[(panel["season"] == season) & (panel["week"] < as_of_week)]
    if not current.empty:
        source_season, source_week = season, int(current["week"].max())
    else:
        previous = panel[panel["season"] == season - 1]
        if previous.empty:
            return None
        source_season, source_week = season - 1, int(previous["week"].max())
        log.info(
            "week %s: no games played yet, carrying availability state from %s week %s",
            as_of_week, source_season, source_week,
        )

    return hazard.curves_for_week(
        panel, source_season, source_week, horizons=max(1, horizon)
    )


def calibrate_field(
    config: LeagueConfig,
    projections: pd.DataFrame,
    state: LeagueState,
    weeks: list[int],
) -> FieldModel:
    """Estimate what a typical surviving opponent scores each week.

    Real observed scores win when there are enough of them. Otherwise we model
    the median manager directly: with `T` teams alive, the typical manager is not
    starting the best option at each slot, he is starting something around the
    `T/2`-th best.

    Note what this deliberately does *not* use: the `field_lineups` block in
    `fitted.yaml`, which measures what the k-th best player at each slot actually
    scored. That sounds better -- real scores rather than our own projections --
    but it picks those players by **season-long average**, which is hindsight no
    manager has in week 1. It came out 1.84x higher than anything our projections
    can field, and comparing a projected lineup against a hindsight-selected cut
    line is not a comparison at all. Building the field from the same projections
    as our own lineup keeps both sides on one scale, which matters far more here
    than either side being individually unbiased.  And because this is a one-use format, by week `w` he has
    already burned his own top `w - 1` choices at that slot, so his typical pick
    slides steadily further down the board.  That gives a per-week field decay
    grounded in the same projections we use for ourselves, rather than a guessed
    constant.
    """
    if len(state.observed_field_scores) >= MIN_OBSERVED_SCORES:
        scores = np.asarray(state.observed_field_scores, dtype=float)
        log.info("calibrating field from %d observed scores", len(scores))
        return FieldModel(mean=float(scores.mean()), sd=float(scores.std(ddof=1)))

    teams = max(2, state.teams_remaining)

    by_week: dict[int, tuple[float, float]] = {}
    for week in weeks:
        frame = projections[projections["week"] == week]
        if frame.empty:
            continue

        depth_used = max(0, week - config.survival.as_of_week)
        if config.survival.is_playoff_week(week):
            # A rival who understands the one-start rule banks for the bracket
            # exactly as we do, so his playoff lineup is NOT the 21st-best
            # option left over after fifteen weeks of greed. Assuming otherwise
            # hands us an edge nobody is actually giving away, and it does so in
            # precisely the weeks the plan is built around -- it made our
            # week-16/17 win odds read 74%/71% against a field we had modelled
            # into the ground. Credit him with having held back roughly a
            # bracket's worth of starters.
            reserved_weeks = max(
                1, config.survival.final_week - config.survival.regular_season_final_week
            )
            depth_used = max(0, depth_used - reserved_weeks * len(config.slots))

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


def _measured_field(
    config: LeagueConfig,
    state: LeagueState,
    weeks: list[int],
    measured: dict,
    teams: int,
) -> FieldModel:
    """Field model read off real historical lineups rather than our projections.

    Deriving the opposing field from our own projections inherited every bias in
    them: a typical opponent came out at 40 points and a twelve-team cut line at
    12, against measured values near 75 and 45. Reading both off what managers
    actually scored breaks that circularity -- the cut line no longer depends on
    the projection model being calibrated.

    Depth `k` is where a median manager sits: half the league is ahead of him,
    and in a one-use format he slides a slot deeper every week as his own pool
    drains.
    """
    depths = sorted(int(k) for k in measured)
    by_week: dict[int, tuple[float, float]] = {}

    for week in weeks:
        depleted = max(0, week - config.survival.as_of_week)
        k = teams // 2 + depleted
        nearest = min(depths, key=lambda d: abs(d - k))
        entry = measured[str(nearest)]
        by_week[week] = (float(entry["mean"]), float(entry["sd"]))

    means = [m for m, _ in by_week.values()]
    sds = [s for _, s in by_week.values()]
    return FieldModel(mean=float(np.mean(means)), sd=float(np.mean(sds)), by_week=by_week)


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
    as_of: pd.Timestamp | None = None,
    hold_players: set[str] | None = None,
    start_players: set[str] | None = None,
) -> PipelineResult:
    """Fetch, project, calibrate and optimise in one go."""
    client = NflverseClient(cache_dir=Path(cache_dir), offline=offline)

    projections, weeks = build_projections(
        config, client, as_of_week=state.current_week, horizon=horizon, as_of=as_of
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
        hold_players=hold_players,
        start_players=start_players,
        use_survival_weights=use_survival_weights,
        wins_so_far=state.wins,
        losses_so_far=state.losses,
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
    # Head-to-head runs to the final whistle: nobody is eliminated early, and the
    # playoff weeks are the whole point of planning, so never truncate them away.
    if survival.is_head_to_head:
        return survival.final_week
    weeks_until_two_left = max(
        0, (state.teams_remaining - 2) // max(1, survival.eliminations_per_week)
    )
    return min(survival.final_week, state.current_week + weeks_until_two_left)


def infer_current_week(config: LeagueConfig, cache_dir: Path | str = ".cache/nflverse") -> int:
    client = NflverseClient(cache_dir=Path(cache_dir))
    return current_week(client.schedule(), config.season)
