"""
Build the free, no-server version of MatchLens for GitHub Pages.

It replays the AI-explained match through the stats engine once, records everything the
live server would stream (player positions, match state, explained moments, recaps, agent
trace), and writes it with the web app into ../docs. The browser then replays it with
static.js. Live AI calls (new matches, personalised rewrites, spotlights) need the full app.

    python build_static.py                      # uses matches/match_00007
    python build_static.py matches/match_00042
"""
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from agents.clients import load_env  # noqa: E402
from agents.orchestrator import LANGUAGE_NAMES  # noqa: E402
from app import exporter  # noqa: E402
from engine.stats_engine import MatchEngine  # noqa: E402

SUMMARIES = {"half_time", "full_time"}
PROFILE_EVERY_S = 300  # player-card snapshot every 5 match minutes


def read_jsonl(path):
    return [json.loads(line) for line in open(path, encoding="utf-8")] if path.exists() else []


def compact_state(st):
    return {"minute": st["minute"], "match_seconds": st["match_seconds"], "score": st["score"],
            "phase": st["phase"], "momentum_home_share": st["momentum_home_share"],
            "teams": st["teams"], "last_5_min": st["last_5_min"], "top_players": st["top_players"][:3]}


def r2(v):
    return round(v, 2)


def main():
    folder = Path(sys.argv[1] if len(sys.argv) > 1 else "matches/match_00007")
    out = ROOT.parent / "docs"
    meta = json.load(open(folder / "metadata.json"))
    events = read_jsonl(folder / "events.jsonl")
    tracking = read_jsonl(folder / "tracking.jsonl")
    pre = {m["insight_id"]: m for m in read_jsonl(folder / "explained.jsonl")}
    if not pre:
        sys.exit(f"STOPPED: {folder} has no explained.jsonl. Run explain_match.py on it first.")
    ai_moments = sum(1 for m in pre.values() if m.get("source") in ("ai", "ai_light"))
    recaps = {r["stage"]: r for r in (json.load(open(folder / "recaps.json")) if (folder / "recaps.json").exists() else [])}
    traces = {}
    for t in read_jsonl(folder / "agent_trace.jsonl"):
        traces.setdefault(str(t.get("ref")), []).append(t)
    languages = sorted({l for m in pre.values() for l in m.get("translations", {})})

    load_env()
    model = os.environ.get("AZURE_OPENAI_MODEL", "gpt-4.1-mini")
    ai_desc = f"Explained by Microsoft Foundry model '{model}'"

    def team(side):
        m = meta[side]
        return {"name": m["name"], "short": m["short_name"], "colours": m["colours"], "formation": m["formation"],
                "players": [{"id": p["id"], "name": p["name"], "number": p["number"], "position": p["position"]}
                            for p in m["starting_xi"] + m["bench"]]}

    session = {"session": "static", "match_id": meta["match_id"], "mode": "precomputed", "ai": ai_desc,
               "ai_available": False, "languages": languages, "venue": meta.get("venue"),
               "competition": meta.get("competition"), "home": team("home"), "away": team("away")}

    # Replay exactly like the live server, recording what it would stream and when.
    engine = MatchEngine(meta)
    timeline, snapshots = [], []
    score, clock = {"home": 0, "away": 0}, "1'"
    next_snap = 0
    ei = ti = 0
    inf = float("inf")
    while ei < len(events) or ti < len(tracking):
        te = events[ei]["match_seconds"] if ei < len(events) else inf
        tt = tracking[ti]["match_seconds"] if ti < len(tracking) else inf
        if tt <= te:
            fr = tracking[ti]
            ti += 1
            timeline.append([fr["match_seconds"], "tick", {
                "t": fr["match_seconds"], "period": fr["period"], "clock": clock, "score": score,
                "ball": {"x": r2(fr["ball"]["x"]), "y": r2(fr["ball"]["y"])},
                "players": [[p["id"], r2(p["x"]), r2(p["y"])] for p in fr["players"]]}])
            continue
        ev = events[ei]
        ei += 1
        t = ev["match_seconds"]
        score, clock = ev.get("score", score), ev.get("clock", clock)
        for item in engine.ingest(ev):
            if item["kind"] == "state":
                timeline.append([t, "state", compact_state(item)])
            elif item["type"] in SUMMARIES:
                if item["type"] in recaps:
                    timeline.append([t, "recap", {**recaps[item["type"]], "clock": item["clock"]}])
                    timeline += [[t, "trace", tr] for tr in traces.get(item["type"], [])]
            elif item["insight_id"] in pre:
                timeline.append([t, "moment", pre[item["insight_id"]]])
                timeline += [[t, "trace", tr] for tr in traces.get(str(item["insight_id"]), [])]
        if t >= next_snap:
            snapshots.append({"t": t, "players": {pid: engine.pstats.profile(pid) for pid in engine.pstats.p}})
            next_snap = t + PROFILE_EVERY_S
    end_t = timeline[-1][0] + 1
    snapshots.append({"t": end_t, "players": {pid: engine.pstats.profile(pid) for pid in engine.pstats.p}})
    calls = sum(len(v) for v in traces.values())
    timeline.append([end_t, "end", {"score": score, "summary": {}}])

    config = {"ai": ai_desc, "ai_available": False,
              "status": f"AI-explained replay · Microsoft Foundry {model}",
              "languages": {c: LANGUAGE_NAMES.get(c, c) for c in languages}}
    matches = [{"id": folder.name, "home": meta["home"]["name"], "away": meta["away"]["name"],
                "venue": meta.get("venue"), "ai_explained": True}]

    # Write the site.
    if out.exists():
        shutil.rmtree(out)
    (out / "data" / "export").mkdir(parents=True)
    static = ROOT / "app" / "static"
    html = (static / "index.html").read_text(encoding="utf-8")
    html = html.replace('href="/static/style.css"', 'href="style.css"')
    html = html.replace('<script src="/static/app.js"></script>',
                        '<script src="static.js"></script>\n  <script src="app.js"></script>')
    (out / "index.html").write_text(html, encoding="utf-8")
    for f in ("style.css", "app.js", "static.js"):
        shutil.copy(static / f, out / f)
    (out / ".nojekyll").write_text("")
    dump = lambda obj, p: p.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str), encoding="utf-8")
    dump({"config": config, "matches": matches, "session": session, "timeline": timeline}, out / "data" / "match.json")
    dump(snapshots, out / "data" / "profiles.json")

    # Full-match downloads, identical to the live app's.
    eng, insights, states = exporter.run_full_match(meta, events)
    for table in exporter.TABLES:
        fmt = "jsonl" if table == "events" else "csv"
        body, _ = exporter.serialise(exporter.build_table(table, eng, insights, states, events, pre), fmt)
        (out / "data" / "export" / f"{table}.{fmt}").write_bytes(body if isinstance(body, bytes) else body.encode("utf-8"))
    (out / "data" / "export.zip").write_bytes(exporter.bundle(meta, eng, insights, states, events, pre))

    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file()) / 1e6
    print(f"Built {out} ({size:.1f} MB): {len(pre)} moments ({ai_moments} by the AI), "
          f"{len(recaps)} recaps, {calls} agent trace entries, languages: en {' '.join(languages)}")


if __name__ == "__main__":
    main()
