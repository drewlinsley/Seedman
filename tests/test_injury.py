import pandas as pd

from seedman.injury import AvailabilityModel, latest_injury_report


def test_designations_are_ordered_sensibly():
    model = AvailabilityModel()
    out = model.play_probability("Out", "Did Not Participate In Practice")
    doubtful = model.play_probability("Doubtful", "Limited Participation in Practice")
    # Doubtful is measured at 0.0085 -- effectively "not playing", not the 25%
    # the NFL's pre-2016 definition implied.
    assert doubtful < 0.05
    questionable = model.play_probability("Questionable", "Limited Participation in Practice")
    healthy = model.play_probability("", "Full Participation in Practice")

    assert out == 0.0
    assert out < doubtful < questionable < healthy <= 1.0


def test_practice_participation_splits_the_questionable_bucket():
    """Practice is the sharpest tiebreaker inside the only uncertain tag.

    Measured over 2021-2024: full participation 0.825, limited 0.765, DNP 0.464.
    """
    model = AvailabilityModel()
    full = model.play_probability("Questionable", "Full Participation in Practice")
    limited = model.play_probability("Questionable", "Limited Participation in Practice")
    dnp = model.play_probability("Questionable", "Did Not Participate In Practice")
    assert full > limited > dnp
    assert full > 1.5 * dnp


def test_listed_without_a_game_status_beats_never_listed():
    """Counterintuitive but measured: being listed and left undesignated means
    the team actively cleared the player (0.95), while the unlisted population
    still contains healthy scratches and depth inactives (0.92)."""
    model = AvailabilityModel()
    assert model.play_probability("(none)", "") > model.play_probability(None, None)


def test_practice_cannot_resurrect_a_ruled_out_player():
    model = AvailabilityModel()
    assert model.play_probability("Out", "Full Participation in Practice") == 0.0


def test_future_weeks_revert_toward_the_position_baseline():
    model = AvailabilityModel()
    # An injured back recovers toward baseline...
    assert model.future_availability("RB", 0, 0.1) == 0.1
    assert model.future_availability("RB", 1, 0.1) > 0.1
    assert model.future_availability("RB", 6, 0.1) > model.future_availability("RB", 2, 0.1)
    # ...and a healthy one accumulates risk.
    assert model.future_availability("RB", 4, 1.0) < 1.0


def test_running_backs_carry_more_future_risk_than_kickers():
    model = AvailabilityModel()
    assert model.future_availability("RB", 5, 1.0) < model.future_availability("K", 5, 1.0)


def test_latest_report_falls_back_to_an_earlier_week():
    """Midweek, this week's report may not be filed yet."""
    injuries = pd.DataFrame(
        [
            {"season": 2026, "week": 1, "gsis_id": "a", "report_status": "Out",
             "practice_status": "", "position": "RB", "team": "AAA"},
            {"season": 2026, "week": 2, "gsis_id": "a", "report_status": "Questionable",
             "practice_status": "", "position": "RB", "team": "AAA"},
        ]
    )
    latest = latest_injury_report(injuries, 2026, week=3)
    assert latest.loc[latest["gsis_id"] == "a", "report_status"].iloc[0] == "Questionable"


def test_empty_injury_frame_is_handled():
    assert latest_injury_report(pd.DataFrame(), 2026, 1).empty
