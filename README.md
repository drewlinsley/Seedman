# seedman

Lineup optimizer for a fantasy football league with one unusual rule: **each NFL
player may be started only once all season.** You field a new lineup every week
and can never go back to a player you have spent.

That rule makes the obvious strategy wrong. Starting your best available player
every week maximises points and loses the league — you arrive at the playoffs
with an empty cupboard. But hoarding is equally wrong: you have to still be in
contention to spend what you saved.

The league this is built for (`configs/league.yaml`) is **head-to-head**: one
opponent a week, roughly 15 regular-season weeks, then a two-week playoff. The
survivor format — lowest score eliminated weekly — is also supported via
`survival.mode`, and the two want genuinely different lineups. See
[The format decides everything](#the-format-decides-everything).

`seedman` prices that tradeoff instead of guessing at it, and the price turns out
to depend entirely on the format. In head-to-head the plan back-loads hard — the
two playoff weeks get the best lineups on the board, while the regular season is
run at whatever strength keeps the record above the bracket line:

```
 week  projected    sd  opponent  win_pct   phase  point_weight
    2      60.14 17.33     63.90     44.4 regular        0.478
    3      64.38 18.06     64.33     50.1 regular        0.494
   ...
   15      24.80 10.43     22.56     55.2 regular        0.765
   16      38.99 14.68     33.89     59.9 PLAYOFF        3.328
   17      37.88 14.26     32.60     60.4 PLAYOFF        3.332

  P(make the playoffs)  = 64.3%
  P(win it all)         = 23.3%
```

Run the same board with `--no-survival` — start the best player available every
week — and it makes the playoffs *more* often (76.9% against 64.3%) and then
loses there, because it arrives at the bracket on its sixteenth-best quarterback
while the opponent saved. P(win it all) falls to **7.3%**, below the 8.3% a
twelve-team coin flip would give you. That gap holds across every assumption
about how hard opponents bank, and widens as the bracket gets tougher:

```
playoff opponent scores        27       31       34       38       42       46
save for the bracket        32.9%    26.7%    22.2%    16.6%    11.9%     8.0%
spend early                 15.1%     9.7%     6.6%     3.7%     1.9%     0.9%
```

Run the same board as a survivor pool and the plan inverts: it spends early,
because a week you might be eliminated in is worth more than a week you might
never reach. Neither answer is a knob — both fall out of the objective.

**A warning about that table.** Those playoff win percentages are the number most
likely to be wrong, and they are wrong in the direction that flatters the whole
strategy. They depend on a model of what your opponent starts in week 16, and
nothing in this repo has ever observed one. See
[The format decides everything](#the-format-decides-everything) for the two
corrections already applied and why the residual uncertainty still points the
same way.

**On the projections themselves, measured against real seasons:** they beat every
naive baseline on ranking, including on held-out 2025 — except at RB and WR in
the season's first few weeks, where a plain prior-season average is better. That
is a real defect with a diagnosed cause, and it is not fixed. See
[What the backtest says](#what-the-backtest-says).

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

### Which ranker to trust in the season's first weeks

Pooled over weeks 2-17 the model out-ranks every baseline at every position. Cut
the same held-out 2025 rows down to **weeks 2-4** and that stops being true:

| weeks 2-4, held-out 2025 | seedman | prior season | season-to-date |
|---|---|---|---|
| QB | **0.633** | 0.502 | 0.405 |
| RB | 0.624 | 0.661 | **0.688** |
| WR | 0.587 | **0.632** | 0.586 |
| TE | **0.600** | 0.536 | 0.523 |

So in the season's opening weeks the model is the best ranker at QB and TE and
the *worst* of the three at RB and WR. The obvious culprit -- one game of current
season outvoting half of last season, since `prior_season_weight` is a fixed
quantum of 2 against `n_cur` -- is not the culprit. Sweeping that weight over
2/4/6/8/12/20 on weeks 2-4 makes the model monotonically worse (average rank
correlation 0.611 down to 0.534), so the fitted 2.0 is already right, even
restricted to the weeks where the model loses.

What is left is the multipliers. Push `prior_season_weight` to 20 and the rate
term is essentially last season's average, yet the model still ranks WRs at 0.560
against that same average's 0.632 -- the gap is the Vegas-context and availability
factors, and early in a season they subtract about 0.07 of rank correlation at
WR. That is a real defect and it is **not fixed**; the honest workaround, until
it is, is to rank by position with whichever column wins above and let the model
supply only EV and standard deviation, which is what a week-2 lineup here does.

Reproduce: `replay_projections(cfg, 2025, first_week=2, last_week=4)` then
`accuracy_table(rows, ["mean", "prior_season", "season_to_date"])`.

### Does the survival objective actually win leagues?

Separately from projection accuracy: simulate whole survivor seasons on real
scores. Each strategy walks the season re-planning every week, then is scored
against 400 draws of an eleven-team field of greedy managers working from the
same projections with 30% noise. Lowest score is cut, every week.

Twelve teams, one elimination a week:

| 2025 (held out) | avg weeks survived | won outright | avg weekly points |
|---|---|---|---|
| points-maximising | 7.80 | 5.5% | 67.6 |
| greedy (start the best available) | 6.07 | 2.8% | 74.5 |
| **survival-weighted** | **1.81** | **1.0%** | 72.1 |

| 2024 | avg weeks survived | won outright | avg weekly points |
|---|---|---|---|
| greedy | 8.75 | **19.5%** | 66.5 |
| points-maximising | 5.92 | 6.8% | 69.7 |
| **survival-weighted** | **4.62** | **3.2%** | 66.4 |

| 2023 | avg weeks survived | won outright | avg weekly points |
|---|---|---|---|
| points-maximising | 10.03 | **21.0%** | 70.8 |
| greedy | 7.69 | 7.0% | 62.8 |
| **survival-weighted** | **2.33** | **1.2%** | 63.3 |

The survival objective -- the centrepiece of this whole design -- finished last
in all three seasons. That is the honest number and it is not a near miss.

The other two seasons rule out the comfortable explanation. The best strategy
won 19.5% of 2024 leagues and 21.0% of 2023 leagues, against the 8.3% a coin
flip gets in a twelve-team league -- so the projections underneath carry a real
edge. The survival weighting then cuts that to 3.2% and 1.2%. It is not that
nothing works; it is that this specific layer subtracts from what does.

Two further explanations are ruled out. The cut line is not mis-estimated:
checked against the simulated field week by week, the model's is accurate to
within half a point on average. The lineups are not weak either -- in 2025 the
survival strategy averaged 72 points a week against a field averaging 63.

The mechanism is most likely the one the design intends: it deliberately fields
weaker lineups early to bank players for later, and early elimination ends the
season before the banked players are ever used. `log Phi` summed across weeks is
the right objective *if* you reach those weeks, and the simulation says you
often do not.

One caveat on how much weight these tables carry. Each strategy walks its season
exactly once, so its sixteen weekly scores are fixed and only the opponents
resample; if the survival strategy's week-2 lineup finishes last that week it
dies in week 2 across nearly all 400 replications, and the 400 tells you little
the 1 did not. So each season is closer to one observation than to 400. Two
seasons agreeing is real evidence, but it is three, and resampling the
strategy's own season -- more seasons, or bootstrapped outcomes -- is the obvious
next piece of work and is not done here.

**Bottom line: the survival weighting is not supported by the evidence, and all
three seasons point against it.** The theory is sound and the implementation does what
the theory says, but a mechanism that trades early safety for late strength has
to earn its keep, and it has not. `--no-survival` is the right default until a
better-powered simulation says otherwise.

Reproduce any of this:

```bash
seedman backtest --seasons 2023 2024 2025
seedman backtest --seasons 2025 --simulate     # full survivor-league simulation
```

## Availability is the biggest remaining lever

Measured against a perfect-foresight oracle -- one that knows exactly who suits
up and nothing else -- availability error costs about **12.6 points a week**
across four skill slots at planning horizons. For scale, the entire gap between
these projections and a naive season-to-date average is 3.7. In a format where
you commit a player to a future week and cannot take him back, this is the
number that matters.

The model that used to do this job was two lines: today's availability decaying
toward a hand-set position baseline at a hand-set rate of 0.7 a week. On
held-out 2025 it measured at **AUC 0.49-0.55** -- a coin flip -- while
confidently reporting ~0.90 availability for a population that was actually
available ~0.70 of the time. Every multi-week availability number this project
produced before that measurement was noise, stated with conviction.

It is now a **two-state discrete-time Markov model**: one logistic hazard for
"plays next week given he played this week", another for "plays next week given
he did not", each with its own covariates (injury history, snap share, workload,
age, current designation and practice participation). Multi-week availability is
that chain iterated forward.

| held-out 2025, AUC | t+1 | t+2 | t+3 | t+4 | t+6 |
|---|---|---|---|---|---|
| fitted hazard | **0.887** | **0.833** | **0.802** | **0.766** | **0.731** |
| AR(1) it replaces | 0.553 | 0.521 | 0.509 | 0.502 | 0.487 |
| carry last week forward | 0.811 | 0.750 | 0.717 | 0.698 | 0.653 |

Calibration at t+3 went from one bucket containing 95% of the data at 0.90
predicted against 0.70 observed, to 0.89→0.89, 0.71→0.77, 0.30→0.23. Downstream
that is worth +2.4 points a week across four slots -- real, but only about a
fifth of the oracle ceiling, because a lot of injury is genuinely unforecastable.

**Why not Cox proportional hazards**, which is the natural instinct here and
where this started: the covariate story is identical, but events land on week
boundaries so essentially every failure time is tied, and Cox handles ties only
by approximation; the optimizer needs an absolute probability rather than a
hazard ratio, which is the part a discrete-time model estimates directly; and
availability is recurrent and reversible, so a time-to-first-event Cox discards
everything after a player's first injury. The extensions that do not
(Andersen-Gill, PWP) end up close to what is implemented here.

The structural gain is what the old model could not express at all: **duration
dependence**. P(returns next week) falls from 36% after one missed game to 15%
after four. An AR(1) pulls everyone back toward a fixed baseline at the same
rate no matter how long they have been out. The Markov chain's fixed point,
`b / (1 - a + b)`, is a per-player long-run availability learned from his own
history -- so the position baseline the old model had to be handed now falls out
of the data instead.

Rookies enter only once they have played, which is both the sensible rule and
the one that matches how the projections treat them: a player with no record
regresses to replacement level, not to a starter.

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

## The format decides everything

This started life as a survivor-pool optimizer, and the league turned out not to
be a survivor pool. The two formats share one rule -- **each NFL player may be
started once all season** -- and disagree about everything that follows from it.

| | survivor | head-to-head (this league) |
|---|---|---|
| who you must beat | the lowest score among everyone alive | one specific opponent |
| a bad week costs | your season | one game |
| week 17 | probably never happens | the final |
| banking a star for the playoffs | a way to get eliminated in September | the entire strategy |

The bar is the whole difference. The lowest of eleven scores is a *soft* target
you clear ~97% of the time; one named opponent is a coin flip. Running the
survivor objective on a head-to-head league quietly optimises for a threat that
does not exist, and it will spend a stud to turn a 95% week into a 98% week.

`survival.mode: head_to_head` switches the objective to what the format actually
rewards:

```
P(title) = P(make the bracket) x P(win week 16) x P(win week 17)
```

Two regimes fall out of that, and they want opposite things:

- **Regular season.** Only the *record* matters, so a 40-point win is worth
  exactly as much as a 1-point win. The value of a point is
  `dP(berth)/dp_w * phi(z_w)`, which peaks when the season is on the bubble and
  decays to nothing once a berth is close to settled either way. `playoff_cut_wins`
  reads the bar off the league's own binomial win spread: 8 wins from 15 games
  for a 6-of-12 bracket.
- **Playoffs.** Lose and you are done, so the objective reverts to the survivor
  shape -- maximise `log P(win)`, and a point is worth `phi(z)/Phi(z)`.

Two corrections were needed to keep this honest, both of which had been
flattering the case for hoarding:

1. **A playoff opponent is better than average, but not by as much as it first
   looked.** Qualifying selects on *skill*, not on one week's luck, so only the
   between-team slice of variance may be truncated. Conditioning the full weekly
   spread priced the bracket bar at 97.6 against a field of 80; conditioning only
   the skill component puts it at 87.2.
2. **Your opponent banks for the bracket too.** The field model ages a rival's
   lineup by one slot a week, which by week 17 had him starting his 21st-best
   option while we started our best -- a 74% win probability against a field we
   had modelled into the ground. Anyone who knows the one-start rule holds
   players back exactly as we do, so playoff-week opponents are credited with a
   bracket's worth of reserves.

### The solver overshoots, so it finishes with a local search

Reweighting linearises a concave objective and maximises the linearisation over
integer points, which lands on a vertex rather than the interior optimum. It
overshoots in a specific direction: the weeks that look most valuable on the
first pass get loaded until a point there is worth far *less* than a point in
the weeks it was taken from. On a flat test board the marginal values ended up
**8x apart**, all in the direction of hoarding for the bracket.

Best-of-iterates cannot repair that, because every iterate shares the bias. So
the solve finishes with an exact local search on the true objective -- swap the
occupants of one slot between two weeks, keep the move only if the objective
genuinely improves. Candidate moves are scored on the objective itself, never on
a linearisation, so the pass can only help. Afterwards the marginal values sit within 4x on a
flat synthetic board.

They do not fully equalise on the real one -- a playoff point still prices around
7x a week-2 point -- and that is a granularity limit rather than a bug left
unfixed: the swap that would close the gap moves a 25-point quarterback against a
4-point flex, which overshoots in the other direction. A first-order condition
describes infinitesimal transfers, and lineups do not come in infinitesimals.

## Read this before you trust a lineup



**The league site is not wired up.** `https://rocco-siffredi.onrender.com` is
blocked by the network egress policy of the environment this was written in, so
its scoring rules, roster shape and your burned-player list could not be read.

Most of it has since been pinned down without the site. The roster shape
(QB/RB/WR/WR/TE/FLEX, no kicker, no defense) came off a lineup screenshot, half PPR
came from the commissioner, and **the scoring was then confirmed arithmetically**: an
opponent's week-1 card totalling 82.46 reconciles to the cent under half PPR with no
yardage bonuses, and only under that — Chris Olave went for 182 receiving yards and
was scored 23.20, so the 100-yard bonus that most templates assume does not exist here.
A single opponent box score is worth more than any amount of guessing at defaults.

What is still `[ASSUMED]` in `configs/league.yaml`: 12 teams, one cut a week through
week 17, and TE eligibility in the FLEX. Every report prints those in a banner, and
`seedman league doctor` lists them as a checklist.

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
  injury.py         designation -> P(plays) for the week in front of you
  availability.py   two-state weekly hazard: P(available) h weeks out
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
tests/               109 tests, no network required
```

```bash
python -m pytest      # 109 passed
```

`tests/test_leakage.py` is the one to read first. A backtest that leaks produces
confident numbers justifying a broken model, so the guard is tested directly: a
monster week 6 must not move the week-4 projection by a thousandth of a point.
