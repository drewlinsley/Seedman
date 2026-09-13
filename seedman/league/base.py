"""What the optimizer needs to know about your standing in the league.

Deliberately small.  Any source -- a hand-maintained YAML file, a CSV export, or
a scraper for a specific league site -- can supply it, and the optimizer neither
knows nor cares which.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class LeagueState:
    """A snapshot of the league as of `current_week`."""

    current_week: int
    # gsis ids of players already burned this season. In a one-use format these
    # are permanently off the board and must be excluded from every future week.
    used_players: set[str] = field(default_factory=set)
    # Teams still alive including you. Drives how low the weekly cut line sits.
    teams_remaining: int = 12
    # Restrict picks to these players (a drafted-roster league). Empty means the
    # whole NFL is available, which is the usual survivor-pool rule.
    available_players: set[str] = field(default_factory=set)
    # Observed weekly scores of the league's teams, when the site exposes them.
    # Far better than guessing at the field's strength.
    observed_field_scores: list[float] = field(default_factory=list)
    source: str = "unknown"

    def describe(self) -> str:
        pool = "entire NFL" if not self.available_players else f"{len(self.available_players)} rostered"
        return (
            f"week {self.current_week} | {self.teams_remaining} teams alive | "
            f"{len(self.used_players)} players burned | pool: {pool} | source: {self.source}"
        )


class LeagueAdapter(Protocol):
    """Anything that can report the current league state."""

    def fetch_state(self) -> LeagueState: ...
