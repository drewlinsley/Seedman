# seedman

Lineup optimizer for a **survivor fantasy football league** — the format where you
field a new lineup every week, each NFL player can only be used **once all
season**, and the lowest score each week is eliminated.

That format makes the obvious strategy wrong. Starting your best available player
every week maximises points and loses the league: you arrive at week 11 with an
empty cupboard and three sharp opponents left. But hoarding is equally wrong, because
you have to still be alive to spend what you saved.

`seedman` prices that tradeoff instead of guessing at it.

Planning weeks 2–12 of 2026, the survival objective gives up 6.3 projected points
to buy a 6-point jump in modelled survival odds (34.1% → 40.1%). Read that as a
statement about the objective, not a measured edge: both numbers come from the
model scoring itself. For measured results against real seasons, see
[What the backtest says](#what-the-backtest-says) — which is less flattering.

```
 week  projected    sd  cut_line  survive_pct  teams_alive  point_weight
    2      84.94 21.61     49.47         94.9           12        0.8161
    3      81.13 23.68     44.68         93.7           11        0.8908
   ...
   11      79.45 21.01     49.37         89.9            3        1.3419
   12      89.42 25.49     61.64         82.1            2        1.6976
```

That last column is the whole idea: a projected point in week 12 is worth **2.1x**
a point in week 2, because by then only two teams remain and the cut line has
climbed from 49 to 62. So the optimizer spends cheap weeks on cheap players and
banks its stars for the weeks that decide the league.

---

## Quickstart

```bash
pip install -e .

seedman sync                  # pull the data (~40MB, cached)
seedman league init           # create league-state.yaml
seedman optimize              # this week's lineup + the rest of the season
```

Then after you set each lineup, add the players you burned to `league-state.yaml`
and re-run. The plan re-solves from wherever you actually are.

```bash
seedman optimize --no-survival     # compare against pure points-maximisation
seedman project --week 3 --position RB --top 20
seedman league doctor              # what still needs verifying
```

---

## What the backtest says

Every season from 2023-2025 was replayed week by week, projecting each week with
only what was knowable beforehand. `ProjectionModel(through_week=...)` clips the
inputs once, so no downstream code can reach into the future -- betting lines are
masked beyond the four weeks a book would have posted, and final scores are
masked the moment a game has not been played.

**The first version failed this badly.** Its projections ranked players *worse*
than simply averaging their season to date, at every skill position, in all three
seasons -- running back rank correlation of 0.35 against the baseline's 0.61.
The cause was a single bad decision: unknown players were shrunk toward the
*median startable* player at their position, so every third-stringer projected
like a real starter. Shrinking toward replacement level instead moved running
back rank correlation from 0.10 to 0.71 in isolation.

After fitting (2021-2024 for the constants, **2025 never used for anything but
reporting**):

| held-out 2025 | seedman | season-to-date | prior season | last week |
|---|---|---|---|---|
| rank correlation (QB/RB/WR/TE) | **0.558** | 0.521 | — | — |
| points of the started player | 67.9 | **71.6** | 52.3 | 56.3 |

| in-sample seasons | seedman | season-to-date |
|---|---|---|
| 2023 points of started player | **74.3** | 61.7 |
| 2024 points of started player | **72.4** | 62.7 |

Read that honestly: the model ranks players better than every baseline in every
season including the held-out one, and it wins the start-the-best-player contest
handily on the two seasons inside the fit -- but on genuinely held-out 2025 it
loses that contest to a season-to-date average by 3.7 points a week. The
top-1 metric is 64 picks a season and swings on a couple of boom weeks, so the
two measures disagreeing is not shocking. It is also not a result to wave away:
**on truly unseen data this model is better at ranking and no better at picking.**

Known residual: the top of the board is under-projected by ~32% in weeks 2-4 and
~14% from week 8. Ranking is unaffected (the bias is common to the whole board)
and the field calibration uses the same projections, so the survival comparison
largely cancels it -- but lineup totals read low early in a season. A calibration
bucketed by games played cuts the early-season bias to 21%, and costs 5.6 points
a week on the decision metric, so it is not enabled.

Reproduce any of this:

```bash
seedman backtest --seasons 2023 2024 2025
seedman backtest --seasons 2025 --simulate     # full survivor-league simulation
```

## Nothing here is hand-tuned any more

Every constant started as a prior taken from published research or experience.
Checking them against 2021-2024 found several wrong by large factors:

| constant | hand-set | measured | |
|---|---|---|---|
| P(plays \| Doubtful) | 0.06 | **0.0085** | 7x too high — Doubtful means "not playing" |
| P(plays \| Questionable) | 0.70 | 0.705 | the one I got right |
| share of variance that is league-wide | 0.25 | **0.036** | 7x too high — made every survival number overconfident |
| QB↔TE same-team correlation | 0.25 | 0.328 | understated the stacking penalty |
| shrinkage target | median starter | **5th percentile** | the bug that broke the whole ranking |

Two measurement traps worth knowing about, because both produced confident
nonsense before they were caught:

* **"Played" cannot mean "appears in the weekly stat file."** That file only
  lists players who recorded something, so a receiver who ran twelve routes
  without a target is indistinguishable from one who was inactive. Snap counts
  are the real signal, joined through the *season's own* roster — a later
  roster has already lost everyone who retired.
* **Availability has to be conditioned on having played the previous week.**
  Without it the denominator fills with players on injured reserve, not yet
  debuted, or not on an NFL roster at all, and a true 92% reads as 70%.

There is also a distinction the first version collapsed: a player the team never
listed is not the same as one listed with a practice line but no game status.
Counterintuitively the second is *more* likely to play (0.953 vs 0.920) — being
listed and left undesignated means the team actively cleared him.

```bash
seedman calibrate --fit 2021 2022 2023 2024 --validate 2025 --tune-rates
```

writes `configs/fitted.yaml`. Buckets thinner than 60 observations keep their
prior rather than trusting a point estimate off three cases, and every value
records which it was.

## Read this before you trust a lineup



**The league site is not wired up.** `https://rocco-siffredi.onrender.com` is
blocked by the network egress policy of the environment this was written in, so
its scoring rules, roster shape and your burned-player list could not be read.

Everything in `configs/league.yaml` marked `[ASSUMED]` is a standard-survivor-format
guess: full PPR, QB/RB/WR/TE/K/DEF with no bench, 12 teams, one cut a week. Every
report prints those assumptions in a banner, and `seedman league doctor` lists them
as a checklist. **Fix them first** — scoring rules in particular change which players
are even worth considering.

Two ways to close the gap:

- **Type the rules in.** Edit `configs/league.yaml`. Five minutes, and it is then
  correct forever.
- **Run the probe from your own machine**, where the site is reachable:

  ```bash
  export SEEDMAN_LEAGUE_URL='https://rocco-siffredi.onrender.com'
  export SEEDMAN_LEAGUE_PASSWORD='...'   # never commit these
  export SEEDMAN_LEAGUE_NAME='Drew Linsley'
  export SEEDMAN_LEAGUE_PIN='...'
  seedman league probe --out ./probe-output
  ```

  It walks the login flow and records every form, field and JSON endpoint it finds
  (credentials redacted from the output). From that, a real adapter is a short piece
  of work. There is deliberately **no speculative scraper** in this repo — a guessed
  HTML parser that silently returns the wrong roster is worse than no parser.

The single highest-value thing you can add is `observed_field_scores` in
`league-state.yaml`: what the other teams actually scored each week. That replaces
the modelled cut line with the real one, and the cut line drives everything.

---

## Where the numbers come from

All data is [nflverse](https://github.com/nflverse) — the open-data project behind
`nflfastR`, refreshed within hours of each game, no scraping and no API key.

| Input | Used for |
|---|---|
| `stats_player_week_{season}` | every scoring component, so **any** league's rules can be recomputed exactly |
| `games.csv` (nfldata) | schedule, byes, and **closing spreads/totals** |
| `injuries_{season}` | official NFL designations and practice participation |
| `rosters_{season}` | team changes, plus cross-site id maps (sleeper/espn/yahoo/pfr) |
| `snap_counts_{season}` | usage, the leading indicator of production |

Scoring is computed from **component stats**, never from someone else's fantasy
total — which is what lets a custom league's bonuses and kicker tiers come out
exact. Verified: reproduces nflverse's own PPR totals to 0.000000 across every
2026 stat line.

### Projections

Three ingredients, all auditable:

1. **A shrunk per-game rate.** Empirical-Bayes blend of this season, last season,
   and a position baseline fitted from startable players. In week 2, one good game
   moves a projection barely at all — correctly.
2. **Game context from the betting market.** A team's Vegas implied total
   (`total/2 ± spread/2`) is the best free predictor of available offense: across
   2018–2025 it tracks actual team points at *r* ≈ 0.39, essentially unbiased
   (23.56 projected vs 23.82 actual). Applied sub-linearly; defenses invert it
   against the opponent's total.
3. **Availability.** See below.

### Injury probability

The NFL dropped "Probable" in 2016 and detached fixed percentages from the rest,
so the old "doubtful = 25%" definition is dead. The priors here are empirical:
Out → 0, Doubtful → 0.06, Questionable → 0.70, no designation → 0.98 — with
practice participation as the tiebreaker inside Questionable, which is where
nearly all the real information sits.

Two things fall out that a naive model misses:

- A player who might not suit up carries **extra variance**, not just a lower
  mean — his points are a mixture of "plays" and "exactly zero". In a format
  decided by the left tail, that matters as much as the average.
- For **future** weeks no report exists yet, so today's health mean-reverts toward
  a position baseline. Running backs decay fastest, kickers barely at all. This is
  what stops the optimizer cheerfully banking a torn hamstring for week 12.

---

## How the optimization works

A mixed-integer program over the **whole remaining season**, because in a one-use
league this week's lineup is inseparable from the rest of the schedule:

```
variables   x[p,w,s] ∈ {0,1}          player p starts in slot s of week w
subject to  Σ_p x[p,w,s] = 1           every slot filled, every week
            Σ_{w,s} x[p,w,s] ≤ L       each player usable L times all season
            Σ x ≤ 2 per team/week      diversification (see below)
maximise    Σ_w ω_w · Σ_{p,s} x[p,w,s] · μ[p,w]
```

The weights `ω_w` are what make it a *survival* optimizer rather than a points
optimizer. Model your week-`w` score as `N(μ_w, σ_w²)` and the cut line — the
lowest score among teams still alive — as `N(m_w, s_w²)`. Then

```
P(survive week w) = Φ(z_w),  z_w = (μ_w − m_w) / √(σ_w² + s_w²)
ω_w = ∂/∂μ_w log Φ(z_w) = φ(z_w)/Φ(z_w) / √(σ_w² + s_w²)
```

Maximising `Σ_w log Φ(z_w)` *is* maximising `P(survive every week)`. The weight is
large when you are near the cut line and small when you are clear of it, so
"spend now versus save for later" is an output of the model, not a tuning knob.

The cut line tightens automatically as the league shrinks — the minimum of 11
opponents sits far below the minimum of 3 — which is why the endgame weeks command
the highest weights.

Three details that turned out to matter more than expected:

- **`ω` depends on the lineup**, so the solve iterates: optimise, recompute how
  safe each week now looks, reweight. But maximising a linearisation of a concave
  objective over an *integer* feasible set lands on a vertex and can oscillate
  between two lineups forever — which, before it was caught, returned answers
  *worse* than where it started. Fractional steps are not available (half a
  quarterback is not a lineup), so every iterate is scored against the true
  objective and the best one is returned. Since iteration 1 uses flat weights, the
  result can never be worse than plain points-maximisation.
- **Players in a lineup are correlated.** The objective is linear and therefore
  blind to variance, so left to itself the solver stacked four players from one
  game for a fraction of a point. A quarterback and his own receiver rise and fall
  together; so, more weakly, does everyone on one offense. Lineup spread is
  computed from the full covariance matrix, and hard caps (≤2 per team, ≤3 per
  game) stop the solver building stacks the objective cannot see the risk of.
- **Some variance cancels.** A low-scoring Sunday drags every team down together,
  including the cut line, so that shared component does not separate you from the
  field. Counting it on both sides understates how decisively a good lineup pulls
  away, so it is netted out of both.

Known simplification: week-to-week survival is treated as independent, so the
cumulative figure ignores that busting week 2 makes week 12 moot. Since you re-run
each week on a rolling horizon, this costs nothing in practice.

---

## Layout

```
seedman/
  config.py         league rules: scoring, roster shape, survival format
  scoring.py        component stats -> fantasy points under your rules
  projections.py    shrunk rates x Vegas context x availability, with the
                    through_week clip that makes backtesting honest
  injury.py         designation -> P(plays), and future-week decay
  correlation.py    same-team / same-game covariance
  survival.py       cut-line distribution and the marginal value of a point
  calibration.py    fits every constant from historical seasons
  fitted.py         loads fitted values, keeps priors where samples are thin
  backtest.py       week-by-week replay, baselines, league simulation
  optimize/solver.py   the MILP and the reweighting loop
  league/           manual adapter, name resolution, site probe
  pipeline.py       data -> projections -> field calibration -> plan
  cli.py
configs/league.yaml  ** edit this first **
configs/fitted.yaml  produced by `seedman calibrate`; do not hand-edit
tests/               86 tests, no network required
```

```bash
python -m pytest      # 86 passed
```

`tests/test_leakage.py` is the one to read first. A backtest that leaks produces
confident numbers justifying a broken model, so the guard is tested directly: a
monster week 6 must not move the week-4 projection by a thousandth of a point.
