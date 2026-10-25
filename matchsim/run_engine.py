"""
Run the stats engine over a generated match, as if it were live.

Usage:
  python run_engine.py matches/match_00007                # instant
  python run_engine.py matches/match_00007 --speed 60     # 1 match minute per second

Writes insights.jsonl, states.jsonl and player_stats.json into the match folder and prints the
insights as they happen.
"""
import argparse
import json
import time
from collections import Counter

from engine.stats_engine import MatchEngine

STARS = {1: "***", 2: "** ", 3: "*  "}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("match_dir")
    ap.add_argument("--speed", type=float, default=0, help="0 = as fast as possible")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    meta = json.load(open(f"{a.match_dir}/metadata.json"))
    events = [json.loads(line) for line in open(f"{a.match_dir}/events.jsonl")]
    engine = MatchEngine(meta)
    insights, states = [], []
    start = time.monotonic()

    for ev in events:
        if a.speed:
            wait = ev["match_seconds"] / a.speed - (time.monotonic() - start)
            if wait > 0:
                time.sleep(wait)
        for item in engine.ingest(ev):
            if item["kind"] == "state":
                states.append(item)
                continue
            insights.append(item)
            if not a.quiet:
                print(f"[{item['clock']:>6}] {STARS[item['priority']]} {item['headline']}", flush=True)

    with open(f"{a.match_dir}/insights.jsonl", "w") as f:
        for i in insights:
            f.write(json.dumps(i) + "\n")
    with open(f"{a.match_dir}/states.jsonl", "w") as f:
        for s in states:
            f.write(json.dumps(s) + "\n")
    with open(f"{a.match_dir}/player_stats.json", "w") as f:
        json.dump(engine.final_report(), f, indent=1)

    counts = Counter(i["type"] for i in insights)
    print(f"\n{len(insights)} insights ({engine.suppressed} held back to avoid clutter), "
          f"{len(states)} state snapshots")
    print(", ".join(f"{k}: {v}" for k, v in counts.most_common()))


if __name__ == "__main__":
    main()
