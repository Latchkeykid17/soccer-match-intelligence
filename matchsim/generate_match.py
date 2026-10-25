"""
Synthetic football match generator.

Simulates a full 90-minute match between two fictional clubs and writes:
  metadata.json      teams, players, venue, competition (all fictional)
  events.jsonl       one event per line: passes, carries, shots, tackles,
                     interceptions, pressure, possession changes, fouls,
                     cards, set pieces, substitutions, goals, period markers
  tracking.jsonl     positions of every player + the ball every 5 seconds
  ground_truth.json  final score and the tactical changes the simulator made
                     (useful to check whether the AI detects them)

Coordinates are metres on a 105 x 68 pitch, x from 0 (left goal line) to 105
(right goal line). The home team attacks to the right in the first half.

Usage:
  python generate_match.py --seed 7 --out matches/match_007
"""
import argparse
import json
import math
import os
import random
from dataclasses import dataclass, field

PITCH_L, PITCH_W = 105.0, 68.0
GOAL_Y = PITCH_W / 2
SNAPSHOT_EVERY = 5.0

FIRST_NAMES = ["Liam", "Mateo", "Kofi", "Jonas", "Rafael", "Theo", "Yusuf", "Arjun", "Luca",
               "Niko", "Samir", "Emeka", "Hugo", "Diego", "Tomas", "Kenji", "Oscar", "Adam",
               "Malik", "Felix", "Ivan", "Marco", "Noah", "Elias", "Bruno", "Andre", "Leon",
               "Omar", "Victor", "Caleb", "Ruben", "Jamal", "Sven", "Pablo", "Dario", "Kwame"]
LAST_NAMES = ["Okafor", "Lindqvist", "Moreau", "Castellano", "Brennan", "Haddad", "Novak",
              "Achebe", "Ferreira", "Kowalski", "Tanaka", "Mendes", "Dubois", "Varga",
              "Osei", "Romero", "Kalinin", "Petrov", "Sorensen", "Adeyemi", "Rossi", "Silva",
              "Mbeki", "Larsen", "Navarro", "Keller", "Fontaine", "Duarte", "Bianchi",
              "Holm", "Quinn", "Abara", "Meyer", "Costa", "Ibarra", "Nakamura"]

CLUBS = [
    ("Harbour City FC", "HCF", "#1E5AA8", "#FFFFFF"),
    ("Northgate Athletic", "NGA", "#C8102E", "#1A1A1A"),
    ("Riverside Rovers", "RIV", "#0B7A3B", "#F2C200"),
    ("Kingsbridge United", "KBU", "#6A1B9A", "#FFFFFF"),
    ("Westmoor Town", "WMT", "#F57C00", "#212121"),
    ("Ashford Albion", "ASA", "#00838F", "#FFFFFF"),
]

# Base shapes in the team's own frame (attacking towards x = 105), ball at halfway.
FORMATIONS = {
    "4-3-3": [("GK", 6, 34), ("RB", 36, 60), ("CB", 27, 44), ("CB", 27, 24), ("LB", 36, 8),
              ("DM", 42, 34), ("CM", 52, 47), ("CM", 52, 21), ("RW", 68, 61), ("ST", 74, 34),
              ("LW", 68, 7)],
    "4-4-2": [("GK", 6, 34), ("RB", 35, 60), ("CB", 26, 43), ("CB", 26, 25), ("LB", 35, 8),
              ("RM", 56, 59), ("CM", 47, 42), ("CM", 47, 26), ("LM", 56, 9), ("ST", 71, 40),
              ("ST", 71, 28)],
    "4-2-3-1": [("GK", 6, 34), ("RB", 36, 60), ("CB", 26, 44), ("CB", 26, 24), ("LB", 36, 8),
                ("DM", 43, 41), ("DM", 43, 27), ("AM", 60, 34), ("RW", 64, 58), ("LW", 64, 10),
                ("ST", 75, 34)],
}
BENCH_ROLES = ["GK", "CB", "CM", "CM", "RW", "ST"]


def o_press_now(match, team):
    return match.tactics(team, False)[0] * 0.3


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


@dataclass
class Player:
    id: str
    name: str
    number: int
    role: str
    bx: float
    by: float
    passing: int
    finishing: int
    pace: int
    tackling: int
    dribbling: int
    stamina: int
    on_pitch: bool = True
    minutes_from: float = 0.0
    yellow: int = 0
    distance_m: float = 0.0
    running_m: float = 0.0      # 15-25 km/h
    sprint_m: float = 0.0       # above 25 km/h
    sprints: int = 0
    sprinting: bool = False
    top_speed_kmh: float = 0.0
    x: float = 0.0  # last absolute position
    y: float = 0.0

    def top_capacity(self):
        """Fastest this player can run, from the pace attribute (29.5-36.5 km/h)."""
        return 29.5 + (self.pace - 40) / 55 * 7

    def form(self, match_s):
        """Skill multiplier that drops with fatigue."""
        played = max(0.0, match_s - self.minutes_from) / 5400
        return 1 - 0.12 * played * (1 - self.stamina / 100)

    def public(self):
        return {"id": self.id, "name": self.name, "number": self.number, "position": self.role,
                "attributes": {"passing": self.passing, "finishing": self.finishing,
                               "pace": self.pace, "tackling": self.tackling,
                               "dribbling": self.dribbling, "stamina": self.stamina}}


