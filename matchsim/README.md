# Synthetic match generator

Generates realistic, fully fictional football matches as a live event feed. This is the
**Ingest** source for the match-intelligence pipeline.

## Run it

```bash
python generate_match.py --seed 7 --out matches/match_00007
python replay.py matches/match_00007 --speed 60          # 1 match minute per second
python replay.py matches/match_00007 --speed 60 --json   # raw events, as a feed
```

Each seed gives a different, reproducible match. No packages needed beyond Python 3.9+.

## Output

| File | Contents |
|---|---|
| `metadata.json` | Fictional competition, venue, weather, both teams (name, colours, formation), starting XI and bench with player attributes |
| `events.jsonl` | One event per line, in time order |
| `tracking.jsonl` | Every 5 seconds: position of every player and the ball |
| `ground_truth.json` | Final score, tactical changes the simulator made, distance covered and top speed per player |

Coordinates are metres on a 105 × 68 pitch. The home team attacks towards x = 105 in the
first half and towards x = 0 in the second.

## Event types

`period_start`, `kick_off`, `pass`, `carry`, `dribble`, `pressure`, `tackle`,
`aerial_duel`, `interception`, `clearance`, `block`, `possession_change`, `shot`, `save`,
`goal`, `foul`, `penalty_awarded`, `card`, `free_kick`, `corner`, `throw_in`, `goal_kick`,
`substitution`, `physical_update`, `period_end`, `match_end`.

Every event has `event_id`, `type`, `period`, `match_seconds`, `clock` (e.g. `45+2`), and
`score`. Most also have `team`, `player_id`, `player_name`, `x`, `y`, `possession_id`.

Extra fields by type:

- **pass**: `pass_type` (short / medium / long / through_ball / cross), `outcome`,
  `receiver_id`, `distance_m`, `ball_speed_kmh`, `difficulty` (0–10),
  `expected_success`, `under_pressure`, `end_x`, `end_y`, `opponents_bypassed`,
  `line_breaking`, and for through balls `receiver_run_speed_kmh`
- **shot**: `outcome` (goal / saved / blocked / off_target), `xg`, `xgot` (on-target shots),
  `shot_speed_kmh`, `distance_m`, `body_part`, `under_pressure`, `penalty`
- **aerial_duel**: winner as `player_id`, loser as `opponent_id`
- **physical_update** (every 60 s, like a GPS feed): per player cumulative `distance_m`,
  `running_m` (15–25 km/h), `sprinting_m` (over 25 km/h), `sprints`, `top_speed_kmh`
- **carry**: `distance_m`, `speed_kmh`, `end_x`, `end_y`
- **pressure**: `on_player_id`, `distance_m`
- **tackle**: `outcome` (won / lost), `opponent_id`
- **possession_change**: `reason` (interception, tackle, throw_in, goal_kick, ...)

## Realism check (average of 40 simulated matches)

| Stat (both teams) | Simulated | Typical top-flight match |
|---|---|---|
| Goals | 3.0 | about 2.8 |
| Passes | about 1,060 | 900–1,100 |
| Pass accuracy | 84% | 80–85% |
| Shots | 19 | 22–26 |
| xG | 2.9 | about 2.8 |
| Fouls / cards | 15 / 3 | about 20 / 4 |
| Corners | 9 | about 10 |
| Penalties | 0.35 | about 0.3 |
| Distance per outfield player (90 min) | 11.3 km | 10–11.5 km |
| Sprints per outfield player | 20 | 15–30 |
| Top sprint speed in the match | 35 km/h | 33–36 km/h |

## How it works

Each team has a formation shape that slides with the ball. On every touch the ball
carrier passes, carries, or shoots, with probabilities driven by space, pressure from
the nearest defender, player attributes, fatigue, and the team's tactics. Teams change
tactics during the match (pressing harder when chasing a game, dropping deeper to
protect a lead, surging after conceding), so the feed contains real tactical stories
for the AI to detect and explain. `ground_truth.json` records those changes so
detection can be checked.

All clubs, players, venues, and the competition are fictional.

---

# Stats engine (Interpret stage)

`engine/stats_engine.py` reads events one at a time, as they arrive live, and keeps the
whole match state up to date. Every number is computed by plain code, so it is exact and
repeatable; the AI agents later explain and narrate these numbers but never invent them.

```bash
python run_engine.py matches/match_00007              # instant
python run_engine.py matches/match_00007 --speed 60   # live, 1 match minute per second
```

It writes three files into the match folder:

- `insights.jsonl`: key moments, each with a factual `headline`, structured `data` (the
  evidence for *why* it matters), and `overlay` timing (`display_at`, `duration_s`, `slot`)
  so it can be rendered on top of a live feed
