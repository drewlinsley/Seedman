"""Fit the model's constants from historical data instead of asserting them.

Almost every number in `injury.py`, `correlation.py` and `projections.py`
started life as a hand-set prior taken from published research or experience.
That is a reasonable place to start and a bad place to stop: several of those
priors turn out to be wrong by large factors when checked against the record.
The worst offender was the "Doubtful" designation, hand-set at 6% from the
NFL's pre-2016 published definition and actually **0.9%** -- a player listed
Doubtful essentially never plays.

Everything here is fitted on seasons you nominate and written to a YAML file
that the runtime loads.  Keep the most recent season out of the fit and use it
as a validation set: the league changes year to year, so a constant tuned on
2021 data and validated on 2021 data tells you nothing.

    seedman calibrate --fit 2021 2022 2023 2024 --out configs/fitted.yaml

Measurement choices that matter, and why:

* **"Played" means offensive snaps > 0**, from the snap-count release, not
  "appears in the weekly stat file".  The stat file only lists players who
  recorded something, so a receiver who ran twelve routes without a target
  looks identical to one who was inactive.
* **Availability is conditioned on having played the previous week.**  Without
  that conditioning the denominator silently fills up with players who were on
  injured reserve, had not yet debuted, or were not on an NFL roster at all,
  which drags a true ~92% down to a meaningless ~70%.
* **Production is measured against the player's own season average**, so that
  "players who get injury designations are different from players who do not"
  cannot masquerade as an effect of the designation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .config import LeagueConfig
from .scoring import build_dst_stat_lines, score_dst, score_players

log = logging.getLogger(__name__)

SKILL_POSITIONS = ("QB", "RB", "WR", "TE")
NOT_ON_REPORT = "not on report"
NO_DESIGNATION = "(none)"

# Buckets thinner than this are reported but not used to overwrite a prior.
MIN_SAMPLE = 60


@dataclass
class SeasonData:
    """Everything one historical season contributes to a fit."""

    season: int
    stats: pd.DataFrame
    injuries: pd.DataFrame
    played: set[tuple[str, int]]
    team_of: dict[tuple[str, int], str]
    teams_by_week: dict[int, set[str]]

    @property
    def max_week(self) -> int:
        weeks = self.stats["week"]
        return int(weeks.max()) if len(weeks) else 0


def load_season(season: int, cache_dir: Path, config: LeagueConfig) -> SeasonData:
    """Assemble one season's stats, injuries and true on-field appearances."""
    cache = Path(cache_dir)
    schedule = pd.read_csv(cache / "games.csv", low_memory=False)
    stats = pd.read_csv(cache / f"stats_player_week_{season}.csv", low_memory=False)
    injuries = pd.read_csv(cache / f"injuries_{season}.csv", low_memory=False)
    snaps = pd.read_csv(cache / f"snap_counts_{season}.csv", low_memory=False)
    roster = pd.read_csv(cache / f"roster_{season}.csv", low_memory=False)

    stats = _regular_season(stats).copy()
    stats["points"] = score_players(stats, config.scoring)

    # Team defenses live at the player level in nflverse but are started as a
    # unit, so they have to be rolled up or any lineup containing a DEF slot is
    # silently unbuildable.
    if "DEF" in config.positions_used:
        dst = build_dst_stat_lines(stats, schedule)
        if not dst.empty:
            dst["points"] = score_dst(dst, config.scoring)
            keep = ["player_id", "player_display_name", "position", "team", "week", "points"]
            stats = pd.concat([stats, dst[keep]], ignore_index=True)

    injuries = _regular_season(injuries)

    # Snap counts key on pro-football-reference ids; the season's own roster is
    # the only reliable bridge to gsis (a later roster has lost retired players).
    bridge = roster.dropna(subset=["gsis_id", "pfr_id"])
    snaps = _regular_season(snaps).copy()
    snaps["gsis_id"] = snaps["pfr_player_id"].map(dict(zip(bridge["pfr_id"], bridge["gsis_id"])))
    snaps = snaps[snaps["gsis_id"].notna() & snaps["position"].isin(SKILL_POSITIONS)]

    on_field = snaps[snaps["offense_snaps"].fillna(0) > 0]
    played = set(map(tuple, on_field[["gsis_id", "week"]].to_numpy()))
    team_of = {(r.gsis_id, r.week): r.team for r in on_field.itertuples()}

    season_games = schedule[
        (schedule["season"] == season) & (schedule["game_type"] == "REG")
    ]
    teams_by_week = {
        int(week): set(group["home_team"]) | set(group["away_team"])
        for week, group in season_games.groupby("week")
    }

    return SeasonData(season, stats, injuries, played, team_of, teams_by_week)