@dataclass
class Team:
    side: str
    name: str
    short: str
    colours: tuple
    formation: str
    players: list
    bench: list
    press: float
    line: float
    directness: float
    goals: int = 0
    subs_left: int = 3
    sub_minutes: list = field(default_factory=list)

    def active(self):
        return [p for p in self.players if p.on_pitch]


def make_player(rng, team_short, n, role, bx, by, used_names):
    while True:
        name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
        if name not in used_names:
            used_names.add(name)
            break
    def attr(base, spread=10):
        return int(clamp(rng.gauss(base, spread), 40, 95))
    is_att = role in ("ST", "RW", "LW", "AM", "RM", "LM")
    is_def = role in ("CB", "RB", "LB", "DM", "GK")
    return Player(
        id=f"{team_short}-{n}", name=name, number=n, role=role, bx=bx, by=by,
        passing=attr(72 if role in ("CM", "DM", "AM") else 66),
        finishing=attr(74 if is_att else 50),
        pace=attr(78 if role in ("RW", "LW", "RB", "LB", "ST") else 66),
        tackling=attr(74 if is_def and role != "GK" else 52),
        dribbling=attr(75 if is_att else 60),
        stamina=attr(76),
    )


def make_team(rng, side, club, used_names):
    name, short, c1, c2 = club
    formation = rng.choice(list(FORMATIONS))
    numbers = rng.sample(range(2, 40), 20)
    players = []
    for i, (role, bx, by) in enumerate(FORMATIONS[formation]):
        num = 1 if role == "GK" else numbers[i]
        players.append(make_player(rng, short, num, role, bx, by, used_names))
    bench = []
    for j, role in enumerate(BENCH_ROLES):
        bench.append(make_player(rng, short, numbers[11 + j], role, 0, 0, used_names))
    return Team(side=side, name=name, short=short, colours=(c1, c2), formation=formation,
                players=players, bench=bench, press=rng.uniform(0.35, 0.65),
                line=rng.uniform(0, 7), directness=rng.uniform(0.08, 0.3),
                sub_minutes=sorted(rng.uniform(55, 85) for _ in range(3)))


