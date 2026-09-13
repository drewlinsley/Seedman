"""Tests for the fitting code and the runtime pieces it feeds."""

import numpy as np
import pandas as pd
import pytest

from seedman import fitted
from seedman.calibration import WEEK_BUCKETS, fit_volatility, week_bucket
from seedman.config import LeagueConfig
from seedman.projections import _calibrate_level, _volatility_for


class _Season:
    """Minimal stand-in for SeasonData: only `.stats` is needed here."""

    def __init__(self, stats):
        self.stats = stats


def test_week_buckets_cover_every_week():
    assert {week_bucket(w) for w in range(1, 19)} == {f"{lo}-{hi}" for lo, hi in WEEK_BUCKETS}


def test_week_bucket_is_monotone():
    order = [f"{lo}-{hi}" for lo, hi in WEEK_BUCKETS]
    seen = [week_bucket(w) for w in range(2, 19)]
    assert seen == sorted(seen, key=order.index)


def test_volatility_rises_with_scoring_level():
    """Better players score more *and* swing harder in absolute terms."""
    rng = np.random.default_rng(0)
    rows = []
    for player in range(80):
        level = 2.0 + player * 0.25
        for week in range(10):
            rows.append(
                {
                    "player_id": f"p{player}",
                    "position": "WR",
                    "week": week,
                    "points": max(0.0, rng.normal(level, 0.5 * level)),
                }
            )
    fit = fit_volatility([_Season(pd.DataFrame(rows))])
    assert fit["WR"]["slope"] > 0


def test_volatility_needs_enough_players():
    rows = [
        {"player_id": "p1", "position": "WR", "week": w, "points": float(w)}
        for w in range(10)
    ]
    assert fit_volatility([_Season(pd.DataFrame(rows))]) == {}


def test_runtime_volatility_falls_back_without_a_fit(tmp_path):
    fitted.reset()
    fitted.get(tmp_path / "absent.yaml")
    try:
        assert _volatility_for(10.0, "WR", fallback=6.0) == 6.0
        # Never returns zero: a degenerate spread makes survival probability
        # collapse to a step function.
        assert _volatility_for(10.0, "WR", fallback=0.0) == 1.0
    finally:
        fitted.reset()


def test_runtime_level_calibration_is_identity_without_a_fit(tmp_path):
    fitted.reset()
    fitted.get(tmp_path / "absent.yaml")
    try:
        assert _calibrate_level(12.3, "QB", week=2) == 12.3
    finally:
        fitted.reset()


def test_level_calibration_never_returns_negative(tmp_path):
    """A negative expectation would silently drop the player from the pool."""
    import yaml

    path = tmp_path / "fitted.yaml"
    bucket = week_bucket(2)
    path.write_text(
        yaml.safe_dump(
            {
                "min_sample_for_use": 1,
                "rate_calibration": {
                    f"QB|{bucket}": {"slope": 1.0, "intercept": -50.0, "n": 900}
                },
            }
        )
    )
    fitted.reset()
    fitted.get(path)
    try:
        assert _calibrate_level(1.0, "QB", week=2) == 0.0
    finally:
        fitted.reset()


def test_level_calibration_is_uniform_within_a_week(tmp_path):
    """The property that makes it decision-neutral: same transform for everyone.

    If two players in one week got different slopes the map could reorder them,
    which is exactly what a player-keyed calibration did -- and it cost 5.6
    points a week on the decision metric.
    """
    import yaml

    path = tmp_path / "fitted.yaml"
    bucket = week_bucket(3)
    path.write_text(
        yaml.safe_dump(
            {
                "min_sample_for_use": 1,
                "rate_calibration": {
                    f"WR|{bucket}": {"slope": 1.4, "intercept": 2.0, "n": 900}
                },
            }
        )
    )
    fitted.reset()
    fitted.get(path)
    try:
        low = _calibrate_level(5.0, "WR", week=3)
        high = _calibrate_level(9.0, "WR", week=3)
        assert high > low
        # An affine map with a common slope preserves ratios of differences.
        assert (high - low) == pytest.approx(1.4 * 4.0)
    finally:
        fitted.reset()


def test_shipped_field_model_is_ordered_and_plausible():
    """Deeper into the board must mean fewer points, at a believable level."""
    constants = fitted.get()
    field = constants.raw.get("field_lineups")
    if not field:
        pytest.skip("no fitted field model shipped")

    depths = sorted(int(k) for k in field)
    means = [field[str(k)]["mean"] for k in depths]
    assert means == sorted(means, reverse=True)
    # A real survivor lineup scores far more than the 40 points the old
    # projection-derived field model claimed.
    assert 60 < means[0] < 160