def _regular_season(frame: pd.DataFrame) -> pd.DataFrame:
    for column in ("season_type", "game_type"):
        if column in frame.columns:
            return frame[frame[column] == "REG"]
    return frame


# ----------------------------------------------------------------------
# availability
# ----------------------------------------------------------------------
def availability_transitions(seasons: list[SeasonData]) -> pd.DataFrame:
    """One row per (player played week W, his team plays W+1) transition."""
    rows = []
    for data in seasons:
        report = {
            (r.gsis_id, r.week): (
                _text(getattr(r, "report_status", None)) or NO_DESIGNATION,
                _text(getattr(r, "practice_status", None)) or NO_DESIGNATION,
            )
            for r in data.injuries.itertuples()
        }
        for player_id, week in data.played:
            next_week = week + 1
            if next_week > data.max_week:
                continue
            team = data.team_of.get((player_id, week))
            if team not in data.teams_by_week.get(next_week, set()):
                continue  # bye week: not an availability question
            status, practice = report.get((player_id, next_week), (NOT_ON_REPORT, NOT_ON_REPORT))
            rows.append(
                {
                    "season": data.season,
                    "report_status": status,
                    "practice_status": practice,
                    "played_next": (player_id, next_week) in data.played,
                }
            )
    return pd.DataFrame(rows)


def fit_availability(transitions: pd.DataFrame) -> dict:
    """P(plays) by report status, and by practice within Questionable."""
    by_status = _rate_table(transitions, "report_status", "played_next")
    questionable = transitions[transitions["report_status"] == "Questionable"]
    by_practice = _rate_table(questionable, "practice_status", "played_next")
    undesignated = transitions[transitions["report_status"] == NO_DESIGNATION]
    undesignated_practice = _rate_table(undesignated, "practice_status", "played_next")

    return {
        "by_report_status": by_status,
        "questionable_by_practice": by_practice,
        "undesignated_by_practice": undesignated_practice,
        "sample_size": int(len(transitions)),
    }


# ----------------------------------------------------------------------
# effectiveness
# ----------------------------------------------------------------------
def fit_effectiveness(seasons: list[SeasonData]) -> dict:
    """Production relative to a player's own season average, given he played.

    Comparing a player only against himself removes the obvious confound: the
    players who collect injury designations are not a random sample.
    """
    rows = []
    for data in seasons:
        points = data.stats.set_index(["player_id", "week"])["points"].to_dict()
        baseline = data.stats.groupby("player_id")["points"].mean()
        regular = baseline[baseline > 3.0].to_dict()  # fantasy-relevant only

        for r in data.injuries.itertuples():
            key = (r.gsis_id, r.week)
            if key not in data.played or r.gsis_id not in regular:
                continue
            rows.append(
                {
                    "report_status": _text(getattr(r, "report_status", None)) or NO_DESIGNATION,
                    "ratio": points.get(key, 0.0) / regular[r.gsis_id],
                }
            )

    frame = pd.DataFrame(rows)
    if frame.empty:
        return {}
    grouped = frame.groupby("report_status")["ratio"].agg(["mean", "size"])
    return {
        status: {"value": round(float(row["mean"]), 4), "n": int(row["size"])}
        for status, row in grouped.iterrows()
    }


