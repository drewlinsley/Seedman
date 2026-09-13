"""The season planner: which player to burn in which week.

Formulated as a mixed-integer program over the *whole remaining season* rather
than one week at a time, because in a one-use-per-player league this week's
lineup is inseparable from the rest of the schedule.  Starting Josh Allen in
week 3 is not just "+18 points this week", it is "-18 points from the best week
he would otherwise have covered".  Only a multi-week model prices that.

    variables   x[p, w, s] in {0,1}   player p starts in slot s of week w
    subject to  sum_p x[p, w, s] == 1           every slot filled, every week
                sum_{w,s} x[p, w, s] <= L       each player usable L times
                x[p, w, s] == 0                 unless p can fill s and plays in w
    maximise    sum_w  omega_w * sum_{p,s} x[p, w, s] * mu[p, w]

The `omega_w` are the survival weights from `survival.py`: the marginal value of
a projected point in week `w`.  They depend on the lineup (a week is only "safe"
because of who you put in it), so the solve is iterative -- optimise, recompute
how safe each week now looks, reweight, repeat.  Since ``log Phi`` is concave in
the weekly mean, this is a standard concave-maximisation-by-linearisation loop
and it settles in a handful of passes.

Flat weights (``omega_w == 1``) reduce this to plain expected-points
maximisation, which is available via `--no-survival` for comparison.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import pulp

from .. import fitted as _fitted
from ..config import LeagueConfig
from ..correlation import lineup_sd
from ..survival import (
    FieldModel,
    cumulative_survival,
    log_survival_probability,
    marginal_point_value,
    survival_probability,
    threshold_moments,
)

log = logging.getLogger(__name__)

# Keeping only the best candidates per position/week bounds the MILP without
# affecting the optimum in practice -- the 45th-best WR in a week is never the
# right answer when 44 better ones are free.
DEFAULT_CANDIDATES_PER_SLOT_WEEK = 45
MAX_ITERATIONS = 12
CONVERGENCE_TOL = 1e-4
WEIGHT_DAMPING = 0.5

# The objective is linear in expected points, so it cannot see variance and will
# happily stack four players from one game for a fraction of a projected point.
# Correlated lineups are penalised in the reported spread, but only a hard cap
# actually stops the solver building them.
MAX_PER_TEAM_DEFAULT = 2
MAX_PER_GAME_DEFAULT = 3

# Share of a lineup's variance that comes from a league-wide weekly shock (a
# low-scoring Sunday hits everybody) rather than from your specific players.
# That common component cancels when you are compared against the field -- it
# moves your score and the cut line together -- so counting it on both sides
# would understate how decisively a good lineup separates from a bad one.
# Measured at 0.036 over 2021-2024: between-week variance is 13.7 against
# within-week variance of 371.5. The hand-set 0.25 was a sevenfold overestimate,
# which made lineups look far more decisively separated from the field than they
# are, and therefore made every survival probability overconfident.
COMMON_VARIANCE_SHARE = _fitted.get().value(
    "common_variance_share", 0.05, label="common_variance_share"
)


@dataclass
class WeekPlan:
    week: int
    picks: list[dict]
    mean: float
    sd: float  # honest total spread, including the league-wide component
    idiosyncratic_sd: float  # the part that actually separates you from the field
    threshold_mean: float
    threshold_sd: float
    survival_probability: float
    # Relative marginal value of one projected point in this week, normalised so
    # the planned weeks average 1. High means "spend here"; low means "coast".
    point_weight: float
    teams_alive: int


@dataclass
class SeasonPlan:
    weeks: list[WeekPlan] = field(default_factory=list)
    cumulative_survival: float = 0.0
    total_points: float = 0.0
    iterations: int = 0
    best_iteration: int = 0
    survival_objective: float = 0.0
    status: str = "unknown"

    def to_frame(self) -> pd.DataFrame:
        rows = []
        for wp in self.weeks:
            for pick in wp.picks:
                rows.append({"week": wp.week, **pick})
        return pd.DataFrame(rows)

    def summary_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "week": wp.week,
                    "projected": round(wp.mean, 2),
                    "sd": round(wp.sd, 2),
                    "cut_line": round(wp.threshold_mean, 2),
                    "survive_pct": round(100 * wp.survival_probability, 1),
                    "teams_alive": wp.teams_alive,
                    "point_weight": round(wp.point_weight, 4),
                }
                for wp in self.weeks
            ]
        )


class SurvivorOptimizer:
    """Plans the remaining season under a survivor league's usage constraint."""

    def __init__(
        self,
        config: LeagueConfig,
        projections: pd.DataFrame,
        *,
        field_model: FieldModel,
        used_players: set[str] | None = None,
        hold_players: set[str] | None = None,
        candidates_per_slot_week: int = DEFAULT_CANDIDATES_PER_SLOT_WEEK,
        use_survival_weights: bool = True,
        max_per_team: int = MAX_PER_TEAM_DEFAULT,
        max_per_game: int = MAX_PER_GAME_DEFAULT,
        common_variance_share: float = COMMON_VARIANCE_SHARE,
        seed: int = 0,
    ) -> None:
        self.config = config
        self.field = field_model
        self.used = used_players or set()
        # Players barred from the *first* planned week only, and still free in
        # every later one. Distinct from `used`, which is permanent. This is how
        # you act on something the model cannot see -- a report you do not trust,
        # a player you want to hold on principle -- without lying to it about
        # his availability for the rest of the season.
        self.hold = hold_players or set()
        self.candidates_per_slot_week = candidates_per_slot_week
        self.use_survival_weights = use_survival_weights
        self.max_per_team = max_per_team
        self.max_per_game = max_per_game
        self.common_variance_share = float(np.clip(common_variance_share, 0.0, 0.9))
        self.seed = seed

        self._common_variance: dict[int, float] = {}
        self.projections = self._prune(projections)
        self.weeks = sorted(self.projections["week"].unique().tolist())

    # ------------------------------------------------------------------
    def _prune(self, projections: pd.DataFrame) -> pd.DataFrame:
        """Drop already-burned players and keep the top candidates per position."""
        if projections.empty:
            return projections

        frame = projections[~projections["player_id"].isin(self.used)].copy()
        frame = frame[frame["position"].isin(self.config.positions_used)]
        # A player with no realistic chance of appearing is not a candidate.
        frame = frame[frame["availability"] > 0.01]

        frame = (
            frame.sort_values("mean", ascending=False)
            .groupby(["week", "position"], group_keys=False)
            .head(self.candidates_per_slot_week)
            .reset_index(drop=True)
        )
        # Both sides of one matchup share a game key, so "at most N from this
        # game" is a single constraint over the pair of teams.
        frame["game_key"] = [
            "-".join(sorted([str(t), str(o)]))
            for t, o in zip(frame["team"], frame["opponent"])
        ]
        return frame

    # ------------------------------------------------------------------
    def _threshold_for_week(self, week: int) -> tuple[float, float, int]:
        alive = self.config.survival.teams_alive_at(week)
        opponents = max(1, alive - 1)
        weeks_ahead = max(0, week - self.config.survival.as_of_week)
        mean, sd = self.field.moments_at(week, weeks_ahead)
        # Only idiosyncratic variance separates one team from another; the
        # league-wide component shifts every score together, us included.
        idiosyncratic = sd * np.sqrt(1.0 - self.common_variance_share)
        thr_mean, thr_sd = threshold_moments(
            opponents, mean, idiosyncratic, seed=self.seed + week
        )
        self._common_variance[week] = (sd**2) * self.common_variance_share
        return thr_mean, thr_sd, alive

    # ------------------------------------------------------------------
    def solve(self) -> SeasonPlan:
        if self.projections.empty:
            return SeasonPlan(status="no-projections")

        thresholds = {w: self._threshold_for_week(w) for w in self.weeks}
        weights = {w: 1.0 for w in self.weeks}

        # Maximising a linearisation of a concave objective over an integer
        # feasible set lands on a vertex, which overshoots the interior optimum
        # and can oscillate between two lineups forever. Fractional steps are
        # not available -- half a quarterback is not a lineup -- so instead we
        # score every iterate against the true objective and keep the best one.
        # Iteration 1 uses flat weights, so the result is never worse than plain
        # expected-points maximisation.
        best_plan: SeasonPlan | None = None
        best_objective = -np.inf
        seen_assignments: set[tuple] = set()

        for iteration in range(1, MAX_ITERATIONS + 1):
            assignment, status = self._solve_milp(weights)
            if assignment is None:
                return best_plan or SeasonPlan(status=status)

            plan = self._build_plan(assignment, thresholds, weights)
            plan.iterations = iteration
            plan.status = status

            objective = sum(
                log_survival_probability(
                    wp.mean, wp.idiosyncratic_sd, wp.threshold_mean, wp.threshold_sd
                )
                for wp in plan.weeks
            )
            if objective > best_objective:
                best_objective = objective
                best_plan = plan
                best_plan.best_iteration = iteration

            if not self.use_survival_weights:
                break

            # Revisiting an assignment means we are cycling between vertices;
            # further iterations cannot find anything new.
            signature = tuple(
                sorted(
                    (int(r.week), str(r.slot), int(r.index))
                    for r in assignment.itertuples(index=False)
                )
            )
            if signature in seen_assignments:
                log.debug("assignment cycle detected at iteration %d", iteration)
                break
            seen_assignments.add(signature)

            new_weights = _normalise(
                {
                    wp.week: marginal_point_value(
                        wp.mean, wp.idiosyncratic_sd, wp.threshold_mean, wp.threshold_sd
                    )
                    for wp in plan.weeks
                }
            )
            # Damped update, which slows the oscillation even though the
            # best-of-iterates rule above is what actually guarantees quality.
            blended = {
                w: (1 - WEIGHT_DAMPING) * weights[w] + WEIGHT_DAMPING * new_weights[w]
                for w in weights
            }
            shift = max(abs(blended[w] - weights[w]) for w in weights)
            weights = blended
            if shift < CONVERGENCE_TOL:
                log.debug("weights converged after %d iterations", iteration)
                break

        assert best_plan is not None  # the loop runs at least once
        best_plan.iterations = iteration
        best_plan.survival_objective = best_objective
        return best_plan

    # ------------------------------------------------------------------
    def _solve_milp(self, weights: dict[int, float]) -> tuple[pd.DataFrame | None, str]:
        problem = pulp.LpProblem("survivor_season_plan", pulp.LpMaximize)
        frame = self.projections

        # Build decision variables only for legal (player, week, slot) triples.
        variables: dict[tuple[int, str, int], pulp.LpVariable] = {}
        by_week_slot: dict[tuple[int, str], list[int]] = {}
        by_player: dict[str, list[int]] = {}

        for row in frame.itertuples():
            for slot in self.config.slots:
                if not slot.accepts(row.position):
                    continue
                key = (row.week, slot.name, row.Index)
                variables[key] = pulp.LpVariable(
                    f"x_{row.week}_{slot.name}_{row.Index}", cat="Binary"
                )
                by_week_slot.setdefault((row.week, slot.name), []).append(row.Index)
                by_player.setdefault(row.player_id, []).append(row.Index)

        if not variables:
            return None, "no-eligible-assignments"

        means = frame["mean"].to_dict()
        index_to_week = frame["week"].to_dict()

        problem += pulp.lpSum(
            variables[(week, slot_name, idx)]
            * means[idx]
            * weights.get(week, 1.0)
            for (week, slot_name, idx) in variables
        )

        # Every slot filled exactly once each week.
        for (week, slot_name), indices in by_week_slot.items():
            problem += (
                pulp.lpSum(variables[(week, slot_name, idx)] for idx in indices) == 1,
                f"fill_{week}_{slot_name}",
            )

        # Usage cap: the constraint that makes this a survivor league.
        limit = self.config.survival.player_reuse_limit
        player_of_index = frame["player_id"].to_dict()
        per_player: dict[str, list[pulp.LpVariable]] = {}
        for (week, slot_name, idx), var in variables.items():
            per_player.setdefault(player_of_index[idx], []).append(var)
        for player_id, vars_ in per_player.items():
            if len(vars_) > limit:
                problem += (pulp.lpSum(vars_) <= limit, f"usage_{_sanitise(player_id)}")

        # A player may only occupy one slot in any single week.
        per_player_week: dict[tuple[str, int], list[pulp.LpVariable]] = {}
        for (week, slot_name, idx), var in variables.items():
            per_player_week.setdefault((player_of_index[idx], week), []).append(var)
        for (player_id, week), vars_ in per_player_week.items():
            if len(vars_) > 1:
                problem += (
                    pulp.lpSum(vars_) <= 1,
                    f"oneslot_{_sanitise(player_id)}_{week}",
                )

        # Manual holds: barred this week, free thereafter.
        if self.hold and self.weeks:
            first = self.weeks[0]
            held: dict[str, list[pulp.LpVariable]] = {}
            for (week, _slot, idx), var in variables.items():
                if week == first and player_of_index[idx] in self.hold:
                    held.setdefault(player_of_index[idx], []).append(var)
            for player_id, vars_ in held.items():
                problem += (
                    pulp.lpSum(vars_) == 0,
                    f"hold_{_sanitise(player_id)}",
                )

        # Diversification. The objective is blind to variance, so without these
        # the solver will stack one game whenever it is worth a tenth of a point.
        self._add_grouping_caps(
            problem, variables, frame["team"].to_dict(), self.max_per_team, "team"
        )
        self._add_grouping_caps(
            problem, variables, frame["game_key"].to_dict(), self.max_per_game, "game"
        )

        status_code = problem.solve(pulp.PULP_CBC_CMD(msg=0))
        status = pulp.LpStatus[status_code]
        if status != "Optimal":
            return None, status

        chosen = [
            {"week": week, "slot": slot_name, "index": idx}
            for (week, slot_name, idx), var in variables.items()
            if var.value() is not None and var.value() > 0.5
        ]
        del index_to_week
        return pd.DataFrame(chosen), status

    # ------------------------------------------------------------------
    @staticmethod
    def _add_grouping_caps(
        problem: pulp.LpProblem,
        variables: dict[tuple[int, str, int], pulp.LpVariable],
        group_of_index: dict[int, str],
        cap: int,
        label: str,
    ) -> None:
        """Limit how many starters may come from one team (or one game) per week."""
        if cap <= 0:
            return
        buckets: dict[tuple[int, str], list[pulp.LpVariable]] = {}
        for (week, _slot, idx), var in variables.items():
            buckets.setdefault((week, str(group_of_index[idx])), []).append(var)
        for (week, group), vars_ in buckets.items():
            if len(vars_) > cap:
                problem += (
                    pulp.lpSum(vars_) <= cap,
                    f"{label}cap_{week}_{_sanitise(group)}",
                )

    # ------------------------------------------------------------------
    def _build_plan(
        self,
        assignment: pd.DataFrame,
        thresholds: dict[int, tuple[float, float, int]],
        weights: dict[int, float],
    ) -> SeasonPlan:
        frame = self.projections
        plan = SeasonPlan()
        survival_probs: list[float] = []

        for week in self.weeks:
            picks_idx = assignment[assignment["week"] == week]
            if picks_idx.empty:
                continue

            rows = frame.loc[picks_idx["index"].tolist()]
            slots = picks_idx.set_index("index")["slot"].to_dict()

            mean = float(rows["mean"].sum())
            # Full covariance, not a sum of squares: a stacked lineup really is
            # riskier and the survival weights need to know it.
            sd = lineup_sd(rows)

            thr_mean, thr_sd, alive = thresholds[week]
            # Drop the league-wide shock from our spread too, for the same
            # reason it was dropped from the cut line: it cancels.
            common = self._common_variance.get(week, 0.0)
            own_idiosyncratic = float(np.sqrt(max(sd**2 - common, 1.0)))
            prob = survival_probability(mean, own_idiosyncratic, thr_mean, thr_sd)
            survival_probs.append(prob)

            picks = []
            for idx, row in rows.iterrows():
                picks.append(
                    {
                        "slot": slots[idx],
                        "player_id": row["player_id"],
                        "name": row["name"],
                        "position": row["position"],
                        "team": row["team"],
                        "opponent": row["opponent"],
                        "projected": round(float(row["mean"]), 2),
                        "sd": round(float(row["sd"]), 2),
                        "availability": round(float(row["availability"]), 3),
                        "status": row["report_status"],
                    }
                )
            picks.sort(key=lambda p: [s.name for s in self.config.slots].index(p["slot"]))

            plan.weeks.append(
                WeekPlan(
                    week=week,
                    picks=picks,
                    mean=mean,
                    sd=sd,
                    idiosyncratic_sd=own_idiosyncratic,
                    threshold_mean=thr_mean,
                    threshold_sd=thr_sd,
                    survival_probability=prob,
                    point_weight=weights.get(week, 1.0),
                    teams_alive=alive,
                )
            )

        # Report what a projected point is *actually* worth in each week under
        # the finished plan, rather than the weight that happened to generate
        # it -- the latter is an artefact of whichever iteration won. Normalised
        # to mean 1 so it reads as "a point here is worth 1.8x a point there".
        realised = _normalise(
            {
                wp.week: marginal_point_value(
                    wp.mean, wp.idiosyncratic_sd, wp.threshold_mean, wp.threshold_sd
                )
                for wp in plan.weeks
            }
        )
        for wp in plan.weeks:
            wp.point_weight = realised[wp.week]

        plan.cumulative_survival = cumulative_survival(survival_probs)
        plan.total_points = sum(wp.mean for wp in plan.weeks)
        return plan


def _normalise(weights: dict[int, float]) -> dict[int, float]:
    """Rescale to mean 1 so the MILP objective keeps a stable magnitude."""
    values = np.array(list(weights.values()), dtype=float)
    values = np.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0)
    total = values.mean()
    if total <= 0:
        return {w: 1.0 for w in weights}
    return {w: float(v / total) for w, v in zip(weights, values)}


def _sanitise(name: str) -> str:
    """PuLP constraint names cannot contain characters CBC treats specially."""
    return "".join(ch if ch.isalnum() else "_" for ch in str(name))
