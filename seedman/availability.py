"""Will this player be on the field in week t+h?

The model this replaces was two lines: today's availability decaying toward a
hand-set position baseline at a hand-set rate of 0.7 a week. Measured against a
perfect-foresight oracle, availability error costs about **12.6 points a week**
across four skill slots at planning horizons -- several times the gap between
these projections and a naive season-to-date average, and by a wide margin the
largest remaining source of error. In a format where you commit a player to a
future week and cannot take him back, that is the number that matters.

**Why discrete time rather than a Cox proportional-hazards model.** Cox is the
natural instinct for "time to injury given history", and the covariate story is
identical, but three things about this data argue against it:

  * Events land on week boundaries, so essentially every failure time is tied.
    Cox's partial likelihood handles ties only by approximation (Efron, Breslow),
    and here the ties are not a nuisance -- they are the entire structure.
  * The optimizer consumes an absolute probability, not a hazard ratio. Cox
    gives the latter; turning it into the former needs a separately estimated
    baseline hazard, which is the part a discrete-time model estimates directly.
  * Availability is recurrent and reversible: players get hurt, come back, get
    hurt again. A single time-to-first-event Cox throws away everything after
    the first injury, and the extensions that do not (Andersen-Gill, PWP) end up
    close to what is written here anyway.

So: a two-state discrete-time Markov model. One logistic hazard for "plays next
week given he played this week", another for "plays next week given he did not",
each with its own covariates. Multi-week availability is the chain iterated
forward, which is the principled version of what the old AR(1) was imitating --
and it can express things the AR(1) structurally could not, such as a player
being out now but very likely back in three weeks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

SKILL_POSITIONS = ("QB", "RB", "WR", "TE")

# Injury-report designations, ordered by severity, as one-hot columns.
DESIGNATIONS = ("Out", "Doubtful", "Questionable", "(none)", "not on report")
PRACTICE = (
    "Did Not Participate In Practice",
    "Limited Participation in Practice",
    "Full Participation in Practice",
)

FEATURES = [
    "age",
    "years_exp",
    "missed_last_4",
    "missed_rate_season",
    "weeks_since_miss",
    "snap_share_recent",
    "points_recent",
    "workload_recent",
]


@dataclass
class PanelRow:
    """Documentation of the panel's shape; construction happens in build_panel."""

    player_id: str
    season: int
    week: int
    played_now: bool
    played_next: bool