# ----------------------------------------------------------------------
# correlation
# ----------------------------------------------------------------------
def fit_correlations(seasons: list[SeasonData], min_pairs: int = 200) -> dict:
    """Same-team correlation between positions, from actual weekly scores.

    Subtle but important: the naive estimator picks each team-week's *top*
    scorer at each position and correlates those. That selection happens after
    seeing the outcome, and it manufactures correlation -- when a quarterback
    throws four touchdowns, the receiver who caught them is by construction the
    one selected. It inflated QB-WR from 0.38 to 0.52 here.

    So the primary at each position is chosen **once per season** (most total
    points) and held fixed across weeks. Week-specific noise then cannot
    influence who is being measured.
    """
    series = []
    for data in seasons:
        frame = data.stats[data.stats["position"].isin(SKILL_POSITIONS)]
        if frame.empty:
            continue
        # Season-level designation, fixed before any week is examined.
        totals = frame.groupby(["team", "position", "player_id"], as_index=False)["points"].sum()
        primary = (
            totals.sort_values("points", ascending=False)
            .groupby(["team", "position"], as_index=False)
            .first()[["team", "position", "player_id"]]
        )
        weekly = frame.merge(primary, on=["team", "position", "player_id"], how="inner")
        series.append(weekly[["season", "week", "team", "position", "points"]])

    if not series:
        return {}

    combined = pd.concat(series, ignore_index=True)
    wide = combined.pivot_table(
        index=["season", "week", "team"], columns="position", values="points"
    )

    out: dict[str, dict] = {}
    for i, first in enumerate(SKILL_POSITIONS):
        for second in SKILL_POSITIONS[i + 1 :]:
            if first not in wide.columns or second not in wide.columns:
                continue
            pair = wide[[first, second]].dropna()
            if len(pair) < min_pairs:
                continue
            out[f"{first}|{second}"] = {
                "value": round(float(pair[first].corr(pair[second])), 4),
                "n": int(len(pair)),
            }
    return out


def fit_common_variance_share(seasons: list[SeasonData], min_weeks: int = 8) -> dict:
    """How much of weekly scoring variance is a league-wide shock.

    Decomposes the variance of team-level top-scorer totals into a between-week
    component (every team up or down together) and a within-week component (one
    team separating from another).  Only the second can eliminate you, which is
    why the first is netted out of both sides of the survival comparison.
    """
    frames = []
    for data in seasons:
        frame = data.stats[data.stats["position"].isin(SKILL_POSITIONS)]
        top = (
            frame.sort_values("points", ascending=False)
            .groupby(["season", "week", "team", "position"], as_index=False)
            .first()
        )
        lineup = top.groupby(["season", "week", "team"], as_index=False)["points"].sum()
        frames.append(lineup)

    if not frames:
        return {}
    combined = pd.concat(frames, ignore_index=True)
    if combined["week"].nunique() < min_weeks:
        return {}

    week_means = combined.groupby(["season", "week"])["points"].mean()
    between = float(week_means.var(ddof=1))
    within = float(
        combined.groupby(["season", "week"])["points"].var(ddof=1).mean()
    )
    total = between + within
    if total <= 0:
        return {}
    return {
        "value": round(between / total, 4),
        "between_week_variance": round(between, 2),
        "within_week_variance": round(within, 2),
        "n": int(len(combined)),
    }


# ----------------------------------------------------------------------
def _rate_table(frame: pd.DataFrame, key: str, target: str) -> dict:
    if frame.empty:
        return {}
    grouped = frame.groupby(key)[target].agg(["mean", "size"])
    return {
        str(name): {"value": round(float(row["mean"]), 4), "n": int(row["size"])}
        for name, row in grouped.iterrows()
    }


def _text(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "na"} else text


