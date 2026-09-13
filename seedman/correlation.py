"""Correlation between players in the same lineup.

Treating six players' scores as independent is the single most dangerous
simplification available here, and the optimizer will actively exploit it: given
independence, stacking a quarterback with his own receiver, tight end and kicker
looks like free expected points at no extra risk.  It is not.  Those four
outcomes rise and fall together, so a stacked lineup has a much fatter left tail
-- and in a survivor league the left tail is the entire game.

The coefficients below are the standard, well-replicated shape of NFL fantasy
correlation:

  * A quarterback and his own pass catchers are strongly positively correlated;
    the same throw scores for both.
  * Everyone else on one offense shares that offense's total output, so same-team
    pairs are mildly positive: when a team scores 10 points, its quarterback,
    back, tight end and kicker all disappoint together.
  * The exception is two players splitting one job -- a committee backfield, or
    two tight ends -- who are genuinely negative with each other.
  * A kicker rises with his own offense, but weakly -- drives that stall in field
    goal range help him and hurt everyone else.
  * A defense is strongly negatively correlated with the opposing offense, and
    mildly positively with its own.
  * Players in the same game but on opposite offenses are mildly positive: a
    shootout lifts both sides.

Magnitudes are order-of-3-significant-figures rather than precisely fitted, which
is the right level of effort: the decisions they change are "do not start four
players from one game", not "prefer 0.31 to 0.34".
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import fitted

# Correlation between two players on the SAME NFL team, keyed by position pair.
SAME_TEAM_PRIOR: dict[tuple[str, str], float] = {
    # A quarterback and his own pass catchers: the same completion scores twice.
    ("QB", "WR"): 0.35,
    ("QB", "TE"): 0.25,
    # Everyone on one offense shares that offense's total output, so same-team
    # pairs are mildly positive even when they compete for touches. The
    # exceptions are players who split the *same* job.
    ("QB", "RB"): 0.08,
    ("QB", "K"): 0.20,
    ("QB", "DEF"): 0.05,
    ("RB", "WR"): 0.02,
    ("RB", "TE"): 0.02,
    ("RB", "K"): 0.15,
    ("RB", "DEF"): 0.05,
    ("WR", "WR"): 0.05,
    ("WR", "TE"): 0.03,
    ("WR", "K"): 0.15,
    ("WR", "DEF"): 0.05,
    ("TE", "K"): 0.15,
    ("TE", "DEF"): 0.05,
    ("K", "DEF"): 0.12,
    # Two backs in one backfield, or two tight ends in one offense, split a
    # fixed pool of carries or routes: genuinely negative.
    ("RB", "RB"): -0.20,
    ("TE", "TE"): -0.15,
}

# Correlation between two players on OPPOSITE teams in the same game.
OPPOSING_TEAM_DEFAULT = 0.10  # shootouts lift both offenses
OPPOSING_DEFENSE = -0.25  # a defense and the offense it is facing


def _resolve_same_team() -> dict[tuple[str, str], float]:
    """Fitted same-team coefficients where measured, priors elsewhere.

    Only the skill-position pairs are measurable from stat lines; kicker and
    defense pairings keep their priors.
    """
    constants = fitted.get()
    out = dict(SAME_TEAM_PRIOR)
    for (a, b), prior in SAME_TEAM_PRIOR.items():
        for key in (f"{a}|{b}", f"{b}|{a}"):
            if f"same_team_correlation.{key}" in _flatten(constants.raw):
                out[(a, b)] = constants.value(
                    f"same_team_correlation.{key}", prior, label=f"corr[{a}-{b}]"
                )
                break
    return out


def _flatten(node, prefix="") -> set[str]:
    """Dotted paths present in the fitted file, for membership checks."""
    keys: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            keys.add(path)
            keys |= _flatten(value, path)
    return keys


SAME_TEAM = _resolve_same_team()


def pair_correlation(
    pos_a: str, team_a: str, opp_a: str, pos_b: str, team_b: str, opp_b: str
) -> float:
    """Correlation between two players' fantasy scores in one week."""
    if team_a == team_b:
        key = (pos_a, pos_b) if (pos_a, pos_b) in SAME_TEAM else (pos_b, pos_a)
        return SAME_TEAM.get(key, 0.0)

    # Opposite sides of the same game.
    if team_a == opp_b and team_b == opp_a:
        if pos_a == "DEF" or pos_b == "DEF":
            return OPPOSING_DEFENSE
        return OPPOSING_TEAM_DEFAULT

    # Different games: independent for our purposes. A genuine league-wide
    # scoring shock does exist, but it moves every team together and therefore
    # cancels when comparing against the field (see `survival.py`).
    return 0.0


def correlation_matrix(rows: pd.DataFrame) -> np.ndarray:
    """Full correlation matrix for a set of players in a single week."""
    n = len(rows)
    matrix = np.eye(n)
    records = rows[["position", "team", "opponent"]].to_dict("records")
    for i in range(n):
        for j in range(i + 1, n):
            a, b = records[i], records[j]
            rho = pair_correlation(
                a["position"], a["team"], a["opponent"],
                b["position"], b["team"], b["opponent"],
            )
            matrix[i, j] = matrix[j, i] = rho
    return matrix


def lineup_sd(rows: pd.DataFrame) -> float:
    """Standard deviation of a lineup's total score, respecting correlation.

    ``Var(sum) = s' R s`` for per-player standard deviations `s` and correlation
    matrix `R`.
    """
    if rows.empty:
        return 0.0
    sds = pd.to_numeric(rows["sd"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    matrix = correlation_matrix(rows)
    variance = float(sds @ matrix @ sds)
    return float(np.sqrt(max(variance, 0.0)))
