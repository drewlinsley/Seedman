import numpy as np

from seedman.survival import (
    FieldModel,
    cumulative_survival,
    marginal_point_value,
    survival_probability,
    threshold_moments,
)


def test_cut_line_rises_as_the_league_shrinks():
    """The minimum of 3 scores is much higher than the minimum of 11.

    This is why late weeks are dangerous and hoarding every stud is wrong.
    """
    many, _ = threshold_moments(11, 95.0, 22.0)
    few, _ = threshold_moments(3, 95.0, 22.0)
    solo, _ = threshold_moments(1, 95.0, 22.0)
    assert many < few < solo
    assert np.isclose(solo, 95.0, atol=0.5)


def test_survival_probability_is_monotone_in_score():
    weaker = survival_probability(80, 20, 60, 12)
    stronger = survival_probability(100, 20, 60, 12)
    assert 0 < weaker < stronger < 1


def test_points_are_worth_more_when_you_are_near_the_cut_line():
    """The whole spend-now-versus-save-later mechanism lives here."""
    at_risk = marginal_point_value(mean=62, sd=20, threshold_mean=60, threshold_sd=12)
    comfortable = marginal_point_value(mean=120, sd=20, threshold_mean=60, threshold_sd=12)
    assert at_risk > comfortable
    assert comfortable >= 0


def test_marginal_value_stays_finite_in_a_hopeless_week():
    """Far in the tail the CDF underflows; the log-space form must still work."""
    value = marginal_point_value(mean=10, sd=5, threshold_mean=200, threshold_sd=5)
    assert np.isfinite(value)
    assert value > 0


def test_cumulative_survival_multiplies():
    assert np.isclose(cumulative_survival([0.9, 0.8, 0.5]), 0.36)


def test_field_model_prefers_explicit_weekly_moments():
    model = FieldModel(mean=95, sd=22, by_week={4: (70.0, 18.0)})
    assert model.moments_at(4, 2) == (70.0, 18.0)
    assert model.moments_at(5, 3) == (95.0, 22.0)


def test_field_decay_reduces_the_mean_over_time():
    model = FieldModel(mean=95, sd=22, decay_per_week=2.0)
    assert model.mean_at(0) == 95
    assert model.mean_at(4) == 87
