import pandas as pd

from seedman.config import LeagueConfig, ScoringRules, YardageBonus
from seedman.scoring import score_dst, score_players, with_derived_stats


def test_scores_match_hand_calculation(config: LeagueConfig, weekly_stats):
    points = score_players(weekly_stats, config.scoring)

    # QB: 300*0.04 + 3*4 + 1*(-2) + 20*0.1 + 1 fumble*(-2) + 1 two-pointer*2
    assert points.iloc[0] == 12 + 12 - 2 + 2 - 2 + 2
    # WR: 8 receptions + 120*0.1 + 1*6
    assert points.iloc[1] == 8 + 12 + 6


def test_derived_stats_sum_their_components(weekly_stats):
    enriched = with_derived_stats(weekly_stats)
    assert enriched["fumbles_lost"].tolist() == [1.0, 0.0]
    assert enriched["two_point_conversions"].tolist() == [1.0, 0.0]


def test_missing_columns_are_treated_as_zero(config: LeagueConfig):
    """nflverse omits columns entirely when a season has no data for them yet."""
    sparse = pd.DataFrame([{"player_id": "x", "receptions": 4, "receiving_yards": 50}])
    assert score_players(sparse, config.scoring).iloc[0] == 4 + 5


def test_yardage_bonus_applies_at_threshold():
    rules = ScoringRules(
        per_stat={"receiving_yards": 0.1},
        bonuses=(YardageBonus(stat="receiving_yards", threshold=100, points=3),),
    )
    frame = pd.DataFrame([{"receiving_yards": 99}, {"receiving_yards": 100}])
    points = score_players(frame, rules)
    assert points.iloc[0] == 9.9
    assert points.iloc[1] == 13.0


def test_dst_points_allowed_tiers_pick_the_first_match():
    rules = ScoringRules(
        dst_per_stat={"def_sacks": 1},
        dst_points_allowed_tiers=((0, 10), (6, 7), (20, 1), (999, -4)),
    )
    frame = pd.DataFrame(
        [
            {"def_sacks": 2, "points_allowed": 0},
            {"def_sacks": 2, "points_allowed": 3},
            {"def_sacks": 2, "points_allowed": 17},
            {"def_sacks": 2, "points_allowed": 45},
        ]
    )
    assert score_dst(frame, rules).tolist() == [12.0, 9.0, 3.0, -2.0]


def test_empty_frame_scores_without_error(config: LeagueConfig):
    assert score_players(pd.DataFrame(), config.scoring).empty
