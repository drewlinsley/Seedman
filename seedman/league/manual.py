"""League state from a file you maintain by hand.

This is the adapter that always works: no login, no scraping, no API contract to
break mid-season.  Drop the names you have already used into a small YAML file
and the optimizer knows what is off the board.

Names are resolved to nflverse player ids fuzzily (case- and punctuation-
insensitive, with a last-name plus first-initial fallback) so you can type
"Ja'Marr Chase" or "jamarr chase" and get the same answer.  Anything that cannot
be resolved is reported loudly rather than silently ignored -- a player the
optimizer wrongly thinks is available is the most expensive kind of bug here.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml

from .base import LeagueState


class UnresolvedPlayers(ValueError):
    """Raised when named players cannot be matched to nflverse ids."""

    def __init__(self, names: list[str]) -> None:
        self.names = names
        super().__init__(
            "could not resolve these players to NFL ids: "
            + ", ".join(sorted(names))
            + ". Check spelling, or use the gsis id directly."
        )


def normalise(name: str) -> str:
    """Strip accents, punctuation, suffixes and case for matching."""
    text = unicodedata.normalize("NFKD", str(name))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", text)
    text = re.sub(r"[^a-z0-9 ]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _short_key(name: str) -> str:
    """`first-initial + last name`, e.g. 'j chase', for looser matching."""
    parts = normalise(name).split()
    if len(parts) < 2:
        return normalise(name)
    return f"{parts[0][:1]} {parts[-1]}"


@dataclass
class PlayerResolver:
    """Maps human-typed names onto nflverse player ids."""

    roster: pd.DataFrame

    def __post_init__(self) -> None:
        self._exact: dict[str, str] = {}
        self._short: dict[str, list[str]] = {}

        frame = self.roster.dropna(subset=["gsis_id"])
        for row in frame.itertuples():
            name = getattr(row, "full_name", None)
            if not isinstance(name, str):
                continue
            self._exact.setdefault(normalise(name), row.gsis_id)
            self._short.setdefault(_short_key(name), []).append(row.gsis_id)

        # Team defenses are addressable by team abbreviation or "<TEAM> DEF".
        for team in frame["team"].dropna().unique():
            for alias in (str(team), f"{team} def", f"{team} dst", f"{team} d st"):
                self._exact.setdefault(normalise(alias), f"DEF_{team}")

    def resolve(self, name: str) -> str | None:
        if not isinstance(name, str) or not name.strip():
            return None
        # A raw gsis id (00-00xxxxx) or synthetic defense id passes straight through.
        if re.fullmatch(r"00-\d{7}", name.strip()) or name.strip().startswith("DEF_"):
            return name.strip()

        key = normalise(name)
        if key in self._exact:
            return self._exact[key]

        candidates = self._short.get(_short_key(name), [])
        if len(candidates) == 1:
            return candidates[0]
        return None

    def resolve_all(self, names: list[str]) -> tuple[set[str], list[str]]:
        resolved: set[str] = set()
        missing: list[str] = []
        for name in names:
            found = self.resolve(name)
            if found:
                resolved.add(found)
            else:
                missing.append(name)
        return resolved, missing


@dataclass
class ManualAdapter:
    """Reads league state from a YAML file plus the nflverse roster."""

    path: Path
    roster: pd.DataFrame
    strict: bool = True

    def fetch_state(self) -> LeagueState:
        path = Path(self.path)
        if not path.exists():
            raise FileNotFoundError(
                f"{path} not found. Run `seedman league init` to create a starter file."
            )

        with path.open() as fh:
            raw = yaml.safe_load(fh) or {}

        resolver = PlayerResolver(self.roster)
        used_names = [str(n) for n in (raw.get("used_players") or [])]
        used, missing = resolver.resolve_all(used_names)

        available_names = [str(n) for n in (raw.get("available_players") or [])]
        available, missing_avail = resolver.resolve_all(available_names)
        missing.extend(missing_avail)

        if missing and self.strict:
            raise UnresolvedPlayers(missing)

        return LeagueState(
            current_week=int(raw.get("current_week", 1)),
            used_players=used,
            teams_remaining=int(raw.get("teams_remaining", 12)),
            available_players=available,
            observed_field_scores=[float(x) for x in (raw.get("observed_field_scores") or [])],
            wins=int((raw.get("record") or {}).get("wins", 0)),
            losses=int((raw.get("record") or {}).get("losses", 0)),
            source=f"manual:{path.name}",
        )


STARTER_TEMPLATE = """\
# Your league state. Update this after you set each week's lineup.
#
# `used_players` is the important one: in a one-use-per-player league these are
# permanently off the board, and the optimizer will never suggest them again.
# Names are matched loosely, so "Ja'Marr Chase" and "jamarr chase" both work.
# Team defenses can be written as "SF", "SF DEF", or "DEF_SF".

current_week: {week}

# Teams still alive, including you.
teams_remaining: 12

# Players you have already started this season.
used_players: []
#  - Josh Allen
#  - Bijan Robinson

# Leave empty if you may pick any NFL player each week (the usual survivor rule).
# List your roster here instead if your league drafts.
available_players: []

# Head-to-head leagues only: your record so far.
record:
  wins: 0
  losses: 0

# Optional: what the other teams actually scored, week by week, all teams pooled.
# Supplying even a couple of weeks of real scores makes the weekly cut line --
# and therefore every survival number -- far more accurate than the default guess.
observed_field_scores: []
"""
