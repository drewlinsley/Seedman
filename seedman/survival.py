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

# Share of a lineup's score variance that is persistent team skill rather than
# week-to-week noise. Weekly lineup SD runs ~22 points while season-long team
# quality spreads maybe 9, so roughly 9^2 / (9^2 + 20^2). Only this slice is
# selected on when a team makes the playoffs.
BETWEEN_TEAM_VARIANCE_SHARE = 0.17


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


# ----------------------------------------------------------------------
# Head-to-head: making a bracket, then winning it
# ----------------------------------------------------------------------
# A survivor league asks one question every week ("am I last?"). A head-to-head
# league asks two different ones, and conflating them is how you end up either
# hoarding into a 4-9 record or spending into a first-round exit:
#
#   1. Regular season -- do I finish with enough WINS to make the bracket? One
#      blowout loss costs exactly as much as a one-point loss, and a 60-point
#      win banks nothing. What matters is P(win), summed.
#   2. Playoffs -- do I win THIS game? Now a loss ends the season, so the
#      objective goes back to the survivor shape: maximise log P(win) and treat
#      an extra point as most valuable when the game is closest.
#
# P(title) = P(make the bracket) * prod over playoff weeks of P(win that week),
# so the log objective is a sum and the solver's existing per-week weights carry
# it unchanged. Only the weights themselves differ between the two phases.


def poisson_binomial_pmf(probabilities: list[float]) -> np.ndarray:
    """Distribution of total wins from independent games with unequal odds."""
    dist = np.zeros(len(probabilities) + 1)
    dist[0] = 1.0
    for index, p in enumerate(probabilities, start=1):
        p = float(np.clip(p, 0.0, 1.0))
        updated = np.zeros_like(dist)
        updated[0] = dist[0] * (1.0 - p)
        updated[1 : index + 1] = dist[1 : index + 1] * (1.0 - p) + dist[0:index] * p
        dist = updated
    return dist


def playoff_cut_wins(total_games: int, berths: int, teams: int) -> int:
    """Wins needed for a bracket spot, read off the league's own win spread.

    Every game in the league is someone's win, so team records are binomial
    around .500. The cut is the smallest total that only `berths` teams are
    expected to reach.
    """
    if berths >= teams:
        return 0
    from scipy.stats import binom

    for wins in range(total_games, -1, -1):
        if teams * binom.sf(wins - 1, total_games, 0.5) > berths:
            return min(total_games, wins + 1)
    return 0


def playoff_probability(
    weekly_win_probabilities: list[float], wins_so_far: int, wins_needed: int
) -> float:
    """P(finishing with enough wins), given games already banked."""
    still_needed = wins_needed - wins_so_far
    if still_needed <= 0:
        return 1.0
    if still_needed > len(weekly_win_probabilities):
        return 0.0
    pmf = poisson_binomial_pmf(weekly_win_probabilities)
    return float(pmf[still_needed:].sum())


def berth_sensitivity(
    weekly_win_probabilities: list[float], wins_so_far: int, wins_needed: int
) -> list[float]:
    """d P(make the bracket) / d p_w, for each regular-season week.

    P is linear in any single `p_w` -- win that game or do not -- so the exact
    derivative is the chance the *other* games land exactly one win short. That
    peaks when the season is genuinely on the bubble and collapses toward zero
    once you are safely in or realistically out, which is the behaviour we want:
    a locked-up berth should stop competing with the playoff weeks for players.
    """
    still_needed = wins_needed - wins_so_far
    out = []
    for index in range(len(weekly_win_probabilities)):
        rest = weekly_win_probabilities[:index] + weekly_win_probabilities[index + 1 :]
        pmf = poisson_binomial_pmf(rest)
        target = still_needed - 1
        out.append(float(pmf[target]) if 0 <= target < len(pmf) else 0.0)
    return out


def playoff_opponent_moments(
    field_mean: float,
    field_sd: float,
    berths: int,
    teams: int,
    between_team_share: float = BETWEEN_TEAM_VARIANCE_SHARE,
) -> tuple[float, float]:
    """A bracket opponent is not an average team -- they qualified.

    The selection acts on team *quality*, not on a single week's luck, so only
    the between-team slice of the variance gets truncated. Conditioning the full
    weekly spread instead would be badly wrong in the direction that flatters
    hoarding: on a field of (80, 22) it prices the playoff bar at 97.6 rather
    than 86.4, and a bar that high makes almost any amount of banking look
    justified.

    `between_team_share` is the fraction of total variance that is persistent
    skill rather than week-to-week noise. It is an estimate, not a fit -- with
    only a single opponent box score on hand there is nothing to fit it against
    -- so it is surfaced in `configs/league.yaml` as an assumption.
    """
    if berths >= teams or field_sd <= 0:
        return field_mean, field_sd
    share = float(np.clip(between_team_share, 0.0, 1.0))
    quality_sd = field_sd * np.sqrt(share)
    noise_var = (field_sd**2) * (1.0 - share)

    p = berths / teams
    z = norm.ppf(1.0 - p)
    lam = norm.pdf(z) / p                       # inverse Mills ratio
    mean = field_mean + quality_sd * lam
    quality_var = (quality_sd**2) * (1.0 + z * lam - lam**2)
    return float(mean), float(np.sqrt(max(quality_var + noise_var, 1e-9)))
