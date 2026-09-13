"""Replay historical seasons to find out whether any of this actually works.

Two questions, and they are not the same question:

1. **Are the projections any good?**  Replay each week using only what was
   knowable beforehand, then compare against what happened. The bar is not
   "correlates with reality" -- almost anything does -- it is "beats the obvious
   alternatives", namely a player's season-to-date average, his previous season,
   and last week's score.

2. **Does the survival objective win more leagues?**  A better projection is
   worth nothing if the strategy built on it does not outlast the field. This
   simulates full survivor seasons against greedy opponents using *actual*
   historical scores, and counts who is still standing.

Everything routes through `ProjectionModel(through_week=...)`, which clips the
inputs once so no downstream code can reach into the future. Hold the most
recent season out of any fitting: the league changes year to year, so a constant
tuned on a season and validated on that same season tells you nothing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from .config import LeagueConfig
from .data import NflverseClient
from .injury import AvailabilityModel
from .projections import ProjectionModel
from .scoring import build_dst_stat_lines, score_dst, score_players

log = logging.getLogger(__name__)

FANTASY_POSITIONS = ("QB", "RB", "WR", "TE")


# ----------------------------------------------------------------------
# actual outcomes
# ----------------------------------------------------------------------
def actual_points(
    config: LeagueConfig, weekly: pd.DataFrame, schedule: pd.DataFrame
) -> pd.DataFrame:
    """What every player actually scored, under this league's rules."""
    if weekly.empty:
        return pd.DataFrame(columns=["player_id", "week", "position", "team", "actual"])

    frame = weekly.copy()
    frame["points"] = score_players(frame, config.scoring)

    parts = [frame[["player_id", "player_display_name", "position", "team", "week", "points"]]]
    if "DEF" in config.positions_used:
        dst = build_dst_stat_lines(frame, schedule)
        if not dst.empty:
            dst["points"] = score_dst(dst, config.scoring)
            parts.append(
                dst[["player_id", "player_display_name", "position", "team", "week", "points"]]
            )

    out = pd.concat(parts, ignore_index=True)
    return out.rename(columns={"points": "actual", "player_display_name": "name"})


# ----------------------------------------------------------------------
# baselines
# ----------------------------------------------------------------------
def baseline_projections(actuals: pd.DataFrame, week: int, prior_actuals: pd.DataFrame) -> pd.DataFrame:
    """The alternatives a model has to beat to be worth running.

    All three use only weeks strictly before `week`, same as the model.
    """
    history = actuals[actuals["week"] < week]
    frames = {}

    if not history.empty:
        frames["season_to_date"] = history.groupby("player_id")["actual"].mean()
        last = history[history["week"] == history["week"].max()]
        frames["last_week"] = last.groupby("player_id")["actual"].mean()
    if not prior_actuals.empty:
        frames["prior_season"] = prior_actuals.groupby("player_id")["actual"].mean()

    if not frames:
        return pd.DataFrame(columns=["player_id"])

    out = pd.DataFrame(frames).reset_index().rename(columns={"index": "player_id"})
    return out


# ----------------------------------------------------------------------
# projection replay
# ----------------------------------------------------------------------
@dataclass
class ReplayResult:
    season: int
    rows: pd.DataFrame
    weeks: list[int] = field(default_factory=list)


def replay_projections(
    config: LeagueConfig,
    season: int,
    *,
    cache_dir: Path | str = ".cache/nflverse",
    first_week: int = 2,
    last_week: int = 17,
) -> ReplayResult:
    """Project each week of `season` using only data available beforehand."""
    client = NflverseClient(cache_dir=Path(cache_dir), offline=True)
    schedule = client.schedule()
    weekly = client.weekly_stats(season)
    weekly_prior = client.weekly_stats(season - 1)
    injuries = client.injuries(season)
    rosters = client.rosters(season)

    truth = actual_points(config, weekly, schedule)
    prior_truth = actual_points(config, weekly_prior, schedule)

    available = sorted(int(w) for w in truth["week"].unique())
    weeks = [w for w in available if first_week <= w <= last_week]

    collected = []
    for week in weeks:
        model = ProjectionModel(
            config,
            weekly_current=weekly,
            weekly_prior=weekly_prior,
            schedule=schedule,
            injuries=injuries,
            rosters=rosters,
            availability_model=AvailabilityModel(),
            through_week=week - 1,  # the guard that makes this a backtest
        )
        projected = model.project_weeks([week], as_of_week=week)
        if projected.empty:
            continue

        week_truth = truth[truth["week"] == week][["player_id", "actual"]]
        merged = projected.merge(week_truth, on="player_id", how="left")
        # A player with no stat line did not record anything: that is a real
        # zero for fantasy purposes, not missing data.
        merged["actual"] = merged["actual"].fillna(0.0)

        baselines = baseline_projections(truth, week, prior_truth)
        if not baselines.empty:
            merged = merged.merge(baselines, on="player_id", how="left")

        merged["season"] = season
        collected.append(merged)

    rows = pd.concat(collected, ignore_index=True) if collected else pd.DataFrame()
    return ReplayResult(season=season, rows=rows, weeks=weeks)