- `states.jsonl`: a snapshot every 60 seconds with team totals, last-5-minute stats,
  momentum, game phase, and the top-rated players
- `player_stats.json`: a full profile for every player who played (see below)

## What it tracks

- **Team**: goals, shots, shots on target, xG, big chances, passes and accuracy, final-third
  entries, possession (by time on the ball), pressures, tackles, interceptions, turnovers,
  fouls, cards, corners, saves
- **Player**: passes, accuracy, average pass difficulty, hard passes completed, key passes,
  assists, shots, xG, goals, tackles won, interceptions, pressures, saves, top speed, hardest
  shot, and a match rating

## Player profiles

`engine/player_stats.py` builds a stats-app style profile for every player, ranked against
everyone else who played:

- **Shooting**: goals, xG, xGOT, non-penalty xG, shots, shots on target, headed shots
- **Physical**: top speed, distance, running, sprinting, number of sprints
- **Passing**: assists, xA, successful passes and %, accurate long balls and %,
  line-breaking passes, chances created, big chances created
- **Possession**: successful dribbles and %, duels won and %, aerial duels won and %,
  touches, touches in the opposition box, dispossessed, fouls won
- **Defending**: defensive contributions, tackles, interceptions, blocked shots, fouls,
  recoveries, possession won in the final third, dribbled past, clearances, clean sheets,
  goals conceded and xG against while on the pitch (plus saves for goalkeepers)
- **Discipline**: yellow and red cards

```bash
python player_card.py matches/match_00007              # player of the match
python player_card.py matches/match_00007 "Keller"     # any player by name
python player_card.py matches/match_00007 --list       # everyone, by rating
```

## Insight types

| Priority | Types |
|---|---|
| 1 (must show) | `goal`, `red_card`, `half_time`, `full_time` |
| 2 (notable) | `momentum_shift`, `phase_change` (control vs chaos), `pressing_surge`, `pressing_drop`, `big_chance_missed`, `shot_speed` |
| 3 (nice to have) | `pass_quality`, `speed_alert`, `physical_milestone`, `player_milestone`, `yellow_card`, `substitution` |

Lower-priority insights are held back when something else was just shown, so the screen
never gets cluttered (about 33 insights per match, roughly one every 3 minutes).

## Patterns explained

- **Momentum**: share of attacking threat (xG, shots, final-third entries) over the last
  5 minutes. Fires when one team holds over 82% of it for a full minute.
- **Control vs chaos**: open-play turnovers per minute over the last 5 minutes. Chaos above
  2.2 per minute; control below 0.8 per minute while one team has over 68% of the ball.
- **Pressing surge / drop**: a team's pressures per minute over the last 5 minutes compared
  with its own match average (above 1.6× or below 0.55×).

Thresholds were calibrated on 40 simulated matches so each pattern only fires at the
extreme 5 to 10% of moments.

## Validation

The simulator logs every tactical change it makes (`ground_truth.json`). On 40 matches the
engine had never seen, it detected **79%** of them within 12 minutes (56 of 71), and every
goal was reported.

---

# AI agent team (Explain stage)

Built with **Microsoft Agent Framework** on a **Microsoft Foundry** model. Every moment the
stats engine detects goes through a team of specialised agents, coordinated by an
orchestrator that owns the shared match memory:

| Agent | Job |
|---|---|
| Tactical Analyst | Explains *why* the moment matters, using the match state and the storyline so far |
| Broadcast Narrator | Overlay caption (max 70 characters), TV commentary, a casual-fan version and an analyst version |
| Fact Checker | Rejects narration that contradicts the data |
| Localizer | Translates the narration into the chosen languages |
| Recap Writer | Half-time and full-time recaps for studio or streaming |
| Personalizer | Re-shapes a moment for one viewer: mode, language, favourite club or player, focus metric |
| Player Spotlight | A followed player's match so far, from their full stat profile |

## How the agents work together

1. The engine detects a moment (for example a pressing surge) with structured evidence.
2. The **Analyst** explains it, reading the last few moments from the shared storyline, so it can
   connect events ("their second big chance since switching to a high press").
3. The **Narrator** writes the on-screen text.
4. The orchestrator checks **every number** in the text against the source data. Then the **Fact
   Checker** reviews the claims. If either finds a problem, the Narrator gets the feedback and
   tries again.
5. The **Localizer** translates the approved text.
6. The moment joins the storyline, and later the **Recap Writer** uses the storyline for recaps.

Failures are expected in live production, so they're handled:

