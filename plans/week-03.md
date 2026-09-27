# Week 3 — Rocco Siffredi, 2026

Record 2-0, confirmed. Week 2 was 99.28; add the opponent's total
to `observed_field_scores`). Lineup locks **Sunday 27 September, 1:00 PM ET**.

## Start this

| slot | player | team | opponent | kickoff | proj | status |
|---|---|---|---|---|---|---|
| QB | **Jared Goff** | DET | vs NYJ | Sun 1:00 | 20.15 | not listed |
| RB | **Christian McCaffrey** | SF | vs ARI | Sun 4:05 | 14.63 | cleared |
| WR1 | **Tetairoa McMillan** | CAR | @ CLE | Sun 1:00 | 8.89 | not listed |
| WR2 | **Rashod Bateman** | BAL | @ DAL | Sun 4:25 | 9.33 | not listed |
| TE | **Travis Kelce** | KC | @ MIA | Sun 1:00 | 7.63 | not listed |
| FLEX | **Khalil Shakir** | BUF | vs LAC | Sun 1:00 | 9.87 | not listed |

**70.5 projected ± 19.6 · opponent ~69.6 · 51.2% to win**
P(make the playoffs) 66.9% · P(win it all) 26.2%

All six audited by gsis id: every one `ACT/A01`. Henry, Worthy and Barkley held.

```bash
seedman optimize --max-per-team 1 --max-per-game 2 --as-of now \
  --hold "Saquon Barkley" "Xavier Worthy" "Derrick Henry" \
  --start "Christian McCaffrey" "Tetairoa McMillan"
```

### Worthy out, by matchup — Drew's lens

Asked for: receivers facing the worst pass defences. Half-PPR points allowed to
receivers per game over weeks 1-2 (rank 1 = worst). Worthy's opponent, Miami,
is **27th of 32** — one of the stingiest — so the swap fits the lens.

