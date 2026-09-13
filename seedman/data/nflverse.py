"""Access to the nflverse open-data releases.

nflverse (https://github.com/nflverse) publishes the whole NFL statistical
record as versioned CSV/parquet release assets, refreshed within hours of each
game.  It is the best free, programmatic, non-scraped source of:

  * weekly per-player stat lines (every component stat, so *any* league's
    scoring can be recomputed exactly rather than trusting someone else's total)
  * official NFL injury-report designations
  * snap counts (the usage signal that leads fantasy production)
  * rosters carrying cross-site id maps (sleeper/espn/yahoo/pfr/...), which is
    how we join a league site's player list to the stats

Schedule and betting lines come from the sibling `nflverse/nfldata` repo, whose
`games.csv` carries closing spreads and totals for games a few weeks out.
"""

from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import requests

log = logging.getLogger(__name__)

RELEASE_BASE = "https://github.com/nflverse/nflverse-data/releases/download"
GAMES_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"

DEFAULT_CACHE = Path(".cache/nflverse")
# In-season data changes daily; a few hours keeps us fresh without hammering.
DEFAULT_TTL_SECONDS = 6 * 3600


class DataUnavailable(RuntimeError):
    """Raised when a dataset cannot be fetched and no cached copy exists."""


@dataclass
class NflverseClient:
    cache_dir: Path = DEFAULT_CACHE
    ttl_seconds: int = DEFAULT_TTL_SECONDS
    timeout: int = 120
    offline: bool = False

    def __post_init__(self) -> None:
        self.cache_dir = Path(self.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # low-level fetch + cache
    # ------------------------------------------------------------------
    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / key

    def _is_fresh(self, path: Path) -> bool:
        if not path.exists():
            return False
        return (time.time() - path.stat().st_mtime) < self.ttl_seconds

    def _download(self, url: str, cache_key: str, *, required: bool = True) -> pd.DataFrame | None:
        """Fetch `url` into the cache and return it as a DataFrame.

        Falls back to a stale cached copy when the network is unavailable, which
        keeps the optimizer usable on a plane or behind a restrictive proxy.
        """
        path = self._cache_path(cache_key)

        if self._is_fresh(path) or self.offline:
            if path.exists():
                return pd.read_csv(path, low_memory=False)
            if self.offline:
                if required:
                    raise DataUnavailable(f"offline and no cached copy of {cache_key}")
                return None

        try:
            resp = requests.get(url, timeout=self.timeout)
            if resp.status_code == 404:
                # Normal early in a season: the asset for this year may not exist yet.
                log.info("nflverse asset not published yet: %s", url)
                if path.exists():
                    return pd.read_csv(path, low_memory=False)
                if required:
                    raise DataUnavailable(f"{url} returned 404 and nothing is cached")
                return None
            resp.raise_for_status()
            path.write_bytes(resp.content)
            return pd.read_csv(io.BytesIO(resp.content), low_memory=False)
        except requests.RequestException as exc:
            if path.exists():
                log.warning("fetch failed for %s (%s); using stale cache", url, exc)
                return pd.read_csv(path, low_memory=False)
            if required:
                raise DataUnavailable(f"could not fetch {url}: {exc}") from exc
            log.warning("optional dataset unavailable: %s (%s)", url, exc)
            return None

    def _release(self, tag: str, filename: str, *, required: bool = True) -> pd.DataFrame | None:
        return self._download(f"{RELEASE_BASE}/{tag}/{filename}", filename, required=required)

    # ------------------------------------------------------------------
    # datasets
    # ------------------------------------------------------------------
    def weekly_stats(self, season: int) -> pd.DataFrame:
        """Per-player, per-week stat lines with every scoring component."""
        df = self._release("stats_player", f"stats_player_week_{season}.csv", required=False)
        if df is None:
            # Season hasn't started; return an empty frame with the right shape
            # so callers can fall back to prior-season priors without branching.
            return pd.DataFrame(columns=["player_id", "season", "week", "position", "team"])
        return df

    def rosters(self, season: int) -> pd.DataFrame:
        """Season rosters, including the cross-site player id map."""
        df = self._release("rosters", f"roster_{season}.csv", required=False)
        if df is None:
            df = self._release("rosters", f"roster_{season - 1}.csv", required=True)
            log.warning("roster_%s unavailable; falling back to %s", season, season - 1)
        return df

    def injuries(self, season: int) -> pd.DataFrame:
        """Official NFL injury-report designations by week."""
        df = self._release("injuries", f"injuries_{season}.csv", required=False)
        if df is None:
            return pd.DataFrame(
                columns=["season", "week", "team", "gsis_id", "report_status", "practice_status"]
            )
        return df

    def snap_counts(self, season: int) -> pd.DataFrame:
        """Per-game offensive snap share — the leading indicator of usage."""
        df = self._release("snap_counts", f"snap_counts_{season}.csv", required=False)
        if df is None:
            return pd.DataFrame(columns=["season", "week", "player", "team", "offense_pct"])
        return df

    def schedule(self) -> pd.DataFrame:
        """All games ever, plus spread/total for games books have posted."""
        df = self._download(GAMES_URL, "games.csv", required=True)
        assert df is not None  # required=True raises rather than returning None
        return df

    def season_schedule(self, season: int) -> pd.DataFrame:
        sched = self.schedule()
        return sched[sched["season"] == season].copy()


def current_week(schedule: pd.DataFrame, season: int, today: pd.Timestamp | None = None) -> int:
    """The week whose games are next to be played (or in progress).

    A week stays "current" until its last game kicks off, so a Sunday-morning run
    still plans the week in front of you rather than skipping ahead.
    """
    today = pd.Timestamp.now().normalize() if today is None else pd.Timestamp(today).normalize()
    games = schedule[schedule["season"] == season].copy()
    if games.empty:
        return 1
    games["gameday"] = pd.to_datetime(games["gameday"], errors="coerce")
    last_day = games.groupby("week")["gameday"].max().sort_index()
    upcoming = last_day[last_day >= today]
    if upcoming.empty:
        return int(last_day.index.max())
    return int(upcoming.index.min())