def calibrate(
    config: LeagueConfig,
    fit_seasons: list[int],
    cache_dir: Path,
    *,
    tune_rates: bool = False,
    fit_hazard: bool = True,
    validate_season: int | None = None,
) -> dict:
    """Run every fit and return a dict ready to be written as YAML."""
    seasons = [load_season(year, cache_dir, config) for year in fit_seasons]
    transitions = availability_transitions(seasons)

    result = {
        "generated": date.today().isoformat(),
        "fitted_on": list(fit_seasons),
        "min_sample_for_use": MIN_SAMPLE,
        "availability": fit_availability(transitions),
        "effectiveness_given_played": fit_effectiveness(seasons),
        "same_team_correlation": fit_correlations(seasons),
        "common_variance_share": fit_common_variance_share(seasons),
        "volatility": fit_volatility(seasons),
        "field_lineups": fit_field_lineups(config, seasons),
    }
    if fit_hazard:
        result["availability_hazard"] = fit_availability_hazard(
            config, fit_seasons, cache_dir=cache_dir
        )
    if tune_rates:
        rate_model = tune_rate_model(
            config, fit_seasons, cache_dir=cache_dir, validate_season=validate_season
        )
        result["rate_model"] = rate_model
        params = {
            key: rate_model[key]["value"]
            for key in ("prior_season_weight", "replacement_weight", "replacement_quantile")
        }
        result["rate_calibration"] = fit_rate_calibration(
            config, fit_seasons, cache_dir=cache_dir, params=params
        )
    return result


def write_calibration(result: dict, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Fitted from historical nflverse data by `seedman calibrate`.\n"
        "# Do not hand-edit: re-run the command instead.\n"
        f"# Fitted on seasons {result['fitted_on']}; keep later seasons out of the fit\n"
        "# so they remain an honest validation set.\n"
    )
    path.write_text(header + yaml.safe_dump(result, sort_keys=False))


# ----------------------------------------------------------------------
# rate-model hyperparameters
# ----------------------------------------------------------------------
# Searched over these. The surface is flat near the optimum, so the exact
# winner matters much less than staying away from the corner that ruined the
# first version: a high replacement quantile, which shrinks unknown players
# toward the median *starter* instead of toward replacement level.
RATE_GRID = {
    "prior_season_weight": [0.0, 1.0, 2.0, 4.0, 8.0],
    "replacement_weight": [0.5, 1.0, 2.0, 3.0, 5.0],
    "replacement_quantile": [0.05, 0.1, 0.2, 0.3],
}


def tune_rate_model(
    config: LeagueConfig,
    fit_seasons: list[int],
    *,
    cache_dir: Path,
    validate_season: int | None = None,
    first_week: int = 2,
    last_week: int = 17,
) -> dict:
    """Grid-search the shrinkage hyperparameters on `fit_seasons`.

    Scored on the decision that is actually made -- "which player do I start at
    this position this week" -- rather than on overall rank correlation across
    every player in the league. Those metrics disagree, and the second one is
    dominated by correctly burying hundreds of third-stringers, which is not a
    decision anybody makes.

    `validate_season` is reported but never optimised against.
    """
    from .backtest import rate_model_inputs, score_rate_model

    seasons = list(fit_seasons) + ([validate_season] if validate_season else [])
    inputs = {
        season: rate_model_inputs(
            config, season, cache_dir=cache_dir, first_week=first_week, last_week=last_week
        )
        for season in seasons
    }

    best: tuple[float, dict] | None = None
    for prior_weight in RATE_GRID["prior_season_weight"]:
        for replacement_weight in RATE_GRID["replacement_weight"]:
            for quantile in RATE_GRID["replacement_quantile"]:
                params = {
                    "prior_season_weight": prior_weight,
                    "replacement_weight": replacement_weight,
                    "replacement_quantile": quantile,
                }
                score = float(
                    np.mean([score_rate_model(inputs[s], params) for s in fit_seasons])
                )
                if best is None or score > best[0]:
                    best = (score, params)

    assert best is not None
    score, params = best
    result = {
        key: {"value": value, "n": int(len(fit_seasons) * (last_week - first_week + 1))}
        for key, value in params.items()
    }
    result["fit_score_points_per_start"] = round(score, 3)
    result["fit_seasons"] = list(fit_seasons)
    if validate_season:
        result["validation_season"] = validate_season
        result["validation_points_per_start"] = round(
            score_rate_model(inputs[validate_season], params), 3
        )
    return result