# ----------------------------------------------------------------------
# metrics
# ----------------------------------------------------------------------
def accuracy_table(rows: pd.DataFrame, methods: list[str]) -> pd.DataFrame:
    """Error and rank-correlation for each projection method, by position.

    Rank correlation is computed *within position-week*, because that is the
    comparison a lineup decision actually makes: not "how many points will this
    player score" but "which of these quarterbacks should I start".
    """
    out = []
    for position in FANTASY_POSITIONS + ("K", "DEF"):
        subset = rows[rows["position"] == position]
        if len(subset) < 50:
            continue
        for method in methods:
            if method not in subset.columns:
                continue
            usable = subset[subset[method].notna()]
            if len(usable) < 50:
                continue
            error = usable[method] - usable["actual"]
            rho = _within_week_spearman(usable, method)
            out.append(
                {
                    "position": position,
                    "method": method,
                    "n": len(usable),
                    "MAE": round(float(error.abs().mean()), 3),
                    "RMSE": round(float(np.sqrt((error**2).mean())), 3),
                    "rank_rho": round(rho, 4),
                    "bias": round(float(error.mean()), 3),
                }
            )
    return pd.DataFrame(out)


def _within_week_spearman(frame: pd.DataFrame, method: str) -> float:
    values = []
    for _, group in frame.groupby(["season", "week"]):
        if len(group) < 5:
            continue
        rho = spearmanr(group[method], group["actual"]).statistic
        if np.isfinite(rho):
            values.append(rho)
    return float(np.mean(values)) if values else float("nan")


def start_the_best_table(rows: pd.DataFrame, methods: list[str], top_n: int = 1) -> pd.DataFrame:
    """The decision metric: start whoever a method ranks first, and see.

    Reported against two reference points -- the best choice available in
    hindsight (an unreachable ceiling) and the median available starter (roughly
    what picking blind would get you).
    """
    out = []
    for position in FANTASY_POSITIONS + ("K", "DEF"):
        subset = rows[rows["position"] == position]
        if len(subset) < 50:
            continue

        per_method: dict[str, list[float]] = {m: [] for m in methods}
        ceiling, median = [], []
        for _, group in subset.groupby(["season", "week"]):
            if len(group) < 10:
                continue
            ceiling.append(float(group["actual"].max()))
            median.append(float(group["actual"].median()))
            for method in methods:
                if method not in group.columns or group[method].isna().all():
                    continue
                picked = group.nlargest(top_n, method)["actual"].mean()
                per_method[method].append(float(picked))

        for method, scores in per_method.items():
            if not scores:
                continue
            out.append(
                {
                    "position": position,
                    "method": method,
                    "weeks": len(scores),
                    "avg_points_of_pick": round(float(np.mean(scores)), 2),
                    "vs_median": round(float(np.mean(scores) - np.mean(median)), 2),
                    "pct_of_ceiling": round(100 * float(np.mean(scores) / np.mean(ceiling)), 1),
                }
            )
    return pd.DataFrame(out)


# ----------------------------------------------------------------------
# full-season league simulation
# ----------------------------------------------------------------------
@dataclass
class StrategyRun:
    """One strategy's walk through a season, scored on actual results."""

    name: str
    weekly_actual: dict[int, float] = field(default_factory=dict)
    weekly_projected: dict[int, float] = field(default_factory=dict)
    picks: list[dict] = field(default_factory=list)
    used: set[str] = field(default_factory=set)


