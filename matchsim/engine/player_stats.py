"""
Full player profiles: shooting, physical, passing, possession, defending,
discipline. Every stat is counted from the live event feed, and each player is
ranked against everyone else who played in the match.

Used by MatchEngine; can also be used on its own:
    tracker = PlayerStatsTracker(metadata)
    for ev in events: tracker.ingest(ev, possessor_before_event)
    tracker.profile(player_id)
"""

PITCH_L, PITCH_W = 105.0, 68.0
BOX_X, BOX_HALF_W = PITCH_L - 16.5, 20.16
FINAL_THIRD_X = 70.0
BIG_CHANCE_XG = 0.30
RECOVERY_REASONS = {"loose_ball", "clearance_recovered", "block", "goalkeeper_save"}

COUNTERS = (
    "goals", "xg", "xgot", "np_xg", "shots", "shots_on_target", "headed_shots",
    "penalties_taken", "penalties_scored",
    "assists", "xa", "passes", "passes_completed", "long_balls", "long_balls_completed",
    "line_breaking_passes", "chances_created", "big_chances_created", "difficulty_sum",
    "dribbles", "dribbles_completed", "duels", "duels_won", "aerials", "aerials_won",
    "touches", "touches_opp_box", "dispossessed", "fouls_won",
    "tackles_attempted", "tackles", "interceptions", "blocks", "clearances", "recoveries",
    "possession_won_final_third", "dribbled_past", "fouls", "saves", "pressures",
    "goals_conceded_on_pitch", "xg_against_on_pitch", "yellow_cards", "red_cards",
    "distance_m", "running_m", "sprinting_m", "sprints", "top_speed_kmh",
)


def pct(a, b):
    return round(100 * a / b, 1) if b else None


# (label, function(values) -> number, higher is better)
SECTIONS = [
    ("Shooting", [
        ("Goals", lambda v: v["goals"], True),
        ("Expected goals (xG)", lambda v: round(v["xg"], 2), True),
        ("xG on target (xGOT)", lambda v: round(v["xgot"], 2), True),
        ("Non-penalty xG", lambda v: round(v["np_xg"], 2), True),
        ("Shots", lambda v: v["shots"], True),
        ("Shots on target", lambda v: v["shots_on_target"], True),
        ("Headed shots", lambda v: v["headed_shots"], True),
    ]),
    ("Physical performance", [
        ("Top speed (km/h)", lambda v: round(v["top_speed_kmh"], 1), True),
        ("Distance covered (km)", lambda v: round(v["distance_m"] / 1000, 2), True),
        ("Running (km)", lambda v: round(v["running_m"] / 1000, 2), True),
        ("Sprinting (km)", lambda v: round(v["sprinting_m"] / 1000, 2), True),
        ("Number of sprints", lambda v: v["sprints"], True),
    ]),
    ("Passing", [
        ("Assists", lambda v: v["assists"], True),
        ("Expected assists (xA)", lambda v: round(v["xa"], 2), True),
        ("Successful passes", lambda v: v["passes_completed"], True),
        ("Successful passes %", lambda v: pct(v["passes_completed"], v["passes"]), True),
        ("Accurate long balls", lambda v: v["long_balls_completed"], True),
        ("Accurate long balls %", lambda v: pct(v["long_balls_completed"], v["long_balls"]), True),
        ("Line-breaking passes", lambda v: v["line_breaking_passes"], True),
        ("Chances created", lambda v: v["chances_created"], True),
        ("Big chances created", lambda v: v["big_chances_created"], True),
    ]),
    ("Possession", [
        ("Successful dribbles", lambda v: v["dribbles_completed"], True),
        ("Successful dribbles %", lambda v: pct(v["dribbles_completed"], v["dribbles"]), True),
        ("Duels won", lambda v: v["duels_won"], True),
        ("Duels won %", lambda v: pct(v["duels_won"], v["duels"]), True),
        ("Aerial duels won", lambda v: v["aerials_won"], True),
        ("Aerial duels won %", lambda v: pct(v["aerials_won"], v["aerials"]), True),
        ("Touches", lambda v: v["touches"], True),
        ("Touches in opposition box", lambda v: v["touches_opp_box"], True),
        ("Dispossessed", lambda v: v["dispossessed"], False),
        ("Fouls won", lambda v: v["fouls_won"], True),
    ]),
    ("Defending", [
        ("Defensive contributions", lambda v: v["tackles"] + v["interceptions"] + v["blocks"]
         + v["clearances"] + v["recoveries"], True),
        ("Tackles", lambda v: v["tackles"], True),
        ("Interceptions", lambda v: v["interceptions"], True),
        ("Blocked shots", lambda v: v["blocks"], True),
        ("Fouls committed", lambda v: v["fouls"], False),
        ("Recoveries", lambda v: v["recoveries"], True),
        ("Possession won final 3rd", lambda v: v["possession_won_final_third"], True),
        ("Dribbled past", lambda v: v["dribbled_past"], False),
        ("Clearances", lambda v: v["clearances"], True),
        ("Clean sheets", lambda v: v["clean_sheet"], True),
        ("Goals conceded while on pitch", lambda v: v["goals_conceded_on_pitch"], False),
        ("xG against while on pitch", lambda v: round(v["xg_against_on_pitch"], 2), False),
    ]),
    ("Discipline", [
        ("Yellow cards", lambda v: v["yellow_cards"], False),
        ("Red cards", lambda v: v["red_cards"], False),
    ]),
]
GOALKEEPER_EXTRA = [("Saves", lambda v: v["saves"], True)]