# ----------------------------------------------------------------------
# level calibration
# ----------------------------------------------------------------------
# Level calibration is keyed by the week being projected, not by the individual
# player's games played. That distinction is the whole trick. Bucketing by a
# player's own record does fix the level, but it hands two players in the same
# week different slopes and so reorders them -- which cost 5.6 points a week on
# the decision metric. A week-keyed map applies the *same* affine transform to
# everyone in that week, so it cannot change a single lineup decision, while
# still correcting a bias that is 30% in week 2 and 10% by week 12.
WEEK_BUCKETS = ((2, 3), (4, 6), (7, 10), (11, 18))

# Calibrate over roughly the pool the optimizer prunes to, per position-week.
CALIBRATION_CANDIDATES = 45


def week_bucket(week: float) -> str:
    """Which calibration window a week falls into.

    Early in a season a shrunk estimate is far below the truth because the
    replacement-level prior still dominates; by midseason it is close. One map
    cannot serve both.
    """
    for low, high in WEEK_BUCKETS:
        if low <= week <= high:
            return f"{low}-{high}"
    return f"{WEEK_BUCKETS[-1][0]}-{WEEK_BUCKETS[-1][1]}"


def fit_rate_calibration(
    config: LeagueConfig,
    fit_seasons: list[int],
    *,
    cache_dir: Path,
    params: dict,
    first_week: int = 2,
    last_week: int = 17,
) -> dict:
    """Linear map from shrunk rate to expected actual points, per position and
    per stage of the season.

    Tuning the shrinkage for *ranking* leaves the level badly biased: the blend
    that best separates real starters from replacement bodies pulls everyone
    toward replacement. Ranking is all the optimizer needs to choose a lineup,
    but the survival maths compares a lineup total against a cut line in real
    points, so the level has to be right too.

    The bias is not constant: measured over 2023-2025 the top of the board came
    in 33% under in weeks 2-4 and 11% under from week 8, because early in a
    season the replacement-level prior still dominates every estimate. A single
    pooled slope cannot fix both ends.

    Keying on the week rather than on the player means every player in a given
    week gets the same affine transform, so the correction is invisible to any
    within-week ranking and changes no lineup -- it only puts lineup totals and
    the cut line back on a scale where comparing them means something.
    """
    from .backtest import apply_rate_model, rate_model_inputs

    collected: dict[tuple[str, str], list[pd.DataFrame]] = {}
    for season in fit_seasons:
        for rates, distribution in rate_model_inputs(
            config, season, cache_dir=cache_dir, first_week=first_week, last_week=last_week
        ):
            frame = rates.copy()
            frame["estimate"] = apply_rate_model(frame, distribution, params)
            frame["bucket"] = week_bucket(float(frame["week"].iloc[0]))
            for position, group in frame.groupby("position"):
                if str(position) not in config.positions_used:
                    continue  # linemen and defensive backs are not startable here
                # Calibrate only over the players the optimizer would actually
                # consider. Fitting across the whole league lets the enormous
                # mass of replacement-level bodies set the slope, which drags
                # the top of the board -- the only part anybody starts -- in the
                # wrong direction. A relationship that is convex overall cannot
                # be fixed by one line through all of it.
                candidates = group.nlargest(CALIBRATION_CANDIDATES, "estimate")
                for bucket, chunk in candidates.groupby("bucket"):
                    collected.setdefault((str(position), str(bucket)), []).append(
                        chunk[["estimate", "actual"]]
                    )

    out: dict[str, dict] = {}
    for (position, bucket), parts in collected.items():
        frame = pd.concat(parts, ignore_index=True).dropna()
        if len(frame) < 150 or frame["estimate"].std() <= 0:
            continue
        slope, intercept = np.polyfit(frame["estimate"], frame["actual"], 1)
        if slope <= 0:
            continue  # a negative slope would invert the ranking; keep identity
        out[f"{position}|{bucket}"] = {
            "slope": round(float(slope), 4),
            "intercept": round(float(intercept), 4),
            "n": int(len(frame)),
        }
    return out


