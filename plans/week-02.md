# Week 2 — set before Sunday 1:00 PM ET

Regenerate any of this with:

```bash
seedman optimize --max-per-team 1 --max-per-game 2 \
  --earliest-kickoff sunday --hold "Derrick Henry" --start "Tucker Kraft"
```

Those flags are decisions, not defaults, and each is priced:

| flag | why | cost |
|---|---|---|
| `--max-per-team 1 --max-per-game 2` | six separate games; a stacked lineup's variance is real | ~0 (EV went *up*) |
| `--earliest-kickoff sunday` | a Thursday starter locks the whole roster three days early | 0.6 pts of title probability |
| `--hold "Derrick Henry"` | banked, by choice | ~0 |
| `--start "Tucker Kraft"` | better than Kelce on both axes | free (+0.6 EV, title unchanged) |

## Recommended

| slot | player | team | kickoff |
|---|---|---|---|
| QB | **Caleb Williams** | CHI vs MIN | Sun 1:00 |
| RB | David Montgomery | HOU vs CIN | Sun 1:00 |
| WR1 | Davante Adams | LA vs NYG | Mon 8:15 |
| WR2 | A.J. Brown | NE vs PIT | Sun 1:00 |
| TE | Tucker Kraft | GB @ NYJ | Sun 1:00 |
| FLEX | Christian McCaffrey | SF vs MIA | Sun 4:25 |

67.7 projected, 59.5% to win. P(playoffs) 66.7%, P(title) 29.0%.

**The QB call is a judgement, not an output.** Keeping Lamar Jackson scores 61.4 /
50.3% / 29.5% — half a point *more* title probability. Caleb is the pick anyway for
two reasons the model does not price:

1. Zay Flowers, Lamar's WR1, is **Doubtful** (hamstring). The projection has no
   idea that a quarterback's best receiver is out.
2. A berth needs 8 wins and the plan projects 8.2. A banked win now is worth more
   than the model's flat treatment of regular-season weeks implies.

Taking Caleb moves the week-17 QB to Patrick Mahomes, who by then is past the
return-from-injury recovery window (0.976 early, 1.097 after week 6). That is most
of why the cost is only half a point.

## After the games

Add the six actually started to `used_players` in `league-state.yaml`, update
`record:`, and append the opponent's total to `observed_field_scores` — real scores
replace the modelled bar, and the bar drives everything.

## Still unverified

- 12 teams / 6 playoff berths — sets the bar at 8 wins against 8.2 projected. If
  it is really 4 berths or 14 teams the whole allocation shifts.
- TE eligibility in the FLEX (RB and WR both confirmed from lineup cards).
