"""Availability modelling: will this player actually be on the field?

Two different questions, answered separately:

1. **This week.**  The official NFL injury report gives a designation
   (Out / Doubtful / Questionable / none) plus practice participation.  Since
   2016 the league dropped "Probable" and stopped attaching fixed percentages to
   the remaining tags, so the historical "doubtful = 25%" definition no longer
   holds; measured over 2021-2024, Questionable players suit up 70.5% of the time
   and Doubtful players 0.9%.  Practice participation is the
   sharpest tiebreaker within Questionable: a full practice on Friday is close
   to a clean bill of health, a DNP is close to a scratch.

2. **Future weeks.**  No injury report exists yet, so we mean-revert: today's
   health decays toward a position-typical baseline availability.  Running backs
   miss the most time, kickers almost none, and a team defense always plays.
   This is what stops the optimizer from happily banking an injured star for
   week 14 as though he were certain to be there.

The constants are **fitted from historical data** by `seedman calibrate` and
loaded from `configs/fitted.yaml`; the values written here are fallback priors
used only where a bucket is too thin to trust. Checking them was worthwhile: the
prior for Doubtful was 0.06, taken from the NFL's pre-2016 published definition,
and the measured rate over 2021-2024 is **0.0085** -- a seven-fold error on a
designation that, it turns out, means "not playing".
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from . import fitted

# Two distinct populations that a single blank string used to conflate. A player
# the team never listed is not the same as one listed with a practice line but no
# game status -- counterintuitively the second is *more* likely to play (0.95 vs
# 0.92 over 2021-2024), because being listed and left undesignated means the team
# has actively cleared him.
NOT_ON_REPORT = "not on report"
NO_DESIGNATION = "(none)"

# Hand-set priors, used only where the fit has too little data to trust. Each is
# superseded by `configs/fitted.yaml` when a bucket clears `min_sample_for_use`.
REPORT_STATUS_PRIOR: dict[str, float] = {
    "Out": 0.0,
    "Doubtful": 0.02,
    "Questionable": 0.70,
    NO_DESIGNATION: 0.95,
    NOT_ON_REPORT: 0.92,
    "": 0.92,
}

# Direct P(plays) per (status, practice) cell. Preferred over the multiplier
# form below because the fit measures these cells directly.
PRACTICE_PRIOR: dict[tuple[str, str], float] = {
    ("Questionable", "Full Participation in Practice"): 0.82,
    ("Questionable", "Limited Participation in Practice"): 0.76,
    ("Questionable", "Did Not Participate In Practice"): 0.46,
    (NO_DESIGNATION, "Full Participation in Practice"): 0.97,
    (NO_DESIGNATION, "Limited Participation in Practice"): 0.96,
    (NO_DESIGNATION, "Did Not Participate In Practice"): 0.82,
}

# Fallback multipliers for cells with no direct estimate.
PRACTICE_ADJUSTMENT: dict[str, float] = {
    "Full Participation in Practice": 1.17,
    "Limited Participation in Practice": 1.08,
    "Did Not Participate In Practice": 0.66,
}

# Production relative to the player's own season average, given he played.
EFFECTIVENESS_WHEN_PLAYING: dict[str, float] = {
    "Out": 0.0,
    "Doubtful": 0.50,
    "Questionable": 0.86,
    NO_DESIGNATION: 0.95,
    NOT_ON_REPORT: 1.0,
    "": 1.0,
}

# Long-run share of games a healthy-today player at this position is available
# for, used as the mean-reversion target for future weeks.
BASELINE_AVAILABILITY: dict[str, float] = {
    "QB": 0.93,
    "RB": 0.87,
    "WR": 0.89,
    "TE": 0.89,
    "K": 0.98,
    "DEF": 1.00,
}
DEFAULT_BASELINE_AVAILABILITY = 0.90

# How much of this week's health signal survives one week into the future.
HEALTH_PERSISTENCE = 0.70


def _fitted_status_priors() -> dict[str, float]:
    """Report-status probabilities, fitted where the sample supports it."""
    constants = fitted.get()
    out = dict(REPORT_STATUS_PRIOR)
    for status in ("Out", "Doubtful", "Questionable", NO_DESIGNATION, NOT_ON_REPORT):
        out[status] = constants.value(
            f"availability.by_report_status.{status}",
            REPORT_STATUS_PRIOR[status],
            label=f"availability[{status}]",
        )
    out[""] = out[NOT_ON_REPORT]
    return out


def _fitted_practice_priors() -> dict[tuple[str, str], float]:
    constants = fitted.get()
    out = dict(PRACTICE_PRIOR)
    sources = {
        "Questionable": "availability.questionable_by_practice",
        NO_DESIGNATION: "availability.undesignated_by_practice",
    }
    for status, prefix in sources.items():
        for practice in PRACTICE_ADJUSTMENT:
            key = (status, practice)
            out[key] = constants.value(
                f"{prefix}.{practice}",
                PRACTICE_PRIOR.get(key, REPORT_STATUS_PRIOR[status]),
                label=f"availability[{status} / {practice.split()[0]}]",
            )
    return out


def _fitted_effectiveness() -> dict[str, float]:
    constants = fitted.get()
    out = dict(EFFECTIVENESS_WHEN_PLAYING)
    for status in ("Questionable", NO_DESIGNATION, "Doubtful"):
        out[status] = constants.value(
            f"effectiveness_given_played.{status}",
            EFFECTIVENESS_WHEN_PLAYING[status],
            label=f"effectiveness[{status}]",
        )
    return out


@dataclass
class AvailabilityModel:
    report_status_prior: dict[str, float] = field(default_factory=_fitted_status_priors)
    practice_prior: dict[tuple[str, str], float] = field(
        default_factory=_fitted_practice_priors
    )
    practice_adjustment: dict[str, float] = field(
        default_factory=lambda: dict(PRACTICE_ADJUSTMENT)
    )
    effectiveness: dict[str, float] = field(default_factory=_fitted_effectiveness)
    baseline_availability: dict[str, float] = field(
        default_factory=lambda: dict(BASELINE_AVAILABILITY)
    )
    persistence: float = HEALTH_PERSISTENCE

    # ------------------------------------------------------------------
    def play_probability(self, report_status: str | None, practice_status: str | None) -> float:
        """P(player appears in this week's game) given the official report.

        A directly measured (status, practice) cell wins; otherwise the status
        prior is nudged by a practice multiplier. `Out` short-circuits, because
        no amount of Friday practice un-rules-out a ruled-out player.
        """
        status = _clean(report_status) or NOT_ON_REPORT
        practice = _clean(practice_status)

        if status == "Out":
            return 0.0

        direct = self.practice_prior.get((status, practice))
        if direct is not None:
            return float(min(max(direct, 0.0), 1.0))

        base = self.report_status_prior.get(status, self.report_status_prior[NOT_ON_REPORT])
        if practice and status in {"Questionable", NO_DESIGNATION}:
            base *= self.practice_adjustment.get(practice, 1.0)
        return float(min(max(base, 0.0), 1.0))

    def effectiveness_multiplier(self, report_status: str | None) -> float:
        """Expected production multiplier *given that* the player suits up."""
        return self.effectiveness.get(_clean(report_status) or NOT_ON_REPORT, 1.0)

    def future_availability(self, position: str, weeks_ahead: int, current: float) -> float:
        """P(available) `weeks_ahead` from now, reverting toward position baseline.

        `weeks_ahead == 0` returns `current` unchanged; further out, an injured
        player recovers toward baseline and a healthy one accumulates risk.
        """
        if weeks_ahead <= 0:
            return current
        baseline = self.baseline_availability.get(position, DEFAULT_BASELINE_AVAILABILITY)
        decay = self.persistence**weeks_ahead
        return float(baseline + (current - baseline) * decay)


def _clean(value: str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "na"} else text


def latest_injury_report(injuries: pd.DataFrame, season: int, week: int) -> pd.DataFrame:
    """The most recent injury report at or before `week`, one row per player.

    Falling back to an earlier week matters midweek, when this week's report has
    not been filed yet but last week's is still the best evidence available.
    """
    if injuries.empty:
        return pd.DataFrame(columns=["gsis_id", "report_status", "practice_status", "week"])

    frame = injuries[(injuries["season"] == season) & (injuries["week"] <= week)].copy()
    if frame.empty:
        return pd.DataFrame(columns=["gsis_id", "report_status", "practice_status", "week"])

    frame = frame.sort_values("week")
    latest = frame.groupby("gsis_id", as_index=False).last()
    return latest[["gsis_id", "report_status", "practice_status", "week", "position", "team"]]


# ----------------------------------------------------------------------
# Roster status: the gate the injury report cannot see
# ----------------------------------------------------------------------
# A player on injured reserve drops OFF the weekly injury report entirely. With
# no practice to participate in there is nothing to report, so the model above
# reads him as "not on report" -- which is the *healthiest* state it knows
# (0.92) -- and prices a man on IR as a starter.
#
# This is not a rounding error at the margin. It is the difference between a
# lineup slot and a certain zero, and it put A.J. Brown, who has been on IR
# since the roster cutdowns, into a recommended week 2 lineup. 96 skill players
# were in a non-playable status this week and every one of them was priced as
# healthy.
#
# The answer was sitting in a roster column nothing was reading. Measured
# against this season's own week 1 box scores -- did a player carrying this
# status on the current roster actually appear in a game?
#
#     ACT  0.695 (n=502)    DEV  0.043 (n=187)    RES  0.052 (n=77)
#     INA / EXE / RET / CUT  0.000 (n=19)
#
# ACT's 0.695 is not an availability number and is deliberately absent below:
# it is third-string quarterbacks who dressed and never took a snap, which the
# usage model already handles. The rest are, and they are what this encodes.
# RES is not quite zero because four of those players were placed on reserve
# *after* playing in week 1, which is the correct reading of that cell rather
# than noise in it.
#
# Historical seasons cannot check this. Their roster files carry one row per
# player holding the status he ended the season on, not a week-by-week panel,
# so the only honest sample is the live one above.
ROSTER_STATUS_PLAY_RATE: dict[str, float] = {
    "RES": 0.052,  # reserve: injured / PUP / non-football injury / suspended
    "DEV": 0.043,  # practice squad, playable only via a gameday elevation
    "INA": 0.000,  # declared inactive for this week's game
    "EXE": 0.000,  # commissioner's exempt list (holdout, personal matter)
    "CUT": 0.000,  # waived or released; not on an NFL roster today
    "RET": 0.000,  # retired
}

# A status describes TODAY, and nothing in this data dates a return. The roster
# file is a live snapshot with no designation date on it, so a player placed on
# IR at the August cutdowns -- out for the season, ineligible to be activated at
# all -- is indistinguishable from one placed there last week under the league's
# four-game minimum. The abbreviation looked like it might separate them (R01
# against R48, "designated for return") and on this season's own box scores it
# does not: 0.065 on n=62 against 0.000 on n=17, which is one man placed on
# reserve *after* playing in week 1, not a signal.
#
# So the gate holds for the whole planning horizon rather than expiring into a
# guessed return curve. That is a deliberate trade and a cheap one. Letting it
# lapse after four weeks reverted A.J. Brown to 75% availability and pencilled
# him in for week 7 -- the same man this gate exists to keep out of a lineup.
# Nothing is permanently lost by the conservative reading: the plan is rebuilt
# every week, and a player who does come back flips to ACT on the next roster
# refresh and re-enters the pool at full value that same day. Only week one of
# the plan is ever acted on; the later weeks are a shape, not a commitment.


def roster_status_availability(status: str) -> float | None:
    """P(plays) given today's roster status, for every week of the horizon.

    Returns ``None`` where the status has nothing to say -- an active player or
    an unrecognised code -- and the injury report and hazard model answer
    instead. There is no horizon argument because there is no return curve to
    model; see above for why.
    """
    code = (status or "").strip().upper()
    return ROSTER_STATUS_PLAY_RATE.get(code)


# What to print for a status, so a lineup can be audited at a glance. The codes
# themselves are opaque, and "RES" in a column is not an answer to "why is this
# man in my lineup".
ROSTER_STATUS_LABEL: dict[str, str] = {
    "RES": "INJURED RESERVE",
    "DEV": "practice squad",
    "INA": "inactive",
    "EXE": "exempt list",
    "CUT": "released",
    "RET": "retired",
}


def roster_status_label(status: str) -> str:
    """A human-readable gate reason, or "" for a player the gate does not touch."""
    return ROSTER_STATUS_LABEL.get((status or "").strip().upper(), "")


def roster_status_map(rosters: pd.DataFrame) -> dict[str, str]:
    """Player id -> current roster status, from each player's latest snapshot.

    The roster file is a live snapshot rather than a panel: one row per player,
    stamped with the week his status was last touched. A player cut at the
    August deadline still carries week 1 while everyone else has moved on, so
    the latest row per player is the current truth -- not the latest week.
    """
    if rosters is None or rosters.empty:
        return {}
    if not {"gsis_id", "status"} <= set(rosters.columns):
        return {}

    frame = rosters.dropna(subset=["gsis_id"])
    if "week" in frame.columns:
        frame = frame.sort_values("week")
    frame = frame.drop_duplicates("gsis_id", keep="last")
    return {
        str(pid): str(code)
        for pid, code in zip(frame["gsis_id"], frame["status"])
        if isinstance(code, str) and code.strip()
    }


# ----------------------------------------------------------------------
# Returning from the injury that ended last season
# ----------------------------------------------------------------------
# A blind spot the report cannot cover. `report_status` is what the model reads,
# and a player nine months past surgery is usually blank there: no game status,
# full participation, nothing to see. Patrick Mahomes in week 2 of 2026 is
# exactly that -- and he hurt his knee in week 15 of 2025, did not play again
# through a run to week 22, and is still carrying a knee line on the practice
# report. The model priced him as a man with nothing wrong with him.
#
# Measured over 2021-2024 on the 55 players whose season ENDED on an injury and
# who came back the next year, each against his OWN pre-injury baseline:
#
#                n    first 6 weeks    weeks 7+
#     ALL       55        0.834          0.815
#     QB        17        0.976          1.097
#     RB        10        0.899          0.711
#     WR        23        0.721          0.700
#     TE         5        0.743          0.717
#     knee       8        0.708      (other injuries 0.855)
#
# A first pass at this put the sample at 280 and the effect at 0.897, by
# comparing each player's last game against the league's last week -- which is
# 22, so every player on a team that missed the playoffs looked like he had
# "missed four games". Those healthy players diluted the penalty. Counting
# against the player's own team's last game is what takes the sample to 55 and
# the effect to 0.834.
#
# Every one of those buckets is under this repo's 60-observation bar for
# trusting a point estimate, so none of them is used raw. Each position is
# shrunk toward the pooled figure with a prior worth 30 observations, and the
# knee modifier -- 8 cases -- is shrunk toward 1.0 the same way. What survives
# shrinkage is the direction, which is all 55 cases can honestly support.
#
# Two things do come through clearly enough to act on. The penalty is a
# discount, not a write-off: even the worst bucket plays. And quarterbacks
# recover inside the season -- 0.976 early against 1.097 after week 6, with the
# same sign in both the buggy and the corrected measurement -- which makes a
# returning QB precisely the asset to bank for a playoff week rather than to
# spend in September.
#
# What this is NOT: a general "is an injury listed" discount. Pooling every
# listing on the practice report gives 0.977 against 1.011 for players not
# listed at all, and *nothing* at quarterback (1.014 against 0.998), because
# most listings are a veteran's rest day. Only the season-ending ones carry.

# Raw measurements, kept separate from what is used, so the shrinkage is visible.
RETURN_PENALTY_MEASURED: dict[str, tuple[float, int]] = {
    "QB": (0.976, 17),
    "RB": (0.899, 10),
    "WR": (0.721, 23),
    "TE": (0.743, 5),
}
POOLED_RETURN_PENALTY = 0.834          # n = 55
SHRINK_PRIOR_WEIGHT = 30.0
KNEE_MEASURED = (0.708 / 0.855, 8)     # relative to other injuries


def _shrink(observed: float, n: int, prior: float) -> float:
    """Pull a thin estimate toward a prior worth `SHRINK_PRIOR_WEIGHT` cases."""
    return (n * observed + SHRINK_PRIOR_WEIGHT * prior) / (n + SHRINK_PRIOR_WEIGHT)


RETURN_PENALTY_PRIOR: dict[str, float] = {
    position: round(_shrink(value, n, POOLED_RETURN_PENALTY), 4)
    for position, (value, n) in RETURN_PENALTY_MEASURED.items()
}
DEFAULT_RETURN_PENALTY = POOLED_RETURN_PENALTY
KNEE_EXTRA_PENALTY = round(_shrink(KNEE_MEASURED[0], KNEE_MEASURED[1], 1.0), 4)
# Only quarterbacks measurably recover inside the season; for everyone else the
# early-season number is the whole season's number, so the penalty does not lift.
RETURN_RECOVERY_WEEK = 7
RETURN_RECOVERS: dict[str, bool] = {"QB": True, "RB": False, "WR": False, "TE": False}
# Games missed to the end of a season before it counts as season-ending.
MIN_GAMES_MISSED = 3


def _fitted_return_penalties() -> dict[str, float]:
    constants = fitted.get()
    out = dict(RETURN_PENALTY_PRIOR)
    for position in RETURN_PENALTY_PRIOR:
        out[position] = constants.value(
            f"return_from_injury.by_position.{position}",
            RETURN_PENALTY_PRIOR[position],
            label=f"return_penalty[{position}]",
        )
    return out


def players_returning_from_injury(
    weekly_prior: pd.DataFrame,
    injuries_prior: pd.DataFrame,
    *,
    min_games_missed: int = MIN_GAMES_MISSED,
) -> dict[str, str]:
    """Players whose previous season ended early on an injury.

    Returns ``player_id -> injury description`` (possibly empty string). A player
    who simply stopped being started does not count: he has to appear on the
    injury report around the time his season ended, which is what separates a
    torn knee from a benching.
    """
    if weekly_prior.empty or "week" not in weekly_prior:
        return {}

    frame = weekly_prior.copy()
    frame["week"] = pd.to_numeric(frame["week"], errors="coerce")
    frame = frame.dropna(subset=["week"])
    if frame.empty:
        return {}

    # How long the player's OWN TEAM played, not how long the league did. A
    # league-wide maximum is week 22, so measuring against it marks every player
    # on a team that missed the playoffs as having "missed four games" -- healthy
    # players, flagged as returning from surgery, diluting the very effect the
    # penalty is meant to price.
    team_column = next(
        (c for c in ("team", "recent_team", "team_abbr") if c in frame.columns), None
    )
    team_last: dict[str, int] = {}
    if team_column:
        team_last = (
            frame.groupby(team_column)["week"].max().astype(int).to_dict()
        )
    league_last = int(frame["week"].max())

    out: dict[str, str] = {}

    listed_by_player: dict[str, pd.DataFrame] = {}
    if not injuries_prior.empty and "gsis_id" in injuries_prior.columns:
        listed_by_player = dict(tuple(injuries_prior.groupby("gsis_id")))

    for player_id, group in frame.groupby("player_id"):
        weeks = group["week"].dropna()
        if weeks.empty:
            continue
        final = int(weeks.max())
        if team_column:
            teams = group[team_column].dropna()
            last_week = max(
                (team_last.get(str(t), league_last) for t in teams.unique()),
                default=league_last,
            )
        else:
            last_week = league_last
        if last_week - final < min_games_missed:
            continue

        listed = listed_by_player.get(player_id)
        if listed is None:
            continue
        near = listed[pd.to_numeric(listed["week"], errors="coerce") >= final - 1]
        if near.empty:
            continue

        description = ""
        for column in ("report_primary_injury", "practice_primary_injury"):
            if column in near.columns:
                values = near[column].dropna()
                if len(values):
                    description = str(values.iloc[-1])
                    break
        out[str(player_id)] = description
    return out


def return_multiplier(
    position: str,
    week: int,
    injury: str,
    *,
    penalties: dict[str, float] | None = None,
) -> float:
    """Production multiplier for a player coming back from a season-ender."""
    table = penalties if penalties is not None else _fitted_return_penalties()
    if RETURN_RECOVERS.get(position, False) and week >= RETURN_RECOVERY_WEEK:
        return 1.0
    penalty = table.get(position, DEFAULT_RETURN_PENALTY)
    if injury and "knee" in str(injury).lower():
        penalty *= KNEE_EXTRA_PENALTY
    return float(min(max(penalty, 0.1), 1.0))
