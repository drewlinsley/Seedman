"""Synthetic fixtures so the suite runs fast and offline."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from seedman.config import LeagueConfig

LEAGUE_YAML = {
    "league": {"name": "Test League", "season": 2026},
    "survival": {
        "player_reuse_limit": 1,
        "teams_remaining": 8,
        "as_of_week": 1,
        "eliminations_per_week": 1,
        "final_week": 6,
    },
    "roster": {
        "slots": [
            {"name": "QB", "eligible": ["QB"]},
            {"name": "RB", "eligible": ["RB"]},
            {"name": "WR", "eligible": ["WR"]},
        ]
    },
    "scoring": {
        "passing_yards": 0.04,
        "passing_tds": 4,
        "passing_interceptions": -2,
        "rushing_yards": 0.1,
        "rushing_tds": 6,
        "receptions": 1.0,
        "receiving_yards": 0.1,
        "receiving_tds": 6,
        "fumbles_lost": -2,
        "two_point_conversions": 2,
    },
}


@pytest.fixture
def config() -> LeagueConfig:
    return LeagueConfig.from_dict(LEAGUE_YAML)


@pytest.fixture
def projections() -> pd.DataFrame:
    """A tidy projection frame: 6 players per position across 4 weeks.

    Player skill is deliberately staggered so the optimum is easy to reason
    about by hand.
    """
    rng = np.random.default_rng(7)
    rows = []
    teams = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
    # Offset each position around the team list so that the best QB, RB and WR
    # are not all on one team -- otherwise the diversification cap, not player
    # quality, decides the lineup.
    for offset, (position, base) in enumerate((("QB", 24.0), ("RB", 18.0), ("WR", 16.0))):
        for rank in range(6):
            for week in range(1, 5):
                team = teams[(rank + 2 * offset) % 6]
                rows.append(
                    {
                        "player_id": f"{position}{rank}",
                        "name": f"{position} number {rank}",
                        "position": position,
                        "team": team,
                        "opponent": teams[(rank + 3) % 6],
                        "week": week,
                        "mean": base - 2.0 * rank,
                        "sd": 5.0 + rng.normal(0, 0.1),
                        "availability": 0.97,
                        "conditional_mean": base - 2.0 * rank,
                        "implied_team_total": 23.0,
                        "report_status": "",
                    }
                )
    return pd.DataFrame(rows)


@pytest.fixture
def weekly_stats() -> pd.DataFrame:
    """A handful of real-shaped nflverse stat lines with known point totals."""
    return pd.DataFrame(
        [
            {
                "player_id": "00-0000001",
                "player_display_name": "Test QB",
                "position": "QB",
                "team": "AAA",
                "season": 2026,
                "week": 1,
                "passing_yards": 300,
                "passing_tds": 3,
                "passing_interceptions": 1,
                "rushing_yards": 20,
                "rushing_tds": 0,
                "receptions": 0,
                "receiving_yards": 0,
                "receiving_tds": 0,
                "sack_fumbles_lost": 1,
                "rushing_fumbles_lost": 0,
                "receiving_fumbles_lost": 0,
                "passing_2pt_conversions": 1,
                "rushing_2pt_conversions": 0,
                "receiving_2pt_conversions": 0,
            },
            {
                "player_id": "00-0000002",
                "player_display_name": "Test WR",
                "position": "WR",
                "team": "BBB",
                "season": 2026,
                "week": 1,
                "passing_yards": 0,
                "passing_tds": 0,
                "passing_interceptions": 0,
                "rushing_yards": 0,
                "rushing_tds": 0,
                "receptions": 8,
                "receiving_yards": 120,
                "receiving_tds": 1,
                "sack_fumbles_lost": 0,
                "rushing_fumbles_lost": 0,
                "receiving_fumbles_lost": 0,
                "passing_2pt_conversions": 0,
                "rushing_2pt_conversions": 0,
                "receiving_2pt_conversions": 0,
            },
        ]
    )
