import pytest

from seedman.config import ConfigError, LeagueConfig, SurvivalFormat
from tests.conftest import LEAGUE_YAML


def test_slots_parse_from_both_shorthand_and_mapping():
    cfg = LeagueConfig.from_dict(
        {**LEAGUE_YAML, "roster": {"slots": ["QB", {"name": "FLEX", "eligible": ["RB", "WR", "TE"]}]}}
    )
    assert cfg.slots[0].eligible == ("QB",)
    assert cfg.slots[1].accepts("TE")
    assert not cfg.slots[1].accepts("QB")


def test_unknown_scoring_stat_is_rejected():
    broken = {**LEAGUE_YAML, "scoring": {"passing_yards": 0.04, "touchdown_dance": 99}}
    with pytest.raises(ConfigError, match="touchdown_dance"):
        LeagueConfig.from_dict(broken)


def test_duplicate_slot_names_are_rejected():
    broken = {**LEAGUE_YAML, "roster": {"slots": ["RB", "RB"]}}
    with pytest.raises(ConfigError, match="duplicate"):
        LeagueConfig.from_dict(broken)


def test_league_with_no_slots_is_rejected():
    with pytest.raises(ConfigError, match="at least one roster slot"):
        LeagueConfig.from_dict({**LEAGUE_YAML, "roster": {"slots": []}})


def test_teams_alive_declines_and_floors_at_two():
    fmt = SurvivalFormat(teams_remaining=6, as_of_week=2, eliminations_per_week=1)
    assert fmt.teams_alive_at(2) == 6
    assert fmt.teams_alive_at(5) == 3
    assert fmt.teams_alive_at(40) == 2


def test_double_elimination_halves_the_runway():
    fmt = SurvivalFormat(teams_remaining=10, as_of_week=1, eliminations_per_week=2)
    assert fmt.teams_alive_at(3) == 6


def test_shipped_league_config_is_valid():
    from seedman.config import default_config_path

    cfg = LeagueConfig.from_yaml(default_config_path())
    cfg.validate()
    # Confirmed from the league site: six slots, two WRs and a flex, and
    # crucially no kicker and no team defense.
    assert {s.name for s in cfg.slots} == {"QB", "RB", "WR1", "WR2", "TE", "FLEX"}
    assert cfg.positions_used == {"QB", "RB", "WR", "TE"}
    flex = next(s for s in cfg.slots if s.name == "FLEX")
    assert flex.accepts("RB") and flex.accepts("WR") and not flex.accepts("QB")
    # Unverified guesses must be declared so reports can shout about them.
    assert cfg.assumptions
