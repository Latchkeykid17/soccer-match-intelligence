"""Web app tests: API, the live SSE stream, live agent mode, personalisation and player profiles."""
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["OFFLINE"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from app import server  # noqa: E402

client = TestClient(server.app)


def read_stream(url, stop_after=None):
    """Collect (event, data) pairs from the SSE stream."""
    out, event = [], None
    with client.stream("GET", url) as r:
        for line in r.iter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                out.append((event, json.loads(line[5:])))
                if event == "end" or (stop_after and len(out) >= stop_after):
                    break
    return out


def test_config_and_matches():
    assert client.get("/api/config").json()["languages"]["fr"] == "French"
    assert any(m["id"] == "match_00007" for m in client.get("/api/matches").json())
    assert client.get("/").status_code == 200


def test_full_stream_engine_mode():
    events = read_stream("/api/stream?match=match_00007&speed=20000")
    kinds = [e for e, _ in events]
    assert kinds[0] == "session" and kinds[-1] == "end"
    assert kinds.count("tick") > 1000 and kinds.count("state") > 80
    moments = [d for e, d in events if e == "moment"]
    goals = [m for m in moments if m["type"] == "goal"]
    truth = json.load(open(server.MATCHES / "match_00007" / "ground_truth.json"))
    assert len(goals) == sum(truth["final_score"].values())
    assert all(m["narration"]["caption"] and m["overlay"]["slot"] for m in moments)
    assert {d["stage"] for e, d in events if e == "recap"} == {"half_time", "full_time"}


def test_live_agent_mode_and_personalisation(monkeypatch):
    monkeypatch.setattr(server, "AI_AVAILABLE", True)
    new = client.post("/api/matches?seed=4242").json()
    events = read_stream(f"/api/stream?match={new['id']}&speed=20000")
    session = events[0][1]
    assert session["mode"] == "live"
    by_id = {}
    for e, d in events:
        if e == "moment":
            by_id.setdefault(d["insight_id"], []).append(d)
    first = next(v for v in by_id.values() if v[0]["priority"] <= 2)
    assert first[0]["pending"] is True and first[-1]["pending"] is False   # engine first, then agents
    assert first[-1]["source"] in ("ai", "fallback")
    assert any(e == "trace" for e, _ in events)

    sid = session["session"]
    pid = session["home"]["players"][9]["id"]
    profile = client.get(f"/api/session/{sid}/player/{pid}").json()
    assert profile["minutes_played"] > 0 and "Physical performance" in profile["sections"]
    view = client.post(f"/api/session/{sid}/personalize", json={
        "insight_id": first[-1]["insight_id"],
        "viewer": {"mode": "casual", "language": "fr", "favourite_club": session["home"]["name"]}}).json()
    assert view["caption"]
    spot = client.post(f"/api/session/{sid}/spotlight", json={"player_id": pid, "viewer": {"mode": "analyst"}}).json()
    assert spot["spotlight"]["headline"]
    shutil.rmtree(server.MATCHES / new["id"], ignore_errors=True)


def test_speed_and_pause_controls():
    events = read_stream("/api/stream?match=match_00007&speed=60", stop_after=1)
    sid = events[0][1]["session"]
    r = client.post(f"/api/session/{sid}/control", json={"speed": 240, "paused": True}).json()
    assert r == {"speed": 240, "paused": True}
    assert client.post("/api/session/nope/control", json={"speed": 2}).status_code == 404


def test_exports_for_spreadsheets_and_pipelines():
    import csv, io, zipfile
    r = client.get("/api/matches/match_00007/export/players.csv")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert len(rows) >= 22 and "Expected goals (xG)" in rows[0] and "Top speed (km/h)" in rows[0]
    for table in ("teams", "moments", "timeline"):
        assert client.get(f"/api/matches/match_00007/export/{table}.json").json()
    lines = client.get("/api/matches/match_00007/export/events.jsonl").text.strip().splitlines()
    assert len(lines) > 1000 and json.loads(lines[0])["type"]
    z = zipfile.ZipFile(io.BytesIO(client.get("/api/matches/match_00007/export.zip").content))
    assert {"players.csv", "teams.csv", "moments.csv", "timeline.csv", "events.jsonl",
            "DATA_DICTIONARY.md", "metadata.json"} <= set(z.namelist())
    assert client.get("/api/matches/match_00007/export/nope.csv").status_code == 400


def test_live_export_is_stats_so_far():
    import csv, io
    events = read_stream("/api/stream?match=match_00007&speed=20000")
    sid = events[0][1]["session"]
    r = client.get(f"/api/session/{sid}/export/teams.csv")
    rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert len(rows) == 2 and rows[0]["team"]
    assert client.get(f"/api/session/{sid}/export.zip").status_code == 200
