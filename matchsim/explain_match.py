"""
Run a match through the full pipeline: live feed -> stats engine -> AI agent team.

Usage:
  python explain_match.py matches/match_00007 --offline              # no Azure needed
  python explain_match.py matches/match_00007 --languages fr,es,pt   # uses your Foundry model
  python explain_match.py matches/match_00007 --languages all        # every supported language
  python explain_match.py matches/match_00007 --limit 5              # only the first 5 moments (cheap test)
  python explain_match.py matches/match_00007 --speed 60             # live pace, 1 match minute per second

Writes explained.jsonl, recaps.json and agent_trace.jsonl into the match folder.
"""
import argparse
import asyncio
import json
import time

from agents.clients import make_client
from agents.orchestrator import MatchIntelligence
from engine.stats_engine import MatchEngine

SUMMARIES = {"half_time", "full_time"}


async def run(a):
    meta = json.load(open(f"{a.match_dir}/metadata.json"))
    events = [json.loads(line) for line in open(f"{a.match_dir}/events.jsonl")]
    client, described = make_client(offline=a.offline)
    langs = [l.strip() for l in a.languages.split(",") if l.strip()]
    team = MatchIntelligence(client, meta, languages=langs)
    langs = team.languages
    print(f"Model: {described}\nLanguages: {', '.join(langs) or 'English only'}\n")

    engine = MatchEngine(meta)
    queue = asyncio.Queue()
    latest_state = {}
    explained, recaps = [], []
    first_lang = langs[0] if langs else None

    async def worker():
        while True:
            item = await queue.get()
            if item is None:
                return
            insight, state = item
            if insight["type"] in SUMMARIES:
                r = await team.recap(insight)
                recaps.append(r)
                print(f"\n=== {r['recap']['headline']} ===\n{r['recap']['summary']}\n")
            else:
                m = await team.explain(insight, state)
                explained.append(m)
                flag = "" if m["verified"] else "  [fallback]"
                print(f"[{m['clock']:>6}] {m['narration']['caption']}{flag}")
                print(f"         fan: {m['narration']['fan_take']}")
                if first_lang and first_lang in m["translations"]:
                    print(f"         {first_lang}: {m['translations'][first_lang]['caption']}")

    task = asyncio.create_task(worker())
    start = time.monotonic()
    sent = 0
    for ev in events:
        if a.speed:
            wait = ev["match_seconds"] / a.speed - (time.monotonic() - start)
            if wait > 0:
                await asyncio.sleep(wait)
        for item in engine.ingest(ev):
            if item["kind"] == "state":
                latest_state = item
            elif item["priority"] <= a.max_priority or item["type"] in SUMMARIES:
                if a.limit and sent >= a.limit and item["type"] not in SUMMARIES:
                    continue
                sent += 1
                await queue.put((item, latest_state))
    await queue.put(None)
    await task

    with open(f"{a.match_dir}/explained.jsonl", "w") as f:
        for m in explained:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    with open(f"{a.match_dir}/recaps.json", "w") as f:
        json.dump(recaps, f, ensure_ascii=False, indent=1)
    with open(f"{a.match_dir}/agent_trace.jsonl", "w") as f:
        for t in team.trace:
            f.write(json.dumps(t) + "\n")
    with open(f"{a.match_dir}/player_stats.json", "w") as f:
        json.dump(engine.final_report(), f, indent=1)
    print("\nAgent summary:", team.trace_summary())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("match_dir")
    ap.add_argument("--offline", action="store_true", help="use the rule-based stand-in instead of Azure")
    ap.add_argument("--languages", default="fr,es",
                    help="comma-separated codes (e.g. fr,es,pt,ar) or 'all' for every supported language")
    ap.add_argument("--limit", type=int, default=0, help="explain at most this many moments")
    ap.add_argument("--max-priority", type=int, default=3, help="1 = only the biggest moments")
    ap.add_argument("--speed", type=float, default=0)
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
