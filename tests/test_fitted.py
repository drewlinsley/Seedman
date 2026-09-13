"""The rules that decide when a measured value replaces a hand-set prior."""

import pytest
import yaml

from seedman import fitted


@pytest.fixture(autouse=True)
def _reset_singleton():
    fitted.reset()
    yield
    fitted.reset()


def _write(tmp_path, payload):
    path = tmp_path / "fitted.yaml"
    path.write_text(yaml.safe_dump(payload))
    return path


def test_well_sampled_value_is_adopted(tmp_path):
    path = _write(tmp_path, {"min_sample_for_use": 60, "a": {"b": {"value": 0.42, "n": 500}}})
    assert fitted.get(path).value("a.b", prior=0.9) == 0.42


def test_thin_bucket_keeps_the_prior(tmp_path):
    """A point estimate off three observations is worse than a considered guess."""
    path = _write(tmp_path, {"min_sample_for_use": 60, "a": {"b": {"value": 0.42, "n": 3}}})
    assert fitted.get(path).value("a.b", prior=0.9) == 0.9


def test_missing_path_keeps_the_prior(tmp_path):
    path = _write(tmp_path, {"min_sample_for_use": 60, "other": {"value": 1.0, "n": 900}})
    assert fitted.get(path).value("a.b", prior=0.9) == 0.9


def test_absent_file_means_every_prior_stands(tmp_path):
    constants = fitted.get(tmp_path / "nope.yaml")
    assert not constants.available
    assert constants.value("anything", prior=0.77) == 0.77


def test_provenance_records_which_values_were_measured(tmp_path):
    path = _write(
        tmp_path,
        {"min_sample_for_use": 60, "fitted_on": [2023], "a": {"value": 0.5, "n": 900}},
    )
    constants = fitted.get(path)
    constants.value("a", prior=0.1, label="alpha")
    constants.value("missing", prior=0.2, label="beta")
    report = constants.provenance()
    assert "alpha" in report and "fitted (n=900)" in report
    assert "beta" in report and "prior (not fitted)" in report


def test_shipped_calibration_is_loadable_and_sane():
    constants = fitted.get()
    if not constants.available:
        pytest.skip("no fitted.yaml shipped")
    assert constants.fitted_on
    # Doubtful really does mean "not playing".
    assert constants.value("availability.by_report_status.Doubtful", 0.5) < 0.05
    # The league-wide weekly shock is small next to between-team spread.
    assert constants.value("common_variance_share", 0.5) < 0.15
