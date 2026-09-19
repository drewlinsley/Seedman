"""The gate the injury report cannot see.

A player on injured reserve is absent from the weekly report by construction --
there is no practice for him to participate in -- so the availability model read
him as "not on report", the healthiest state it knows, and priced a man on IR as
a startable player. These tests pin the roster-status gate that closes it.
"""

from __future__ import annotations

import pandas as pd
import pytest

from seedman.injury import (
    ROSTER_STATUS_PLAY_RATE,
    roster_status_availability,
    roster_status_map,
)


# ----------------------------------------------------------------------
# The gate itself
# ----------------------------------------------------------------------
def test_reserve_is_not_startable_this_week():
    assert roster_status_availability("RES") == pytest.approx(0.052)


def test_reserve_beats_no_one_to_a_lineup_slot():
    """The whole point: IR must not outrank a listed-and-cleared starter."""
    from seedman.injury import REPORT_STATUS_PRIOR, NOT_ON_REPORT

    assert roster_status_availability("RES") < REPORT_STATUS_PRIOR[NOT_ON_REPORT]


@pytest.mark.parametrize("code", ["INA", "EXE", "CUT", "RET"])
def test_statuses_that_mean_he_is_not_playing(code):
    assert roster_status_availability(code) == 0.0


def test_active_defers_to_the_injury_report():
    """ACT's measured 0.695 is usage, not availability -- it must not gate."""
    assert roster_status_availability("ACT") is None


def test_an_unrecognised_code_defers_rather_than_benching_everyone():
    assert roster_status_availability("XYZ") is None
    assert roster_status_availability("") is None
    assert roster_status_availability(None) is None


def test_case_and_whitespace_do_not_open_the_gate():
    assert roster_status_availability("  res ") == pytest.approx(0.052)


# ----------------------------------------------------------------------
# The gate does not expire
# ----------------------------------------------------------------------
def test_a_gated_status_holds_for_every_week_of_the_horizon():
    """Nothing in this data dates a return, so none is extrapolated.

    An earlier version let reserve lapse after the league's four-game minimum.
    It reverted A.J. Brown to 75% availability and pencilled him into week 7 --
    the exact player the gate exists to keep out of a lineup.
    """
    for code in ROSTER_STATUS_PLAY_RATE:
        first = roster_status_availability(code)
        assert first is not None
        assert first <= 0.06, f"{code} is not a gate at {first}"


def test_the_gate_takes_no_horizon_argument_at_all():
    """A signature that cannot express a return curve cannot guess one."""
    import inspect

    params = list(inspect.signature(roster_status_availability).parameters)
    assert params == ["status"]


def test_active_is_absent_from_the_rate_table_on_purpose():
    assert "ACT" not in ROSTER_STATUS_PLAY_RATE


# ----------------------------------------------------------------------
# Reading the roster snapshot
# ----------------------------------------------------------------------
def _roster(rows):
    return pd.DataFrame(rows, columns=["gsis_id", "status", "week"])


def test_map_reads_the_latest_row_per_player():
    """The file is a snapshot, not a panel: a stale week is still current truth."""
    frame = _roster([("p1", "RES", 1), ("p1", "ACT", 2), ("p2", "CUT", 1)])
    assert roster_status_map(frame) == {"p1": "ACT", "p2": "CUT"}


def test_map_survives_a_missing_status_column():
    frame = pd.DataFrame({"gsis_id": ["p1"], "week": [2]})
    assert roster_status_map(frame) == {}


def test_map_survives_an_empty_roster():
    assert roster_status_map(pd.DataFrame()) == {}
    assert roster_status_map(None) == {}


def test_map_drops_players_with_no_id_or_no_status():
    frame = _roster([(None, "ACT", 2), ("p2", None, 2), ("p3", "  ", 2), ("p4", "ACT", 2)])
    assert roster_status_map(frame) == {"p4": "ACT"}


# ----------------------------------------------------------------------
# End to end: the gate reaches the projections
# ----------------------------------------------------------------------
SCHEDULE = pd.DataFrame(
    [
        {"season": 2026, "week": w, "home_team": "AAA", "away_team": "BBB",
         "home_score": None, "away_score": None, "result": None, "total": None,
         "spread_line": 3.0, "total_line": 44.0, "gameday": f"2026-09-{w:02d}"}
        for w in range(1, 11)
    ]
)

STATS = pd.DataFrame(
    [
        {"player_id": pid, "player_display_name": name, "position": "WR",
         "team": "AAA", "season": 2026, "week": w, "receptions": 6,
         "receiving_yards": 90, "receiving_tds": 1}
        for pid, name in [("00-0000001", "Healthy"), ("00-0000002", "OnReserve")]
        for w in (1, 2, 3)
    ]
)