def _greedy_lineup(
    config: LeagueConfig,
    week_frame: pd.DataFrame,
    used: set[str],
    *,
    rng: np.random.Generator | None = None,
    noise: float = 0.0,
    max_per_team: int = 2,
) -> pd.DataFrame:
    """Start the best available player at each slot, ignoring the future.

    This is what most managers actually do, and it is the baseline the planner
    has to beat. With `noise` it also stands in for a field of rival managers
    working from imperfect projections.
    """
    pool = week_frame[~week_frame["player_id"].isin(used)].copy()
    if pool.empty:
        return pool

    pool["score"] = pool["mean"]
    if rng is not None and noise > 0:
        pool["score"] = pool["score"] * np.maximum(
            0.0, 1.0 + rng.normal(0.0, noise, size=len(pool))
        )

    chosen: list[int] = []
    taken: set[str] = set()
    per_team: dict[str, int] = {}
    for slot in config.slots:
        candidates = pool[
            pool["position"].isin(slot.eligible) & ~pool["player_id"].isin(taken)
        ].sort_values("score", ascending=False)
        for idx, row in candidates.iterrows():
            if max_per_team and per_team.get(row["team"], 0) >= max_per_team:
                continue
            chosen.append(idx)
            taken.add(row["player_id"])
            per_team[row["team"]] = per_team.get(row["team"], 0) + 1
            break
    return pool.loc[chosen]


def simulate_field(
    config: LeagueConfig,
    weekly_projections: dict[int, pd.DataFrame],
    truth: pd.DataFrame,
    *,
    n_opponents: int,
    seed: int,
    noise: float = 0.30,
) -> np.ndarray:
    """Actual weekly scores for a field of greedy, imperfectly-informed rivals.

    Returns an array shaped (weeks, opponents). Each rival keeps its own
    one-use-per-player ledger, so the field depletes exactly as a real league
    would.
    """
    rng = np.random.default_rng(seed)
    weeks = sorted(weekly_projections)
    scores = np.zeros((len(weeks), n_opponents))
    used: list[set[str]] = [set() for _ in range(n_opponents)]

    for w_index, week in enumerate(weeks):
        frame = weekly_projections[week]
        actual = truth[truth["week"] == week].set_index("player_id")["actual"]
        for opponent in range(n_opponents):
            lineup = _greedy_lineup(config, frame, used[opponent], rng=rng, noise=noise)
            if lineup.empty:
                continue
            used[opponent].update(lineup["player_id"])
            scores[w_index, opponent] = float(
                actual.reindex(lineup["player_id"]).fillna(0.0).sum()
            )
    return scores


def survival_outcomes(
    strategy_scores: np.ndarray, field_scores: np.ndarray
) -> tuple[int, bool]:
    """Walk a season week by week; return (weeks survived, won outright).

    Elimination is the genuine rule: lowest score among those still alive goes
    out. Opponents that have already been eliminated stop competing.

    A strategy that outlasts the whole field counts as surviving every week, not
    as surviving up to the week it won. Reporting the week it won would penalise
    winning: a strategy that took the league in week 11 would score below one
    that merely hung on until week 15 before being cut.
    """
    n_weeks, n_opponents = field_scores.shape
    alive = np.ones(n_opponents, dtype=bool)

    for week in range(n_weeks):
        if not alive.any():
            return n_weeks, True  # everybody else is gone; never eliminated

        contenders = field_scores[week][alive]
        mine = strategy_scores[week]
        if mine < contenders.min():
            return week, False  # lowest score in the league: eliminated

        # The weakest surviving opponent goes out.
        living = np.flatnonzero(alive)
        alive[living[int(np.argmin(contenders))]] = False

    return n_weeks, bool(not alive.any())


