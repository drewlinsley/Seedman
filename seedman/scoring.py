"""Turn raw nflverse stat lines into fantasy points under a league's rules.

We deliberately score from *component stats* rather than consuming a provider's
pre-computed fantasy total.  Every league tweaks something -- half-PPR, yardage
bonuses, kicker distance tiers -- and scoring from components means the
optimizer's numbers match the league site's numbers exactly instead of
approximately.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import ScoringRules

# Derived scoring keys -> the raw columns they sum over.
_DERIVED = {
    "fumbles_lost": ["sack_fumbles_lost", "rushing_fumbles_lost", "receiving_fumbles_lost"],
    "two_point_conversions": [
        "passing_2pt_conversions",
        "rushing_2pt_conversions",
        "receiving_2pt_conversions",
    ],
}


def _column(df: pd.DataFrame, name: str) -> pd.Series:
    """Fetch a stat column as floats, treating absent/NaN as zero.

    nflverse omits columns that have no data yet early in a season, and leaves
    NaN for stats a position never accrues, so missing means zero here.
    """
    if name in df.columns:
        return pd.to_numeric(df[name], errors="coerce").fillna(0.0)
    return pd.Series(0.0, index=df.index, dtype=float)


def with_derived_stats(df: pd.DataFrame) -> pd.DataFrame:
    """Add the composite columns that scoring rules refer to by a single name."""
    out = df.copy()
    for derived, sources in _DERIVED.items():
        out[derived] = sum(_column(df, src) for src in sources)
    return out


def score_players(df: pd.DataFrame, rules: ScoringRules) -> pd.Series:
    """Fantasy points for each row of a weekly player-stats frame."""
    if df.empty:
        return pd.Series(dtype=float, index=df.index)

    enriched = with_derived_stats(df)
    points = pd.Series(0.0, index=df.index, dtype=float)

    for stat, weight in rules.per_stat.items():
        points += _column(enriched, stat) * weight

    for bonus in rules.bonuses:
        qualifies = _column(enriched, bonus.stat) >= bonus.threshold
        points += qualifies.astype(float) * bonus.points

    return points


def score_dst(df: pd.DataFrame, rules: ScoringRules) -> pd.Series:
    """Fantasy points for team-defense rows.

    Expects one row per team-week with defensive counting stats plus a
    `points_allowed` column.
    """
    if df.empty:
        return pd.Series(dtype=float, index=df.index)

    points = pd.Series(0.0, index=df.index, dtype=float)
    for stat, weight in rules.dst_per_stat.items():
        points += _column(df, stat) * weight

    if rules.dst_points_allowed_tiers:
        allowed = _column(df, "points_allowed")
        tier_points = pd.Series(np.nan, index=df.index, dtype=float)
        # Tiers are ordered ascending; the first one the score fits into wins.
        for max_allowed, tier_value in rules.dst_points_allowed_tiers:
            hit = tier_points.isna() & (allowed <= max_allowed)
            tier_points[hit] = tier_value
        points += tier_points.fillna(0.0)

    return points


def build_dst_stat_lines(weekly: pd.DataFrame, schedule: pd.DataFrame) -> pd.DataFrame:
    """Aggregate individual defensive stat lines into team-defense rows.

    nflverse reports defense at the player level; survivor lineups start a whole
    unit, so we roll players up per team-week and attach points allowed from the
    final score.
    """
    if weekly.empty:
        return pd.DataFrame(
            columns=["player_id", "player_display_name", "position", "team", "season", "week"]
        )

    agg_cols = [
        "def_sacks",
        "def_interceptions",
        "fumble_recovery_opp",
        "def_tds",
        "def_safeties",
        "special_teams_tds",
        "def_punt_blocks",
        "def_fg_blocks",
    ]
    frame = weekly.copy()
    for col in agg_cols:
        frame[col] = _column(frame, col)

    grouped = (
        frame.groupby(["season", "week", "team"], as_index=False)[agg_cols].sum().reset_index(drop=True)
    )

    points_allowed = _points_allowed_table(schedule)
    grouped = grouped.merge(points_allowed, on=["season", "week", "team"], how="left")
    grouped["points_allowed"] = grouped["points_allowed"].fillna(0.0)

    grouped["position"] = "DEF"
    grouped["player_id"] = "DEF_" + grouped["team"].astype(str)
    grouped["player_display_name"] = grouped["team"].astype(str) + " D/ST"
    return grouped


def _points_allowed_table(schedule: pd.DataFrame) -> pd.DataFrame:
    """One row per team-week giving the points that team's defense allowed."""
    played = schedule.dropna(subset=["home_score", "away_score"]).copy()
    if played.empty:
        return pd.DataFrame(columns=["season", "week", "team", "points_allowed"])

    home = played[["season", "week", "home_team", "away_score"]].rename(
        columns={"home_team": "team", "away_score": "points_allowed"}
    )
    away = played[["season", "week", "away_team", "home_score"]].rename(
        columns={"away_team": "team", "home_score": "points_allowed"}
    )
    out = pd.concat([home, away], ignore_index=True)
    out["points_allowed"] = pd.to_numeric(out["points_allowed"], errors="coerce")
    return out