| WR in Worthy's place | opp pass D | proj | usage (snaps; tgts wk 1, wk 2) | win | P(title) |
|---|---|---|---|---|---|
| **Tetairoa McMillan** CAR @ CLE | #7 | 8.89 | 80%; 8, 10 — 101 yds last week | **51.2%** | 26.2% |
| Ladd McConkey LAC @ BUF | #3 | 8.82 | 46%; 7, 3 | 51.1% | 26.3% |
| Jakobi Meyers JAX vs NE (solver's pick) | #22 | 8.81 | 90%; 2, 1 | 51.1% | 26.4% |
| Josh Downs IND vs HOU | **#1** | 7.62 | 83%; 4, 9 — Pierce and Dulin Out | 49.5% | 26.2% |
| Kayshon Boutte HOU vs IND | #2 | 7.52 | 72%; 2, 5 | 49.3% | 26.3% |

Every row is within 0.2 of title, so the lens is free to decide. McMillan is the
pick because he is the only one with all three: a bad opposing pass defence, the
best projection, and the best usage — Carolina's clear WR1 at 80% of snaps with
ten targets last week. Josh Downs is the pure play on the lens (#1) with real
usage now that Indianapolis has lost Pierce and Dulin, but a 20.5 implied total
and a projection 1.3 lower. McConkey plays fewer than half of the Chargers'
snaps. Meyers gets snaps and no targets.

Dropping Worthy also frees the Kansas City slot under the one-per-team cap, and
**Travis Kelce** takes the tight end spot over Jake Ferguson: 7.63 against 5.79,
on 11 targets, 9 catches and 101 yards in week 2. Every candidate lineup above
includes that upgrade.

The caveat, once: points-allowed-by-position measured monotonically *worse* than
the model on held-out 2025, and two games is two games. It is used here because
it was asked for and because it costs nothing this week.

## "Don't blow all the good players" — what each stud actually costs

Every row is its own 15-week plan. The title column is the one that answers the
question; the win column is what you get for it this week.

| lineup | proj | win | P(playoffs) | P(title) |
|---|---|---|---|---|
| solver's own pick — McCaffrey, Henry banked | 69.1 | 49.2% | 67.0% | 26.5% |
| Henry instead of McCaffrey | 70.7 | 51.6% | 66.8% | 26.4% |
| Henry and McCaffrey | 75.6 | 58.0% | 66.8% | 26.3% |
| TreVeyon Henderson at RB (the riser) | 69.6 | 50.0% | 66.4% | 26.2% |
| Josh Allen | 78.9 | 62.1% | **70.8%** | **24.9%** |

**Henry and McCaffrey are each close to free** — a tenth of a point of title
each. That is the model saying their best remaining week is about now: BAL @ DAL
is the highest total on the board (53.5) and SF is a 7.5-point favourite at home,
and the running back pool behind them is deep enough for the bracket. Spending
both buys 8.8 points of win probability for 0.2 of title.

**Josh Allen is the one that costs.** +12.9 this week for −1.6 of title, and note
where the win goes: P(playoffs) *rises* to 70.8% (a 3-0 start matters for the
berth) while P(title) falls, because he is the week-16 quarterback. He stays
banked. So do Lamar (week 4), James Cook, CeeDee Lamb and Justin Jefferson.

## Health, and backups getting starter time

The scan: snap-share jumps from week 1 to week 2, cross-referenced with
same-position teammates now on IR, Out or Doubtful.

**Real, and acted on**

- **Derrick Henry / McCaffrey** — nothing to do with backups; they are simply
  the two studs whose spend is free this week. See above.

**Real, but not this week**

- **Aaron Jones (MIN)** — 46% → 81% of snaps with Jordan Mason on IR; 23 carries
  for 105 in week 2. The model has him at 8.31 and pencils him for week 8. Forcing
  him now costs about 2 points of win probability. MIN @ TB, implied 22.0.
  **Kyler Murray is back** (see the bug below — the model had him Out), which is
  good for the whole Minnesota offense.
- **TreVeyon Henderson (NE)** — 0% → 60% of snaps, 16 carries, a touchdown;
  Rhamondre Stevenson dropped 85% → 36%. A genuine takeover. 8.01 projected, and
  the honest FLEX alternative if you would rather keep McCaffrey banked: 69.6 and
  50.0% against 75.6 and 58.0%.
- **Jaylen Warren (PIT)** — 37% → 71% and Rico Dowdle is Out, so he is the lead
  back. But he is **Questionable (shoulder, limited)** at 0.765 availability, and
  Pittsburgh's implied total is 19.5. Forcing him costs 5.6 points. Pass.

**Do not start**

- **Saquon Barkley** — 71% → **16%** of snaps in a game Philadelphia won by four,
  4 carries for 9 yards, and he is on the week-3 report with full participation
  and *no injury named*. The model prices him at 0.974 because it cannot see any
  of that. Held out by hand. Monday night, so the news will land before his game
  but after the 1:00 locks.
- **Caleb Williams is Out** (hamstring); Tyson Bagent is Questionable with a
  concussion. Nothing from Chicago's passing game — Odunze, Loveland, Kmet.
- **Jayden Daniels is Out** (elbow), Marcus Mariota starts, Washington's implied
  total is 16.0. Nothing from Washington, including Ben Sinnott despite his
  68% of snaps with Okonkwo out.
- **Puka Nacua Doubtful** (hip). Stafford loses his WR1; Konata Mumpfield at 69%
  of snaps projects 6.22. Pass on both.
- **Nico Collins Out** (hamstring). Houston's targets spread across Hutchinson
  (81% of snaps), Boutte and Noel — 7.5, 7.3, 7.1. No one to start.
- **Brock Bowers** Questionable, did not practise, knee. 0.46 availability.

## Three bugs found this morning, all fixed

Each one changed the pool the lineup was drawn from, so they are recorded here
rather than only in the README.

1. **Every 1:00 PM game was missing.** nflverse stamps kickoffs in Eastern time;
   the container's clock is UTC. At 10:39 ET the filter read 14:39, every 1:00
   kickoff parsed as already started, and nine games — eighteen teams — left the
   pool with no sign anything was gone. Every early run this morning was drawn
   from the late slate alone. Only bites on game day between roughly 9 AM and 1
   PM ET, which is why last Saturday was fine.
2. **Recovered players stayed Out.** The report lookup took the latest row per
   player, so a man Out in week 1 who healed and dropped off the report stayed
   Out for good. TreVeyon Henderson, **Kyler Murray** and three others were at
   zero for games they were cleared for. Worse, pandas' `groupby().last()` takes
   the last non-null value per *column*, so week 1's "Out" was glued to week 2's
   "Full Participation" — a row no report ever carried.
3. **A forced start that was not in the pool was silently dropped.** It now
   raises.

## The bar

The model has your opponent at ~69.6 this week. Your opponents scored 82.46 in
week 1 and you scored 85 and 99. Absolute win probabilities on this page are
therefore optimistic; the *differences* between lineups are what to read. Real
opponent totals in `observed_field_scores` are the fix, and week 2's is missing.

## After the games

1. These six into `used_players`.
2. `record:` — and confirm week 2.
3. Both opponents' totals into `observed_field_scores`.
