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

import itertools
import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import pulp
from scipy.stats import norm

from .. import fitted as _fitted
from ..config import LeagueConfig
from ..correlation import lineup_sd
from ..survival import (
    berth_sensitivity,
    playoff_cut_wins,
    playoff_opponent_moments,
    playoff_probability,
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
# Local-search budget for the post-solve polish. Each round is O(slots * weeks^2)
# objective evaluations, which is cheap next to a MILP re-solve.
POLISH_ROUNDS = 6
POLISH_TOL = 1e-9

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
    # "regular" or "PLAYOFF" -- which of the two objectives this week serves.
    phase: str = "regular"


@dataclass
class SeasonPlan:
    weeks: list[WeekPlan] = field(default_factory=list)
    cumulative_survival: float = 0.0
    # Head-to-head headline numbers. `cumulative_survival` means "never finish
    # last" in a survivor pool and is meaningless here -- the product of sixteen
    # coin flips is near zero however good the plan is, because you are supposed
    # to lose some.
    playoff_probability: float = 0.0
    title_probability: float = 0.0
    wins_needed: int = 0
    expected_wins: float = 0.0
    head_to_head: bool = False
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
        """One row per planned week, labelled for the format being played.

        The two modes are asking different questions, so they get different
        columns: how many teams are left and whether you survived, against who
        you face and whether the week is a bracket game.
        """
        rows = []
        for wp in self.weeks:
            row = {
                "week": wp.week,
                "projected": round(wp.mean, 2),
                "sd": round(wp.sd, 2),
            }
            if self.head_to_head:
                row["opponent"] = round(wp.threshold_mean, 2)
                row["win_pct"] = round(100 * wp.survival_probability, 1)
                row["phase"] = wp.phase
            else:
                row["cut_line"] = round(wp.threshold_mean, 2)
                row["survive_pct"] = round(100 * wp.survival_probability, 1)
                row["teams_alive"] = wp.teams_alive
            row["point_weight"] = round(wp.point_weight, 4)
            rows.append(row)
        return pd.DataFrame(rows)


class SurvivorOptimizer:
    """Plans the remaining season under the one-start-per-player constraint.

    Named for the survivor format it was written against; it now serves both
    that and head-to-head, which differ in the objective rather than in the
    usage constraint they share.
    """

    def __init__(
        self,
        config: LeagueConfig,
        projections: pd.DataFrame,
        *,
        field_model: FieldModel,
        used_players: set[str] | None = None,
        hold_players: set[str] | None = None,
        start_players: set[str] | None = None,
        candidates_per_slot_week: int = DEFAULT_CANDIDATES_PER_SLOT_WEEK,
        use_survival_weights: bool = True,
        max_per_team: int = MAX_PER_TEAM_DEFAULT,
        max_per_game: int = MAX_PER_GAME_DEFAULT,
        common_variance_share: float = COMMON_VARIANCE_SHARE,
        wins_so_far: int = 0,
        losses_so_far: int = 0,
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
        # Players you insist on starting THIS week. The mirror of `hold`, and the
        # honest way to ask "I want him, what does it cost?" -- the plan reoptimises
        # around the constraint and the objective tells you the price.
        self.start = start_players or set()
        conflict = self.hold & self.start
        if conflict:
            # Silently picking a winner here could start a player you meant to
            # sit, or sit one you meant to start, and you would not find out
            # until the games had kicked off.
            raise ValueError(
                "these players are both held and forced to start: "
                + ", ".join(sorted(conflict))
                + ". Drop one of the two instructions."
            )
        self.candidates_per_slot_week = candidates_per_slot_week
        self.use_survival_weights = use_survival_weights
        self.max_per_team = max_per_team
        self.max_per_game = max_per_game
        self.common_variance_share = float(np.clip(common_variance_share, 0.0, 0.9))
        self.seed = seed

        # Head-to-head only: the record so far, and the record a bracket costs.
        # Both games already played and games still to come count toward the
        # same total, so a 1-0 start genuinely lowers what the rest must deliver.
        self.wins_so_far = int(wins_so_far)
        self.losses_so_far = int(losses_so_far)

        self._common_variance: dict[int, float] = {}
        self.projections = self._prune(projections)
        self.weeks = sorted(self.projections["week"].unique().tolist())

        # Games left to play are the regular-season weeks still in the plan --
        # not a count from `as_of_week`, which double-counts any week already
        # played and would have us chasing a win total that is one too high.
        survival = config.survival
        self.regular_weeks = [w for w in self.weeks if not survival.is_playoff_week(w)]
        self.playoff_weeks = [w for w in self.weeks if survival.is_playoff_week(w)]
        total_games = self.wins_so_far + self.losses_so_far + len(self.regular_weeks)
        self.wins_needed = playoff_cut_wins(
            total_games, survival.playoff_berths, survival.teams_remaining
        )

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
        """The score you actually have to beat in `week`.

        In a survivor league that is the lowest of everyone still alive, which
        is a far softer bar than the average team. Head-to-head it is one
        specific opponent -- a much harder bar, and harder again in the bracket,
        where the opponent is there because they are good.
        """
        survival = self.config.survival
        alive = survival.teams_alive_at(week)
        opponents = survival.opponents_at(week)
        weeks_ahead = max(0, week - survival.as_of_week)
        mean, sd = self.field.moments_at(week, weeks_ahead)

        if survival.is_playoff_week(week):
            mean, sd = playoff_opponent_moments(
                mean, sd, survival.playoff_berths, survival.teams_remaining
            )

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
        best_assignments: dict[int, pd.DataFrame] = {}

        for iteration in range(1, MAX_ITERATIONS + 1):
            assignment, status = self._solve_milp(weights)
            if assignment is None:
                return best_plan or SeasonPlan(status=status)

            plan = self._build_plan(assignment, thresholds, weights)
            plan.iterations = iteration
            plan.status = status

            objective = self._objective(plan)
            if objective > best_objective:
                best_objective = objective
                best_plan = plan
                best_plan.best_iteration = iteration
                best_assignments[iteration] = assignment.copy()

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

            new_weights = _normalise(self._point_weights(plan))
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
        if self.use_survival_weights:
            best_assignment = best_assignments[best_plan.best_iteration]
            best_plan = self._polish(best_assignment, thresholds, weights, best_plan)
        best_plan.iterations = iteration
        best_plan.survival_objective = self._objective(best_plan)
        self._annotate(best_plan)
        return best_plan

    # ------------------------------------------------------------------
    def _polish(
        self,
        assignment: pd.DataFrame,
        thresholds: dict[int, tuple[float, float, int]],
        weights: dict[int, float],
        plan: SeasonPlan,
    ) -> SeasonPlan:
        """Hill-climb the real objective by swapping one player between weeks.

        Reweighting linearises a concave objective and then maximises the
        linearisation over integer points, which lands on a vertex rather than
        the interior optimum. In practice it overshoots: the weeks that looked
        most valuable on the first pass get loaded until a point there is worth
        far *less* than a point in the weeks it was taken from. On a flat test
        board the marginal values ended up 8x apart, all in the direction of
        hoarding for the bracket.

        Best-of-iterates keeps that from being catastrophic but cannot repair
        it, because every iterate has the same bias. So finish with an exact
        local search on the true objective: swap the occupants of one slot
        between two weeks, keep the move if the objective genuinely improves.
        Each candidate move is evaluated on the objective itself, never on a
        linearisation, so this can only help.
        """
        frame = self.projections
        # (week, slot) -> projections row index currently assigned there.
        current = {
            (int(r.week), str(r.slot)): int(r.index)
            for r in assignment.itertuples(index=False)
        }
        # Which row is player p in week w, if he is a candidate there at all.
        row_of = {
            (str(r.player_id), int(r.week)): int(r.Index) for r in frame.itertuples()
        }
        player_of = frame["player_id"].astype(str).to_dict()

        # Cache one WeekPlan per week. A swap touches exactly two weeks, so
        # re-pricing those two is all the work a candidate move needs -- the
        # objective itself is then arithmetic over the cached numbers.
        slots_by_week: dict[int, dict[int, str]] = {}
        for (week, slot), idx in current.items():
            slots_by_week.setdefault(week, {})[idx] = slot
        cache = {
            week: self._week_plan(week, slots, thresholds, weights)
            for week, slots in slots_by_week.items()
        }

        def assemble(overrides: dict[int, WeekPlan] | None = None) -> SeasonPlan:
            merged = dict(cache)
            merged.update(overrides or {})
            trial_plan = SeasonPlan(weeks=[merged[w] for w in sorted(merged)])
            trial_plan.status = plan.status
            trial_plan.best_iteration = plan.best_iteration
            return trial_plan

        best = self._objective(assemble())
        improved = True
        rounds = 0
        slot_names = [s.name for s in self.config.slots]
        while improved and rounds < POLISH_ROUNDS:
            improved = False
            rounds += 1
            for slot in slot_names:
                for week_a, week_b in itertools.combinations(sorted(cache), 2):
                    key_a, key_b = (week_a, slot), (week_b, slot)
                    if key_a not in current or key_b not in current:
                        continue
                    idx_a, idx_b = current[key_a], current[key_b]
                    # The same two players, projected in each other's week.
                    moved_a = row_of.get((player_of[idx_a], week_b))
                    moved_b = row_of.get((player_of[idx_b], week_a))
                    if moved_a is None or moved_b is None:
                        continue

                    trial = dict(current)
                    trial[key_a], trial[key_b] = moved_b, moved_a
                    if not self._respects_caps(trial):
                        continue
                    if self.start and not self._respects_forced_starts(trial):
                        continue

                    rebuilt = {}
                    for week in (week_a, week_b):
                        slots = {
                            idx: s
                            for (w, s), idx in trial.items()
                            if w == week
                        }
                        rebuilt[week] = self._week_plan(
                            week, slots, thresholds, weights
                        )
                    candidate = assemble(rebuilt)
                    score = self._objective(candidate)
                    if score > best + POLISH_TOL:
                        best, current, plan = score, trial, candidate
                        cache.update(rebuilt)
                        improved = True
        if rounds > 1:
            log.debug("polish improved the objective over %d rounds", rounds - 1)
        return self._finalise(plan)

    def _respects_forced_starts(self, mapping: dict[tuple[int, str], int]) -> bool:
        """A forced start must survive the polish; it is a constraint, not a hint."""
        if not self.weeks:
            return True
        first = self.weeks[0]
        present = {
            str(self.projections.at[idx, "player_id"])
            for (week, _slot), idx in mapping.items()
            if week == first
        }
        return self.start <= present

    def _respects_caps(self, mapping: dict[tuple[int, str], int]) -> bool:
        """Re-check the per-team and per-game limits after a swap."""
        frame = self.projections
        per_week_team: dict[tuple[int, str], int] = {}
        per_week_game: dict[tuple[int, str], int] = {}
        for (week, _slot), idx in mapping.items():
            row = frame.loc[idx]
            tk = (week, str(row["team"]))
            gk = (week, str(row["game_key"]))
            per_week_team[tk] = per_week_team.get(tk, 0) + 1
            per_week_game[gk] = per_week_game.get(gk, 0) + 1
            if per_week_team[tk] > self.max_per_team:
                return False
            if per_week_game[gk] > self.max_per_game:
                return False
        return True


    # ------------------------------------------------------------------
    def _annotate(self, plan: SeasonPlan) -> None:
        """Attach the numbers a head-to-head manager actually reads.

        `cumulative_survival` is the survivor headline and is actively
        misleading here: the product of sixteen near-coin-flips rounds to zero
        no matter how good the plan is, because losing some regular-season games
        is not merely survivable, it is expected.
        """
        if not self.config.survival.is_head_to_head:
            return
        plan.head_to_head = True
        win_probs, _ = self._win_probabilities(plan)
        plan.wins_needed = self.wins_needed
        plan.expected_wins = self.wins_so_far + float(sum(win_probs))
        plan.playoff_probability = playoff_probability(
            win_probs, self.wins_so_far, self.wins_needed
        )
        bracket = 1.0
        for wp in plan.weeks:
            if self.config.survival.is_playoff_week(wp.week):
                bracket *= wp.survival_probability
        plan.title_probability = plan.playoff_probability * bracket

    # ------------------------------------------------------------------
    def _win_probabilities(self, plan: SeasonPlan) -> tuple[list[float], list[WeekPlan]]:
        """Per-week P(beat this week's opponent), split regular season / bracket."""
        regular = [
            wp for wp in plan.weeks if not self.config.survival.is_playoff_week(wp.week)
        ]
        return [wp.survival_probability for wp in regular], regular

    def _objective(self, plan: SeasonPlan) -> float:
        """What the plan is actually worth, in logs so the solver can sum it.

        Survivor: you must not finish last in *any* week, so it is the product
        of every week's survival probability.

        Head-to-head: P(title) = P(make the bracket) * P(win each playoff week).
        The regular-season weeks enter only through the first factor, which is
        why a 40-point win there is worth exactly as much as a 1-point win and
        no more -- the thing a survivor objective cannot express, and the reason
        it wanted to spend studs on games that were already won.
        """
        survival = self.config.survival
        if not survival.is_head_to_head:
            return sum(
                log_survival_probability(
                    wp.mean, wp.idiosyncratic_sd, wp.threshold_mean, wp.threshold_sd
                )
                for wp in plan.weeks
            )

        win_probs, _ = self._win_probabilities(plan)
        berth = playoff_probability(win_probs, self.wins_so_far, self.wins_needed)
        total = np.log(max(berth, 1e-12))
        for wp in plan.weeks:
            if survival.is_playoff_week(wp.week):
                total += log_survival_probability(
                    wp.mean, wp.idiosyncratic_sd, wp.threshold_mean, wp.threshold_sd
                )
        return float(total)

    def _point_weights(self, plan: SeasonPlan) -> dict[int, float]:
        """Marginal value of one projected point, week by week.

        Head-to-head splits into two regimes. A playoff week keeps the survivor
        shape -- lose and you are done -- so a point is worth
        ``phi(z)/Phi(z)``. A regular-season week is worth only what it does to
        the odds of qualifying: ``dP(berth)/dp_w * phi(z)``, which is largest
        when the season is on the bubble and falls away once the berth is close
        to settled either way.
        """
        survival = self.config.survival
        if not survival.is_head_to_head:
            return {
                wp.week: marginal_point_value(
                    wp.mean, wp.idiosyncratic_sd, wp.threshold_mean, wp.threshold_sd
                )
                for wp in plan.weeks
            }

        win_probs, regular = self._win_probabilities(plan)
        berth = max(
            playoff_probability(win_probs, self.wins_so_far, self.wins_needed), 1e-12
        )
        sensitivity = berth_sensitivity(win_probs, self.wins_so_far, self.wins_needed)

        weights: dict[int, float] = {}
        for wp in plan.weeks:
            denom = float(np.hypot(wp.idiosyncratic_sd, wp.threshold_sd))
            if denom <= 0:
                weights[wp.week] = 0.0
                continue
            if survival.is_playoff_week(wp.week):
                weights[wp.week] = marginal_point_value(
                    wp.mean, wp.idiosyncratic_sd, wp.threshold_mean, wp.threshold_sd
                )
            else:
                index = regular.index(wp)
                z = (wp.mean - wp.threshold_mean) / denom
                dp_dmu = float(norm.pdf(z)) / denom
                weights[wp.week] = sensitivity[index] * dp_dmu / berth
        return weights

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

        # Forced starts: this player occupies one of this week's slots.
        if self.start and self.weeks:
            first = self.weeks[0]
            for player_id in self.start:
                chosen = [
                    variables[key]
                    for key in variables
                    if key[0] == first and str(frame.at[key[2], "player_id"]) == player_id
                ]
                if chosen:
                    problem += (
                        pulp.lpSum(chosen) == 1,
                        f"force_{_sanitise(player_id)}",
                    )

        # Usage cap: the one-start-per-season rule, and the only reason any of
        # this is harder than starting your best player every week.
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
    def _week_plan(
        self,
        week: int,
        slots: dict[int, str],
        thresholds: dict[int, tuple[float, float, int]],
        weights: dict[int, float],
    ) -> WeekPlan:
        """Score one week's lineup. Pulled out of `_build_plan` so that the
        polish can re-price the two weeks a swap touches instead of all sixteen,
        which is the difference between a local search that runs in seconds and
        one that runs in minutes."""
        frame = self.projections
        rows = frame.loc[list(slots)]

        mean = float(rows["mean"].sum())
        # Full covariance, not a sum of squares: a stacked lineup really is
        # riskier and the weights need to know it.
        sd = lineup_sd(rows)

        thr_mean, thr_sd, alive = thresholds[week]
        # Drop the league-wide shock from our spread too, for the same reason it
        # was dropped from the threshold: it cancels.
        common = self._common_variance.get(week, 0.0)
        own_idiosyncratic = float(np.sqrt(max(sd**2 - common, 1.0)))
        prob = survival_probability(mean, own_idiosyncratic, thr_mean, thr_sd)

        order = [s.name for s in self.config.slots]
        picks = [
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
            for idx, row in rows.iterrows()
        ]
        picks.sort(key=lambda p: order.index(p["slot"]))

        return WeekPlan(
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
            phase=(
                "PLAYOFF" if self.config.survival.is_playoff_week(week) else "regular"
            ),
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

        for week in self.weeks:
            picks_idx = assignment[assignment["week"] == week]
            if picks_idx.empty:
                continue
            week_plan = self._week_plan(
                week,
                picks_idx.set_index("index")["slot"].to_dict(),
                thresholds,
                weights,
            )
            plan.weeks.append(week_plan)

        return self._finalise(plan)

    # ------------------------------------------------------------------
    def _finalise(self, plan: SeasonPlan) -> SeasonPlan:
        """Fill in the whole-plan totals and the realised per-week weights.

        Shared by the MILP path and the polish, so a polished plan is never
        returned with the aggregates left at zero -- which it was, silently,
        until a test that reads `cumulative_survival` caught it.
        """
        # Report what a projected point is *actually* worth in each week under
        # the finished plan, rather than the weight that happened to generate
        # it -- the latter is an artefact of whichever iteration won. Normalised
        # to mean 1 so it reads as "a point here is worth 1.8x a point there".
        realised = _normalise(self._point_weights(plan))
        for wp in plan.weeks:
            wp.point_weight = realised[wp.week]

        plan.cumulative_survival = cumulative_survival(
            [wp.survival_probability for wp in plan.weeks]
        )
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
