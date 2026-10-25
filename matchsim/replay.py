"""
Stream a generated match "live", at match pace or sped up.

Each event is sent when its match time comes around, the way a live data
feed would deliver it. For now events go to the terminal; later the same
loop sends them to Azure Event Hubs for the ingest stage.

Usage:
  python replay.py matches/match_00007              # real time (slow!)
  python replay.py matches/match_00007 --speed 60   # 1 match minute per second
  python replay.py matches/match_00007 --speed 60 --json   # raw JSON lines
"""
import argparse
import json
import sys
import time

SKIP_IN_TEXT = {"pressure", "carry", "physical_update", "aerial_duel"}  # very frequent; still sent with --json


def describe(ev):
    who = ev.get("player_name", "")
    team = ev.get("team_name", "")
    t = ev["type"]
    if t == "pass":
        to = f" to {ev['receiver_name']}" if ev.get("receiver_name") else ""
        return f"{who} {ev['pass_type'].replace('_', ' ')} pass{to}, {ev['distance_m']} m, {ev['outcome']} (difficulty {ev['difficulty']}/10)"
    if t == "shot":
        return f"SHOT by {who} ({team}), {ev['outcome']}, xG {ev['xg']}, {ev['shot_speed_kmh']} km/h"
    if t == "goal":
        s = ev["score"]
        return f"GOAL! {who} scores for {team}. Score {s['home']}-{s['away']}"
    if t == "tackle":
        return f"{who} tackle on {ev['opponent_name']}: {ev['outcome']}"
    if t == "possession_change":
        return f"{team} win possession ({ev['reason'].replace('_', ' ')})"
    if t == "card":
        return f"{ev['card'].upper()} card for {who} ({team})"
    if t == "substitution":
        return f"Substitution {team}: {ev['player_on_name']} on for {ev['player_off_name']}"
    if t in ("period_start", "period_end", "match_end"):
        return t.replace("_", " ").upper()
    return f"{t.replace('_', ' ')}: {who} ({team})".strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("match_dir")
    ap.add_argument("--speed", type=float, default=1.0, help="1 = real time, 60 = a minute per second")
    ap.add_argument("--json", action="store_true", help="print raw event JSON")
    a = ap.parse_args()

    events = [json.loads(line) for line in open(f"{a.match_dir}/events.jsonl")]
    start = time.monotonic()
    for ev in events:
        due = ev["match_seconds"] / a.speed
        wait = due - (time.monotonic() - start)
        if wait > 0:
            time.sleep(wait)
        if a.json:
            print(json.dumps(ev), flush=True)
        elif ev["type"] not in SKIP_IN_TEXT:
            print(f"[{ev['clock']:>6}] {describe(ev)}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, BrokenPipeError):
        sys.exit(0)
