# Week 2 — Rocco Siffredi, 2026

Record 1-0. Lineup locks **Sunday 20 September, 1:00 PM ET**.

## Start this

| slot | player | team | opponent | kickoff | proj | status |
|---|---|---|---|---|---|---|
| QB | **Brock Purdy** | SF | vs MIA | Sun 4:25 | 15.15 | not listed |
| RB | **Kenneth Walker III** | KC | vs IND | Sun 8:20 | 12.67 | not listed |
| WR1 | **Davante Adams** | LA | vs NYG | Mon 8:15 | 9.99 | cleared |
| WR2 | **Deebo Samuel Sr.** | SF | vs MIA | Sun 4:25 | 10.73 | not listed |
| TE | **Tucker Kraft** | GB | @ NYJ | Sun 1:00 | 5.60 | not listed |
| FLEX | **Aaron Jones** | MIN | @ CHI | Sun 1:00 | 7.34 | not listed |

**61.5 projected ± 19.4 · opponent ~61.2 · 50.4% to win**
P(make the playoffs) 65.1% · P(win it all) **29.2%** — the highest of anything run
this week.

All six audited against the roster file: every one `ACT/A01`.

Purdy and Deebo are both 49ers, so this needs `--max-per-team 2`. That is a
per-run flag, not a change to `configs/league.yaml` — the cap returns to one next
week unless it is passed again.

```bash
seedman optimize --max-per-team 2 --max-per-game 2 --earliest-kickoff sunday \
  --hold "Derrick Henry" "Ryan Flournoy" --avoid-opponent CIN \
  --start "Brock Purdy" "Deebo Samuel Sr." "Kenneth Walker III" \
          "Aaron Jones" "Tucker Kraft"
```

The spread widens to ±19.4 from ±17.8, which is the correlation model doing its
job rather than a defect: a quarterback and his own receiver rise and fall on the
same throws, so a stack has fatter tails in both directions. At a coin flip that
extra variance is working for you. It would not be in a week you were already
favoured to win, and it is exactly what makes stacking dangerous in a
single-elimination playoff week.

### The quarterback decision, priced four ways