def build_panel(
    seasons: list[int], cache_dir: Path | str, config, *, min_games_seen: int = 1
) -> pd.DataFrame:
    """One row per player-week, with next week's availability as the outcome.

    Only weeks in which the player's team actually plays are included -- a bye is
    not an availability question and counting it as a miss corrupts both the
    outcome and the injury-history covariates.

    `min_games_seen` implements the rookie rule: a player enters the panel only
    once he has appeared, so we never model someone we have never seen. This
    selects on having played, which is exactly the population we ever start.
    """
    from .scoring import score_players

    cache = Path(cache_dir)
    frames = []

    for season in seasons:
        stats = pd.read_csv(cache / f"stats_player_week_{season}.csv", low_memory=False)
        snaps = pd.read_csv(cache / f"snap_counts_{season}.csv", low_memory=False)
        roster = pd.read_csv(cache / f"roster_{season}.csv", low_memory=False)
        injuries = pd.read_csv(cache / f"injuries_{season}.csv", low_memory=False)
        schedule = pd.read_csv(cache / "games.csv", low_memory=False)

        stats = _regular(stats).copy()
        stats["points"] = score_players(stats, config.scoring)
        injuries = _regular(injuries)

        bridge = roster.dropna(subset=["gsis_id", "pfr_id"])
        snaps = _regular(snaps).copy()
        snaps["gsis_id"] = snaps["pfr_player_id"].map(
            dict(zip(bridge["pfr_id"], bridge["gsis_id"]))
        )
        snaps = snaps[snaps["gsis_id"].notna() & snaps["position"].isin(SKILL_POSITIONS)]

        on_field = snaps[snaps["offense_snaps"].fillna(0) > 0]
        played = set(map(tuple, on_field[["gsis_id", "week"]].to_numpy()))
        snap_pct = (
            snaps.set_index(["gsis_id", "week"])["offense_pct"].astype(float).to_dict()
        )

        games = schedule[(schedule["season"] == season) & (schedule["game_type"] == "REG")]
        teams_by_week = {
            int(w): set(g["home_team"]) | set(g["away_team"]) for w, g in games.groupby("week")
        }
        max_week = int(max(teams_by_week)) if teams_by_week else 0

        report = {
            (r.gsis_id, r.week): (
                _text(getattr(r, "report_status", None)) or "(none)",
                _text(getattr(r, "practice_status", None)) or "",
            )
            for r in injuries.itertuples()
        }

        # Player attributes that do not vary within a season.
        info = roster.dropna(subset=["gsis_id"]).drop_duplicates("gsis_id").set_index("gsis_id")
        birth = pd.to_datetime(info.get("birth_date"), errors="coerce")
        age = ((pd.Timestamp(f"{season}-09-01") - birth).dt.days / 365.25).to_dict()
        exp = pd.to_numeric(info.get("years_exp"), errors="coerce").to_dict()
        position = info["position"].to_dict()
        team_of = on_field.sort_values("week").groupby("gsis_id")["team"].last().to_dict()

        points = stats.set_index(["player_id", "week"])["points"].to_dict()
        touches = (
            stats.assign(
                _t=pd.to_numeric(stats.get("carries"), errors="coerce").fillna(0)
                + pd.to_numeric(stats.get("targets"), errors="coerce").fillna(0)
            )
            .set_index(["player_id", "week"])["_t"]
            .to_dict()
        )

        candidates = sorted({pid for pid, _ in played})
        for pid in candidates:
            if position.get(pid) not in SKILL_POSITIONS:
                continue
            team = team_of.get(pid)
            weeks_played = sorted(w for p, w in played if p == pid)
            if len(weeks_played) < min_games_seen:
                continue
            debut = weeks_played[0]

            history: list[int] = []  # 1 = played, 0 = missed, in week order
            last_miss_week: int | None = None

            for week in range(debut, max_week + 1):
                if team not in teams_by_week.get(week, set()):
                    continue  # bye: not an availability question
                now = (pid, week) in played
                nxt_week = week + 1
                if team not in teams_by_week.get(nxt_week, set()):
                    nxt_week += 1  # skip a bye when looking ahead
                has_next = nxt_week <= max_week and team in teams_by_week.get(nxt_week, set())

                status, practice = report.get((pid, week), ("not on report", ""))
                recent = history[-4:]
                seen = len(history)

                row = {
                    "player_id": pid,
                    "season": season,
                    "week": week,
                    "position": position.get(pid),
                    "played_now": now,
                    "played_next": ((pid, nxt_week) in played) if has_next else np.nan,
                    "age": age.get(pid, np.nan),
                    "years_exp": exp.get(pid, np.nan),
                    "missed_last_4": float(sum(1 for h in recent if h == 0)),
                    "missed_rate_season": (
                        float(sum(1 for h in history if h == 0) / seen) if seen else 0.0
                    ),
                    "weeks_since_miss": (
                        float(week - last_miss_week) if last_miss_week else float(seen + 1)
                    ),
                    "snap_share_recent": float(
                        np.mean(
                            [snap_pct.get((pid, w), 0.0) for w in range(max(1, week - 2), week + 1)]
                        )
                    ),
                    "points_recent": float(
                        np.mean([points.get((pid, w), 0.0) for w in range(max(1, week - 3), week + 1)])
                    ),
                    "workload_recent": float(
                        np.mean([touches.get((pid, w), 0.0) for w in range(max(1, week - 3), week + 1)])
                    ),
                    "status": status,
                    "practice": practice,
                }
                frames.append(row)

                history.append(1 if now else 0)
                if not now:
                    last_miss_week = week

    panel = pd.DataFrame(frames)
    return panel.dropna(subset=["played_next"])