def other(side):
    return "away" if side == "home" else "home"


class PlayerStatsTracker:
    def __init__(self, metadata):
        self.p = {}
        for side in ("home", "away"):
            starters = {pl["id"] for pl in metadata[side]["starting_xi"]}
            for pl in metadata[side]["starting_xi"] + metadata[side]["bench"]:
                row = {k: 0 for k in COUNTERS}
                row.update({"id": pl["id"], "name": pl["name"], "number": pl["number"],
                            "position": pl["position"], "team": side,
                            "team_name": metadata[side]["name"],
                            "on_pitch": pl["id"] in starters,
                            "on_since": 0.0 if pl["id"] in starters else None,
                            "seconds_played": 0.0, "clean_sheet": 0})
                self.p[pl["id"]] = row
        self.t = 0.0
        self.period = 1
        self.last_pass_to = {}
        self.finished = False

    # ---------------------------------------------------------- helpers
    def _attack_frame(self, x, y, side):
        right = (side == "home") == (self.period == 1)
        return (x, y) if right else (PITCH_L - x, PITCH_W - y)

    def _in_opp_box(self, ev, side, use_end=False):
        if "x" not in ev:
            return False
        x, y = (ev["end_x"], ev["end_y"]) if use_end else (ev["x"], ev["y"])
        ax, ay = self._attack_frame(x, y, side)
        return ax >= BOX_X and abs(ay - PITCH_W / 2) <= BOX_HALF_W

    def _in_final_third(self, ev, side):
        if "x" not in ev:
            return False
        return self._attack_frame(ev["x"], ev["y"], side)[0] >= FINAL_THIRD_X

    def _touch(self, pid, ev, side):
        row = self.p.get(pid)
        if row:
            row["touches"] += 1
            if self._in_opp_box(ev, side):
                row["touches_opp_box"] += 1

    def _on_pitch(self, side):
        return [r for r in self.p.values() if r["team"] == side and r["on_pitch"]]

    def _go_off(self, row):
        if row["on_pitch"] and row["on_since"] is not None:
            row["seconds_played"] += self.t - row["on_since"]
        row["on_pitch"] = False

    def minutes(self, row):
        s = row["seconds_played"]
        if row["on_pitch"] and row["on_since"] is not None and not self.finished:
            s += self.t - row["on_since"]
        return round(s / 60)

    # ---------------------------------------------------------- ingest
    def ingest(self, ev, possessor_before=None):
        self.t = ev["match_seconds"]
        self.period = ev.get("period", self.period)
        etype, side, pid = ev["type"], ev.get("team"), ev.get("player_id")
        row = self.p.get(pid)

        if etype == "pass":
            self._touch(pid, ev, side)
            row["passes"] += 1
            row["difficulty_sum"] += ev["difficulty"]
            long_ball = ev["pass_type"] == "long"
            row["long_balls"] += long_ball
            if ev["outcome"] == "complete":
                row["passes_completed"] += 1
                row["long_balls_completed"] += long_ball
                row["line_breaking_passes"] += bool(ev.get("line_breaking"))
                self.last_pass_to[ev["receiver_id"]] = ev
        elif etype in ("carry", "dribble"):
            self._touch(pid, ev, side)
        elif etype == "shot":
            self._touch(pid, ev, side)
            row["shots"] += 1
            row["xg"] += ev["xg"]
            row["np_xg"] += 0 if ev.get("penalty") else ev["xg"]
            row["xgot"] += ev.get("xgot", 0) or 0
            row["shots_on_target"] += ev["outcome"] in ("goal", "saved")
            row["headed_shots"] += ev.get("body_part") == "head"
            if ev.get("penalty"):
                row["penalties_taken"] += 1
                row["penalties_scored"] += ev["outcome"] == "goal"
            key = self.last_pass_to.get(pid)
            if key and key.get("possession_id") == ev.get("possession_id") and not ev.get("penalty"):
                passer = self.p[key["player_id"]]
                passer["chances_created"] += 1
                passer["xa"] += ev["xg"]
                passer["big_chances_created"] += ev["xg"] >= BIG_CHANCE_XG
            for r in self._on_pitch(other(side)):
                r["xg_against_on_pitch"] += ev["xg"]
        elif etype == "goal":
            row["goals"] += 1
            if ev.get("assist_id") in self.p:
                self.p[ev["assist_id"]]["assists"] += 1
            for r in self._on_pitch(other(side)):
                r["goals_conceded_on_pitch"] += 1
        elif etype == "tackle":
            opp = self.p.get(ev.get("opponent_id"))
            row["tackles_attempted"] += 1
            row["duels"] += 1
            if opp:
                opp["duels"] += 1
                opp["dribbles"] += 1
            if ev["outcome"] == "won":
                row["tackles"] += 1
                row["duels_won"] += 1
                self._touch(pid, ev, side)
                if self._in_final_third(ev, side):
                    row["possession_won_final_third"] += 1
                if opp:
                    opp["dispossessed"] += 1
            else:
                row["dribbled_past"] += 1
                if opp:
                    opp["duels_won"] += 1
                    opp["dribbles_completed"] += 1
        elif etype == "aerial_duel":
            loser = self.p.get(ev.get("opponent_id"))
            row["aerials"] += 1
            row["aerials_won"] += 1
            row["duels"] += 1
            row["duels_won"] += 1
            if loser:
                loser["aerials"] += 1
                loser["duels"] += 1
        elif etype == "interception":
            row["interceptions"] += 1
            self._touch(pid, ev, side)
            if self._in_final_third(ev, side):
                row["possession_won_final_third"] += 1
        elif etype == "clearance":
            row["clearances"] += 1
            self._touch(pid, ev, side)
        elif etype == "block":
            row["blocks"] += 1
        elif etype == "possession_change":
            if ev["reason"] in RECOVERY_REASONS and possessor_before and possessor_before != side and row:
                row["recoveries"] += 1
                if self._in_final_third(ev, side):
                    row["possession_won_final_third"] += 1
        elif etype == "pressure":
            row["pressures"] += 1
        elif etype == "foul":
            row["fouls"] += 1
            if ev.get("fouled_id") in self.p:
                self.p[ev["fouled_id"]]["fouls_won"] += 1
        elif etype == "save":
            row["saves"] += 1
            self._touch(pid, ev, side)
        elif etype == "card":
            if ev["card"] == "red":
                row["red_cards"] += 1
                self._go_off(row)
            else:
                row["yellow_cards"] += 1
        elif etype == "substitution":
            self._go_off(self.p[ev["player_off_id"]])
            on = self.p[ev["player_on_id"]]
            on["on_pitch"], on["on_since"] = True, self.t
        elif etype == "physical_update":
            for r in ev["players"]:
                target = self.p.get(r["player_id"])
                if target:
                    target["distance_m"] = r["distance_m"]
                    target["running_m"] = r["running_m"]
                    target["sprinting_m"] = r["sprinting_m"]
                    target["sprints"] = r["sprints"]
                    target["top_speed_kmh"] = max(target["top_speed_kmh"], r["top_speed_kmh"])
        elif etype == "match_end":
            for r in self.p.values():
                self._go_off(r) if r["on_pitch"] else None
                r["on_pitch"] = False
            self.finished = True
            for r in self.p.values():
                if r["seconds_played"] >= 60 * 60 and r["goals_conceded_on_pitch"] == 0:
                    r["clean_sheet"] = 1

    # ---------------------------------------------------------- output
    def played(self):
        return [r for r in self.p.values() if self.minutes(r) > 0 or r["on_pitch"]]

    def rating(self, row):
        r = 6.0 + 0.8 * row["goals"] + 0.5 * row["assists"] + 0.2 * row["chances_created"]
        r += 0.3 * row["xa"] + 0.2 * row["xg"] + 0.15 * row["tackles"] + 0.1 * row["interceptions"]
        r += 0.05 * row["recoveries"] + 0.08 * row["clearances"] + 0.1 * row["blocks"]
        r += 0.35 * row["saves"] + 0.05 * row["line_breaking_passes"] + 0.05 * row["aerials_won"]
        r -= 0.1 * row["dispossessed"] + 0.4 * row["yellow_cards"] + 1.5 * row["red_cards"]
        if row["position"] == "GK":
            r -= 0.5 * row["goals_conceded_on_pitch"]
        if row["passes"] >= 10:
            r += (row["passes_completed"] / row["passes"] - 0.8) * 3
        return round(max(3.0, min(10.0, r)), 1)

    def _sections(self, row):
        sections = [(name, list(stats)) for name, stats in SECTIONS]
        if row["position"] == "GK":
            sections[4][1].extend(GOALKEEPER_EXTRA)
        return sections

    def profile(self, player_id):
        row = self.p[player_id]
        pool = self.played()
        out = {"player_id": row["id"], "name": row["name"], "number": row["number"],
               "position": row["position"], "team": row["team"], "team_name": row["team_name"],
               "minutes_played": self.minutes(row), "rating": self.rating(row), "sections": {}}
        for name, stats in self._sections(row):
            items = []
            for label, fn, higher in stats:
                value = fn(row)
                values = [fn(r) for r in pool]
                values = [v for v in values if v is not None]
                rank = None
                if value is not None and values and not (higher and value == 0):
                    better = sum(1 for v in values if (v > value if higher else v < value))
                    rank = better + 1
                items.append({"stat": label, "value": value, "rank": rank, "of": len(values)})
            out["sections"][name] = items
        return out

    def all_profiles(self):
        return sorted((self.profile(r["id"]) for r in self.played()), key=lambda x: -x["rating"])

    def team_totals(self, side):
        rows = [r for r in self.p.values() if r["team"] == side]
        s = lambda k: sum(r[k] for r in rows)
        return {"xgot": round(s("xgot"), 2), "np_xg": round(s("np_xg"), 2),
                "headed_shots": s("headed_shots"), "xa": round(s("xa"), 2),
                "line_breaking_passes": s("line_breaking_passes"),
                "chances_created": s("chances_created"), "big_chances_created": s("big_chances_created"),
                "long_balls_completed": s("long_balls_completed"), "dribbles_completed": s("dribbles_completed"),
                "duels_won": s("duels_won"), "aerials_won": s("aerials_won"), "clearances": s("clearances"),
                "blocks": s("blocks"), "recoveries": s("recoveries"),
                "possession_won_final_third": s("possession_won_final_third"),
                "touches_opp_box": s("touches_opp_box"),
                "distance_km": round(s("distance_m") / 1000, 1), "sprints": s("sprints")}