class Match:
    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.seed = seed
        used = set()
        home_club, away_club = self.rng.sample(CLUBS, 2)
        self.home = make_team(self.rng, "home", home_club, used)
        self.away = make_team(self.rng, "away", away_club, used)
        self.match_id = f"SYN-{seed:05d}"
        self.events, self.tracking, self.tactics_log = [], [], []
        self.t = 0.0          # continuous match seconds
        self.period = 1
        self.period_start = 0.0
        self.next_snapshot = 0.0
        self.possession_id = 0
        self.eid = 0
        self.surges = {}      # team side -> match_s when a temporary surge ends

    # ---------- geometry ----------
    def opp(self, team):
        return self.away if team is self.home else self.home

    def attacks_right(self, team):
        return (team is self.home) == (self.period == 1)

    def to_abs(self, team, x, y):
        return (x, y) if self.attacks_right(team) else (PITCH_L - x, PITCH_W - y)

    def shape(self, team, in_poss, ball_x, ball_y):
        """Positions of the team's active players in its own attacking frame."""
        out = {}
        shift = (ball_x - PITCH_L / 2) * (0.55 if in_poss else 0.45)
        for p in team.active():
            if p.role == "GK":
                x = clamp(6 + shift * 0.12, 2, 20)
                y = clamp(GOAL_Y + (ball_y - GOAL_Y) * 0.15, 28, 40)
            else:
                bx = p.bx if in_poss else p.bx * 0.8 + team.line
                x = clamp(bx + shift + self.rng.uniform(-2, 2), 3, PITCH_L - 3)
                pull = 0.25 if in_poss else 0.35
                y = clamp(p.by + (ball_y - GOAL_Y) * pull + self.rng.uniform(-2, 2), 2, PITCH_W - 2)
            out[p.id] = (x, y)
        return out

    def opp_positions_in_frame(self, team, bx, by):
        """Defending team's positions converted into the attacking team's frame."""
        o = self.opp(team)
        pos = self.shape(o, False, PITCH_L - bx, PITCH_W - by)
        return {pid: (PITCH_L - x, PITCH_W - y) for pid, (x, y) in pos.items()}

    def player(self, pid):
        for p in self.home.players + self.away.players + self.home.bench + self.away.bench:
            if p.id == pid:
                return p
        return None

    # ---------- events ----------
    def clock(self):
        into = self.t - self.period_start
        base = 0 if self.period == 1 else 45
        minute = int(into // 60)
        if minute >= 45:
            return f"{base + 45}+{minute - 44}", base + 45
        return f"{base + minute + 1}'", base + minute + 1

    def emit(self, etype, team=None, player=None, x=None, y=None, **extra):
        self.eid += 1
        label, minute = self.clock()
        ev = {"event_id": self.eid, "match_id": self.match_id, "type": etype,
              "period": self.period, "match_seconds": round(self.t, 1),
              "minute": minute, "clock": label,
              "score": {"home": self.home.goals, "away": self.away.goals}}
        if team is not None:
            ev["team"] = team.side
            ev["team_name"] = team.name
            ev["possession_id"] = self.possession_id
        if player is not None:
            ev["player_id"] = player.id
            ev["player_name"] = player.name
        if x is not None and team is not None:
            ax, ay = self.to_abs(team, x, y)
            ev["x"], ev["y"] = round(ax, 1), round(ay, 1)
        for k, v in extra.items():
            if isinstance(v, float):
                v = round(v, 2)
            ev[k] = v
        self.events.append(ev)
        return ev

    def end_xy(self, team, x, y):
        ax, ay = self.to_abs(team, x, y)
        return {"end_x": round(ax, 1), "end_y": round(ay, 1)}

    def advance(self, seconds, team, bx, by):
        """Move the clock forward, writing tracking snapshots on the way."""
        target = self.t + seconds
        while self.next_snapshot <= target:
            self.t = self.next_snapshot
            self.snapshot(team, bx, by)
            self.next_snapshot += SNAPSHOT_EVERY
        self.t = target

    def snapshot(self, team, bx, by):
        own = self.shape(team, True, bx, by)
        opp = self.opp_positions_in_frame(team, bx, by)
        frame = {"match_seconds": round(self.t, 1), "period": self.period, "players": []}
        for t_obj, pos in ((team, own), (self.opp(team), opp)):
            for pid, (x, y) in pos.items():
                ax, ay = self.to_abs(team, x, y)
                p = self.player(pid)
                ball_ax, ball_ay = self.to_abs(team, bx, by)
                near = math.hypot(ax - ball_ax, ay - ball_ay)
                press = self.tactics(t_obj, False)[0]
                self.physical_step(p, near, t_obj is team, press)
                p.x, p.y = ax, ay
                frame["players"].append({"id": pid, "team": t_obj.side,
                                         "x": round(ax, 1), "y": round(ay, 1)})
        ax, ay = self.to_abs(team, bx, by)
        frame["ball"] = {"x": round(ax, 1), "y": round(ay, 1)}
        self.tracking.append(frame)
        if round(self.t) % 60 == 0:
            self.emit_physical()

    def physical_step(self, p, near_ball, in_poss, press):
        """How far and how fast a player moved in the last 5 seconds."""
        if p.role == "GK":
            probs = (0.80, 0.19, 0.01, 0.0)
        else:
            f = 1.5 if near_ball < 15 else 0.75 if near_ball > 40 else 1.0
            if p.role in ("RW", "LW", "RB", "LB", "ST", "RM", "LM"):
                f *= 1.2
            elif p.role == "CB":
                f *= 0.8
            if not in_poss:
                f *= (press / 0.5) ** 0.7
            f *= p.form(self.t) ** 3
            run, spr, jog = 0.05 * f, 0.016 * f, 0.33
            probs = (max(0.05, 1 - jog - run - spr), jog, run, spr)
        state = self.rng.choices(range(4), probs)[0]
        secs = SNAPSHOT_EVERY
        if state == 0:
            dist = self.rng.uniform(2, 5.5) * secs / 3.6
        elif state == 1:
            dist = self.rng.uniform(7, 13) * secs / 3.6
        elif state == 2:
            dist = self.rng.uniform(15, 24.5) * secs / 3.6
            p.running_m += dist
        else:
            # a short sprint (1.5-4 s) inside the interval, the rest at running pace
            sprint_s = self.rng.uniform(1.5, 4.0)
            speed = 25 + (p.top_capacity() - 25) * self.rng.betavariate(2, 2.2)
            sprint = speed * sprint_s / 3.6
            rest = self.rng.uniform(15, 22) * (secs - sprint_s) / 3.6
            dist = sprint + rest
            p.sprint_m += sprint
            p.running_m += rest
            p.top_speed_kmh = max(p.top_speed_kmh, speed)
            if not p.sprinting:
                p.sprints += 1
        p.sprinting = state == 3
        p.distance_m += dist

    def emit_physical(self):
        """Cumulative physical data for everyone who has played, like a GPS feed."""
        rows = []
        for p in self.home.players + self.away.players:
            if p.distance_m > 0:
                rows.append({"player_id": p.id, "distance_m": round(p.distance_m),
                             "running_m": round(p.running_m), "sprinting_m": round(p.sprint_m),
                             "sprints": p.sprints, "top_speed_kmh": round(p.top_speed_kmh, 1),
                             "on_pitch": p.on_pitch})
        self.emit("physical_update", players=rows)

    # ---------- football logic ----------
    def xg(self, x, y):
        dx, dy = PITCH_L - x, abs(y - GOAL_Y)
        dist = math.hypot(dx, dy)
        angle = math.atan2(7.32 * dx, dx * dx + dy * dy - (7.32 / 2) ** 2)
        if angle < 0:
            angle += math.pi
        return clamp(1 / (1 + math.exp(-(-1.45 - 0.12 * dist + 1.6 * angle))), 0.01, 0.8)

    def nearest(self, positions, x, y, exclude=None):
        best, bd = None, 1e9
        for pid, (px, py) in positions.items():
            if pid == exclude:
                continue
            d = math.hypot(px - x, py - y)
            if d < bd:
                best, bd = pid, d
        return best, bd

    def tactics(self, team, attacking):
        """Current pressing / directness, including temporary surges."""
        press, direct = team.press, team.directness
        if self.surges.get(team.side, -1) > self.t:
            press += 0.2
            direct += 0.1
        return clamp(press, 0.1, 0.95), clamp(direct, 0.02, 0.6)

    def change_possession(self, new_team, reason, x, y, player):
        self.possession_id += 1
        self.emit("possession_change", new_team, player, x, y, reason=reason)

    def check_tactical_shifts(self):
        minute = (self.t - self.period_start) / 60 + (45 if self.period == 2 else 0)
        for team in (self.home, self.away):
            o = self.opp(team)
            key_trail, key_lead = f"{team.side}-chasing", f"{team.side}-protecting"
            logged = {e["key"] for e in self.tactics_log}
            if minute > 60 and team.goals < o.goals and key_trail not in logged:
                team.press += 0.2; team.line += 5; team.directness += 0.12
                self.tactics_log.append({"key": key_trail, "team": team.side,
                                         "match_seconds": round(self.t), "change": "high press, higher line, more direct play while trailing"})
            if minute > 75 and team.goals > o.goals and key_lead not in logged:
                team.press -= 0.15; team.line -= 6
                self.tactics_log.append({"key": key_lead, "team": team.side,
                                         "match_seconds": round(self.t), "change": "dropped deep to protect the lead"})

    def maybe_substitute(self, team, bx, by):
        if not team.sub_minutes or self.t / 60 < team.sub_minutes[0] or not team.bench:
            return
        team.sub_minutes.pop(0)
        outfield = [p for p in team.active() if p.role != "GK"]
        tired = min(outfield, key=lambda p: p.form(self.t))
        options = [b for b in team.bench if b.role != "GK"]
        if not options:
            return
        on = self.rng.choice(options)
        team.bench.remove(on)
        on.bx, on.by, on.role = tired.bx, tired.by, tired.role
        on.minutes_from = self.t
        on.x, on.y = tired.x, tired.y
        tired.on_pitch = False
        team.players.append(on)
        self.emit("substitution", team, on, player_off_id=tired.id, player_off_name=tired.name,
                  player_on_id=on.id, player_on_name=on.name)

    def play_period(self, kickoff_team, length_s):
        team = kickoff_team
        bx, by = PITCH_L / 2, GOAL_Y
        carrier = next(p for p in team.active() if p.role in ("ST", "AM", "CM"))
        self.possession_id += 1
        self.emit("kick_off", team, carrier, bx, by)
        end = self.period_start + length_s

        while self.t < end:
            self.check_tactical_shifts()
            for tm in (self.home, self.away):
                self.maybe_substitute(tm, bx, by)
            if not carrier.on_pitch:
                carrier = self.rng.choice([p for p in team.active() if p.role != "GK"])

            o = self.opp(team)
            own = self.shape(team, True, bx, by)
            opp = self.opp_positions_in_frame(team, bx, by)
            presser_id, press_d = self.nearest(opp, bx, by)
            presser = self.player(presser_id)
            o_press, _ = self.tactics(o, False)
            _, direct = self.tactics(team, True)
            form = carrier.form(self.t)

            # Pressure on the ball carrier
            # How often the nearest defender closes the ball down depends strongly
            # on the defending team's pressing intensity (0.5 = average).
            base = 0.9 if press_d < 3 else 0.55 if press_d < 6 else 0.25 if press_d < 10 else 0.0
            if self.rng.random() < clamp(base * (o_press / 0.5) ** 2, 0, 0.95):
                self.emit("pressure", o, presser, *self.flip(bx, by),
                          on_player_id=carrier.id, on_player_name=carrier.name,
                          distance_m=float(round(max(press_d, 0.5), 1)))

            # Decide action
            xg = self.xg(bx, by)
            shoot_p = clamp(xg * 1.3, 0, 0.55) if bx > 76 else 0
            if 68 < bx < 88 and abs(by - GOAL_Y) < 18 and press_d > 3:
                shoot_p = max(shoot_p, 0.06)
            carry_p = 0.22 if press_d > 7 else 0.14
            r = self.rng.random()

            if r < shoot_p:
                bx, by, team, carrier = self.shoot(team, carrier, bx, by, xg, press_d)
            elif r < shoot_p + carry_p and carrier.role != "GK":
                bx, by, team, carrier = self.carry(team, carrier, bx, by, presser, press_d, own)
            else:
                bx, by, team, carrier = self.pass_ball(team, carrier, bx, by, own, opp,
                                                       press_d, direct, form)
        return team

    def flip(self, bx, by):
        """Ball position in the defending team's frame (for their events)."""
        return PITCH_L - bx, PITCH_W - by

    def pass_ball(self, team, carrier, bx, by, own, opp, press_d, direct, form):
        o = self.opp(team)
        candidates = []
        for pid, (rx, ry) in own.items():
            if pid == carrier.id:
                continue
            d = math.hypot(rx - bx, ry - by)
            if d < 4:
                continue
            _, marked = self.nearest(opp, rx, ry)
            progress = rx - bx
            w = math.exp(-((d - 12) ** 2) / 120) + direct * max(0, d - 25) / 25
            w *= 1 + max(-0.6, progress / 40)
            w *= clamp(marked / 6, 0.25, 1.5)
            candidates.append((w, pid, rx, ry, d, marked))
        if not candidates:
            self.advance(2, team, bx, by)
            return bx, by, team, carrier
        total = sum(c[0] for c in candidates)
        pick = self.rng.uniform(0, total)
        for c in candidates:
            pick -= c[0]
            if pick <= 0:
                break
        _, rid, rx, ry, dist, marked = c
        receiver = self.player(rid)

        if rx > 88 and abs(by - GOAL_Y) > 18 and abs(ry - GOAL_Y) < 12:
            ptype = "cross"
        elif rx - bx > 15 and rx > 68 and marked > 3:
            ptype = "through_ball"
        elif dist > 30:
            ptype = "long"
        elif dist > 18:
            ptype = "medium"
        else:
            ptype = "short"

        p_success = 0.985 - 0.006 * max(0, dist - 12) - (0.09 if press_d < 3 else 0.03 if press_d < 6 else 0)
        p_success -= 0.09 if marked < 2.5 else 0.03 if marked < 5 else 0
        p_success -= {"cross": 0.3, "through_ball": 0.18, "long": 0.12, "medium": 0.02, "short": 0}[ptype]
        p_success += (carrier.passing * form - 70) * 0.004
        p_success = clamp(p_success, 0.08, 0.98)
        difficulty = round(clamp((1 - p_success) * 12, 0, 10), 1)

        speed = {"short": (8, 14), "medium": (12, 18), "long": (17, 25), "through_ball": (13, 20), "cross": (19, 27)}[ptype]
        ball_ms = self.rng.uniform(*speed)
        travel = dist / ball_ms
        success = self.rng.random() < p_success

        if success:
            ex, ey = rx, ry
        else:
            ex = clamp(rx + self.rng.uniform(-6, 6), 1, PITCH_L - 1)
            ey = clamp(ry + self.rng.uniform(-6, 6), 1, PITCH_W - 1)

        # Line-breaking: a completed forward pass that takes several opponents out of the game
        bypassed = 0
        if success and rx - bx >= 8:
            bypassed = sum(1 for pid, (ox, oy) in opp.items()
                           if self.player(pid).role != "GK" and bx + 1 < ox < rx - 1)

        self.emit("pass", team, carrier, bx, by, **self.end_xy(team, ex, ey),
                  pass_type=ptype, outcome="complete" if success else "incomplete",
                  receiver_id=receiver.id if success else None,
                  receiver_name=receiver.name if success else None,
                  distance_m=float(round(dist, 1)), ball_speed_kmh=float(round(ball_ms * 3.6, 1)),
                  difficulty=difficulty, expected_success=float(round(p_success, 2)),
                  under_pressure=press_d < 5, opponents_bypassed=bypassed,
                  line_breaking=bool(success and bypassed >= 4))
        aerial_def = None
        if ptype in ("cross", "long") and self.rng.random() < (0.85 if ptype == "cross" else 0.25):
            def_id, _ = self.nearest(opp, rx, ry)
            aerial_def = self.player(def_id)
            if success:
                self.emit("aerial_duel", team, receiver, ex, ey, opponent_id=aerial_def.id,
                          opponent_name=aerial_def.name)
            else:
                self.emit("aerial_duel", o, aerial_def, *self.flip(ex, ey), opponent_id=receiver.id,
                          opponent_name=receiver.name)
        if ptype == "through_ball" and success:
            run = clamp(self.rng.gauss(26 + (receiver.pace - 70) * 0.25, 2.5), 18, 36.5)
            receiver.top_speed_kmh = max(receiver.top_speed_kmh, run)
            self.events[-1]["receiver_run_speed_kmh"] = float(round(run, 1))

        self.advance(travel + self.rng.uniform(1.2, 3.0), team, ex, ey)
        if success:
            return ex, ey, team, receiver

        # Failed pass: intercepted, cleared, picked up loose, or out of play
        nid, nd = self.nearest(opp, ex, ey)
        if aerial_def is not None:
            nid, nd = aerial_def.id, 0.0
        if ptype == "cross":
            if self.rng.random() < 0.35:
                return self.corner(team)
            return self.clearance(team, self.player(nid), *self.flip(ex, ey))
        r = self.rng.random()
        nx, ny = self.flip(ex, ey)
        if r < 0.22 or nd < 3:
            defender = self.player(nid)
            if nx < 35 and self.rng.random() < 0.8:
                return self.clearance(team, defender, nx, ny)
            self.emit("interception", o, defender, nx, ny)
            self.change_possession(o, "interception", nx, ny, defender)
            return nx, ny, o, defender
        if r < 0.68:
            recoverer = self.player(nid)
            self.change_possession(o, "loose_ball", nx, ny, recoverer)
            self.advance(self.rng.uniform(1, 2.5), o, nx, ny)
            return nx, ny, o, recoverer
        return self.out_of_play(team, ex, ey)

    def clearance(self, team, defender, nx, ny):
        """Defender (opponent of `team`) hacks the ball clear from near their own goal."""
        o = self.opp(team)
        self.emit("clearance", o, defender, nx, ny)
        cx, cy = self.rng.uniform(40, 65), self.rng.uniform(8, PITCH_W - 8)
        self.advance(self.rng.uniform(2, 4), o, cx, cy)
        if self.rng.random() < 0.45:
            rec = self.rng.choice([p for p in o.active() if p.role not in ("GK", "CB")] or o.active())
            self.change_possession(o, "clearance_recovered", cx, cy, rec)
            return cx, cy, o, rec
        tx, ty = self.flip(cx, cy)
        rec = self.rng.choice([p for p in team.active() if p.role not in ("GK", "ST")] or team.active())
        self.change_possession(team, "loose_ball", tx, ty, rec)
        return tx, ty, team, rec

    def out_of_play(self, team, ex, ey):
        """Ball went out after `team` played it; restart for the opponent."""
        o = self.opp(team)
        nx, ny = self.flip(ex, ey)
        if ex > PITCH_L - 8 and abs(ey - GOAL_Y) < 25:
            restart, nx, ny, wait = "goal_kick", 6, GOAL_Y, self.rng.uniform(15, 30)
            taker = next(p for p in o.active() if p.role == "GK")
        else:
            restart, wait = "throw_in", self.rng.uniform(8, 20)
            ny = 0.5 if ny < GOAL_Y else PITCH_W - 0.5
            taker = self.rng.choice([p for p in o.active() if p.role in ("RB", "LB", "CM", "RM", "LM")] or o.active())
        self.change_possession(o, restart, nx, ny, taker)
        self.emit(restart, o, taker, nx, ny)
        self.advance(wait, o, nx, ny)
        return nx, ny, o, taker

    def carry(self, team, carrier, bx, by, presser, press_d, own):
        o = self.opp(team)
        if press_d < 5.5 and self.rng.random() < 0.6 + o_press_now(self, o):
            # Duel: the presser tries to win the ball
            win_p = clamp(0.45 + (presser.tackling * presser.form(self.t) - carrier.dribbling * carrier.form(self.t)) * 0.01, 0.15, 0.8)
            in_box = bx > 88.5 and abs(by - GOAL_Y) < 20.16
            if self.rng.random() < 0.42 * (0.3 if in_box else 1.0):  # defenders are careful in the box
                self.emit("foul", o, presser, *self.flip(bx, by), fouled_id=carrier.id, fouled_name=carrier.name)
                if self.rng.random() < 0.19:
                    presser.yellow += 1
                    if presser.yellow >= 2 and presser.role != "GK":
                        self.emit("card", o, presser, card="red", reason="second_yellow")
                        presser.on_pitch = False
                    else:
                        self.emit("card", o, presser, card="yellow")
                if bx > 88.5 and abs(by - GOAL_Y) < 20.16:
                    self.emit("penalty_awarded", team, carrier, bx, by)
                    self.advance(self.rng.uniform(50, 90), team, 94, GOAL_Y)
                    taker = max([p for p in team.active() if p.role != "GK"], key=lambda p: p.finishing)
                    return self.shoot(team, taker, 94, GOAL_Y, 0.76, 99, penalty=True)
                self.emit("free_kick", team, carrier, bx, by)
                self.advance(self.rng.uniform(15, 35), team, bx, by)
                return bx, by, team, carrier
            if self.rng.random() < win_p:
                nx, ny = self.flip(bx, by)
                self.emit("tackle", o, presser, nx, ny, outcome="won",
                          opponent_id=carrier.id, opponent_name=carrier.name)
                self.change_possession(o, "tackle", nx, ny, presser)
                self.advance(self.rng.uniform(1, 2.5), o, nx, ny)
                return nx, ny, o, presser
            self.emit("tackle", o, presser, *self.flip(bx, by), outcome="lost",
                      opponent_id=carrier.id, opponent_name=carrier.name)
            self.emit("dribble", team, carrier, bx, by, outcome="beat_opponent", opponent_id=presser.id)

        space = clamp(press_d, 2, 20)
        length = self.rng.uniform(3, 6 + space * 0.6)
        ex = clamp(bx + length * self.rng.uniform(0.6, 1.0), 2, PITCH_L - 2)
        ey = clamp(by + self.rng.uniform(-5, 5), 2, PITCH_W - 2)
        speed = clamp(self.rng.gauss(17 + (carrier.pace - 70) * 0.2 + space * 0.4, 3), 9, 35)
        carrier.top_speed_kmh = max(carrier.top_speed_kmh, speed)
        self.emit("carry", team, carrier, bx, by, **self.end_xy(team, ex, ey),
                  distance_m=float(round(math.hypot(ex - bx, ey - by), 1)),
                  speed_kmh=float(round(speed, 1)))
        self.advance(math.hypot(ex - bx, ey - by) / (speed / 3.6), team, ex, ey)
        return ex, ey, team, carrier

    def shoot(self, team, carrier, bx, by, xg, press_d, penalty=False):
        o = self.opp(team)
        finish = carrier.finishing * carrier.form(self.t)
        dist = math.hypot(PITCH_L - bx, by - GOAL_Y)
        r = self.rng.random()
        if penalty:
            xg_adj = 0.76
            shot_speed = clamp(self.rng.gauss(100, 10), 70, 130)
            goal_p = clamp(0.62 + (finish - 50) / 200, 0.6, 0.86)
            outcome = "goal" if r < goal_p else "saved" if r < goal_p + (1 - goal_p) * 0.65 else "off_target"
            body = "foot"
        else:
            xg_adj = xg * (0.7 if press_d < 2.5 else 1.0)
            shot_speed = clamp(self.rng.gauss(95 - dist * 0.4 + (finish - 70) * 0.3, 9), 45, 130)
            goal_p = clamp(xg_adj * (0.7 + (finish - 50) / 80), 0.005, 0.85)
            if r < goal_p:
                outcome = "goal"
            elif r < goal_p + 0.28:
                outcome = "saved"
            elif r < goal_p + 0.52:
                outcome = "blocked"
            else:
                outcome = "off_target"
            body = "head" if self.rng.random() < (0.35 if bx > 94 else 0.05) else "foot"
        extra = {}
        if outcome in ("goal", "saved"):
            # Post-shot xG: how likely a shot placed like this was to score
            if penalty:
                xgot = self.rng.uniform(0.8, 0.98) if outcome == "goal" else self.rng.uniform(0.45, 0.8)
            elif outcome == "goal":
                xgot = clamp(self.rng.betavariate(4, 2) * 0.7 + xg_adj * 0.3 + 0.1, 0.15, 0.99)
            else:
                xgot = clamp(self.rng.betavariate(1.5, 6) + xg_adj * 0.3, 0.01, 0.9)
            extra["xgot"] = float(round(xgot, 3))
        self.emit("shot", team, carrier, bx, by, outcome=outcome, xg=float(round(xg_adj, 3)),
                  shot_speed_kmh=float(round(shot_speed if body == "foot" else shot_speed * 0.6, 1)),
                  distance_m=float(round(dist, 1)), body_part=body, under_pressure=press_d < 3,
                  penalty=penalty, **extra)
        self.advance(self.rng.uniform(1, 2), team, PITCH_L - 1, GOAL_Y)

        gk = next(p for p in o.active() if p.role == "GK")
        if outcome == "goal":
            team.goals += 1
            self.emit("goal", team, carrier, bx, by, xg=float(round(xg_adj, 3)),
                      assist_id=self.last_passer(team, carrier))
            self.surges[o.side] = self.t + 300
            self.advance(self.rng.uniform(45, 70), team, PITCH_L / 2, GOAL_Y)
            taker = self.rng.choice([p for p in o.active() if p.role in ("ST", "AM", "CM")] or o.active())
            self.possession_id += 1
            self.emit("kick_off", o, taker, PITCH_L / 2, GOAL_Y)
            return PITCH_L / 2, GOAL_Y, o, taker
        if outcome == "saved":
            self.emit("save", o, gk, 2, GOAL_Y, shot_xg=float(round(xg_adj, 3)))
            if self.rng.random() < 0.5:
                return self.corner(team)
            self.change_possession(o, "goalkeeper_save", 2, GOAL_Y, gk)
            self.advance(self.rng.uniform(4, 10), o, 2, GOAL_Y)
            return 6, GOAL_Y, o, gk
        if outcome == "blocked":
            defender = self.rng.choice([p for p in o.active() if p.role in ("CB", "RB", "LB", "DM")] or o.active())
            nx, ny = self.flip(bx + 2, by)
            self.emit("block", o, defender, nx, ny, shooter_id=carrier.id)
        if outcome == "blocked" and self.rng.random() < 0.6:
            return self.corner(team)
        if outcome == "blocked":
            self.change_possession(o, "block", nx, ny, defender)
            return nx, ny, o, defender
        self.change_possession(o, "goal_kick", 6, GOAL_Y, gk)
        self.emit("goal_kick", o, gk, 6, GOAL_Y)
        self.advance(self.rng.uniform(15, 30), o, 6, GOAL_Y)
        return 6, GOAL_Y, o, gk

    def corner(self, team):
        cy = 0.5 if self.rng.random() < 0.5 else PITCH_W - 0.5
        taker = max([p for p in team.active() if p.role != "GK"], key=lambda p: p.passing)
        self.emit("corner", team, taker, PITCH_L - 0.5, cy)
        self.advance(self.rng.uniform(20, 40), team, PITCH_L - 0.5, cy)
        return PITCH_L - 0.5, cy, team, taker

    def last_passer(self, team, scorer):
        recent = [e for e in self.events[-40:] if e["type"] not in ("physical_update", "pressure", "aerial_duel")]
        for ev in reversed(recent[-8:]):
            if ev["type"] == "pass" and ev.get("team") == team.side and ev.get("receiver_id") == scorer.id:
                return ev["player_id"]
        return None

    def run(self):
        first_len = 45 * 60 + self.rng.uniform(60, 240)
        second_len = 45 * 60 + self.rng.uniform(120, 360)
        starter = self.home if self.rng.random() < 0.5 else self.away

        self.emit("period_start", period=1)
        self.play_period(starter, first_len)
        self.emit("period_end", period=1)

        self.period, self.period_start = 2, self.t
        self.emit("period_start", period=2)
        self.play_period(self.opp(starter), second_len)
        self.emit("period_end", period=2)
        self.emit("match_end", final_score={"home": self.home.goals, "away": self.away.goals})

    def metadata(self):
        rng = random.Random(self.seed + 1)
        def team_meta(t):
            starters = [p for p in t.players if p.minutes_from == 0]
            subs = [p for p in t.players if p.minutes_from > 0] + t.bench
            return {"name": t.name, "short_name": t.short, "colours": list(t.colours),
                    "formation": t.formation,
                    "starting_xi": [p.public() for p in starters],
                    "bench": [p.public() for p in subs]}
        meta = {"match_id": self.match_id, "seed": self.seed, "synthetic": True,
                "competition": "Synthetic Premier Cup (fictional)",
                "venue": rng.choice(["Harbourside Park", "North Field Arena", "Riverbank Stadium", "Kingsway Ground"]),
                "weather": rng.choice(["clear", "light rain", "overcast", "windy"]),
                "pitch": {"length_m": PITCH_L, "width_m": PITCH_W},
                "coordinates": "metres; x 0-105 left to right, y 0-68; home attacks right in period 1",
                "home": team_meta(self.home), "away": team_meta(self.away)}
        return meta

    def ground_truth(self):
        players = []
        for p in self.home.players + self.away.players:
            players.append({"id": p.id, "name": p.name, "distance_km": round(p.distance_m / 1000, 2),
                            "running_km": round(p.running_m / 1000, 2),
                            "sprinting_km": round(p.sprint_m / 1000, 2), "sprints": p.sprints,
                            "top_speed_kmh": round(p.top_speed_kmh, 1)})
        return {"final_score": {"home": self.home.goals, "away": self.away.goals},
                "tactical_changes": [{k: v for k, v in e.items() if k != "key"} for e in self.tactics_log],
                "players": players}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    out = a.out or f"matches/match_{a.seed:05d}"
    os.makedirs(out, exist_ok=True)

    m = Match(a.seed)
    m.run()
    with open(f"{out}/metadata.json", "w") as f:
        json.dump(m.metadata(), f, indent=2)
    with open(f"{out}/events.jsonl", "w") as f:
        for ev in m.events:
            f.write(json.dumps(ev) + "\n")
    with open(f"{out}/tracking.jsonl", "w") as f:
        for fr in m.tracking:
            f.write(json.dumps(fr) + "\n")
    with open(f"{out}/ground_truth.json", "w") as f:
        json.dump(m.ground_truth(), f, indent=2)
    print(f"{m.home.name} {m.home.goals} - {m.away.goals} {m.away.name}  "
          f"({len(m.events)} events, {len(m.tracking)} tracking frames) -> {out}")


if __name__ == "__main__":
    main()