| lineup | proj | win | P(title) | cap change? |
|---|---|---|---|---|
| Caleb Williams (solver's pick) | 66.6 | **57.8%** | 28.6% | no |
| **Purdy + Deebo stacked (chosen)** | 61.5 | 50.4% | **29.2%** | yes (`--max-per-team 2`) |
| Purdy + Pickens | 60.8 | 49.4% | 28.8% | no |
| Purdy + Luther Burden | 59.2 | 47.0% | 28.9% | no |

Purdy costs about 8 points of week-2 win probability against Caleb, and only half
of that is the quarterback. The rest is collateral: **Deebo Samuel is also a
49er**, so the one-per-team cap ejects him the moment Purdy goes in. Pickens
recovers most of it without touching the cap; stacking Purdy with Deebo recovers
slightly more and widens the spread to ±19.4, which is the correlation doing its
job — the two rise and fall on the same throws.

The finding worth keeping: **P(title) moves barely at all across all four**
(28.6% to 29.2%). This decision is close to free in season terms and expensive
in weekly ones. Caleb banking to week 17 is where the small title gain comes
from — a quarterback is worth far more in a playoff week.

At 49.4% this lineup is a coin flip. It is no longer favoured in week 2.

### Kenneth Walker over McCaffrey, and over Bucky Irving

| lineup | proj | win | P(title) |
|---|---|---|---|
| **Walker (Deebo fills WR1)** | **66.6** | **57.8%** | 28.6% |
| Walker, Pickens forced at WR1 | 65.9 | 56.9% | 28.6% |
| McCaffrey | 66.2 | 57.4% | 28.6% |
| Bucky Irving | 64.0 | 54.1% | 28.5% |

Walker for McCaffrey is close to a wash *in isolation*. It pays because
dropping McCaffrey frees the San Francisco slot under the one-per-team cap and
**Deebo Samuel** comes in over Pickens. McCaffrey banks to week 4.

Irving is 3.7pp worse and the reason is his usage, not his opponent:

| | week 1 | snap share | implied total |
|---|---|---|---|
| Walker | 23 car, 173 yds, **2 TD**, 3 rec | 68%, next back 36% | KC 26.25 |
| Irving | 8 car, 45 yds, 7 rec, 1 TD | 61%, **Gainwell 46%** | TB 25.00 |
| McCaffrey | 10 car, 68 yds, 5 rec | 55%, **Black 43%** | SF 29.00 |

Irving is a receiving back in a committee. Walker is a workhorse. McCaffrey,
notably, is now in a 55/43 split of his own.

### The matchup agrees, which is not evidence

Cleveland against Tampa's backs, Indianapolis against Kansas City's:

| defence | half-PPR allowed to RBs, wk 1 | rank |
|---|---|---|
| IND (Walker's opponent) | 38.1 | 30th of 32 |
| CLE (Irving's opponent) | 15.3 | 12th of 32 |

This points the same way as the answer above, and it should be given no weight
for two reasons. It is **one game** — Indianapolis played one backfield. And
points-allowed-by-position, blended into the projection at four strengths and
scored on held-out 2025, made the model **monotonically worse at every position
and every weight** (RB: 0.6233 → 0.6229 → 0.6162 → 0.5923). The implied team
total already carries defensive quality and a sportsbook prices it better. See
"Soft defences: measured, and deliberately not used" in the README.

DVOA itself is proprietary to Football Outsiders/FTN and is not in this data.
Nothing here is a DVOA figure.

### On whether a better quarterback matchup existed

| QB | proj | implied total |
|---|---|---|
| **Caleb Williams** CHI vs MIN | **20.27** | 26.00 |
| Brock Purdy SF vs MIA | 15.15 | 29.00 |
| Lamar Jackson BAL vs NO | 14.83 | 27.50 |
| Patrick Mahomes KC vs IND | 13.61 | 26.25 |

Caleb leads by 5.1 points. The caveat worth keeping in view: that rests on a
week 1 of 269 passing yards, two passing touchdowns and **two rushing
touchdowns**. Four touchdowns is not a repeatable line and in week 2 the model
has almost nothing else to weigh against it, so 20.27 is the softest number on
this card. He is still the start — a rushing quarterback keeps his floor even
when the touchdowns regress — and Purdy, who has the better game environment,
conflicts with Deebo under the one-per-team cap anyway.

### Drew's other two calls, both of which beat the solver

**Aaron Jones at FLEX.** The model prices him at 7.34 off a week 1 in which he
split Minnesota's backfield almost evenly with Jordan Mason — 46% of snaps to
45%, 12 carries to 15. **Mason is now on injured reserve.** Behind Jones the
depth chart is DeeJay Dallas (9% of snaps), a rookie, and a practice-squad back.
Mason's week 1 line was 15 carries, 59 yards and a touchdown, roughly 11 half-PPR
points, and most of it is now Jones's. The projection cannot know that: it is
built from a rate, and the rate is from the split. This is the same class of
edge as the Zay Flowers one — a change in who else is on the field, which no
individual projection can represent.

**Flournoy out.** Ryan Flournoy is the Cowboys' WR3: 71% of week 1 snaps behind
Pickens and Lamb, 4 targets for 22 yards. He is a volume artifact — the model
likes him because Dallas throws and he is on the field. Barring him made the
solver spend **George Pickens** (84% of snaps, the actual WR1) whom it had been
banking for week 12. That swap alone is worth more than it costs.

| lineup | proj | win | P(title) |
|---|---|---|---|
| solver's own pick (Flournoy + Godwin) | 65.4 | 56.2% | 28.8% |
| solver's free pick, Flournoy barred | 65.2 | 56.0% | 28.7% |
| **the above** | **66.2** | **57.4%** | **28.6%** |

Caleb and Jones are in the same game on opposite offenses, which the model treats
as *mildly positive* — a shootout lifts both sides. That is the standard,
well-replicated shape rather than a fit, and for a quarterback against an
opposing back specifically the game-script logic is murkier than it is for a
receiver. It moves the spread, not the pick.

## What changed since the last version of this file

The previous recommendation started **A.J. Brown, who is on injured reserve**
and was going to score zero. He has been on reserve since the roster cutdowns.

The model could not see him. A player on IR drops off the weekly injury report
entirely — with no practice to participate in there is nothing to report — so
the availability model read him as "not on report", which is the *healthiest*
state it knows, and priced him as a starter. Only 2 of the 278 players on
reserve appear on this week's report at all. Nothing downstream could have
caught it.

96 skill players were in a non-playable status this week and every one of them
was priced as healthy: Josh Jacobs (exempt list), Isiah Pacheco, James Conner,
Tank Dell, Jordan Mason. The model now reads the roster status column, which it
was never reading, and the printed `status` column names the reason.

**The fix did not cost points — it revealed points we never had.** The old
lineup's 67.7 projection included a certain zero. The honest number for that
same lineup was never above the low 60s.

## Each decision, priced

Every one of these was run as its own 16-week plan.

| decision | week 2 | win | P(title) |
|---|---|---|---|
| balanced plan (Lamar at QB) | 61.1 | 49.8% | 29.3% |
| **+ Caleb Williams at QB** | **65.4** | **56.2%** | **28.8%** |
| spend everything now (`--no-survival`) | 72.1 | 65.2% | **6.2%** |

- **Avoiding CIN costs nothing.** Ran with and without `--avoid-opponent CIN`:
  the lineup is byte-identical. No CIN-facing player was in the optimum anyway.
- **Forcing McCaffrey costs nothing.** The solver already wanted him.
- **Holding Derrick Henry costs nothing this week.** He is pencilled for week 3.
- **Caleb over Lamar buys +6.4pp of week-2 win probability for −0.5pp of title.**
  Take it. The model does not know that **Zay Flowers is Doubtful (hamstring,
  limited practice)** — Lamar's WR1, and a hit no QB projection can see, because
  a projection knows nothing about who else is in the huddle. Lamar banks to
  week 8 instead; Mahomes still takes week 17, past his recovery window.
- **Spending everything now is still catastrophic**: 6.2% title odds, below the
  8.3% a twelve-team coin flip would give you. That has held across every
  assumption tested.

## Why 57% and not more

This is a genuinely thin week. A.J. Brown and the CIN-facing players are out of
the pool, BUF and DET already played Thursday, and the solver is banking the rest
for the bracket. Kraft at 5.60 is the best tight end it will part with; the ones
it likes are held for weeks 12 through 17.

## After the games

1. Add the six you actually started to `used_players` in `league-state.yaml`.
2. Update `record:`.
3. Append your opponent's total to `observed_field_scores:` — real scores
   replace the modelled bar, and the bar drives everything.

## Still unverified

- **12 teams / 6 playoff berths.** Sets the bar at 8 wins against 8.3 projected.
  This is the assumption that most moves how much banking a stud is worth.
- **TE eligibility in the FLEX.** RB and WR are both confirmed from lineup cards.
- **The playoff opponent's strength**, modelled with a 0.17 between-team
  variance share that is an estimate, not a fit.

## Known soft spot in the fix

The gate holds a gated player out for the whole horizon, because nothing in this
data dates a return. That is deliberate and slightly conservative: a player who
does come back flips to `ACT` on the next roster refresh and re-enters the pool
at full value that day. Since only week one of the plan is ever acted on, the
cost is a shape, not a commitment — but it means James Conner and Tank Dell are
absent from the pencilled weeks rather than banked for a return.
