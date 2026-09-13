"""Availability modelling: will this player actually be on the field?

Two different questions, answered separately:

1. **This week.**  The official NFL injury report gives a designation
   (Out / Doubtful / Questionable / none) plus practice participation.  Since
   2016 the league dropped "Probable" and stopped attaching fixed percentages to
   the remaining tags, so the historical "doubtful = 25%" definition no longer
   holds; empirically Questionable players suit up roughly three-quarters of the
   time and Doubtful players almost never do.  Practice participation is the
   sharpest tiebreaker within Questionable: a full practice on Friday is close
   to a clean bill of health, a DNP is close to a scratch.

2. **Future weeks.**  No injury report exists yet, so we mean-revert: today's
   health decays toward a position-typical baseline availability.  Running backs
   miss the most time, kickers almost none, and a team defense always plays.
   This is what stops the optimizer from happily banking an injured star for
   week 14 as though he were certain to be there.

Every constant here is a documented, tunable prior rather than a fitted value --
they are the right order of magnitude and are explicitly overridable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

# P(plays) by injury-report designation, before practice refinement.
REPORT_STATUS_PRIOR: dict[str, float] = {
    "Out": 0.0,
    "Doubtful": 0.06,
    "Questionable": 0.70,
    "": 0.98,  # no designation still leaves room for a late scratch or illness
}

# Multiplicative adjustments applied to a Questionable player's base
# probability, based on the final practice report of the week.
PRACTICE_ADJUSTMENT: dict[str, float] = {
    "Full Participation in Practice": 1.30,
    "Limited Participation in Practice": 1.02,
    "Did Not Participate In Practice": 0.50,
}

# When a dinged-up player does suit up he is usually on a snap count or playing
# hurt, so his expected production is discounted even conditional on playing.
EFFECTIVENESS_WHEN_PLAYING: dict[str, float] = {
    "Out": 0.0,
    "Doubtful": 0.75,
    "Questionable": 0.90,
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
# 0 would mean "next week is a coin flip against the baseline"; 1 would mean
# "today's injury is permanent".
HEALTH_PERSISTENCE = 0.70


@dataclass
class AvailabilityModel:
    report_status_prior: dict[str, float] = field(
        default_factory=lambda: dict(REPORT_STATUS_PRIOR)
    )
    practice_adjustment: dict[str, float] = field(
        default_factory=lambda: dict(PRACTICE_ADJUSTMENT)
    )
    effectiveness: dict[str, float] = field(
        default_factory=lambda: dict(EFFECTIVENESS_WHEN_PLAYING)
    )
    baseline_availability: dict[str, float] = field(
        default_factory=lambda: dict(BASELINE_AVAILABILITY)
    )
    persistence: float = HEALTH_PERSISTENCE

    # ------------------------------------------------------------------
    def play_probability(self, report_status: str | None, practice_status: str | None) -> float:
        """P(player appears in this week's game) given the official report."""
        status = _clean(report_status)
        base = self.report_status_prior.get(status, self.report_status_prior[""])

        # Practice participation only adds information inside the genuinely
        # uncertain bucket; Out means out regardless of Wednesday's practice.
        if status == "Questionable":
            adj = self.practice_adjustment.get(_clean(practice_status), 1.0)
            base *= adj
        elif status == "" and _clean(practice_status) == "Did Not Participate In Practice":
            # Undesignated but not practising: usually a rest day for a veteran,
            # occasionally a problem. Shade down slightly.
            base *= 0.95

        return float(min(max(base, 0.0), 1.0))

    def effectiveness_multiplier(self, report_status: str | None) -> float:
        """Expected production multiplier *given that* the player suits up."""
        return self.effectiveness.get(_clean(report_status), 1.0)

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
