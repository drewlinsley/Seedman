# Week 4 — Rocco Siffredi, 2026

Record **2-1 assumed** (week 3 scored 58.66 if the agreed lineup was set —
confirm). Week 3's six are marked burned as an assumption; check them against
the site's burned pool. Locks at the first kickoff among your starters: **Sunday
4 October, 1:00 PM ET**. IND @ WAS (London, 9:30) and PIT @ CLE (Thursday) are
already locked.

## Start this

| slot | player | team | opponent | kickoff | proj | status |
|---|---|---|---|---|---|---|
| QB | **Lamar Jackson** | BAL | vs TEN | Sun 1:00 | 13.89 | cleared |
| RB | **Emanuel Wilson** | SEA | vs LAC | Sun 4:25 | 3.76* | not listed |
| WR1 | **Ja'Marr Chase** | CIN | vs JAX | Sun 1:00 | 11.32 | not listed |
| WR2 | **DJ Moore** | BUF | vs NE | Sun 1:00 | 8.07 | cleared |
| TE | **Brock Bowers** | LV | vs KC | Sun 4:25 | 6.93 | cleared |
| FLEX | **D'Andre Swift** | CHI | vs NYJ | Sun 1:00 | 10.54 | cleared |

**54.5 projected ± 16.1 · opponent ~63.3 · 36.4% to win**
P(make the playoffs) 54.8% · P(win it all) 20.7%

\* The model's number; see below for why it is too low.

All six audited by gsis id: every one `ACT/A01`. Lamar, Swift full practice;
Moore and Bowers limited with no designation.

```bash
seedman optimize --max-per-team 1 --max-per-game 2 --as-of now \
  --start "Emanuel Wilson" "Ja'Marr Chase"
```

## "Use Emmanuel Henderson"

**Emmanuel Henderson Jr.** is a Seattle wide receiver on the **practice squad**
(`DEV/P01`), a 2026 seventh-rounder with zero NFL snaps. Seattle's receiver room
is fully healthy — Smith-Njigba, Kupp and Shaheed all practising in full, Tory
Horton and Montorie Foster also on the active roster ahead of him. Nothing gives
Seattle a reason to elevate him, and elevated he would be the WR6. He is not in
the projection pool at all, and `--start` refuses him rather than silently
dropping him. Expected score: about zero, i.e. one wasted slot.

**Emanuel Wilson** is the Seattle player with the story: both of Seattle's other
backs are **Out** — Zach Charbonnet (knee) and Jadarian Price (chest) — leaving
Wilson and George Holani. This lineup assumes Wilson is who was meant.

### Why 3.76 undersells Wilson

The projection is a rate, and the rate comes from a three-way split: 6%, 41%
and 36% of snaps behind and beside Price. Same failure as Aaron Jones's 7.34 in
week 2, when Jordan Mason went on IR. What changes this week:

- Price's share (48%, 34%, 28% of snaps) is gone.
- Holani is the change-of-pace / receiving back: 3, 4 and 8 carries, 14 yards on
  9 carries last week, five targets.
- Wilson's one lead-back game this season: **21 carries, 92 yards** (week 2).
- Seattle is a 7-point home favourite, implied 24.75 — clock-killing script.

A rough read: 15–18 carries at his ~4.0 career yards per carry plus touchdown
equity is about **8 points**, not 3.76. That is a judgment, not a fit — but it is
the difference between Wilson costing a lot and costing almost nothing.

## Priced

| lineup | proj | win | P(playoffs) | P(title) |
|---|---|---|---|---|
| solver's own pick, no Wilson | 56.6 | 39.6% | 55.4% | 21.5% |
| Wilson, solver fills (Waddle at WR) | 49.9 | 29.2% | 54.2% | 21.0% |
| **Wilson + Ja'Marr Chase (chosen)** | **54.5** | **36.4%** | 54.8% | 20.7% |
| Wilson + Jahmyr Gibbs | 57.6 | 41.4% | 56.8% | 20.0% |
| Wilson + Derrick Henry (bumps Lamar) | 52.2 | 33.1% | 54.0% | 20.9% |

All Wilson rows use the model's 3.76. At ~8, add roughly 4 points and ~9 of win
probability to each, which puts Wilson + Chase level with the solver's own pick.

**Chase** is the best exchange rate on the board: +7.2 points of win probability
for 0.3 of title. CIN vs JAX is tied for the highest total of the week (51.5),
Cincinnati is implied 27.0, and Chase had 12 targets, 9 catches and 98 yards
last week at 100% of snaps. **Gibbs** costs a full point of title — he is the
week-17 running back — and stays banked. So do Josh Allen (week 16), Henry,
Cook, Lamb, Jefferson and the rest.

## Health and opportunity, week 4

**Outs that matter:** Justin Jefferson (ankle) — Jordan Addison is Minnesota's
WR1 at 98% of snaps with 9 targets last week; DeVonta Smith and Marquise Brown
(Philadelphia's top two receivers) plus Dallas Goedert; Breece Hall (Braelon
Allen leads the Jets backfield); Baker Mayfield; Caleb Williams again (Case
Keenum starts — it is why Swift's ceiling is capped).

**Saquon Barkley:** week 2's 16% of snaps was the anomaly — he was back to 72%
in week 3, so the hand-hold is lifted. But Philadelphia is implied 19.5 with
three receivers and its tight end out, and the model has him at 5.95. Not this
week.

**Brock Bowers:** 0%, 0%, then 79% of snaps — 13 targets, 10 catches, 116 yards
in his first game back. The model has him at 6.93 on a single game of data;
that is the other number on this card likely to be low.

## The record

The lineup barely depends on whether week 3 was a win: re-solved at 3-0 it is
the same six give or take one receiver. What it moves is the season — at 3-0,
P(playoffs) is 76.6% and P(title) 29.6%; at 2-1, 55.4% and 21.5%. Confirm it.

## After the games

1. Confirm week 3's six in `used_players`, and add these six.
2. `record:` — confirm week 3, add week 4.
3. Opponent totals into `observed_field_scores` (weeks 2–4 are missing).
