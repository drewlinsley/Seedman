"""Weekly fantasy-point projections: mean and spread, per player per week.

The model is deliberately simple and auditable, because in a survivor league the
optimizer's *ranking* of players matters far more than the third decimal place
of any one projection.  Three ingredients:

1. **A shrunk per-game rate.**  Empirical-Bayes blend of this season's scoring
   rate, last season's, and a position baseline.  Early in the year one good
   game means almost nothing, so the prior dominates and gradually gives way as
   real games accumulate.

2. **Game context from the betting market.**  A team's Vegas implied total
   (`total_line / 2 +/- spread_line / 2`) is the single best free predictor of
   how much offense there is to go around: across 2018-2025 it tracks actual
   team points at r ~= 0.39 and is essentially unbiased.  Player output scales
   with it, but sub-linearly, so the multiplier is damped by an exponent.
   Defenses are scored off the *opponent's* implied total, inverted.

3. **Availability.**  Expected points are multiplied by P(plays) and by an
   effectiveness discount for players who suit up hurt (see `injury.py`).  A bye
   week is availability zero.

Variance matters as much as the mean here: surviving a weekly cut is about the
left tail, not the average.  Each projection carries a standard deviation built
from the player's own game-to-game volatility plus the extra variance injected
by the possibility that he does not play at all.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import fitted
from .config import LeagueConfig
from .injury import NOT_ON_REPORT, AvailabilityModel, _clean, latest_injury_report
from .scoring import build_dst_stat_lines, score_dst, score_players

# Replacement-level per-game scoring, used when a player has little or no
# current-season record. Getting this wrong is expensive: an earlier version
# shrank unknown players toward the *median startable* player at their position,
# which handed every third-stringer a projection in line with a real starter and
# collapsed running-back rank correlation from 0.71 to 0.10. The prior a player
# with no evidence deserves is replacement level, not average-starter level.
FALLBACK_POSITION_MEAN = {"QB": 8.0, "RB": 3.5, "WR": 3.5, "TE": 2.5, "K": 6.0, "DEF": 5.0}
FALLBACK_POSITION_SD = {"QB": 7.0, "RB": 6.5, "WR": 6.8, "TE": 5.2, "K": 4.0, "DEF": 6.0}

# Empirical-Bayes weights, in "games of evidence". Tuned on 2023-2024 and
# validated on 2025 by `seedman calibrate --tune`; overridden by fitted.yaml.
# The surface is flat near the optimum -- anything with a low replacement
# quantile and a modest weight performs within noise of the best.
PRIOR_SEASON_WEIGHT = 2.0  # how much last season is worth once this one starts
REPLACEMENT_WEIGHT = 3.0  # pull toward replacement level for thin records
REPLACEMENT_QUANTILE = 0.20  # which percentile of a position counts as replacement

# How strongly player output tracks the team's implied total.
CONTEXT_EXPONENT = {"QB": 0.70, "RB": 0.65, "WR": 0.75, "TE": 0.70, "K": 0.50, "DEF": 1.00}
DEFAULT_CONTEXT_EXPONENT = 0.70
CONTEXT_CLAMP = (0.75, 1.30)

# Sportsbooks post lines roughly four weeks ahead. Live, the later weeks simply
# have no line yet; in a backtest the historical file has every line filled in,
# so they must be masked or the model gets to see the future.
LINE_LOOKAHEAD_WEEKS = 4


def _rate_hyperparameters() -> tuple[float, float, float]:
    """Prior-season weight, replacement weight and replacement quantile."""
    constants = fitted.get()
    return (
        constants.value("rate_model.prior_season_weight", PRIOR_SEASON_WEIGHT,
                        label="rate.prior_season_weight"),
        constants.value("rate_model.replacement_weight", REPLACEMENT_WEIGHT,
                        label="rate.replacement_weight"),
        constants.value("rate_model.replacement_quantile", REPLACEMENT_QUANTILE,
                        label="rate.replacement_quantile"),
    )


@dataclass
class Projection:
    """Everything the optimizer needs to know about one player in one week."""

    player_id: str
    name: str
    position: str
    team: str
    week: int
    mean: float  # expected points, already discounted for availability
    sd: float  # standard deviation including did-not-play risk
    availability: float
    conditional_mean: float  # expected points given that he plays
    opponent: str
    implied_team_total: float
    report_status: str


class ProjectionModel:
    """Builds week-by-week projections from nflverse inputs."""

    def __init__(
        self,
        config: LeagueConfig,
        *,
        weekly_current: pd.DataFrame,
        weekly_prior: pd.DataFrame,
        schedule: pd.DataFrame,
        injuries: pd.DataFrame,
        rosters: pd.DataFrame,
        availability_model: AvailabilityModel | None = None,
        availability_curves: pd.DataFrame | None = None,
        through_week: int | None = None,
    ) -> None:
        self.config = config
        self.injuries = injuries
        self.rosters = rosters
        self.availability = availability_model or AvailabilityModel()
        # Fitted multi-week availability, indexed by player with one column per
        # horizon. When absent we fall back to the AR(1), which measured at AUC
        # 0.49-0.55 on held-out 2025 -- i.e. no better than a coin flip.
        self.availability_curves = availability_curves
        self.through_week = through_week

        # Everything the model is allowed to see is clipped here, once, so that
        # no downstream method can accidentally reach into the future. Live this
        # is a no-op (the data simply stops at today); in a backtest it is the
        # difference between a real result and a fantasy.
        if through_week is not None:
            weekly_current = weekly_current[weekly_current["week"] <= through_week]
            schedule = _mask_future_data(
                schedule,
                config.season,
                lines_through=through_week + LINE_LOOKAHEAD_WEEKS,
                scores_through=through_week,
            )
        self.schedule = schedule

        (
            self._prior_season_weight,
            self._replacement_weight,
            self._replacement_quantile,
        ) = _rate_hyperparameters()

        self.current = self._prepare(weekly_current)
        self.prior = self._prepare(weekly_prior)
        self.position_mean, self.position_sd = self._fit_position_priors()
        self._league_avg_implied = self._average_implied_total()

    # ------------------------------------------------------------------
    # preparation
    # ------------------------------------------------------------------
    def _prepare(self, weekly: pd.DataFrame) -> pd.DataFrame:
        """Score a weekly stat frame and append team-defense rows."""
        if weekly is None or weekly.empty:
            return pd.DataFrame(
                columns=["player_id", "player_display_name", "position", "team", "week", "points"]
            )

        frame = weekly.copy()
        frame["points"] = score_players(frame, self.config.scoring)

        if "DEF" in self.config.positions_used:
            dst = build_dst_stat_lines(frame, self.schedule)
            if not dst.empty:
                dst["points"] = score_dst(dst, self.config.scoring)
                keep = ["player_id", "player_display_name", "position", "team", "week", "points"]
                frame = pd.concat([frame, dst[keep]], ignore_index=True)

        frame["position"] = frame["position"].fillna("UNK")
        return frame

    def _fit_position_priors(self) -> tuple[dict[str, float], dict[str, float]]:
        """Replacement-level scoring rate and typical volatility, per position.

        Replacement level is a low quantile of the per-player season averages,
        not the average of the good ones. It is the number a player about whom
        we know nothing should regress to, and the whole ranking depends on it
        being low.
        """
        means = dict(FALLBACK_POSITION_MEAN)
        sds = dict(FALLBACK_POSITION_SD)

        # Prior seasons are always safe to fit on. Falling back to the current
        # season is only legitimate when nothing is being withheld.
        history = self.prior
        if history.empty:
            if self.through_week is not None:
                return means, sds
            history = self.current
        if history.empty:
            return means, sds

        for position, group in history.groupby("position"):
            if position not in means:
                continue
            per_player = group.groupby("player_id")["points"].agg(["mean", "count"])
            # Require a few appearances so that one-game cameos do not define
            # the replacement level.
            eligible = per_player[per_player["count"] >= 4]
            if len(eligible) < 5:
                continue
            means[position] = float(eligible["mean"].quantile(self._replacement_quantile))

            weekly_sd = group.groupby("player_id")["points"].std().dropna()
            if not weekly_sd.empty:
                sds[position] = float(weekly_sd.mean())

        return means, sds

    def _average_implied_total(self) -> float:
        """League-average implied team total, used to normalise game context."""
        season = self.config.season
        games = self.schedule[self.schedule["season"] == season]
        totals = pd.to_numeric(games["total_line"], errors="coerce").dropna()
        if totals.empty:
            # Early in a season no line is posted yet; the previous season's
            # scoring environment is the best available stand-in.
            previous = self.schedule[self.schedule["season"] == season - 1]
            totals = pd.to_numeric(previous["total_line"], errors="coerce").dropna()
        if totals.empty:
            return 22.5
        return float(totals.mean() / 2.0)

    # ------------------------------------------------------------------
    # player-rate estimation
    # ------------------------------------------------------------------
    def _player_rates(self) -> pd.DataFrame:
        """Shrunk per-game scoring rate and volatility for every known player."""
        cur = _per_player_summary(self.current).add_suffix("_cur")
        pri = _per_player_summary(self.prior).add_suffix("_pri")

        rates = cur.join(pri, how="outer")
        rates = rates.join(_identity_table(self.current, self.prior), how="left")

        rates["position"] = rates["position"].fillna("UNK")
        pos_mean = rates["position"].map(self.position_mean)
        pos_sd = rates["position"].map(self.position_sd)
        pos_mean = pos_mean.fillna(float(np.mean(list(self.position_mean.values()))))
        pos_sd = pos_sd.fillna(float(np.mean(list(self.position_sd.values()))))

        n_cur = rates["games_cur"].fillna(0.0)
        r_cur = rates["mean_cur"].fillna(0.0)
        n_pri = rates["games_pri"].fillna(0.0)
        r_pri = rates["mean_pri"].fillna(0.0)

        # Last season counts as a fixed quantum of evidence rather than one
        # scaled by how many games it contained, so its influence fades
        # naturally as the current season accumulates.
        w_pri = np.where(n_pri > 0, self._prior_season_weight, 0.0)
        k0 = self._replacement_weight

        numerator = n_cur * r_cur + w_pri * r_pri + k0 * pos_mean
        denominator = n_cur + w_pri + k0
        rates["rate"] = numerator / denominator


        # Volatility: a player's own spread once he has enough games, otherwise
        # the position's. Scale it with the player's level so that a 20 ppg back
        # is not handed a replacement-level standard deviation.
        own_sd = rates[["sd_cur", "sd_pri"]].mean(axis=1)
        enough = (n_cur + n_pri) >= 5
        # Scale volatility with the player's level, against the position's
        # median rate rather than replacement level (which is near zero and
        # would blow the ratio up).
        median_rate = rates.groupby("position")["rate"].transform("median").clip(lower=1.0)
        scaled_pos_sd = pos_sd * (rates["rate"] / median_rate).clip(0.5, 1.8)
        rates["sd"] = np.where(enough & own_sd.notna(), own_sd, scaled_pos_sd)
        rates["sd"] = pd.to_numeric(rates["sd"], errors="coerce").fillna(pos_sd).clip(lower=1.0)

        return rates.reset_index().rename(columns={"index": "player_id"})

    # ------------------------------------------------------------------
    # game context
    # ------------------------------------------------------------------
    def team_context(self, week: int) -> pd.DataFrame:
        """Per-team implied total and opponent for a given week.

        Teams absent from the result are on bye.
        """
        season = self.config.season
        games = self.schedule[
            (self.schedule["season"] == season) & (self.schedule["week"] == week)
        ].copy()
        if games.empty:
            return pd.DataFrame(columns=["team", "opponent", "implied_total", "is_home"])

        total = pd.to_numeric(games["total_line"], errors="coerce")
        spread = pd.to_numeric(games["spread_line"], errors="coerce")
        # Verified against 2018-2025 outcomes: spread_line is from the home
        # team's perspective (positive => home favoured).
        fallback_total = self._league_avg_implied * 2.0
        total = total.fillna(fallback_total)
        spread = spread.fillna(0.0)

        home = pd.DataFrame(
            {
                "team": games["home_team"],
                "opponent": games["away_team"],
                "implied_total": total / 2.0 + spread / 2.0,
                "is_home": True,
            }
        )
        away = pd.DataFrame(
            {
                "team": games["away_team"],
                "opponent": games["home_team"],
                "implied_total": total / 2.0 - spread / 2.0,
                "is_home": False,
            }
        )
        ctx = pd.concat([home, away], ignore_index=True)
        opp_totals = ctx.set_index("team")["implied_total"]
        ctx["opponent_implied_total"] = ctx["opponent"].map(opp_totals)
        return ctx

    def _context_multiplier(self, position: str, own: float, opp: float) -> float:
        """Scale a player's baseline rate by how good this week's spot is."""
        league_avg = self._league_avg_implied
        if league_avg <= 0:
            return 1.0

        if position == "DEF":
            # A defense wants a bad opposing offense, so the ratio inverts.
            reference = opp if np.isfinite(opp) and opp > 0 else league_avg
            ratio = league_avg / reference
        else:
            reference = own if np.isfinite(own) and own > 0 else league_avg
            ratio = reference / league_avg

        exponent = CONTEXT_EXPONENT.get(position, DEFAULT_CONTEXT_EXPONENT)
        return float(np.clip(ratio**exponent, *CONTEXT_CLAMP))

    # ------------------------------------------------------------------
    # projections
    # ------------------------------------------------------------------
    def project_weeks(self, weeks: list[int], *, as_of_week: int) -> pd.DataFrame:
        """Project every eligible player across `weeks`.

        `as_of_week` anchors how far into the future each week is, which drives
        the mean-reversion of today's injury information.
        """
        rates = self._player_rates()
        rates = rates[rates["position"].isin(self.config.positions_used)]
        rates = rates[rates["rate"] > 0]

        report = latest_injury_report(self.injuries, self.config.season, as_of_week)
        report = report.rename(columns={"gsis_id": "player_id"})
        status = report.set_index("player_id")["report_status"].to_dict()
        practice = report.set_index("player_id")["practice_status"].to_dict()

        team_now = self._current_team_map()

        rows: list[dict] = []
        for week in weeks:
            ctx = self.team_context(week)
            if ctx.empty:
                continue
            ctx_by_team = ctx.set_index("team")
            weeks_ahead = max(0, week - as_of_week)

            for rec in rates.itertuples(index=False):
                team = team_now.get(rec.player_id, getattr(rec, "team", None))
                if not isinstance(team, str) or team not in ctx_by_team.index:
                    continue  # bye week, free agent, or unknown team

                spot = ctx_by_team.loc[team]
                own_total = float(spot["implied_total"])
                opp_total = float(spot.get("opponent_implied_total", np.nan))

                # Absent from the report entirely is a different state from
                # listed-without-a-game-status, and they have different play
                # rates, so they must not collapse to the same blank string.
                # (`.get` can also hand back a NaN pandas stored for a blank
                # cell, hence the normalise.)
                if rec.player_id in status:
                    report_status = _clean(status[rec.player_id]) or "(none)"
                    practice_status = _clean(practice.get(rec.player_id, ""))
                else:
                    report_status = NOT_ON_REPORT
                    practice_status = ""

                if rec.position == "DEF":
                    # A unit is never "questionable"; individual injuries are
                    # already reflected in the market line.
                    avail_now = 1.0
                    effectiveness = 1.0
                else:
                    avail_now = self.availability.play_probability(report_status, practice_status)
                    effectiveness = self.availability.effectiveness_multiplier(report_status)

                avail = self._future_availability(rec.player_id, rec.position, weeks_ahead, avail_now)

                # Shrinkage tuned for ranking leaves the level biased low, by
                # more early in a season than late. The correction is uniform
                # across everyone in this week, so it moves no ranking -- it
                # only restores a scale on which a lineup total and a cut line
                # are comparable.
                calibrated_rate = _calibrate_level(float(rec.rate), rec.position, week)

                multiplier = self._context_multiplier(rec.position, own_total, opp_total)
                conditional_mean = calibrated_rate * multiplier * effectiveness
                # Spread is rebuilt from the calibrated mean rather than carried
                # over from the player's raw history. Pairing a shrunk 14-point
                # projection with the 13-point spread of a 24-point player makes
                # every lineup look far riskier than it is, and the survival
                # maths is driven by exactly that ratio.
                conditional_sd = _volatility_for(
                    conditional_mean, rec.position, fallback=float(rec.sd) * multiplier
                )

                mean, sd = _mixture_moments(avail, conditional_mean, conditional_sd)

                rows.append(
                    {
                        "player_id": rec.player_id,
                        "name": getattr(rec, "name", rec.player_id),
                        "position": rec.position,
                        "team": team,
                        "week": week,
                        "mean": mean,
                        "sd": sd,
                        "availability": avail,
                        "conditional_mean": conditional_mean,
                        "opponent": str(spot["opponent"]),
                        "implied_team_total": own_total,
                        "report_status": "" if report_status in (NOT_ON_REPORT, "(none)") else report_status,
                    }
                )

        return pd.DataFrame(rows)

    def _future_availability(
        self, player_id: str, position: str, weeks_ahead: int, current: float
    ) -> float:
        """P(available) `weeks_ahead` out, from the fitted hazard where possible.

        The injury report still owns week zero -- it is by far the sharpest
        signal for the game in front of you, and the hazard model is not fitted
        to beat it there. Beyond that no report exists yet, which is exactly
        where the fitted chain earns its keep.
        """
        if weeks_ahead <= 0:
            return current

        curves = self.availability_curves
        if curves is not None and not curves.empty:
            column = f"h{weeks_ahead}"
            if column in curves.columns and player_id in curves.index:
                value = curves.at[player_id, column]
                if pd.notna(value):
                    return float(value)

        return self.availability.future_availability(position, weeks_ahead, current)

    def _current_team_map(self) -> dict[str, str]:
        """Player -> team, preferring the most recent source available.

        Rosters reflect offseason moves that last season's stat lines do not.
        """
        mapping: dict[str, str] = {}
        for frame in (self.prior, self.current):
            if frame.empty:
                continue
            latest = frame.sort_values("week").groupby("player_id")["team"].last()
            mapping.update(latest.dropna().to_dict())

        if not self.rosters.empty and "gsis_id" in self.rosters.columns:
            roster_map = (
                self.rosters.dropna(subset=["gsis_id"])
                .sort_values("week" if "week" in self.rosters.columns else "gsis_id")
                .groupby("gsis_id")["team"]
                .last()
            )
            mapping.update(roster_map.dropna().to_dict())

        # Team defenses are keyed by a synthetic id and never change teams.
        for team in set(mapping.values()):
            mapping[f"DEF_{team}"] = team
        return mapping


def _volatility_for(mean: float, position: str, *, fallback: float) -> float:
    """Weekly standard deviation implied by a player's scoring level.

    Measured over 2021-2024: the ratio of spread to mean falls steadily with
    level, from roughly 1.2 at replacement to 0.45 for a 20-point starter.
    """
    entry = (fitted.get().raw.get("volatility") or {}).get(position)
    if not entry:
        return max(fallback, 1.0)
    return float(max(entry["slope"] * mean + entry["intercept"], 1.0))


def _calibrate_level(rate: float, position: str, week: int) -> float:
    """Put a shrunk rate back on the scale of real fantasy points."""
    from .calibration import week_bucket

    calibration = fitted.get().raw.get("rate_calibration") or {}
    entry = calibration.get(f"{position}|{week_bucket(week)}")
    if not entry:
        return rate
    # Never return a negative expectation: a player cannot be worse than nothing
    # for lineup purposes, and the optimizer treats <=0 as "not a candidate".
    return max(0.0, rate * entry["slope"] + entry["intercept"])


def _mask_future_data(
    schedule: pd.DataFrame, season: int, *, lines_through: int, scores_through: int
) -> pd.DataFrame:
    """Hide anything that would not have been knowable yet.

    Two different horizons, and conflating them is a subtle way to leak: a
    sportsbook posts lines several weeks ahead, but a final score is only known
    once the game has actually been played.
    """
    masked = schedule.copy()
    in_season = masked["season"] == season

    masked.loc[in_season & (masked["week"] > lines_through), ["spread_line", "total_line"]] = np.nan
    # Final scores feed the points-allowed table behind team-defense scoring.
    masked.loc[
        in_season & (masked["week"] > scores_through),
        ["home_score", "away_score", "result", "total"],
    ] = np.nan
    return masked


def _mixture_moments(availability: float, mean: float, sd: float) -> tuple[float, float]:
    """Moments of a player's points when he might not play at all.

    Points are `N(mean, sd^2)` with probability `availability` and exactly zero
    otherwise.  The did-not-play branch adds variance beyond the on-field
    volatility -- which is precisely the risk a survivor lineup must respect.
    """
    a = float(np.clip(availability, 0.0, 1.0))
    expected = a * mean
    second_moment = a * (sd**2 + mean**2)
    variance = max(second_moment - expected**2, 0.0)
    return expected, float(np.sqrt(variance))


def _per_player_summary(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["games", "mean", "sd"]).rename_axis("player_id")
    grouped = frame.groupby("player_id")["points"].agg(games="count", mean="mean", sd="std")
    return grouped


def _identity_table(*frames: pd.DataFrame) -> pd.DataFrame:
    """Latest known name/position/team for each player id."""
    parts = []
    for frame in frames:
        if frame.empty:
            continue
        cols = ["player_id", "player_display_name", "position", "team", "week"]
        available = [c for c in cols if c in frame.columns]
        parts.append(frame[available])
    if not parts:
        return pd.DataFrame(columns=["name", "position", "team"]).rename_axis("player_id")

    combined = pd.concat(parts, ignore_index=True)
    if "week" in combined.columns:
        combined = combined.sort_values("week")
    latest = combined.groupby("player_id").last()
    latest = latest.rename(columns={"player_display_name": "name"})
    for col in ("name", "position", "team"):
        if col not in latest.columns:
            latest[col] = None
    return latest[["name", "position", "team"]]
