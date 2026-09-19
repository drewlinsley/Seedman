# Week 2 — Rocco Siffredi, 2026

Record 1-0. Lineup locks **Sunday 20 September, 1:00 PM ET**.

## Start this

| slot | player | team | opponent | proj | status |
|---|---|---|---|---|---|
| QB | **Caleb Williams** | CHI | vs MIN | 20.27 | not listed |
| RB | **Aaron Jones** | MIN | @ CHI | 7.34 | not listed |
| WR1 | **Davante Adams** | LA | vs NYG (Mon) | 9.99 | cleared |
| WR2 | **George Pickens** | DAL | vs WAS | 10.08 | not listed |
| TE | **Tucker Kraft** | GB | @ NYJ | 5.60 | not listed |
| FLEX | **Christian McCaffrey** | SF | vs MIA | 12.96 | cleared |

**66.2 projected ± 18.8 · opponent ~61.2 · 57.4% to win**
P(make the playoffs) 66.3% · P(win it all) 28.6%

Regenerate with:

```bash
seedman optimize --max-per-team 1 --max-per-game 2 --earliest-kickoff sunday \
  --hold "Derrick Henry" "Ryan Flournoy" --avoid-opponent CIN \
  --start "Christian McCaffrey" "Tucker Kraft" "Caleb Williams" \
          "Davante Adams" "Aaron Jones"
```

### Two of these are Drew's calls, and both beat the solver

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
