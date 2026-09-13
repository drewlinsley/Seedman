"""Load the data-fitted constants, falling back to hand-set priors.

`configs/fitted.yaml` is produced by `seedman calibrate` from historical
seasons. This module is the single place that decides, per constant, whether
the fitted value is trustworthy enough to use:

* a bucket with fewer than `min_sample_for_use` observations keeps its prior --
  "Doubtful, did not practise" has a handful of cases a year and a point
  estimate off three of them is worse than a considered guess;
* an absent file means every prior stands, so the package works before anyone
  has run a calibration.

Every value that ends up in use is tagged with its provenance so reports can
say which numbers are measured and which are asserted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

DEFAULT_FITTED_PATH = Path(__file__).resolve().parent.parent / "configs" / "fitted.yaml"
DEFAULT_MIN_SAMPLE = 60


@dataclass
class FittedConstants:
    """Fitted values plus a record of what was actually adopted."""

    raw: dict[str, Any] = field(default_factory=dict)
    min_sample: int = DEFAULT_MIN_SAMPLE
    adopted: dict[str, str] = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return bool(self.raw)

    @property
    def fitted_on(self) -> list[int]:
        return list(self.raw.get("fitted_on", []))

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, path: Path | str | None = None) -> FittedConstants:
        target = Path(path) if path else DEFAULT_FITTED_PATH
        if not target.exists():
            log.info("no fitted constants at %s; using priors", target)
            return cls()
        with target.open() as fh:
            raw = yaml.safe_load(fh) or {}
        return cls(raw=raw, min_sample=int(raw.get("min_sample_for_use", DEFAULT_MIN_SAMPLE)))

    # ------------------------------------------------------------------
    def value(self, path: str, prior: float, *, label: str | None = None) -> float:
        """Fitted value at a dotted path, or `prior` if absent or under-sampled."""
        node: Any = self.raw
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                self._record(label or path, prior, "prior (not fitted)")
                return prior
            node = node[part]

        if not isinstance(node, dict) or "value" not in node:
            self._record(label or path, prior, "prior (malformed)")
            return prior

        n = int(node.get("n", 0))
        if n < self.min_sample:
            self._record(label or path, prior, f"prior (only n={n})")
            return prior

        fitted = float(node["value"])
        self._record(label or path, fitted, f"fitted (n={n:,})")
        return fitted

    def _record(self, label: str, value: float, source: str) -> None:
        self.adopted[label] = f"{value:.4g}  [{source}]"

    def provenance(self) -> str:
        if not self.adopted:
            return "no constants resolved yet"
        lines = [f"  {label:<52} {detail}" for label, detail in sorted(self.adopted.items())]
        header = (
            f"fitted on seasons {self.fitted_on}" if self.available else "no fitted file loaded"
        )
        return f"{header}\n" + "\n".join(lines)


_CACHE: FittedConstants | None = None


def get(path: Path | str | None = None) -> FittedConstants:
    """Process-wide singleton, so the YAML is read once."""
    global _CACHE
    if _CACHE is None or path is not None:
        _CACHE = FittedConstants.load(path)
    return _CACHE


def reset() -> None:
    """Drop the cache (tests that point at a different file need this)."""
    global _CACHE
    _CACHE = None