def run_strategies(
    config: LeagueConfig,
    season: int,
    *,
    cache_dir: Path | str = ".cache/nflverse",
    first_week: int = 2,
    last_week: int = 17,
    horizon: int = 8,
    teams: int = 12,
) -> tuple[dict[str, StrategyRun], dict[int, pd.DataFrame], pd.DataFrame]:
    """Walk `season` week by week, re-planning each week like a real manager.

    Every strategy sees the same leakage-safe projections, so any difference
    between them is the allocation policy and nothing else.
    """
    from dataclasses import replace as dc_replace

    from .optimize import SurvivorOptimizer
    from .pipeline import calibrate_field
    from .league.base import LeagueState

    client = NflverseClient(cache_dir=Path(cache_dir), offline=True)
    schedule = client.schedule()
    weekly = client.weekly_stats(season)
    weekly_prior = client.weekly_stats(season - 1)
    injuries = client.injuries(season)
    rosters = client.rosters(season)
    truth = actual_points(config, weekly, schedule)

    available = sorted(int(w) for w in truth["week"].unique())
    weeks = [w for w in available if first_week <= w <= last_week]

    runs = {
        name: StrategyRun(name=name)
        for name in ("survival", "points", "greedy")
    }
    week_frames: dict[int, pd.DataFrame] = {}

    for week in weeks:
        model = ProjectionModel(
            config,
            weekly_current=weekly,
            weekly_prior=weekly_prior,
            schedule=schedule,
            injuries=injuries,
            rosters=rosters,
            availability_model=AvailabilityModel(),
            through_week=week - 1,
        )
        planning_weeks = [w for w in weeks if week <= w < week + horizon]
        projected = model.project_weeks(planning_weeks, as_of_week=week)
        if projected.empty:
            continue

        this_week = projected[projected["week"] == week]
        week_frames[week] = this_week
        actual = truth[truth["week"] == week].set_index("player_id")["actual"]
        # Teams still alive shrinks by one a week, which is what makes late
        # weeks dangerous and therefore drives the survival weights.
        alive = max(2, teams - (week - weeks[0]))

        for name, run in runs.items():
            if name == "greedy":
                lineup = _greedy_lineup(config, this_week, run.used)
            else:
                cfg = dc_replace(
                    config,
                    survival=dc_replace(
                        config.survival, teams_remaining=alive, as_of_week=week
                    ),
                )
                state = LeagueState(current_week=week, teams_remaining=alive)
                field = calibrate_field(cfg, projected, state, planning_weeks)
                plan = SurvivorOptimizer(
                    cfg,
                    projected,
                    field_model=field,
                    used_players=set(run.used),
                    use_survival_weights=(name == "survival"),
                    candidates_per_slot_week=30,
                ).solve()
                if not plan.weeks:
                    continue
                ids = [p["player_id"] for p in plan.weeks[0].picks]
                lineup = this_week[this_week["player_id"].isin(ids)]

            if lineup.empty:
                continue
            run.used.update(lineup["player_id"])
            run.weekly_projected[week] = float(lineup["mean"].sum())
            run.weekly_actual[week] = float(
                actual.reindex(lineup["player_id"]).fillna(0.0).sum()
            )
            run.picks.append(
                {"week": week, "names": list(lineup["name"]), "actual": run.weekly_actual[week]}
            )

    return runs, week_frames, truth


def evaluate_strategies(
    config: LeagueConfig,
    runs: dict[str, StrategyRun],
    week_frames: dict[int, pd.DataFrame],
    truth: pd.DataFrame,
    *,
    teams: int = 12,
    replications: int = 200,
    seed: int = 0,
) -> pd.DataFrame:
    """Monte-Carlo each strategy against many draws of the opposing field.

    The strategies only observe how many teams are alive, never which ones, so
    a single walk through the season can be scored against many fields.
    """
    weeks = sorted(week_frames)
    results: dict[str, list[tuple[int, bool]]] = {name: [] for name in runs}

    for rep in range(replications):
        field = simulate_field(
            config, week_frames, truth, n_opponents=teams - 1, seed=seed + rep
        )
        for name, run in runs.items():
            mine = np.array([run.weekly_actual.get(w, 0.0) for w in weeks])
            results[name].append(survival_outcomes(mine, field))

    rows = []
    for name, outcomes in results.items():
        survived = np.array([o[0] for o in outcomes])
        won = np.array([o[1] for o in outcomes])
        scores = [runs[name].weekly_actual.get(w, 0.0) for w in weeks]
        rows.append(
            {
                "strategy": name,
                "avg_weeks_survived": round(float(survived.mean()), 2),
                "never_eliminated_pct": round(100 * float((survived >= len(weeks)).mean()), 1),
                "won_outright_pct": round(100 * float(won.mean()), 1),
                "avg_weekly_points": round(float(np.mean(scores)), 2),
                "worst_week": round(float(np.min(scores)), 2),
            }
        )
    return pd.DataFrame(rows).sort_values("avg_weeks_survived", ascending=False)