# ----------------------------------------------------------------------
# volatility and the opposing field
# ----------------------------------------------------------------------
def fit_volatility(seasons: list[SeasonData], min_games: int = 6) -> dict:
    """Weekly standard deviation as a function of a player's scoring level.

    Carrying a player's raw historical spread alongside a shrunk mean produces
    nonsense -- a 14-point projection paired with a 13-point standard deviation,
    when real players at that level run about 7. Measured over 2021-2024 the
    ratio falls steadily with level (about 1.2 at 2.5 points a game, 0.45 at 20),
    so spread is fitted as a line in the mean and rebuilt from the calibrated
    projection rather than inherited.
    """
    frames = []
    for data in seasons:
        grouped = data.stats.groupby(["player_id", "position"])["points"].agg(
            ["mean", "std", "count"]
        )
        frames.append(grouped[grouped["count"] >= min_games].reset_index())
    if not frames:
        return {}

    combined = pd.concat(frames, ignore_index=True).dropna(subset=["std"])
    out: dict[str, dict] = {}
    for position, group in combined.groupby("position"):
        if len(group) < 40 or group["mean"].std() <= 0:
            continue
        slope, intercept = np.polyfit(group["mean"], group["std"], 1)
        out[str(position)] = {
            "slope": round(float(slope), 4),
            "intercept": round(float(intercept), 4),
            "n": int(len(group)),
        }
    return out


def fit_field_lineups(
    config: LeagueConfig, seasons: list[SeasonData], depths: tuple[int, ...] = (1, 3, 6, 10, 15, 20)
) -> dict:
    """What a manager who fields the k-th best option at each slot actually scores.

    The field model used to be derived from our own projections, which inherited
    every bias in them: it put a typical opponent at 40 points and the cut line
    of a twelve-team league at 12, when the real answer is around 100 and 60.
    Measuring it instead means the cut line no longer depends on the projection
    model being calibrated.

    Players are chosen by **season-long average**, fixed before any week is
    scored, so the spread reported is the one a manager who picked in advance
    actually experiences -- not the artificially stable spread you get by taking
    the k-th best of each week after the fact.
    """
    totals: dict[int, list[float]] = {k: [] for k in depths}

    for data in seasons:
        stats = data.stats
        season_mean = stats.groupby(["player_id", "position"])["points"].mean().reset_index()
        weekly = stats.pivot_table(index="week", columns="player_id", values="points")
        team_of = stats.sort_values("week").groupby("player_id")["team"].last().to_dict()

        for k in depths:
            chosen: list[str] = []
            for slot in config.slots:
                pool = season_mean[season_mean["position"].isin(slot.eligible)]
                pool = pool.nlargest(k, "points")
                if len(pool) < k:
                    chosen = []
                    break
                chosen.append(str(pool["player_id"].iloc[-1]))
            if not chosen:
                continue
            present = [p for p in chosen if p in weekly.columns]
            if len(present) < len(chosen):
                continue

            for week in weekly.index:
                # Skip weeks where any of these players is on bye. A manager
                # would simply start somebody else; counting the zero both
                # understates the field and inflates its spread, which drags the
                # cut line down and makes early weeks look safer than they are.
                playing = data.teams_by_week.get(int(week), set())
                if any(team_of.get(p) not in playing for p in present):
                    continue
                totals[k].append(float(weekly.loc[week, present].fillna(0.0).sum()))

    out: dict[str, dict] = {}
    for k, values in totals.items():
        if len(values) < 30:
            continue
        out[str(k)] = {
            "mean": round(float(np.mean(values)), 2),
            "sd": round(float(np.std(values, ddof=1)), 2),
            "n": int(len(values)),
        }
    return out


def fit_availability_hazard(
    config: LeagueConfig, fit_seasons: list[int], *, cache_dir: Path
) -> dict:
    """Fit the two-state weekly availability model and return it as plain data.

    This replaces a hand-set AR(1) that measured at AUC 0.49-0.55 on held-out
    2025 -- no better than a coin flip -- while confidently reporting ~0.90
    availability for a population that was actually available ~0.70 of the time.
    The fitted chain reaches AUC 0.89 at one week and 0.80 at three, and is
    calibrated.
    """
    from .availability import AvailabilityHazard, build_panel

    panel = build_panel(fit_seasons, cache_dir, config)
    if panel.empty:
        return {}
    return AvailabilityHazard().fit(panel).to_dict()
