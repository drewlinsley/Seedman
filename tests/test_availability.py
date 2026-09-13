"""Tests for the two-state availability hazard.

The model this replaces measured at AUC 0.49-0.55 on held-out data -- a coin
flip -- so the tests here are mostly about the structural properties that made
the old one useless, rather than about numerical accuracy.
"""

import numpy as np
import pandas as pd
import pytest

from seedman.availability import (
    DESIGNATIONS,
    FEATURES,
    AvailabilityHazard,
    FittedHazard,
    design_matrix,
)


def _rows(n=400, seed=0):
    """A panel where availability genuinely depends on state and history."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        played_now = bool(rng.random() < 0.75)
        missed = 0.0 if played_now else float(rng.integers(1, 5))
        # Continuation is easy, recovery is hard and gets harder the longer
        # a player has been out -- the real pattern, exaggerated.
        p = 0.9 if played_now else max(0.05, 0.45 - 0.1 * missed)
        rows.append(
            {
                "player_id": f"p{i}",
                "season": 2024,
                "week": 5,
                "position": ["QB", "RB", "WR", "TE"][i % 4],
                "played_now": played_now,
                "played_next": bool(rng.random() < p),
                "age": 25.0 + (i % 10),
                "years_exp": float(i % 8),
                "missed_last_4": missed,
                "missed_rate_season": missed / 4.0,
                "weeks_since_miss": 10.0 if played_now else 1.0,
                "snap_share_recent": 0.8 if played_now else 0.1,
                "points_recent": 12.0 if played_now else 1.0,
                "workload_recent": 15.0 if played_now else 1.0,
                "status": "not on report" if played_now else "Out",
                "practice": "Full Participation in Practice" if played_now else "",
            }
        )
    return pd.DataFrame(rows)


@pytest.fixture
def panel():
    return _rows()


@pytest.fixture
def model(panel):
    return AvailabilityHazard().fit(panel)


def test_design_matrix_shape_matches_names(panel):
    features, names = design_matrix(panel)
    assert features.shape == (len(panel), len(names))
    assert set(FEATURES) <= set(names)
    assert all(f"status={d}" in names for d in DESIGNATIONS)


def test_design_matrix_has_no_missing_values():
    """NaNs would silently poison a logistic fit."""
    frame = _rows(50)
    frame.loc[0, "age"] = np.nan
    frame.loc[1, "snap_share_recent"] = np.nan
    features, _ = design_matrix(frame)
    assert np.isfinite(features).all()


def test_players_on_the_field_are_likelier_to_stay_there(model, panel):
    a, b = model.transition_probabilities(panel)
    assert a.mean() > b.mean()


def test_availability_curve_decays_from_playing_and_recovers_from_out(model):
    """The two states must move in opposite directions, which is the whole point."""
    playing = _rows(200, seed=1)
    playing["played_now"] = True
    out = playing.copy()
    out["played_now"] = False

    up = model.availability_curve(playing, horizons=6)
    down = model.availability_curve(out, horizons=6)

    assert up[:, 0].mean() > down[:, 0].mean()
    # A healthy player's availability erodes; an injured one's improves.
    assert up[:, 5].mean() < up[:, 0].mean()
    assert down[:, 5].mean() > down[:, 0].mean()


def test_curve_converges_to_the_chains_fixed_point(model):
    """Long-run availability is b / (1 - a + b) -- a per-player baseline, learned.

    The old model had to be *told* a position baseline; here it falls out.
    """
    frame = _rows(100, seed=2)
    a, b = model.transition_probabilities(frame)
    # The chain mixes at rate (a - b) per step; the slowest row here is ~0.87,
    # so 60 steps leaves 1e-4 of error and 200 is needed to assert 1e-6.
    curve = model.availability_curve(frame, horizons=200)
    stationary = b / (1.0 - a + b)
    assert np.allclose(curve[:, -1], stationary, atol=1e-6)


def test_duration_dependence_is_representable(model):
    """A player out four weeks must not look like one out for one.

    The AR(1) this replaces could not express this at all: it pulled everyone
    back toward a fixed baseline at the same rate regardless of how long they
    had been out. Measured, P(returns next week) falls from 36% after one missed
    game to 15% after four.
    """
    short = _rows(200, seed=3)
    short["played_now"] = False
    short["missed_last_4"] = 1.0
    long = short.copy()
    long["missed_last_4"] = 4.0

    assert model.availability_curve(short, 4)[:, 0].mean() > (
        model.availability_curve(long, 4)[:, 0].mean()
    )


def test_probabilities_stay_in_range(model):
    curve = model.availability_curve(_rows(150, seed=4), horizons=10)
    assert (curve >= 0).all() and (curve <= 1).all()


def test_serialisation_round_trips(model, panel):
    """Coefficients go to YAML as numbers, not a pickle -- so this must match."""
    restored = FittedHazard.from_dict(model.to_dict())
    assert restored is not None
    a = model.availability_curve(panel, horizons=6)
    b = restored.availability_curve(panel, horizons=6)
    assert np.abs(a - b).max() < 1e-4


def test_fitted_hazard_rejects_a_feature_mismatch(model, panel):
    """A stale fitted file must fail loudly, not score against wrong columns."""
    payload = model.to_dict()
    payload["features"] = payload["features"][:-1]
    with pytest.raises(ValueError, match="re-run"):
        FittedHazard.from_dict(payload).availability_curve(panel)


def test_missing_hazard_returns_none():
    assert FittedHazard.from_dict({}) is None
    assert FittedHazard.from_dict({"features": []}) is None


def test_curves_for_week_indexes_by_player(model, panel):
    curves = model.curves_for_week(panel, season=2024, week=5, horizons=4)
    assert list(curves.columns) == ["h1", "h2", "h3", "h4"]
    assert curves.index.is_unique
    assert len(curves) == len(panel)
    assert model.curves_for_week(panel, season=2024, week=99).empty


def test_projection_falls_back_when_no_curves_supplied(config):
    """Absent a fitted hazard the model must still work, on its prior."""
    from seedman.projections import ProjectionModel

    schedule = pd.DataFrame(
        [{"season": 2026, "week": w, "home_team": "AAA", "away_team": "BBB",
          "home_score": None, "away_score": None, "spread_line": 0.0,
          "total_line": 44.0, "gameday": f"2026-09-{w:02d}"} for w in range(1, 6)]
    )
    model = ProjectionModel(
        config,
        weekly_current=pd.DataFrame(),
        weekly_prior=pd.DataFrame(),
        schedule=schedule,
        injuries=pd.DataFrame(),
        rosters=pd.DataFrame(),
        availability_curves=None,
    )
    assert model._future_availability("nobody", "RB", 3, 0.9) != 0.9


def test_shipped_hazard_loads_and_is_sane():
    from seedman import fitted

    payload = fitted.get().raw.get("availability_hazard")
    if not payload:
        pytest.skip("no fitted hazard shipped")
    hazard = FittedHazard.from_dict(payload)
    assert hazard is not None
    assert hazard.continuation["n"] > 1000 and hazard.recovery["n"] > 1000
    assert len(hazard.features) == len(hazard.continuation["coef"])
