"""Coming back from the injury that ended last season.

The weekly report cannot see this. Nine months after surgery a player is cleared,
practising fully, and blank in `report_status` -- which is the only column the
availability model reads. Measured over 2021-2025, 280 such players produced at
0.897 of their own pre-injury baseline in the first six weeks back.
"""

from __future__ import annotations

import pandas as pd
import pytest

from seedman.injury import (
    MIN_GAMES_MISSED,
    players_returning_from_injury,
    return_multiplier,
)

PENALTIES = {"QB": 0.935, "RB": 0.939, "WR": 0.866, "TE": 0.847}


def _weekly(rows: list[tuple[str, int]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"player_id": pid, "week": week} for pid, week in rows]
    )


def _injuries(rows: list[tuple[str, int, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"gsis_id": pid, "week": week, "practice_primary_injury": what,
             "report_primary_injury": None}
            for pid, week, what in rows
        ]
    )


# ----------------------------------------------------------------------
def test_a_season_ending_injury_is_detected():
    weekly = _weekly([("hurt", w) for w in range(1, 16)] + [("fine", w) for w in range(1, 19)])
    injuries = _injuries([("hurt", 15, "Knee")])
    found = players_returning_from_injury(weekly, injuries)
    assert "hurt" in found
    assert found["hurt"] == "Knee"


def test_a_player_who_finished_the_season_is_not_returning():
    weekly = _weekly([("fine", w) for w in range(1, 19)])
    assert players_returning_from_injury(weekly, _injuries([("fine", 15, "Knee")])) == {}


def test_being_benched_is_not_an_injury():
    """No injury-report entry near the end means he was dropped, not hurt."""
    weekly = _weekly([("benched", w) for w in range(1, 12)] + [("fine", w) for w in range(1, 19)])
    injuries = _injuries([("benched", 2, "Ankle")])   # months earlier, unrelated
    assert players_returning_from_injury(weekly, injuries) == {}


def test_missing_fewer_games_than_the_threshold_does_not_count():
    last = 18
    weekly = _weekly(
        [("nick", w) for w in range(1, last - MIN_GAMES_MISSED + 2)]
        + [("fine", w) for w in range(1, last + 1)]
    )
    injuries = _injuries([("nick", last - MIN_GAMES_MISSED + 1, "Hamstring")])
    assert "nick" not in players_returning_from_injury(weekly, injuries)


def test_no_injury_data_at_all_flags_nobody():
    weekly = _weekly([("hurt", w) for w in range(1, 10)] + [("fine", w) for w in range(1, 19)])
    assert players_returning_from_injury(weekly, pd.DataFrame()) == {}


def test_empty_input_is_not_an_error():
    assert players_returning_from_injury(pd.DataFrame(), pd.DataFrame()) == {}


# ----------------------------------------------------------------------
def test_the_penalty_is_a_discount_not_a_write_off():
    """0.85-0.94 -- a reason to prefer him later, not to bench him forever."""
    for position in PENALTIES:
        value = return_multiplier(position, 2, "Ankle", penalties=PENALTIES)
        assert 0.8 < value < 1.0


def test_a_knee_costs_more_than_other_injuries():
    knee = return_multiplier("QB", 2, "Knee", penalties=PENALTIES)
    other = return_multiplier("QB", 2, "Ankle", penalties=PENALTIES)
    assert knee < other


def test_the_injury_description_is_matched_case_insensitively():
    assert return_multiplier("QB", 2, "left Knee", penalties=PENALTIES) == pytest.approx(
        return_multiplier("QB", 2, "KNEE", penalties=PENALTIES)
    )


def test_quarterbacks_recover_inside_the_season_and_others_do_not():
    """0.935 early against 1.035 after week 6 is the QB pattern; WRs stay down.

    This is the whole reason a returning quarterback is a bank-for-later asset
    rather than one to write off: the discount expires, and it expires right
    around when the weeks start mattering more.
    """
    assert return_multiplier("QB", 9, "Knee", penalties=PENALTIES) == 1.0
    assert return_multiplier("WR", 9, "Knee", penalties=PENALTIES) < 1.0


def test_an_unknown_position_still_gets_the_pooled_penalty():
    value = return_multiplier("K", 2, "", penalties=PENALTIES)
    assert 0.8 < value < 1.0


def test_the_multiplier_never_leaves_its_bounds():
    for position in list(PENALTIES) + ["DEF"]:
        for week in (1, 6, 7, 17):
            value = return_multiplier(position, week, "Knee", penalties=PENALTIES)
            assert 0.0 < value <= 1.0


# ----------------------------------------------------------------------
# shrinkage — every bucket here is under the 60-observation bar
# ----------------------------------------------------------------------
def test_thin_estimates_are_shrunk_toward_the_pooled_figure():
    """No bucket has 60 cases, so none of them is trusted raw."""
    from seedman.injury import (
        POOLED_RETURN_PENALTY,
        RETURN_PENALTY_MEASURED,
        RETURN_PENALTY_PRIOR,
    )

    for position, (raw, _n) in RETURN_PENALTY_MEASURED.items():
        used = RETURN_PENALTY_PRIOR[position]
        assert min(raw, POOLED_RETURN_PENALTY) <= used <= max(raw, POOLED_RETURN_PENALTY)
        if abs(raw - POOLED_RETURN_PENALTY) > 0.01:
            assert used != pytest.approx(raw), f"{position} used its raw estimate"


def test_a_thinner_bucket_is_shrunk_harder():
    """TE has 5 cases and WR has 23; TE should end up nearer the pooled value."""
    from seedman.injury import (
        POOLED_RETURN_PENALTY as P,
        RETURN_PENALTY_MEASURED as M,
        RETURN_PENALTY_PRIOR as U,
    )

    def pulled(position: str) -> float:
        raw = M[position][0]
        return abs(U[position] - raw) / max(abs(P - raw), 1e-9)

    assert pulled("TE") > pulled("WR")


def test_the_knee_modifier_is_shrunk_toward_no_effect():
    """Eight cases is not enough to price a 17% penalty at face value."""
    from seedman.injury import KNEE_EXTRA_PENALTY, KNEE_MEASURED

    assert KNEE_MEASURED[0] < KNEE_EXTRA_PENALTY < 1.0


def test_the_team_not_the_league_decides_when_a_season_ended():
    """A player on a team that missed the playoffs has not 'missed four games'.

    This was a real bug: comparing against the league's last week (22) flagged
    every healthy player on a non-playoff team as returning from surgery, which
    put the measured sample at 280 instead of 55 and diluted the effect from
    0.834 to 0.897.
    """
    weekly = pd.DataFrame(
        [{"player_id": "healthy", "week": w, "team": "NOPLAYOFFS"} for w in range(1, 19)]
        + [{"player_id": "champ", "week": w, "team": "DEEPRUN"} for w in range(1, 23)]
    )
    injuries = _injuries([("healthy", 18, "Ankle")])
    assert players_returning_from_injury(weekly, injuries) == {}


def test_a_player_whose_own_team_played_on_without_him_is_flagged():
    weekly = pd.DataFrame(
        [{"player_id": "hurt", "week": w, "team": "DEEPRUN"} for w in range(1, 16)]
        + [{"player_id": "champ", "week": w, "team": "DEEPRUN"} for w in range(1, 23)]
    )
    injuries = _injuries([("hurt", 15, "Knee")])
    assert players_returning_from_injury(weekly, injuries) == {"hurt": "Knee"}