def _regular(frame: pd.DataFrame) -> pd.DataFrame:
    for column in ("season_type", "game_type"):
        if column in frame.columns:
            return frame[frame[column] == "REG"]
    return frame


def _text(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "na"} else text


def design_matrix(panel: pd.DataFrame) -> tuple[np.ndarray, list[str]]:
    """Numeric features plus one-hot designation, practice and position."""
    parts = [panel[FEATURES].astype(float).fillna(0.0).to_numpy()]
    names = list(FEATURES)

    for value in DESIGNATIONS:
        parts.append((panel["status"] == value).astype(float).to_numpy()[:, None])
        names.append(f"status={value}")
    for value in PRACTICE:
        parts.append((panel["practice"] == value).astype(float).to_numpy()[:, None])
        names.append(f"practice={value.split()[0]}")
    for value in SKILL_POSITIONS:
        parts.append((panel["position"] == value).astype(float).to_numpy()[:, None])
        names.append(f"pos={value}")

    return np.hstack(parts), names


@dataclass
class AvailabilityHazard:
    """Two-state discrete-time Markov model of weekly availability.

    Two logistic hazards, fitted separately because the populations behave
    nothing alike: among players who suited up last week, 88.4% suit up again;
    among those who did not, only 23.7% do. Collapsing that into one equation
    with a state dummy would force a single set of slopes onto both regimes.

    Multi-week availability is the chain iterated forward from the player's
    current state. This is what the AR(1) it replaces could not express: that
    model pulled everyone monotonically toward a fixed position baseline, so a
    player four weeks into an injury and a player who missed last week converged
    at the same rate. The data says otherwise -- P(returns next week) falls from
    36% after one missed game to 15% after four -- and a Markov chain whose
    recovery hazard depends on how long a player has been out reproduces that
    duration dependence directly.
    """

    continuation: object | None = None  # P(plays | played last week)
    recovery: object | None = None  # P(plays | missed last week)
    feature_names: list[str] = field(default_factory=list)
    fitted_seasons: list[int] = field(default_factory=list)
    n_continuation: int = 0
    n_recovery: int = 0

    def fit(self, panel: pd.DataFrame) -> AvailabilityHazard:
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
        from sklearn.pipeline import make_pipeline

        played = panel[panel["played_now"]]
        missed = panel[~panel["played_now"]]

        x_played, names = design_matrix(played)
        x_missed, _ = design_matrix(missed)
        self.feature_names = names
        self.n_continuation = len(played)
        self.n_recovery = len(missed)
        self.fitted_seasons = sorted(panel["season"].unique().tolist())

        def _model():
            # Modest regularisation: several covariates are near-collinear
            # (missed_last_4 against weeks_since_miss, snaps against touches).
            return make_pipeline(
                StandardScaler(), LogisticRegression(C=1.0, max_iter=2000)
            )

        self.continuation = _model().fit(x_played, played["played_next"].astype(int))
        self.recovery = _model().fit(x_missed, missed["played_next"].astype(int))
        return self

    # ------------------------------------------------------------------
    def transition_probabilities(self, panel: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """Per-row P(plays next | played now) and P(plays next | missed now)."""
        features, _ = design_matrix(panel)
        a = self.continuation.predict_proba(features)[:, 1]
        b = self.recovery.predict_proba(features)[:, 1]
        return a, b

    def availability_curve(self, panel: pd.DataFrame, horizons: int = 8) -> np.ndarray:
        """P(available) at h = 1..`horizons` weeks ahead, one row per input row.

        Iterating ``p <- a * p + b * (1 - p)`` from the player's current state.
        The chain's fixed point, ``b / (1 - a + b)``, is that player's own
        long-run availability -- so the position-level baseline the old model
        had to be told now falls out of his history instead.
        """
        a, b = self.transition_probabilities(panel)
        state = panel["played_now"].to_numpy(dtype=float)

        curve = np.zeros((len(panel), horizons))
        p = state
        for h in range(horizons):
            p = a * p + b * (1.0 - p)
            curve[:, h] = p
        return curve


    def curves_for_week(
        self, panel: pd.DataFrame, season: int, week: int, horizons: int = 8
    ) -> pd.DataFrame:
        """Availability curves for every player, as known at `week`.

        Returns one row per player and one column per horizon, indexed by player
        id so the projection model can look up `P(available at week + h)`
        directly. Players with no row that week (bye, or not yet debuted) are
        simply absent and the caller falls back to its prior.
        """
        rows = panel[(panel["season"] == season) & (panel["week"] == week)]
        if rows.empty:
            return pd.DataFrame()

        curve = self.availability_curve(rows, horizons=horizons)
        out = pd.DataFrame(
            curve, columns=[f"h{h}" for h in range(1, horizons + 1)],
            index=rows["player_id"].to_numpy(),
        )
        out.index.name = "player_id"
        return out[~out.index.duplicated(keep="last")]


    # ------------------------------------------------------------------
    # serialisation
    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Plain-data form: standardiser moments plus logistic coefficients.

        Stored as numbers in `fitted.yaml` rather than a pickled estimator, for
        the same reason every other fitted constant is: a pickle breaks silently
        when scikit-learn changes, cannot be read by a human checking whether a
        coefficient is sane, and would make scikit-learn a runtime dependency
        for what is ultimately a dot product and a logistic.
        """
        def _dump(pipeline, n):
            scaler, logistic = pipeline[0], pipeline[-1]
            return {
                "mean": [round(float(v), 6) for v in scaler.mean_],
                "scale": [round(float(v), 6) for v in scaler.scale_],
                "coef": [round(float(v), 6) for v in logistic.coef_[0]],
                "intercept": round(float(logistic.intercept_[0]), 6),
                "n": int(n),
            }

        return {
            "features": list(self.feature_names),
            "fitted_on": list(self.fitted_seasons),
            "continuation": _dump(self.continuation, self.n_continuation),
            "recovery": _dump(self.recovery, self.n_recovery),
        }


@dataclass
class FittedHazard:
    """Runtime side of `AvailabilityHazard`: evaluates stored coefficients.

    Deliberately has no scikit-learn import. Fitting is an offline job; scoring
    is standardise, dot, logistic.
    """

    features: list[str]
    continuation: dict
    recovery: dict
    fitted_on: list[int] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict) -> FittedHazard | None:
        if not raw or "continuation" not in raw or "recovery" not in raw:
            return None
        return cls(
            features=list(raw.get("features", [])),
            continuation=raw["continuation"],
            recovery=raw["recovery"],
            fitted_on=list(raw.get("fitted_on", [])),
        )

    def _predict(self, block: dict, features: np.ndarray) -> np.ndarray:
        mean = np.asarray(block["mean"], dtype=float)
        scale = np.asarray(block["scale"], dtype=float)
        coef = np.asarray(block["coef"], dtype=float)
        scale = np.where(scale == 0, 1.0, scale)
        z = ((features - mean) / scale) @ coef + float(block["intercept"])
        return 1.0 / (1.0 + np.exp(-z))

    def availability_curve(self, panel: pd.DataFrame, horizons: int = 8) -> np.ndarray:
        features, names = design_matrix(panel)
        if names != self.features:
            raise ValueError(
                "fitted hazard was built on different features; re-run "
                "`seedman calibrate --fit-hazard`"
            )
        a = self._predict(self.continuation, features)
        b = self._predict(self.recovery, features)

        curve = np.zeros((len(panel), horizons))
        p = panel["played_now"].to_numpy(dtype=float)
        for h in range(horizons):
            p = a * p + b * (1.0 - p)
            curve[:, h] = p
        return curve

    def curves_for_week(
        self, panel: pd.DataFrame, season: int, week: int, horizons: int = 8
    ) -> pd.DataFrame:
        rows = panel[(panel["season"] == season) & (panel["week"] == week)]
        if rows.empty:
            return pd.DataFrame()
        curve = self.availability_curve(rows, horizons=horizons)
        out = pd.DataFrame(
            curve,
            columns=[f"h{h}" for h in range(1, horizons + 1)],
            index=rows["player_id"].to_numpy(),
        )
        out.index.name = "player_id"
        return out[~out.index.duplicated(keep="last")]
