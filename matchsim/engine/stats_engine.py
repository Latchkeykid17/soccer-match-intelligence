"""
Match intelligence engine: the Interpret stage.

Feed it match events one at a time, as they arrive live. It keeps the full
match state up to date and returns:

  * insights  - key moments and patterns worth showing on screen, each with
                structured evidence (for the AI to explain) and overlay timing
  * states    - a snapshot of the match every 60 seconds of match time

All numbers here come from plain code, so they are exact and repeatable.
The AI agents only explain and narrate them; they never invent stats.

Usage:
    engine = MatchEngine(metadata)
    for event in feed:
        for item in engine.ingest(event):
            ...  # item["kind"] is "insight" or "state"
"""
from collections import deque

from .player_stats import PlayerStatsTracker

PITCH_L = 105.0
FINAL_THIRD_X = 70.0
WINDOW_S = 300            # rolling window for "last 5 minutes" stats
STATE_EVERY_S = 60
PATTERN_EVERY_S = 30
SIDES = ("home", "away")

# Events after which the acting team has the ball
POSSESSION_EVENTS = {"pass", "carry", "dribble", "shot", "kick_off", "throw_in",
                     "goal_kick", "corner", "free_kick", "possession_change"}
OPEN_PLAY_TURNOVERS = {"interception", "tackle", "block", "loose_ball", "clearance_recovered"}

# Pattern thresholds (calibrated on simulated matches; see README)
# Each pattern must also hold for two checks in a row (a full minute) before it fires.
MOMENTUM_ON = 0.82        # share of attacking threat in the last 5 min (top ~10% of moments)
MOMENTUM_RESET = 0.62     # below this, nobody is dominating any more
CHAOS_TURNOVERS_PER_MIN = 2.2    # top ~5%
CONTROL_TURNOVERS_PER_MIN = 0.8  # bottom ~12%
CONTROL_POSSESSION = 0.68        # top ~20%
PRESS_SURGE_RATIO = 1.6          # pressing vs the team's own match average
PRESS_DROP_RATIO = 0.55
SPRINT_KMH = 33.0
ROCKET_SHOT_KMH = 110.0
BIG_CHANCE_XG = 0.30
HARD_PASS_DIFFICULTY = 7.0
PASS_MILESTONES = (50, 75, 100)

# Overlay defaults by priority: 1 = must show, 2 = notable, 3 = nice to have
OVERLAY = {1: ("full_banner", 12), 2: ("lower_third", 9), 3: ("corner_badge", 6)}
MIN_GAP_S = {2: 30, 3: 60}  # minimum seconds since the last shown insight


class Rolling:
    """Sum of values in the last `window` seconds of match time."""

    def __init__(self, window=WINDOW_S):
        self.window = window
        self.items = deque()

    def add(self, t, value=1.0):
        self.items.append((t, value))

    def total(self, now):
        while self.items and self.items[0][0] < now - self.window:
            self.items.popleft()
        return sum(v for _, v in self.items)

    def clear(self):
        self.items.clear()


def other(side):
    return "away" if side == "home" else "home"


def attack_x(x, side, period):
    """x in the team's own attacking frame (0 = own goal, 105 = opponent's goal)."""
    attacks_right = (side == "home") == (period == 1)
    return x if attacks_right else PITCH_L - x


def new_team_stats():
    return {"goals": 0, "shots": 0, "shots_on_target": 0, "xg": 0.0, "big_chances": 0,
            "passes": 0, "passes_completed": 0, "final_third_entries": 0,
            "pressures": 0, "tackles": 0, "tackles_won": 0, "interceptions": 0,
            "turnovers": 0, "fouls": 0, "yellow_cards": 0, "red_cards": 0,
            "corners": 0, "saves": 0, "possession_s": 0.0}


