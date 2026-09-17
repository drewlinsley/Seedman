"""Command line interface."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

from . import pipeline
from .config import ConfigError, LeagueConfig, default_config_path
from .data import NflverseClient
from .league.base import LeagueState
from .league.manual import STARTER_TEMPLATE, ManualAdapter, UnresolvedPlayers

DEFAULT_STATE_FILE = Path("league-state.yaml")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    # pandas' default frame width truncates lineups in narrow terminals.
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 40)

    try:
        return args.handler(args)
    except (ConfigError, UnresolvedPlayers, FileNotFoundError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


# ----------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="seedman",
        description="Survivor fantasy football optimizer.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="league YAML (default: configs/league.yaml or $SEEDMAN_LEAGUE_CONFIG)",
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=DEFAULT_STATE_FILE,
        help=f"league state YAML (default: {DEFAULT_STATE_FILE})",
    )
    parser.add_argument("--cache", type=Path, default=Path(".cache/nflverse"))
    sub = parser.add_subparsers(dest="command", required=True)

    p_sync = sub.add_parser("sync", help="download/refresh the nflverse data cache")
    p_sync.set_defaults(handler=cmd_sync)

    p_proj = sub.add_parser("project", help="show projections for one week")
    p_proj.add_argument("--week", type=int, default=None)
    p_proj.add_argument("--position", default=None)
    p_proj.add_argument("--top", type=int, default=20)
    p_proj.add_argument("--as-of", default="now",
                        help="drop players whose game has kicked off")
    p_proj.set_defaults(handler=cmd_project)

    p_opt = sub.add_parser("optimize", help="plan the rest of the season")
    p_opt.add_argument("--week", type=int, default=None, help="override current week")
    p_opt.add_argument(
        "--horizon",
        type=int,
        default=None,
        help="weeks to plan ahead (default: through the week the league is down to 2 teams)",
    )
    p_opt.add_argument(
        "--max-per-team", type=int, default=2, help="cap starters from one NFL team"
    )
    p_opt.add_argument(
        "--max-per-game", type=int, default=3, help="cap starters from one game"
    )
    p_opt.add_argument(
        "--no-survival",
        action="store_true",
        help="maximise raw expected points instead of survival odds (for comparison)",
    )
    p_opt.add_argument("--candidates", type=int, default=45)
    p_opt.add_argument("--offline", action="store_true")
    p_opt.add_argument("--csv", type=Path, default=None, help="write the plan to CSV")
    p_opt.add_argument(
        "--hold",
        nargs="+",
        default=None,
        metavar="PLAYER",
        help="bar these players from this week only; they stay free for later weeks",
    )
    p_opt.add_argument(
        "--as-of",
        default="now",
        help="drop players whose game has kicked off (ISO timestamp, or 'now', or 'off')",
    )
    p_opt.add_argument(
        "--earliest-kickoff",
        default=None,
        metavar="TIME",
        help=(
            "bar players whose game starts before TIME, this week only "
            "(e.g. 'sunday' to skip Thursday night, or an ISO timestamp). "
            "Starting a player locks the roster at his kickoff, so a Thursday "
            "starter costs three days of injury news on the other five."
        ),
    )
    p_opt.set_defaults(handler=cmd_optimize)

    p_cal = sub.add_parser("calibrate", help="fit model constants from historical seasons")
    p_cal.add_argument("--fit", type=int, nargs="+", default=[2021, 2022, 2023, 2024],
                       help="seasons to fit on (hold the newest out)")
    p_cal.add_argument("--validate", type=int, default=None,
                       help="season to report but never optimise against")
    p_cal.add_argument("--tune-rates", action="store_true",
                       help="also grid-search the shrinkage hyperparameters (slow)")
    p_cal.add_argument("--no-hazard", action="store_true",
                       help="skip fitting the availability hazard model")
    p_cal.add_argument("--out", type=Path, default=Path("configs/fitted.yaml"))
    p_cal.set_defaults(handler=cmd_calibrate)

    p_bt = sub.add_parser("backtest", help="replay seasons and score the projections")
    p_bt.add_argument("--seasons", type=int, nargs="+", default=[2023, 2024, 2025])
    p_bt.add_argument("--simulate", action="store_true",
                      help="also simulate full survivor seasons (slow)")
    p_bt.add_argument("--replications", type=int, default=300)
    p_bt.add_argument("--teams", type=int, default=12)
    p_bt.set_defaults(handler=cmd_backtest)

    p_league = sub.add_parser("league", help="league state helpers")
    league_sub = p_league.add_subparsers(dest="league_command", required=True)

    p_init = league_sub.add_parser("init", help="create a starter league-state file")
    p_init.set_defaults(handler=cmd_league_init)

    p_doctor = league_sub.add_parser("doctor", help="list what still needs verifying")
    p_doctor.set_defaults(handler=cmd_league_doctor)

    p_probe = league_sub.add_parser("probe", help="crawl the league site (run locally)")
    p_probe.add_argument("--out", type=Path, default=Path("probe-output"))
    p_probe.set_defaults(handler=cmd_league_probe)

    return parser


# ----------------------------------------------------------------------
def _load_config(args: argparse.Namespace) -> LeagueConfig:
    path = args.config or default_config_path()
    return LeagueConfig.from_yaml(path)


def _load_state(args: argparse.Namespace, config: LeagueConfig) -> LeagueState:
    """League state from file if present, else sensible defaults from config."""
    client = NflverseClient(cache_dir=args.cache)
    week_override = getattr(args, "week", None)

    if args.state.exists():
        roster = client.rosters(config.season)
        state = ManualAdapter(path=args.state, roster=roster).fetch_state()
    else:
        state = LeagueState(
            current_week=config.survival.as_of_week,
            teams_remaining=config.survival.teams_remaining,
            source="config-defaults",
        )
        print(
            f"note: {args.state} not found; using config defaults. "
            "Run `seedman league init` to track which players you have burned.\n"
        )

    if week_override:
        state.current_week = week_override
    return state


def _sync_config_to_state(config: LeagueConfig, state: LeagueState) -> LeagueConfig:
    """League state is the live source of truth; fold it into the config."""
    from dataclasses import replace

    survival = replace(
        config.survival,
        teams_remaining=state.teams_remaining,
        as_of_week=state.current_week,
    )
    return replace(config, survival=survival)


# ----------------------------------------------------------------------
def cmd_sync(args: argparse.Namespace) -> int:
    config = _load_config(args)
    client = NflverseClient(cache_dir=args.cache)
    datasets = {
        "schedule + betting lines": lambda: client.schedule(),
        f"weekly stats {config.season}": lambda: client.weekly_stats(config.season),
        f"weekly stats {config.season - 1}": lambda: client.weekly_stats(config.season - 1),
        f"injuries {config.season}": lambda: client.injuries(config.season),
        f"rosters {config.season}": lambda: client.rosters(config.season),
        f"snap counts {config.season}": lambda: client.snap_counts(config.season),
    }
    for label, fetch in datasets.items():
        frame = fetch()
        print(f"  {label:<34} {len(frame):>7,} rows")
    print(f"\ncached in {args.cache}")
    return 0


def cmd_project(args: argparse.Namespace) -> int:
    config = _load_config(args)
    state = _load_state(args, config)
    config = _sync_config_to_state(config, state)

    client = NflverseClient(cache_dir=args.cache)
    week = args.week or state.current_week
    projections, _ = pipeline.build_projections(
        config, client, as_of_week=week, horizon=1, as_of=_parse_as_of(args.as_of)
    )
    frame = projections[projections["week"] == week]
    if args.position:
        frame = frame[frame["position"] == args.position.upper()]

    frame = frame.sort_values("mean", ascending=False).head(args.top)
    columns = [
        "name", "position", "team", "opponent", "mean", "sd",
        "availability", "implied_team_total", "report_status",
    ]
    print(f"\nWeek {week} projections\n")
    print(frame[columns].to_string(index=False, float_format=lambda v: f"{v:.2f}"))
    return 0


def cmd_optimize(args: argparse.Namespace) -> int:
    config = _load_config(args)
    state = _load_state(args, config)
    config = _sync_config_to_state(config, state)

    print(f"League: {config.name} ({config.season})")
    print(f"State:  {state.describe()}\n")

    horizon = args.horizon
    if horizon is None:
        end = pipeline.contest_end_week(config, state)
        horizon = max(1, end - state.current_week + 1)
        print(
            f"planning weeks {state.current_week}-{end} "
            f"(league reaches a winner around week {end})\n"
        )

    result = pipeline.run(
        config,
        state,
        cache_dir=args.cache,
        horizon=horizon,
        use_survival_weights=not args.no_survival,
        offline=args.offline,
        candidates=args.candidates,
        max_per_team=args.max_per_team,
        max_per_game=args.max_per_game,
        as_of=_parse_as_of(args.as_of),
        hold_players=_resolve_holds(args, config, state.current_week),
    )
    plan = result.plan

    if plan.status != "Optimal" or not plan.weeks:
        print(f"solver status: {plan.status}", file=sys.stderr)
        return 1

    _print_plan(plan, config)

    if args.csv:
        plan.to_frame().to_csv(args.csv, index=False)
        print(f"\nplan written to {args.csv}")

    if config.assumptions:
        print("\n" + "!" * 72)
        print("UNVERIFIED ASSUMPTIONS (fix these in your league config):")
        for note in config.assumptions:
            print(f"  - {note}")
        print("!" * 72)
    return 0


def _resolve_holds(
    args: argparse.Namespace, config: LeagueConfig, week: int | None = None
) -> set[str] | None:
    """Everyone barred from *this week only*, still free in every later one.

    Two sources: names you passed to `--hold`, and whole games ruled out by
    `--earliest-kickoff`. A silently-unresolved name would start the very player
    you meant to sit, so a typo raises rather than being dropped.
    """
    client = NflverseClient(cache_dir=args.cache)
    holds: set[str] = set()

    if args.hold:
        from .league.manual import PlayerResolver, UnresolvedPlayers

        resolved, missing = PlayerResolver(client.rosters(config.season)).resolve_all(
            args.hold
        )
        if missing:
            raise UnresolvedPlayers(missing)
        holds |= resolved

    cutoff = getattr(args, "earliest_kickoff", None)
    if cutoff and week is not None:
        early = _teams_kicking_off_before(client, config.season, week, cutoff)
        if early:
            roster = client.rosters(config.season)
            barred = roster[roster["team"].isin(early)]["gsis_id"].dropna()
            holds |= set(barred.astype(str))
            print(
                f"  holding week {week} players from {', '.join(sorted(early))} "
                f"(game starts before {cutoff})\n"
            )

    return holds or None


def _teams_kicking_off_before(
    client: NflverseClient, season: int, week: int, cutoff: str
) -> set[str]:
    """Teams whose week-`week` game starts before `cutoff`.

    `cutoff` is an ISO timestamp, or a weekday name, which resolves to midnight
    at the start of that day in the week being planned -- so 'sunday' means
    "nothing that kicks off Thursday, Friday or Saturday".
    """
    games = client.schedule()
    games = games[(games["season"] == season) & (games["week"] == week)]
    if games.empty:
        return set()

    kickoff = pd.to_datetime(
        games["gameday"].astype(str)
        + " "
        + games["gametime"].fillna("13:00").astype(str),
        errors="coerce",
    )
    text = str(cutoff).strip().lower()
    weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    if text in weekdays:
        same_day = games["weekday"].astype(str).str.lower() == text
        if not same_day.any():
            return set()
        moment = kickoff[same_day].min().normalize()
    else:
        moment = pd.Timestamp(cutoff)

    early = games[kickoff.notna() & (kickoff < moment)]
    return set(early["home_team"]) | set(early["away_team"])


def _parse_as_of(value: str):
    """Resolve the --as-of flag to a timestamp, or None to disable the filter."""
    if not value or str(value).lower() in {"off", "none", "false"}:
        return None
    if str(value).lower() == "now":
        return pd.Timestamp.now()
    return pd.Timestamp(value)


def _print_plan(plan, config: LeagueConfig) -> None:
    first = plan.weeks[0]
    print("=" * 72)
    print(f"START THIS WEEK (week {first.week})")
    print("=" * 72)
    picks = pd.DataFrame(first.picks)
    print(picks.to_string(index=False))
    h2h = config.survival.is_head_to_head
    bar = "opponent" if h2h else "cut line"
    verb = "win" if h2h else "survive"
    print(
        f"\n  projected {first.mean:.1f} +/- {first.sd:.1f} | "
        f"{bar} ~{first.threshold_mean:.1f} | "
        f"{verb} {100 * first.survival_probability:.1f}%"
    )

    print("\n" + "=" * 72)
    print(f"SEASON PLAN ({len(plan.weeks)} weeks, solved in {plan.iterations} iterations)")
    print("=" * 72)
    print(plan.summary_frame().to_string(index=False))
    if h2h:
        print(
            f"\n  record {plan.expected_wins:.1f} wins projected, "
            f"{plan.wins_needed} needed for a bracket spot"
        )
        print(f"  P(make the playoffs)  = {100 * plan.playoff_probability:.1f}%")
        print(f"  P(win it all)         = {100 * plan.title_probability:.1f}%")
    else:
        print(
            f"\n  P(surviving all {len(plan.weeks)} planned weeks) = "
            f"{100 * plan.cumulative_survival:.1f}%"
        )
    print(f"  total projected points over the horizon = {plan.total_points:.1f}")

    print("\n" + "-" * 72)
    print("PENCILLED IN FOR LATER (who is reserved for which week)")
    print("-" * 72)
    for wp in plan.weeks[1:]:
        names = ", ".join(f"{p['slot']}:{p['name']}" for p in wp.picks)
        print(f"  week {wp.week:>2}: {names}")


def cmd_calibrate(args: argparse.Namespace) -> int:
    from .calibration import calibrate, write_calibration

    config = _load_config(args)
    if args.validate in args.fit:
        print(
            f"error: season {args.validate} is in --fit; a validation season must be held out",
            file=sys.stderr,
        )
        return 1

    print(f"fitting on {args.fit}" + (f", validating on {args.validate}" if args.validate else ""))
    result = calibrate(
        config,
        args.fit,
        args.cache,
        tune_rates=args.tune_rates,
        fit_hazard=not args.no_hazard,
        validate_season=args.validate,
    )
    write_calibration(result, args.out)

    print(f"\nwrote {args.out}\n")
    availability = result.get("availability", {}).get("by_report_status", {})
    for status, entry in sorted(availability.items(), key=lambda kv: kv[1]["value"]):
        print(f"  P(plays | {status:<16}) = {entry['value']:.4f}   (n={entry['n']:,})")
    hazard = result.get("availability_hazard")
    if hazard:
        print(
            f"\n  availability hazard: {hazard['continuation']['n']:,} continuation rows, "
            f"{hazard['recovery']['n']:,} recovery rows"
        )
    rate = result.get("rate_model")
    if rate:
        print(
            f"\n  rate model: fit {rate['fit_score_points_per_start']} pts/start"
            + (
                f", held-out {rate['validation_season']}: "
                f"{rate['validation_points_per_start']} pts/start"
                if "validation_points_per_start" in rate
                else ""
            )
        )
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    from .backtest import (
        accuracy_table,
        evaluate_strategies,
        replay_projections,
        run_strategies,
        start_the_best_table,
    )

    config = _load_config(args)
    methods = ["mean", "season_to_date", "prior_season", "last_week"]

    for season in args.seasons:
        result = replay_projections(config, season, cache_dir=args.cache)
        if result.rows.empty:
            print(f"season {season}: no data", file=sys.stderr)
            continue

        print(f"\n{'=' * 76}\nSEASON {season}  ({len(result.rows):,} player-weeks)\n{'=' * 76}")
        accuracy = accuracy_table(result.rows, methods)
        print("\nrank correlation within position-week (higher is better):")
        print(
            accuracy.pivot(index="position", columns="method", values="rank_rho")
            .reindex(columns=methods)
            .round(4)
            .to_string()
        )
        print("\nactual points of the player each method says to start:")
        decision = start_the_best_table(result.rows, methods)
        print(
            decision.pivot(index="position", columns="method", values="avg_points_of_pick")
            .reindex(columns=methods)
            .round(2)
            .to_string()
        )

        if args.simulate:
            runs, frames, truth = run_strategies(config, season, cache_dir=args.cache,
                                                 teams=args.teams)
            table = evaluate_strategies(config, runs, frames, truth, teams=args.teams,
                                        replications=args.replications, seed=season * 1000)
            print(f"\nsurvivor-league simulation ({args.replications} fields, "
                  f"{args.teams} teams):")
            print(table.to_string(index=False))

    print("\n'mean' is this model. It has to beat the other three to be worth running.")
    return 0


def cmd_league_init(args: argparse.Namespace) -> int:
    if args.state.exists():
        print(f"{args.state} already exists; leaving it alone.")
        return 0
    config = _load_config(args)
    week = pipeline.infer_current_week(config, cache_dir=args.cache)
    args.state.write_text(STARTER_TEMPLATE.format(week=week))
    print(f"wrote {args.state} (current week detected as {week})")
    print("Edit it after each lineup you set, then re-run `seedman optimize`.")
    return 0


def cmd_league_doctor(args: argparse.Namespace) -> int:
    config = _load_config(args)
    print(f"League config: {args.config or default_config_path()}\n")
    print("Verify each of these against the league site, then update the config:\n")
    checks = [
        ("Scoring", "Every stat weight in the `scoring:` block, especially PPR value and bonuses."),
        ("Lineup", f"Slots are currently {[s.name for s in config.slots]} with no bench."),
        ("Reuse", f"A player may be started {config.survival.player_reuse_limit}x per season."),
        ("Field", f"{config.survival.teams_remaining} teams alive as of week {config.survival.as_of_week}."),
        ("Cuts", f"{config.survival.eliminations_per_week} team(s) eliminated per week, through week {config.survival.final_week}."),
        ("Pool", "Whether you pick from all NFL players or only a drafted roster."),
    ]
    for label, detail in checks:
        print(f"  [{label:<7}] {detail}")

    if config.assumptions:
        print("\nFlagged as unverified:")
        for note in config.assumptions:
            print(f"  - {note}")

    print(
        "\nThe highest-value thing you can add is `observed_field_scores` in your "
        "state file:\nevery other team's weekly totals. That replaces the modelled "
        "cut line with the real one."
    )
    return 0


def cmd_league_probe(args: argparse.Namespace) -> int:
    from .league.probe import LeagueProbe

    probe = LeagueProbe.from_env(out_dir=args.out)
    result = probe.run()
    print(f"probed {probe.base_url}, {len(result.steps)} requests\n")
    for step in result.steps:
        status = step.get("status", step.get("error", "?"))
        print(f"  {step['step']:<18} {status}  -> {step.get('saved_to', '-')}")
        for form in step.get("forms", []):
            names = [f["name"] or f["id"] for f in form["fields"] if f["name"] or f["id"]]
            if names:
                print(f"      form action={form['action']!r} method={form['method']} fields={names}")
    for note in result.notes:
        print(f"\n{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
