import pandas as pd
import pytest

from seedman.league.manual import ManualAdapter, PlayerResolver, UnresolvedPlayers, normalise

ROSTER = pd.DataFrame(
    [
        {"gsis_id": "00-0000001", "full_name": "Ja'Marr Chase", "team": "CIN", "position": "WR"},
        {"gsis_id": "00-0000002", "full_name": "Kenneth Walker III", "team": "SEA", "position": "RB"},
        {"gsis_id": "00-0000003", "full_name": "Amon-Ra St. Brown", "team": "DET", "position": "WR"},
    ]
)


@pytest.fixture
def resolver():
    return PlayerResolver(ROSTER)


@pytest.mark.parametrize(
    "written",
    ["Ja'Marr Chase", "jamarr chase", "JAMARR CHASE", "Ja Marr Chase"],
)
def test_names_resolve_regardless_of_punctuation_and_case(resolver, written):
    assert resolver.resolve(written) == "00-0000001"


def test_generational_suffixes_are_ignored(resolver):
    assert resolver.resolve("Kenneth Walker") == "00-0000002"
    assert resolver.resolve("Kenneth Walker III") == "00-0000002"


def test_periods_in_names_are_handled(resolver):
    assert resolver.resolve("Amon-Ra St Brown") == "00-0000003"


def test_raw_ids_pass_through(resolver):
    assert resolver.resolve("00-0000001") == "00-0000001"
    assert resolver.resolve("DEF_SF") == "DEF_SF"


def test_team_defenses_resolve_by_several_aliases(resolver):
    for alias in ("CIN", "CIN DEF", "CIN D/ST"):
        assert resolver.resolve(alias) == "DEF_CIN"


def test_unknown_names_return_none(resolver):
    assert resolver.resolve("Nobody At All") is None


def test_normalise_strips_accents():
    assert normalise("Amón-Rá St. Brown") == "amonra st brown"


def test_adapter_reads_state(tmp_path):
    path = tmp_path / "state.yaml"
    path.write_text(
        "current_week: 4\n"
        "teams_remaining: 7\n"
        "used_players:\n  - Ja'Marr Chase\n  - CIN\n"
        "observed_field_scores: [88.0, 91.5]\n"
    )
    state = ManualAdapter(path=path, roster=ROSTER).fetch_state()
    assert state.current_week == 4
    assert state.teams_remaining == 7
    assert state.used_players == {"00-0000001", "DEF_CIN"}
    assert state.observed_field_scores == [88.0, 91.5]


def test_a_misspelled_name_fails_loudly(tmp_path):
    """Silently dropping a used player would hand back a lineup you cannot set."""
    path = tmp_path / "state.yaml"
    path.write_text("current_week: 2\nused_players:\n  - Jamar Chaise\n")
    with pytest.raises(UnresolvedPlayers, match="Jamar Chaise"):
        ManualAdapter(path=path, roster=ROSTER).fetch_state()


def test_missing_state_file_explains_the_fix(tmp_path):
    with pytest.raises(FileNotFoundError, match="league init"):
        ManualAdapter(path=tmp_path / "nope.yaml", roster=ROSTER).fetch_state()