class MatchEngine:
    def __init__(self, metadata):
        self.meta = metadata
        self.match_id = metadata["match_id"]
        self.team_names = {s: metadata[s]["name"] for s in SIDES}
        self.players = {}
        for side in SIDES:
            starters = {p["id"] for p in metadata[side]["starting_xi"]}
            for p in metadata[side]["starting_xi"] + metadata[side]["bench"]:
                self.players[p["id"]] = {
                    "id": p["id"], "name": p["name"], "number": p["number"],
                    "position": p["position"], "team": side, "on_pitch": p["id"] in starters,
                    "passes": 0, "passes_completed": 0, "difficulty_sum": 0.0,
                    "hard_passes_completed": 0, "key_passes": 0, "assists": 0,
                    "shots": 0, "xg": 0.0, "goals": 0, "tackles_won": 0,
                    "interceptions": 0, "pressures": 0, "saves": 0, "fouls": 0,
                    "top_speed_kmh": 0.0, "max_shot_speed_kmh": 0.0}
        self.pstats = PlayerStatsTracker(metadata)
        self.distance_milestone_done = False
        self.teams = {s: new_team_stats() for s in SIDES}
        roll_keys = ("xg", "shots", "final_third", "pressures", "passes",
                     "passes_completed", "possession_s", "turnovers")
        self.roll = {s: {k: Rolling() for k in roll_keys} for s in SIDES}

        self.t = 0.0
        self.period = 1
        self.period_start_t = 0.0
        self.clock = "1'"
        self.score = {"home": 0, "away": 0}
        self.possessor = None
        self.last_t = 0.0
        self.possessions = {}
        self.last_pass_to = {}
        self.last_conceded_t = {s: None for s in SIDES}

        self.next_state_t = STATE_EVERY_S
        self.next_pattern_t = PATTERN_EVERY_S
        self.next_insight_id = 1
        self.last_shown_t = -1e9
        self.cooldowns = {}
        self.momentum_leader = None
        self.phase = "balanced"
        self.speed_record = 0.0
        self.suppressed = 0
        self.pending = {}

    # ------------------------------------------------------------------ input
    def ingest(self, ev):
        out = []
        t = ev["match_seconds"]
        while t >= self.next_state_t:
            out.append(self.state(self.next_state_t))
            self.next_state_t += STATE_EVERY_S

        self._count_possession_time(t)
        self.t = t
        self.period = ev.get("period", self.period)
        self.clock = ev.get("clock", self.clock)
        self.score = ev.get("score", self.score)

        pid = ev.get("possession_id")
        if pid is not None and pid not in self.possessions and ev.get("team"):
            self.possessions[pid] = {"team": ev["team"], "start_t": t, "passes_completed": 0}

        self.pstats.ingest(ev, self.possessor)
        handler = getattr(self, "_on_" + ev["type"], None)
        if handler:
            out += [i for i in (handler(ev) or []) if i]
        if ev.get("team") and ev["type"] in POSSESSION_EVENTS:
            self.possessor = ev["team"]

        if t >= self.next_pattern_t:
            self.next_pattern_t = t + PATTERN_EVERY_S
            out += [i for i in self._detect_patterns() if i]
        return out

    def _count_possession_time(self, t):
        dt = min(max(0.0, t - self.last_t), 15.0)  # cap: ignore long stoppages
        if self.possessor and dt > 0:
            self.teams[self.possessor]["possession_s"] += dt
            self.roll[self.possessor]["possession_s"].add(t, dt)
        self.last_t = t

    # ------------------------------------------------------------ insights
    def _insight(self, itype, priority, headline, data, team=None, player_id=None, cooldown=None):
        """Create an insight, unless it is rate-limited or on cooldown."""
        if cooldown:
            key, seconds = cooldown
            if self.t - self.cooldowns.get(key, -1e9) < seconds:
                return None
        if priority > 1 and self.t - self.last_shown_t < MIN_GAP_S[priority]:
            self.suppressed += 1
            return None
        if cooldown:
            self.cooldowns[cooldown[0]] = self.t
        self.last_shown_t = self.t
        slot, duration = OVERLAY[priority]
        player = self.players.get(player_id) if player_id else None
        ins = {"kind": "insight", "insight_id": self.next_insight_id, "match_id": self.match_id,
               "type": itype, "priority": priority, "match_seconds": round(self.t, 1),
               "clock": self.clock, "period": self.period, "score": dict(self.score),
               "team": team, "team_name": self.team_names.get(team),
               "player_id": player_id, "player_name": player["name"] if player else None,
               "headline": headline, "data": data,
               "overlay": {"display_at": round(self.t, 1), "duration_s": duration, "slot": slot}}
        self.next_insight_id += 1
        return ins

    # ------------------------------------------------------------ handlers
    def _on_period_start(self, ev):
        self.period_start_t = self.t
        for side in SIDES:
            for r in self.roll[side].values():
                r.clear()
        self.momentum_leader = None
        self.phase = "balanced"

    def _on_pass(self, ev):
        side, p = ev["team"], self.players[ev["player_id"]]
        team, roll = self.teams[side], self.roll[side]
        team["passes"] += 1
        p["passes"] += 1
        p["difficulty_sum"] += ev["difficulty"]
        roll["passes"].add(self.t)
        out = [self._check_final_third(ev)]
        if ev.get("receiver_run_speed_kmh"):
            out.append(self._speed(ev["receiver_id"], ev["receiver_run_speed_kmh"], "run onto a through ball"))
        if ev["outcome"] != "complete":
            return out
        team["passes_completed"] += 1
        p["passes_completed"] += 1
        roll["passes_completed"].add(self.t)
        self.last_pass_to[ev["receiver_id"]] = ev
        poss = self.possessions.get(ev.get("possession_id"))
        if poss:
            poss["passes_completed"] += 1
        if ev["difficulty"] >= HARD_PASS_DIFFICULTY:
            p["hard_passes_completed"] += 1
            out.append(self._insight(
                "pass_quality", 3,
                f"{p['name']} completes a {ev['pass_type'].replace('_', ' ')} rated {ev['difficulty']}/10",
                {"pass_type": ev["pass_type"], "difficulty": ev["difficulty"],
                 "expected_success": ev["expected_success"], "distance_m": ev["distance_m"],
                 "ball_speed_kmh": ev["ball_speed_kmh"], "under_pressure": ev["under_pressure"],
                 "receiver_name": ev.get("receiver_name")},
                team=side, player_id=p["id"]))
        if p["passes_completed"] in PASS_MILESTONES:
            out.append(self._insight(
                "player_milestone", 3,
                f"{p['name']}: {p['passes_completed']} completed passes",
                {"milestone": "passes_completed", "value": p["passes_completed"],
                 "pass_accuracy": round(p["passes_completed"] / p["passes"], 3),
                 "team_rank": self._rank_in_team(p, "passes_completed")},
                team=side, player_id=p["id"]))
        return out

    def _on_carry(self, ev):
        return [self._check_final_third(ev), self._speed(ev["player_id"], ev["speed_kmh"], "carry")]

    def _check_final_third(self, ev):
        if "end_x" not in ev or ev.get("outcome", "complete") != "complete":
            return None
        side = ev["team"]
        start = attack_x(ev["x"], side, self.period)
        end = attack_x(ev["end_x"], side, self.period)
        if start < FINAL_THIRD_X <= end:
            self.teams[side]["final_third_entries"] += 1
            self.roll[side]["final_third"].add(self.t)
        return None

    def _speed(self, player_id, kmh, how):
        p = self.players.get(player_id)
        if not p:
            return None
        p["top_speed_kmh"] = max(p["top_speed_kmh"], kmh)
        if kmh >= SPRINT_KMH and kmh > self.speed_record:
            previous = self.speed_record
            self.speed_record = kmh
            return self._insight(
                "speed_alert", 3, f"{p['name']} hits {kmh:.1f} km/h, fastest of the match",
                {"speed_kmh": kmh, "action": how, "previous_record_kmh": round(previous, 1) or None},
                team=p["team"], player_id=player_id)
        return None

    def _on_pressure(self, ev):
        side = ev["team"]
        self.teams[side]["pressures"] += 1
        self.players[ev["player_id"]]["pressures"] += 1
        self.roll[side]["pressures"].add(self.t)

    def _on_tackle(self, ev):
        side = ev["team"]
        self.teams[side]["tackles"] += 1
        if ev["outcome"] == "won":
            self.teams[side]["tackles_won"] += 1
            self.players[ev["player_id"]]["tackles_won"] += 1

    def _on_interception(self, ev):
        self.teams[ev["team"]]["interceptions"] += 1
        self.players[ev["player_id"]]["interceptions"] += 1

    def _on_possession_change(self, ev):
        self.possessions[ev["possession_id"]] = {"team": ev["team"], "start_t": self.t, "passes_completed": 0}
        if ev["reason"] in OPEN_PLAY_TURNOVERS and self.possessor and self.possessor != ev["team"]:
            loser = self.possessor
            self.teams[loser]["turnovers"] += 1
            self.roll[loser]["turnovers"].add(self.t)

    def _on_shot(self, ev):
        side, p = ev["team"], self.players[ev["player_id"]]
        team = self.teams[side]
        team["shots"] += 1
        team["xg"] += ev["xg"]
        p["shots"] += 1
        p["xg"] += ev["xg"]
        p["max_shot_speed_kmh"] = max(p["max_shot_speed_kmh"], ev["shot_speed_kmh"])
        self.roll[side]["shots"].add(self.t)
        self.roll[side]["xg"].add(self.t, ev["xg"])
        if ev["outcome"] in ("goal", "saved"):
            team["shots_on_target"] += 1
        if ev["xg"] >= BIG_CHANCE_XG:
            team["big_chances"] += 1

        key = self.last_pass_to.get(p["id"])
        if key and key.get("possession_id") == ev.get("possession_id"):
            self.players[key["player_id"]]["key_passes"] += 1
        self.last_shot = ev

        if ev["outcome"] == "goal":
            return None  # the goal event gets the big insight
        if ev["xg"] >= BIG_CHANCE_XG:
            return [self._insight(
                "big_chance_missed", 2,
                f"Big chance for {p['name']} ({ev['outcome'].replace('_', ' ')}), xG {ev['xg']:.2f}",
                {"xg": ev["xg"], "outcome": ev["outcome"], "distance_m": ev["distance_m"],
                 "under_pressure": ev["under_pressure"], "body_part": ev["body_part"],
                 "shot_speed_kmh": ev["shot_speed_kmh"],
                 "team_xg_total": round(team["xg"], 2), "team_goals": team["goals"]},
                team=side, player_id=p["id"])]
        if ev["shot_speed_kmh"] >= ROCKET_SHOT_KMH:
            return [self._insight(
                "shot_speed", 2, f"{p['name']} strikes it at {ev['shot_speed_kmh']:.0f} km/h",
                {"shot_speed_kmh": ev["shot_speed_kmh"], "outcome": ev["outcome"],
                 "distance_m": ev["distance_m"], "xg": ev["xg"]},
                team=side, player_id=p["id"])]
        return None

    def _on_goal(self, ev):
        side, p = ev["team"], self.players[ev["player_id"]]
        opp = other(side)
        self.teams[side]["goals"] += 1
        p["goals"] += 1
        self.last_conceded_t[opp] = self.t
        scored, against = ev["score"][side], ev["score"][opp]
        if scored - 1 == 0 and against == 0:
            effect = "opener"
        elif scored == against:
            effect = "equaliser"
        elif scored - 1 == against:
            effect = "go_ahead"
        elif scored < against:
            effect = "pulls_one_back"
        else:
            effect = "extends_lead"

        assist = None
        if ev.get("assist_id"):
            a = self.players[ev["assist_id"]]
            a["assists"] += 1
            last = self.last_pass_to.get(p["id"], {})
            assist = {"player_id": a["id"], "player_name": a["name"],
                      "pass_type": last.get("pass_type"), "pass_difficulty": last.get("difficulty")}

        poss = self.possessions.get(ev.get("possession_id"), {})
        shot = getattr(self, "last_shot", {}) or {}
        share = self._momentum_share()
        scorer_share = share if side == "home" else 1 - share
        goals_so_far = p["goals"]
        return [self._insight(
            "goal", 1,
            f"GOAL: {p['name']} ({self.team_names[side]}). "
            f"{self.team_names['home']} {ev['score']['home']}-{ev['score']['away']} {self.team_names['away']}",
            {"scoreline_effect": effect, "xg": shot.get("xg"), "shot_speed_kmh": shot.get("shot_speed_kmh"),
             "distance_m": shot.get("distance_m"), "body_part": shot.get("body_part"),
             "assist": assist, "build_up_passes": poss.get("passes_completed"),
             "build_up_seconds": round(self.t - poss["start_t"], 1) if poss else None,
             "scorer_goals_this_match": goals_so_far,
             "milestone": {2: "brace", 3: "hat_trick"}.get(goals_so_far),
             "scoring_team_momentum_share": round(scorer_share, 2),
             "against_run_of_play": scorer_share < 0.35,
             "xg_match_total": {s: round(self.teams[s]["xg"], 2) for s in SIDES}},
            team=side, player_id=p["id"])]

    def _on_save(self, ev):
        self.teams[ev["team"]]["saves"] += 1
        self.players[ev["player_id"]]["saves"] += 1

    def _on_foul(self, ev):
        self.teams[ev["team"]]["fouls"] += 1
        self.players[ev["player_id"]]["fouls"] += 1

    def _on_corner(self, ev):
        self.teams[ev["team"]]["corners"] += 1

    def _on_card(self, ev):
        side, p = ev["team"], self.players[ev["player_id"]]
        if ev["card"] == "red":
            self.teams[side]["red_cards"] += 1
            p["on_pitch"] = False
            return [self._insight(
                "red_card", 1, f"RED CARD: {p['name']} ({self.team_names[side]}) is sent off",
                {"reason": ev.get("reason", "straight_red"), "minutes_remaining": max(0, 90 - ev["minute"]),
                 "team_down_to": 11 - self.teams[side]["red_cards"]},
                team=side, player_id=p["id"])]
        self.teams[side]["yellow_cards"] += 1
        return [self._insight("yellow_card", 3, f"Yellow card: {p['name']}",
                              {"team_yellows": self.teams[side]["yellow_cards"]},
                              team=side, player_id=p["id"])]

    def _on_substitution(self, ev):
        side = ev["team"]
        off, on = self.players[ev["player_off_id"]], self.players[ev["player_on_id"]]
        off["on_pitch"], on["on_pitch"] = False, True
        return [self._insight(
            "substitution", 3, f"{on['name']} on for {off['name']}",
            {"player_off": self.player_summary(off), "player_on": {"id": on["id"], "name": on["name"],
                                                                   "position": on["position"]},
             "score_state": self._score_state(side)},
            team=side, player_id=on["id"])]

    def _on_period_end(self, ev):
        return [self._summary("half_time" if self.period == 1 else "full_time")]

    def _on_match_end(self, ev):
        return None

    def _on_physical_update(self, ev):
        out = []
        for row in ev["players"]:
            out.append(self._speed(row["player_id"], row["top_speed_kmh"], "sprint"))
        if not self.distance_milestone_done:
            leader = max(ev["players"], key=lambda r: r["distance_m"], default=None)
            if leader and leader["distance_m"] >= 10000:
                self.distance_milestone_done = True
                p = self.players[leader["player_id"]]
                out.append(self._insight(
                    "physical_milestone", 3, f"{p['name']} is the first to cover 10 km",
                    {"distance_km": round(leader["distance_m"] / 1000, 2),
                     "sprints": leader["sprints"], "sprinting_km": round(leader["sprinting_m"] / 1000, 2),
                     "top_speed_kmh": leader["top_speed_kmh"]},
                    team=p["team"], player_id=p["id"]))
        return out

    # ------------------------------------------------------------ patterns
    def _threat(self, side):
        r = self.roll[side]
        return 8 * r["xg"].total(self.t) + r["shots"].total(self.t) + 0.5 * r["final_third"].total(self.t)

    def _momentum_share(self):
        h, a = self._threat("home"), self._threat("away")
        return (h + 1) / (h + a + 2)

    def _recent_possession(self, side):
        h = self.roll["home"]["possession_s"].total(self.t)
        a = self.roll["away"]["possession_s"].total(self.t)
        mine = h if side == "home" else a
        return mine / (h + a) if h + a else 0.5

    def _score_state(self, side):
        diff = self.score[side] - self.score[other(side)]
        return "leading" if diff > 0 else "trailing" if diff < 0 else "level"

    def _sustained(self, key, value):
        """True when the same non-empty value was also seen at the previous check."""
        previous = self.pending.get(key)
        self.pending[key] = value
        return value is not None and value == previous

    def _detect_patterns(self):
        if self.t - self.period_start_t < WINDOW_S:
            return []
        out = []
        window_min = WINDOW_S / 60

        # Momentum: who is creating the threat right now
        share = self._momentum_share()
        leader = "home" if share >= MOMENTUM_ON else "away" if share <= 1 - MOMENTUM_ON else None
        if self._sustained("momentum", leader) and leader != self.momentum_leader:
            out.append(self._insight(
                "momentum_shift", 2, f"{self.team_names[leader]} take control of the game",
                {"threat_share": round(share if leader == "home" else 1 - share, 2),
                 "last_5_min": {s: {"xg": round(self.roll[s]["xg"].total(self.t), 2),
                                    "shots": int(self.roll[s]["shots"].total(self.t)),
                                    "final_third_entries": int(self.roll[s]["final_third"].total(self.t)),
                                    "possession": round(self._recent_possession(s), 2)} for s in SIDES},
                 "previous_leader": self.momentum_leader, "score_state": self._score_state(leader)},
                team=leader, cooldown=("momentum", 420)))
            self.momentum_leader = leader
        elif not leader and max(share, 1 - share) < MOMENTUM_RESET:
            self.momentum_leader = None

        # Control vs chaos: how often the ball changes hands in open play
        turnovers = sum(self.roll[s]["turnovers"].total(self.t) for s in SIDES) / window_min
        pos_home = self._recent_possession("home")
        dominant = "home" if pos_home >= CONTROL_POSSESSION else "away" if pos_home <= 1 - CONTROL_POSSESSION else None
        if turnovers >= CHAOS_TURNOVERS_PER_MIN:
            phase = "chaos"
        elif turnovers <= CONTROL_TURNOVERS_PER_MIN and dominant:
            phase = f"control_{dominant}"
        else:
            phase = "balanced"
        confirmed = self._sustained("phase", phase)
        if confirmed and phase != self.phase and phase != "balanced":
            team = dominant if phase.startswith("control") else None
            headline = ("The game turns chaotic: the ball keeps changing hands" if phase == "chaos"
                        else f"{self.team_names[team]} settle into control")
            out.append(self._insight(
                "phase_change", 2, headline,
                {"phase": "chaos" if phase == "chaos" else "control",
                 "turnovers_per_min": round(turnovers, 2),
                 "pass_accuracy_last_5": {s: self._recent_accuracy(s) for s in SIDES},
                 "possession_last_5": {s: round(self._recent_possession(s), 2) for s in SIDES},
                 "tempo_passes_per_min": {s: round(self.roll[s]["passes"].total(self.t) / window_min, 1)
                                          for s in SIDES}},
                team=team, cooldown=("phase", 420)))
        if confirmed or phase == "balanced":
            self.phase = phase

        # Pressing: a team suddenly presses much more (or less) than its own average
        minutes = max(1.0, self.t / 60)
        if self.t > 900:
            for side in SIDES:
                avg = self.teams[side]["pressures"] / minutes
                now = self.roll[side]["pressures"].total(self.t) / window_min
                data = {"pressures_per_min_last_5": round(now, 2), "match_average": round(avg, 2),
                        "score_state": self._score_state(side),
                        "minutes_since_conceding": (round((self.t - self.last_conceded_t[side]) / 60, 1)
                                                    if self.last_conceded_t[side] is not None else None),
                        "opponent_pass_accuracy_last_5": self._recent_accuracy(other(side))}
                trend = ("surge" if now >= avg * PRESS_SURGE_RATIO and now - avg >= 0.8
                         else "drop" if now <= avg * PRESS_DROP_RATIO and avg - now >= 0.8 else None)
                if not self._sustained(f"press-{side}", trend):
                    continue
                if trend == "surge":
                    out.append(self._insight(
                        "pressing_surge", 2, f"{self.team_names[side]} crank up the press",
                        data, team=side, cooldown=(f"press-{side}", 600)))
                else:
                    out.append(self._insight(
                        "pressing_drop", 2, f"{self.team_names[side]} drop off and sit deeper",
                        data, team=side, cooldown=(f"press-{side}", 600)))
        return out

    def _recent_accuracy(self, side):
        att = self.roll[side]["passes"].total(self.t)
        return round(self.roll[side]["passes_completed"].total(self.t) / att, 3) if att else None

    # ------------------------------------------------------------ summaries
    def rating(self, p):
        return self.pstats.rating(self.pstats.p[p["id"]])

    def player_summary(self, p):
        return {"id": p["id"], "name": p["name"], "team": p["team"], "position": p["position"],
                "rating": self.rating(p), "goals": p["goals"], "assists": p["assists"],
                "shots": p["shots"], "xg": round(p["xg"], 2), "passes": p["passes"],
                "pass_accuracy": round(p["passes_completed"] / p["passes"], 3) if p["passes"] else None,
                "avg_pass_difficulty": round(p["difficulty_sum"] / p["passes"], 1) if p["passes"] else None,
                "key_passes": p["key_passes"], "tackles_won": p["tackles_won"],
                "interceptions": p["interceptions"], "pressures": p["pressures"], "saves": p["saves"],
                "minutes_played": self.pstats.minutes(self.pstats.p[p["id"]]),
                "distance_km": round(self.pstats.p[p["id"]]["distance_m"] / 1000, 2),
                "sprints": self.pstats.p[p["id"]]["sprints"],
                "top_speed_kmh": round(max(p["top_speed_kmh"], self.pstats.p[p["id"]]["top_speed_kmh"]), 1) or None,
                "max_shot_speed_kmh": round(p["max_shot_speed_kmh"], 1) or None}

    def _rank_in_team(self, p, key):
        mates = sorted((q for q in self.players.values() if q["team"] == p["team"]),
                       key=lambda q: -q[key])
        return 1 + [q["id"] for q in mates].index(p["id"])

    def team_summary(self, side):
        s = dict(self.teams[side])
        total = self.teams["home"]["possession_s"] + self.teams["away"]["possession_s"]
        s["possession"] = round(s.pop("possession_s") / total, 3) if total else 0.5
        s["pass_accuracy"] = round(s["passes_completed"] / s["passes"], 3) if s["passes"] else None
        s["xg"] = round(s["xg"], 2)
        s.update(self.pstats.team_totals(side))
        return s

    def final_report(self):
        """Full-time report: team totals and a complete profile for every player who played."""
        return {"match_id": self.match_id, "score": dict(self.score),
                "teams": {s: {"name": self.team_names[s], **self.team_summary(s)} for s in SIDES},
                "players": self.pstats.all_profiles()}

    def top_players(self, n=3, side=None):
        pool = [p for p in self.players.values()
                if (p["passes"] or p["shots"] or p["saves"]) and (side is None or p["team"] == side)]
        return [self.player_summary(p) for p in sorted(pool, key=self.rating, reverse=True)[:n]]

    def _summary(self, stage):
        teams = {s: self.team_summary(s) for s in SIDES}
        score_leader = ("home" if self.score["home"] > self.score["away"]
                        else "away" if self.score["away"] > self.score["home"] else None)
        xg_diff = teams["home"]["xg"] - teams["away"]["xg"]
        xg_leader = "home" if xg_diff > 0.5 else "away" if xg_diff < -0.5 else None
        top = self.top_players(1)
        return self._insight(
            stage, 1,
            f"{'Half-time' if stage == 'half_time' else 'Full-time'}: "
            f"{self.team_names['home']} {self.score['home']}-{self.score['away']} {self.team_names['away']}",
            {"teams": teams, "xg_tells_different_story": bool(xg_leader and xg_leader != score_leader),
             "xg_leader": xg_leader, "score_leader": score_leader,
             "top_players": self.top_players(3),
             "player_of_the_match": top[0] if top and stage == "full_time" else None})

    def state(self, at=None):
        at = self.t if at is None else at
        clock_t, self.t = self.t, at
        share = self._momentum_share()
        snap = {"kind": "state", "match_id": self.match_id, "match_seconds": at,
                "minute": int(at // 60), "period": self.period, "score": dict(self.score),
                "teams": {s: self.team_summary(s) for s in SIDES},
                "last_5_min": {s: {"xg": round(self.roll[s]["xg"].total(at), 2),
                                   "shots": int(self.roll[s]["shots"].total(at)),
                                   "pressures": int(self.roll[s]["pressures"].total(at)),
                                   "possession": round(self._recent_possession(s), 2),
                                   "pass_accuracy": self._recent_accuracy(s)} for s in SIDES},
                "momentum_home_share": round(share, 3), "phase": self.phase,
                "top_players": self.top_players(3)}
        self.t = clock_t
        return snap