- Rate limits, timeouts and invalid JSON are retried with back-off.
- Narration that still fails the checks is replaced by the engine's factual headline, so the
  overlay is never empty and never wrong.
- Minor moments skip the Analyst and Fact Checker to save time and cost.
- Every agent call is recorded in `agent_trace.jsonl` (agent, status, attempt, milliseconds).

## Languages and local expressions

`--languages` takes any comma-separated codes, or `all` for 41 languages (French, Spanish,
Portuguese, German, Italian, Arabic, Chinese, Japanese, Korean, Hindi, Swahili, Yoruba, Hausa,
Bengali, Urdu, Amharic, Turkish, Russian, Thai, Vietnamese and more). Large sets are split into
parallel batches of 6 languages per Localizer call.

The Localizer adapts rather than translates word for word. `agents/phrasebook.py` gives it local
football expressions (for example *fermer la boutique*, *Bombenschuss*, *¡Vaya pepinazo!*,
*catenaccio*, *golaço*), each tied to a situation the stats engine can detect. Agents may only use
an expression when the moment matches it, so an idiom never implies something that didn't happen.
For languages without entries, the model uses local expressions it's sure of, under the same rule.

## Run it

```bash
pip install -r requirements.txt
python explain_match.py matches/match_00007 --offline --limit 5   # no Azure: rule-based stand-in
az login --use-device-code
bash setup_azure.sh                                              # one-time Azure setup, writes .env
python explain_match.py matches/match_00007 --limit 3 --languages fr
python explain_match.py matches/match_00007 --languages fr,es,pt,ar
```

Outputs in the match folder: `explained.jsonl` (each moment with explanation, narration,
translations, overlay timing and whether it was verified), `recaps.json`, and
`agent_trace.jsonl`.

## Tests

```bash
python -m pytest -q tests
```

The tests use fault-injecting stand-in models to prove that recovery works: invented numbers are
caught and retried, persistent failures fall back to the facts, fact-checker rejections are fed back,
rate-limit errors are retried, a dead agent still produces an overlay, and invalid JSON is retried.

---

# Web app (Render and Personalize stages)

`app/` is MatchLens, the live match screen.

```bash
uvicorn app.server:app --port 8000      # then open http://localhost:8000
```

In Codespaces, a pop-up offers **Open in Browser** when the server starts.

- **Pitch view:** players and ball move live from the tracking feed. A followed player gets a
  gold ring and name tag.
- **Overlays:** each moment lands in the slot the engine chose (full banner for goals and red
  cards, lower third for notable moments, corner badge for minor ones), coloured by team.
- **Panels:** Moments (the explained feed), Match (recaps, momentum chart, team numbers), Player
  (live stat card with ranks, plus an AI spotlight), Agents (each agent lighting up as it works,
  with a live log).
- **Viewer profile:** Fan or Analyst (different text, different stats), language (41), favourite
  club (moments from that club's point of view), followed player, and a pinned metric in the
  scoreboard.

The server picks a mode per match:

- **AI-explained replay:** the match has `explained.jsonl` from `explain_match.py`, so every
  moment shows instantly.
- **Live agents:** no precomputed file, but Azure is configured. The engine's headline shows
  first, then the agents' version replaces it a few seconds later.
- **Engine facts:** no Azure; shows the engine's factual headlines.

**New match** generates a brand-new synthetic match on the spot.

## Download the data

The Match tab has a **Download the data** panel, also available through the API:

| Download | Format | For |
|---|---|---|
| Player stats | `players.csv` | Every profile stat for every player (spreadsheets) |
| Team stats | `teams.csv` | Both teams side by side |
| Key moments | `moments.csv` | Engine headline, AI caption, fan and analyst text, fact-check result, evidence |
| Minute by minute | `timeline.csv` | Score, possession, xG, shots, pressing and momentum per minute |
| Event feed | `events.jsonl` | Every raw event, one JSON object per line (pipelines) |
| Everything | `.zip` | All of the above plus `metadata.json` and a `DATA_DICTIONARY.md` |

During a live match the files hold the stats so far; after it, the full match. Every table is
also available as `.json` or `.jsonl`:

```
GET /api/matches/{match}/export/{players|teams|moments|timeline|events}.{csv|json|jsonl}
GET /api/matches/{match}/export.zip
GET /api/session/{session}/export/{table}.{format}      # stats so far in a live session
```

CSV files are UTF-8 with a byte-order mark, so Excel shows accented names correctly.

## Deploy to Azure

`deploy_azure.sh` publishes the app to **Azure Container Apps** from Azure Cloud Shell. It stores
the API key as a secret and prints the public link.
