"""League configuration: scoring rules, roster shape, and survival format.

Everything the optimizer needs to know about *your specific league* lives in a
single YAML file so that none of it is hard-coded in the model.  See
``configs/league.yaml`` for the shipped default and the notes on which values
are confirmed versus assumed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Stat columns exposed by the nflverse weekly player-stats release that the
# scoring engine understands directly.  Anything listed in a league's `scoring`
# block must either appear here or be a derived key handled in `scoring.py`.
KNOWN_STAT_COLUMNS = {
    "completions",
    "attempts",
    "passing_yards",
    "passing_tds",
    "passing_interceptions",
    "passing_2pt_conversions",
    "passing_first_downs",
    "sacks_suffered",
    "carries",
    "rushing_yards",
    "rushing_tds",
    "rushing_2pt_conversions",
    "rushing_first_downs",
    "receptions",
    "targets",
    "receiving_yards",
    "receiving_tds",
    "receiving_2pt_conversions",
    "receiving_first_downs",
    "special_teams_tds",
    "punt_return_yards",
    "kickoff_return_yards",
    "pat_made",
    "pat_missed",
    "fg_made",
    "fg_missed",
    "fg_made_0_19",
    "fg_made_20_29",
    "fg_made_30_39",
    "fg_made_40_49",
    "fg_made_50_59",
    "fg_made_60_",
}

# Derived keys computed by `scoring.py` from several raw columns.
DERIVED_SCORING_KEYS = {
    "fumbles_lost",  # sack + rushing + receiving fumbles lost
    "two_point_conversions",  # passing + rushing + receiving 2pt
}

VALID_SCORING_KEYS = KNOWN_STAT_COLUMNS | DERIVED_SCORING_KEYS


class ConfigError(ValueError):
    """Raised when a league YAML file is malformed or internally inconsistent."""


@dataclass(frozen=True)
class Slot:
    """One startable lineup position.

    `eligible` lists the player positions that may fill the slot, so a FLEX is
    just a slot with several eligible positions.
    """

    name: str
    eligible: tuple[str, ...]

    def accepts(self, position: str) -> bool:
        return position in self.eligible


@dataclass(frozen=True)
class YardageBonus:
    """A one-off bonus awarded when a stat clears a threshold in a game."""

    stat: str
    threshold: float
    points: float


@dataclass(frozen=True)
class ScoringRules:
    """Linear per-stat weights plus optional threshold bonuses."""

    per_stat: dict[str, float] = field(default_factory=dict)
    bonuses: tuple[YardageBonus, ...] = ()
    # Team-defense scoring is structurally different (points-allowed tiers), so
    # it gets its own block rather than being squeezed into `per_stat`.
    dst_per_stat: dict[str, float] = field(default_factory=dict)
    dst_points_allowed_tiers: tuple[tuple[float, float], ...] = ()

    def validate(self) -> None:
        unknown = set(self.per_stat) - VALID_SCORING_KEYS
        if unknown:
            raise ConfigError(
                f"unknown scoring stat(s): {sorted(unknown)}. "
                f"Valid keys: {sorted(VALID_SCORING_KEYS)}"
            )
        for bonus in self.bonuses:
            if bonus.stat not in VALID_SCORING_KEYS:
                raise ConfigError(f"unknown bonus stat: {bonus.stat!r}")


@dataclass(frozen=True)
class SurvivalFormat:
    """How the league decides who advances, and how player inventory is consumed.

    Two shapes, and the difference decides everything about when to spend a
    player. In `survivor` the field shrinks and the bar each week is the *lowest*
    score among everyone still alive, so one bad week ends the season. In
    `head_to_head` nobody is eliminated weekly; you play one opponent, and the
    bar is that single opponent's score. Missing the playoffs takes a bad
    *record*, not a bad week, which is what makes banking a stud for a playoff
    week a real strategy rather than a way to get knocked out in September.
    """

    # How many times a single NFL player may be started across the whole season.
    # 1 means "burn him once", and it is what creates the spend-vs-save tension
    # in either mode.
    player_reuse_limit: int = 1
    # "survivor" or "head_to_head".
    mode: str = "survivor"
    # Teams in the league (survivor: still alive *including you*, as of `as_of_week`).
    teams_remaining: int = 12
    as_of_week: int = 1
    eliminations_per_week: int = 1
    # Last week that counts, inclusive of any playoff weeks.
    final_week: int = 17
    # head_to_head only: last week of the regular season, and how many teams
    # make the bracket. Weeks after this and up to `final_week` are playoffs.
    regular_season_final_week: int = 15
    playoff_berths: int = 6

    HEAD_TO_HEAD = "head_to_head"
    SURVIVOR = "survivor"

    def validate(self) -> None:
        if self.player_reuse_limit < 1:
            raise ConfigError("player_reuse_limit must be >= 1")
        if self.teams_remaining < 2:
            raise ConfigError("teams_remaining must be >= 2")
        if self.mode not in (self.SURVIVOR, self.HEAD_TO_HEAD):
            raise ConfigError(
                f"survival.mode must be 'survivor' or 'head_to_head', got {self.mode!r}"
            )
        if self.mode == self.SURVIVOR and self.eliminations_per_week < 1:
            raise ConfigError("eliminations_per_week must be >= 1 in survivor mode")
        if self.mode == self.HEAD_TO_HEAD:
            if not 1 <= self.playoff_berths < self.teams_remaining:
                raise ConfigError(
                    "playoff_berths must be between 1 and teams_remaining - 1"
                )
            if self.regular_season_final_week > self.final_week:
                raise ConfigError(
                    "regular_season_final_week cannot be after final_week"
                )

    @property
    def is_head_to_head(self) -> bool:
        return self.mode == self.HEAD_TO_HEAD

    def is_playoff_week(self, week: int) -> bool:
        """Weeks you must win outright, as opposed to merely win often enough."""
        return self.is_head_to_head and week > self.regular_season_final_week

    def opponents_at(self, week: int) -> int:
        """How many teams you have to outscore in `week`.

        The whole difference between the two formats in one number: one named
        opponent, or everyone else still breathing.
        """
        if self.is_head_to_head:
            return 1
        return max(1, self.teams_alive_at(week) - 1)

    def teams_alive_at(self, week: int) -> int:
        """Project how many teams are still alive at the start of `week`."""
        if self.is_head_to_head:
            return self.teams_remaining
        weeks_elapsed = max(0, week - self.as_of_week)
        alive = self.teams_remaining - weeks_elapsed * self.eliminations_per_week
        return max(2, alive)


@dataclass(frozen=True)
class LeagueConfig:
    name: str
    season: int
    slots: tuple[Slot, ...]
    scoring: ScoringRules
    survival: SurvivalFormat
    # Free-form record of which fields were verified against the league site and
    # which are assumptions, so the optimizer can warn loudly about the latter.
    assumptions: tuple[str, ...] = ()

    @property
    def positions_used(self) -> set[str]:
        return {pos for slot in self.slots for pos in slot.eligible}

    def validate(self) -> None:
        if not self.slots:
            raise ConfigError("league must define at least one roster slot")
        names = [s.name for s in self.slots]
        if len(names) != len(set(names)):
            raise ConfigError(f"duplicate slot names: {names}")
        self.scoring.validate()
        self.survival.validate()

    @classmethod
    def from_yaml(cls, path: str | Path) -> LeagueConfig:
        path = Path(path)
        if not path.exists():
            raise ConfigError(f"league config not found: {path}")
        with path.open() as fh:
            raw = yaml.safe_load(fh) or {}
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> LeagueConfig:
        league = raw.get("league") or {}
        roster = raw.get("roster") or {}
        scoring_raw = raw.get("scoring") or {}
        survival_raw = raw.get("survival") or {}

        slots: list[Slot] = []
        for entry in roster.get("slots", []):
            if isinstance(entry, str):
                slots.append(Slot(name=entry, eligible=(entry,)))
                continue
            name = entry.get("name")
            if not name:
                raise ConfigError(f"roster slot missing 'name': {entry!r}")
            eligible = entry.get("eligible") or [name]
            slots.append(Slot(name=name, eligible=tuple(eligible)))

        bonuses = tuple(
            YardageBonus(
                stat=b["stat"], threshold=float(b["threshold"]), points=float(b["points"])
            )
            for b in scoring_raw.get("bonuses", [])
        )
        tiers = tuple(
            (float(t["max_points_allowed"]), float(t["points"]))
            for t in scoring_raw.get("dst_points_allowed_tiers", [])
        )
        per_stat = {
            k: float(v)
            for k, v in scoring_raw.items()
            if k not in {"bonuses", "dst_points_allowed_tiers", "dst"}
        }
        scoring = ScoringRules(
            per_stat=per_stat,
            bonuses=bonuses,
            dst_per_stat={k: float(v) for k, v in (scoring_raw.get("dst") or {}).items()},
            dst_points_allowed_tiers=tiers,
        )

        survival = SurvivalFormat(
            player_reuse_limit=int(survival_raw.get("player_reuse_limit", 1)),
            mode=str(survival_raw.get("mode", "survivor")),
            teams_remaining=int(survival_raw.get("teams_remaining", 12)),
            as_of_week=int(survival_raw.get("as_of_week", 1)),
            eliminations_per_week=int(survival_raw.get("eliminations_per_week", 1)),
            final_week=int(survival_raw.get("final_week", 17)),
            regular_season_final_week=int(
                survival_raw.get("regular_season_final_week", 15)
            ),
            playoff_berths=int(survival_raw.get("playoff_berths", 6)),
        )

        cfg = cls(
            name=league.get("name", "unnamed league"),
            season=int(league.get("season", 2026)),
            slots=tuple(slots),
            scoring=scoring,
            survival=survival,
            assumptions=tuple(raw.get("assumptions", [])),
        )
        cfg.validate()
        return cfg


def default_config_path() -> Path:
    """Resolve the league config, honouring $SEEDMAN_LEAGUE_CONFIG."""
    env = os.environ.get("SEEDMAN_LEAGUE_CONFIG")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "configs" / "league.yaml"