def _projections(config, statuses):
    from seedman.projections import ProjectionModel

    roster = pd.DataFrame(
        [{"gsis_id": pid, "status": code, "week": 3, "team": "AAA"}
         for pid, code in statuses.items()]
    )
    model = ProjectionModel(
        config,
        weekly_current=STATS,
        weekly_prior=pd.DataFrame(),
        schedule=SCHEDULE,
        injuries=pd.DataFrame(),
        rosters=roster,
        through_week=3,
    )
    frame = model.project_weeks([4, 9], as_of_week=4)
    return frame.set_index(["player_id", "week"])


def test_reserve_collapses_the_projection_the_report_left_untouched(config):
    """Identical production, identical (empty) injury report -- only status differs."""
    frame = _projections(config, {"00-0000001": "ACT", "00-0000002": "RES"})

    healthy = frame.loc[("00-0000001", 4)]
    reserve = frame.loc[("00-0000002", 4)]

    assert healthy["mean"] > 5.0, "the control must be a real projection"
    assert reserve["mean"] < 0.1 * healthy["mean"]
    assert reserve["availability"] == pytest.approx(0.052)


def test_reserve_is_not_banked_as_a_healthy_stud_for_a_later_week(config):
    """Beyond the window the hazard decays from TODAY -- so today must be the floor.

    Without the clamp an IR player decays from the phantom 0.92 and reappears as
    a fully-fit week 9 starter, which in a one-start-per-player format is the
    more expensive error of the two: it reserves a lineup slot for a man who is
    not there. With it he decays from 0.052 instead and stays behind all season.

    What this does NOT pin is the rate of that decay. Mean reversion pulls him
    to ~0.75 by five weeks out, which is plausible for a return designation and
    far too generous for a season-ending one -- and the roster file's own
    R01/R48 split may be exactly that distinction. Two weeks of live snapshots
    cannot tell them apart, and historical rosters carry no week-by-week panel
    to learn it from, so the gap is documented rather than guessed at.
    """
    frame = _projections(config, {"00-0000001": "ACT", "00-0000002": "RES"})

    for week in (4, 9):
        healthy = frame.loc[("00-0000001", week)]["availability"]
        reserve = frame.loc[("00-0000002", week)]["availability"]
        assert reserve < healthy, f"week {week}: {reserve:.3f} vs {healthy:.3f}"

    near = frame.loc[("00-0000002", 4)]["availability"]
    assert near == pytest.approx(0.052), "inside the window the gate is absolute"


def test_an_active_player_is_untouched_by_the_gate(config):
    """A regression guard: the gate must cost nothing to everyone it is not about."""
    gated = _projections(config, {"00-0000001": "ACT", "00-0000002": "ACT"})
    ungated = _projections(config, {})

    for week in (4, 9):
        assert gated.loc[("00-0000001", week)]["mean"] == pytest.approx(
            ungated.loc[("00-0000001", week)]["mean"]
        )


# ----------------------------------------------------------------------
# What the lineup actually prints
# ----------------------------------------------------------------------
def test_the_gate_reason_is_what_gets_printed():
    """"RES" in a column is not an answer to "why is this man in my lineup"."""
    from seedman.injury import roster_status_label

    assert roster_status_label("RES") == "INJURED RESERVE"
    assert roster_status_label("EXE") == "exempt list"
    assert roster_status_label("ACT") == ""
    assert roster_status_label("") == ""


def test_the_roster_gate_outranks_the_report_in_the_printed_column():
    """The report has nothing to say about a man on IR, so it must not speak."""
    from seedman.injury import NOT_ON_REPORT
    from seedman.projections import _status_label

    assert _status_label("RES", NOT_ON_REPORT) == "INJURED RESERVE"
    assert _status_label("RES", "Questionable") == "INJURED RESERVE"


def test_the_two_blank_report_states_are_spelled_out_not_hidden():
    """They are different states carrying different availability (0.95 vs 0.92)."""
    from seedman.injury import NOT_ON_REPORT
    from seedman.projections import _status_label

    assert _status_label("ACT", NOT_ON_REPORT) == "not listed"
    assert _status_label("ACT", "(none)") == "cleared"
    assert _status_label("ACT", NOT_ON_REPORT) != _status_label("ACT", "(none)")


def test_a_real_designation_is_passed_through_untouched():
    from seedman.projections import _status_label

    assert _status_label("ACT", "Doubtful") == "Doubtful"
