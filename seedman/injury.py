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