# ----------------------------------------------------------------------
# hyperparameter tuning support
# ----------------------------------------------------------------------
def rate_model_inputs(
    config: LeagueConfig,
    season: int,
    *,
    cache_dir: Path | str = ".cache/nflverse",
    first_week: int = 1,
    last_week: int = 17,
) -> list[tuple[pd.DataFrame, dict[str, pd.Series]]]:
    """Per-week raw ingredients for the shrinkage blend, plus what happened.

    Extracted once so a grid search can re-score hundreds of parameter settings
    without rebuilding a projection model each time. The `through_week` clip
    still applies, so every ingredient is leakage-safe.
    """
    client = NflverseClient(cache_dir=Path(cache_dir), offline=True)
    schedule = client.schedule()
    weekly = client.weekly_stats(season)
    weekly_prior = client.weekly_stats(season - 1)
    injuries = client.injuries(season)
    rosters = client.rosters(season)
    truth = actual_points(config, weekly, schedule)

    available = sorted(int(w) for w in truth["week"].unique())
    out = []
    for week in [w for w in available if first_week <= w <= last_week]:
        model = ProjectionModel(
            config,
            weekly_current=weekly,
            weekly_prior=weekly_prior,
            schedule=schedule,
            injuries=injuries,
            rosters=rosters,
            through_week=week - 1,
        )
        rates = model._player_rates()
        if rates.empty:
            continue
        rates = rates[
            ["player_id", "position", "games_cur", "mean_cur", "games_pri", "mean_pri"]
        ].copy()
        week_truth = truth[truth["week"] == week][["player_id", "actual"]]
        rates = rates.merge(week_truth, on="player_id", how="left")
        rates["actual"] = rates["actual"].fillna(0.0)
        rates["week"] = week

        # Per-position distribution of prior-season rates, from which any
        # replacement quantile can be read off later.
        distribution = {}
        for position in config.positions_used:
            group = model.prior[model.prior["position"] == position]
            per_player = group.groupby("player_id")["points"].agg(["mean", "count"])
            eligible = per_player[per_player["count"] >= 4]["mean"]
            distribution[position] = eligible if len(eligible) >= 5 else pd.Series(dtype=float)
        out.append((rates, distribution))
    return out


def apply_rate_model(
    rates: pd.DataFrame, distribution: dict[str, pd.Series], params: dict
) -> pd.Series:
    """The shrinkage blend, as a pure function of its hyperparameters."""
    quantile = params["replacement_quantile"]
    levels = {
        position: (float(series.quantile(quantile)) if len(series) else np.nan)
        for position, series in distribution.items()
    }
    # Explicit float coercion throughout: a position with no fitted level maps
    # to NaN and silently turns the whole column to object dtype otherwise.
    replacement = pd.to_numeric(rates["position"].map(levels), errors="coerce").fillna(4.0)

    n_cur = pd.to_numeric(rates["games_cur"], errors="coerce").fillna(0.0)
    r_cur = pd.to_numeric(rates["mean_cur"], errors="coerce").fillna(0.0)
    n_pri = pd.to_numeric(rates["games_pri"], errors="coerce").fillna(0.0)
    r_pri = pd.to_numeric(rates["mean_pri"], errors="coerce").fillna(0.0)

    w_pri = pd.Series(
        np.where(n_pri > 0, float(params["prior_season_weight"]), 0.0), index=rates.index
    )
    k0 = float(params["replacement_weight"])
    return ((n_cur * r_cur + w_pri * r_pri + k0 * replacement) / (n_cur + w_pri + k0)).astype(float)


def score_rate_model(
    inputs: list[tuple[pd.DataFrame, dict[str, pd.Series]]], params: dict
) -> float:
    """Average actual points of the player these parameters say to start."""
    picked = []
    for rates, distribution in inputs:
        if rates.empty:
            continue
        frame = rates.copy()
        frame["estimate"] = apply_rate_model(frame, distribution, params)
        for position, group in frame.groupby("position"):
            if position not in FANTASY_POSITIONS or len(group) < 10:
                continue
            picked.append(float(group.nlargest(1, "estimate")["actual"].mean()))
    return float(np.mean(picked)) if picked else float("nan")
