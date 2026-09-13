"""Survival probability and the marginal value of a fantasy point.

A survivor league does not reward points.  It rewards *not finishing last*, week
after week, until everyone else is gone.  Those are different objectives, and
the difference is the whole problem:

  * Maximising expected points would happily start your best remaining player
    every week until the cupboard is bare.
  * Maximising survival spends stars in the weeks where an extra point actually
    changes your odds, and banks them in the weeks where you are already safe.

The bridge between the two is the derivative below.  Model your week-`w` score
as ``N(mu_w, sigma_w^2)`` and the week's cut line -- the lowest score among the
teams still alive -- as ``N(m_w, s_w^2)``.  Then

    P(survive week w) = Phi(z_w),   z_w = (mu_w - m_w) / sqrt(sigma_w^2 + s_w^2)

and the marginal survival value of one extra projected point in week `w` is

    d/dmu_w  log Phi(z_w)  =  phi(z_w) / Phi(z_w) / sqrt(sigma_w^2 + s_w^2)

That quantity is large when you are near the cut line and small when you are
comfortably clear of it.  Feeding it to the optimizer as a per-week weight makes
"spend now versus save for later" an output of the model instead of a knob.

Note the cut line tightens as the league shrinks: the minimum of 11 opponents'
scores is much lower than the minimum of 3.  So late weeks are automatically
more dangerous, which is exactly why hoarding every stud for week 17 is wrong
too -- you have to still be alive to use them.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field

import numpy as np
from scipy.special import log_ndtr
from scipy.stats import norm

DEFAULT_SAMPLES = 40_000


@dataclass(frozen=True)
class FieldModel:
    """How a typical surviving opponent scores in a given week.

    Two ways to specify it.  `by_week` carries explicit ``week -> (mean, sd)``
    moments, which is what the pipeline derives from real projections: in a
    one-use-per-player format every manager's lineups get measurably weaker as
    the season drains their personal pool, and that decline is not linear.
    `mean`/`sd` plus `decay_per_week` are the simple fallback when no
    week-specific estimate is available.
    """

    mean: float = 95.0
    sd: float = 22.0
    decay_per_week: float = 0.0
    by_week: dict[int, tuple[float, float]] = dataclass_field(default_factory=dict)

    def mean_at(self, weeks_ahead: int) -> float:
        return self.mean - self.decay_per_week * max(0, weeks_ahead)

    def moments_at(self, week: int, weeks_ahead: int) -> tuple[float, float]:
        """Mean and SD of a single opponent's score in `week`."""
        if week in self.by_week:
            return self.by_week[week]
        return self.mean_at(weeks_ahead), self.sd


def threshold_moments(
    n_opponents: int,
    field_mean: float,
    field_sd: float,
    *,
    samples: int = DEFAULT_SAMPLES,
    seed: int = 0,
) -> tuple[float, float]:
    """Mean and SD of the lowest score among `n_opponents` independent teams.

    Closed forms for the minimum of n normals are awkward; simulation is exact
    enough and costs microseconds at this scale.  The seed keeps a given league
    configuration reproducible run to run.
    """
    n = max(1, int(n_opponents))
    if field_sd <= 0:
        return float(field_mean), 0.0

    rng = np.random.default_rng(seed)
    draws = rng.normal(field_mean, field_sd, size=(samples, n))
    minima = draws.min(axis=1)
    return float(minima.mean()), float(minima.std(ddof=1))


def survival_probability(
    mean: float, sd: float, threshold_mean: float, threshold_sd: float
) -> float:
    """P(your score beats the week's cut line)."""
    denom = float(np.hypot(sd, threshold_sd))
    if denom <= 0:
        return 1.0 if mean > threshold_mean else 0.0
    return float(norm.cdf((mean - threshold_mean) / denom))


def log_survival_probability(
    mean: float, sd: float, threshold_mean: float, threshold_sd: float
) -> float:
    """Numerically stable log of `survival_probability`."""
    denom = float(np.hypot(sd, threshold_sd))
    if denom <= 0:
        return 0.0 if mean > threshold_mean else -np.inf
    return float(log_ndtr((mean - threshold_mean) / denom))


def marginal_point_value(
    mean: float, sd: float, threshold_mean: float, threshold_sd: float
) -> float:
    """d/dmean of log P(survive): how much one projected point is worth.

    Computed through `log_ndtr` so that a hopeless week (deep negative z) still
    returns a sane, large-but-finite weight instead of dividing by an underflowed
    CDF.
    """
    denom = float(np.hypot(sd, threshold_sd))
    if denom <= 0:
        return 0.0
    z = (mean - threshold_mean) / denom
    # phi(z)/Phi(z) via logs: exp(log phi(z) - log Phi(z)).
    ratio = float(np.exp(norm.logpdf(z) - log_ndtr(z)))
    return ratio / denom


def cumulative_survival(weekly_probabilities: list[float]) -> float:
    """P(surviving every listed week), assuming week-to-week independence."""
    total = 1.0
    for p in weekly_probabilities:
        total *= float(np.clip(p, 0.0, 1.0))
    return total


def estimate_field_from_projections(
    replacement_lineup_mean: float, replacement_lineup_sd: float
) -> FieldModel:
    """Build a field model from what a median opponent can realistically start.

    Used when the league's actual scoring history is not available: assume rival
    managers field roughly the lineup our own optimizer would call mid-tier.
    """
    return FieldModel(mean=replacement_lineup_mean, sd=replacement_lineup_sd)
