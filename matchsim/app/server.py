"""
MatchLens web app: the Render and Personalize stages.

Replays a synthetic match "live": the stats engine runs in real time on the event feed,
and every moment it detects is explained by the AI agent team, either precomputed
(explained.jsonl from explain_match.py) or live as the match plays. The browser receives
a Server-Sent Events stream of player positions, match state, explained moments with
overlay timing, recaps and agent activity, and asks for personalised views on demand.

Run (from the matchsim folder):
    uvicorn app.server:app --reload --port 8000
"""
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)  # .env and matches/ are relative to the project folder

from agents.clients import make_client  # noqa: E402
from agents.orchestrator import LANGUAGE_NAMES, MatchIntelligence  # noqa: E402
from engine.stats_engine import MatchEngine  # noqa: E402

MATCHES = ROOT / "matches"
STATIC = Path(__file__).resolve().parent / "static"
SUMMARIES = {"half_time", "full_time"}
LIVE_LANGUAGES = [l for l in os.environ.get("LIVE_LANGUAGES", "fr,es,pt,de,ar").split(",") if l]
MAX_SESSIONS = 30

CLIENT, CLIENT_DESC = make_client(offline=os.environ.get("OFFLINE") == "1")
AI_AVAILABLE = not CLIENT_DESC.startswith("offline")

app = FastAPI(title="MatchLens", description="Explainable live match intelligence")
SESSIONS = {}


# ---------------------------------------------------------------- helpers
def read_jsonl(path):
    return [json.loads(line) for line in open(path, encoding="utf-8")] if path.exists() else []


def engine_moment(insight, pending=False):
    """A moment straight from the stats engine, before (or without) the AI agents."""
    head = insight["headline"]
    return {**{k: insight[k] for k in ("insight_id", "match_id", "type", "priority", "clock", "match_seconds",
                                      "score", "team", "team_name", "player_id", "player_name", "headline",
                                      "data", "overlay")},
            "explanation": {"why_it_matters": "", "tactical_context": "", "significance": 4 - insight["priority"],
                            "tags": []},
            "narration": {"caption": head[:80], "commentary": head, "fan_take": head, "analyst_take": head},
            "translations": {}, "verified": True, "source": "engine", "pending": pending}


def compact_state(state, names):
    return {"minute": state["minute"], "match_seconds": state["match_seconds"], "score": state["score"],
            "phase": state["phase"], "momentum_home_share": state["momentum_home_share"],
            "teams": state["teams"], "last_5_min": state["last_5_min"], "top_players": state["top_players"][:3]}


class Session:
    def __init__(self, match_id, speed):
        self.id = uuid.uuid4().hex[:12]
        self.dir = MATCHES / match_id
        if not (self.dir / "events.jsonl").exists():
            raise HTTPException(404, "unknown match")
        self.meta = json.load(open(self.dir / "metadata.json"))
        self.events = read_jsonl(self.dir / "events.jsonl")
        self.tracking = read_jsonl(self.dir / "tracking.jsonl")
        self.pre = {m["insight_id"]: m for m in read_jsonl(self.dir / "explained.jsonl")}
        recaps = json.load(open(self.dir / "recaps.json")) if (self.dir / "recaps.json").exists() else []
        self.pre_recaps = {r["stage"]: r for r in recaps}
        self.pre_trace = {}
        for t in read_jsonl(self.dir / "agent_trace.jsonl"):
            self.pre_trace.setdefault(str(t.get("ref")), []).append(t)
        self.mode = "precomputed" if self.pre else "live" if AI_AVAILABLE else "engine"
        langs = sorted({l for m in self.pre.values() for l in m.get("translations", {})}) if self.pre \
            else (LIVE_LANGUAGES if self.mode == "live" else [])
        self.languages = langs
        self.engine = MatchEngine(self.meta)
        self.team = MatchIntelligence(CLIENT, self.meta, languages=langs if self.mode == "live" else [])
        self.speed = max(1.0, float(speed))
        self.paused = False
        self.out = asyncio.Queue()
        self.work = asyncio.Queue()
        self.state = None
        self.score = {"home": 0, "away": 0}
        self.clock = "1'"
        self.vt = 0.0
        self.trace_sent = 0
        self.created = time.time()

    # ---------------------------------------------------------- outbound
    async def send(self, name, data):
        await self.out.put((name, data))

    def describe(self):
        def team(side):
            m = self.meta[side]
            players = [{"id": p["id"], "name": p["name"], "number": p["number"], "position": p["position"]}
                       for p in m["starting_xi"] + m["bench"]]
            return {"name": m["name"], "short": m["short_name"], "colours": m["colours"],
                    "formation": m["formation"], "players": players}
        return {"session": self.id, "match_id": self.meta["match_id"], "mode": self.mode,
                "ai": CLIENT_DESC, "ai_available": AI_AVAILABLE, "languages": self.languages,
                "venue": self.meta.get("venue"), "competition": self.meta.get("competition"),
                "home": team("home"), "away": team("away"), "speed": self.speed}

    # ---------------------------------------------------------- timeline
    async def wait_until(self, target):
        """Advance the virtual match clock to `target` seconds, honouring speed and pause."""
        last = time.monotonic()
        while self.vt < target:
            await asyncio.sleep(0.03)
            now = time.monotonic()
            if not self.paused:
                self.vt += (now - last) * self.speed  # free-running clock: events already due fire at once
            last = now

    async def produce(self):
        ei = ti = 0
        inf = float("inf")
        while ei < len(self.events) or ti < len(self.tracking):
            te = self.events[ei]["match_seconds"] if ei < len(self.events) else inf
            tt = self.tracking[ti]["match_seconds"] if ti < len(self.tracking) else inf
            if tt <= te:
                await self.wait_until(tt)
                fr = self.tracking[ti]
                ti += 1
                await self.send("tick", {"t": fr["match_seconds"], "period": fr["period"], "clock": self.clock,
                                         "score": self.score, "ball": fr["ball"],
                                         "players": [[p["id"], p["x"], p["y"]] for p in fr["players"]]})
            else:
                await self.wait_until(te)
                ev = self.events[ei]
                ei += 1
                self.score = ev.get("score", self.score)
                self.clock = ev.get("clock", self.clock)
                for item in self.engine.ingest(ev):
                    await self.handle(item)
        await self.work.put(None)

    async def handle(self, item):
        names = self.team.team_names
        if item["kind"] == "state":
            self.state = item
            await self.send("state", compact_state(item, names))
            return
        if item["type"] in SUMMARIES:
            await self.work.put(("recap", item, None))
            return
        pre = self.pre.get(item["insight_id"])
        if pre:
            self.team.explained[pre["insight_id"]] = pre
            self.team.storyline.append({"clock": pre["clock"], "type": pre["type"],
                                        "caption": pre["narration"]["caption"],
                                        "why": pre["explanation"]["why_it_matters"][:200],
                                        "significance": pre["explanation"]["significance"]})
            await self.send("moment", pre)
            for t in self.pre_trace.get(str(pre["insight_id"]), []):
                await self.send("trace", t)
        elif self.mode == "live":
            await self.send("moment", engine_moment(item, pending=True))
            await self.work.put(("explain", item, self.state))
        else:
            m = engine_moment(item)
            self.team.explained[m["insight_id"]] = m
            await self.send("moment", m)

    async def worker(self):
        while True:
            job = await self.work.get()
            if job is None:
                break
            kind, item, state = job
            try:
                if kind == "explain":
                    m = await self.team.explain(item, state)
                    await self.send("moment", {**m, "pending": False})
                else:
                    pre = self.pre_recaps.get(item["type"])
                    recap = pre or await self.team.recap(item)
                    await self.send("recap", {**recap, "clock": item["clock"]})
                    for t in self.pre_trace.get(item["type"], []):
                        await self.send("trace", t)
            except Exception as exc:  # never let one moment stop the broadcast
                await self.send("error", {"message": f"{type(exc).__name__}: {exc}"})
            for t in self.team.trace[self.trace_sent:]:
                await self.send("trace", t)
            self.trace_sent = len(self.team.trace)
        await self.send("end", {"score": self.score, "summary": self.team.trace_summary()})


# ---------------------------------------------------------------- API
class Control(BaseModel):
    speed: float | None = None
    paused: bool | None = None


class Viewer(BaseModel):
    mode: str = "casual"
    language: str = "en"
    favourite_club: str | None = None
    favourite_player: str | None = None
    focus_metric: str | None = None


class PersonalizeRequest(BaseModel):
    insight_id: int
    viewer: Viewer


class SpotlightRequest(BaseModel):
    player_id: str
    viewer: Viewer


def get_session(sid):
    s = SESSIONS.get(sid)
    if not s:
        raise HTTPException(404, "session expired, restart the match")
    return s


@app.get("/api/config")
def config():
    return {"ai": CLIENT_DESC, "ai_available": AI_AVAILABLE, "languages": LANGUAGE_NAMES}


@app.get("/api/matches")
def matches():
    out = []
    for d in sorted(MATCHES.glob("*/metadata.json")):
        meta = json.load(open(d))
        folder = d.parent
        out.append({"id": folder.name, "home": meta["home"]["name"], "away": meta["away"]["name"],
                    "venue": meta.get("venue"), "ai_explained": (folder / "explained.jsonl").exists()})
    return out


@app.post("/api/matches")
def new_match(seed: int | None = None):
    """Generate a brand-new synthetic match (the data-innovation part, on demand)."""
    from generate_match import Match
    seed = seed if seed is not None else int(time.time()) % 100000
    folder = MATCHES / f"match_{seed:05d}"
    folder.mkdir(parents=True, exist_ok=True)
    m = Match(seed)
    m.run()
    json.dump(m.metadata(), open(folder / "metadata.json", "w"), indent=1)
    with open(folder / "events.jsonl", "w") as f:
        for ev in m.events:
            f.write(json.dumps(ev) + "\n")
    with open(folder / "tracking.jsonl", "w") as f:
        for fr in m.tracking:
            f.write(json.dumps(fr) + "\n")
    json.dump(m.ground_truth(), open(folder / "ground_truth.json", "w"), indent=1)
    return {"id": folder.name, "home": m.home.name, "away": m.away.name}


@app.get("/api/stream")
async def stream(request: Request, match: str = "match_00007", speed: float = 60):
    if len(SESSIONS) >= MAX_SESSIONS:
        for sid in sorted(SESSIONS, key=lambda k: SESSIONS[k].created)[:5]:
            SESSIONS.pop(sid, None)
    s = Session(match, speed)
    SESSIONS[s.id] = s

    async def gen():
        yield f"event: session\ndata: {json.dumps(s.describe())}\n\n"
        tasks = [asyncio.create_task(s.produce()), asyncio.create_task(s.worker())]
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    name, data = await asyncio.wait_for(s.out.get(), 1.0)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
                if name == "end":
                    break
        finally:
            for t in tasks:
                t.cancel()

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/session/{sid}/control")
def control(sid: str, body: Control):
    s = get_session(sid)
    if body.speed:
        s.speed = max(1.0, min(600.0, body.speed))
    if body.paused is not None:
        s.paused = body.paused
    return {"speed": s.speed, "paused": s.paused}


@app.get("/api/session/{sid}/player/{pid}")
def player(sid: str, pid: str):
    s = get_session(sid)
    if pid not in s.engine.pstats.p:
        raise HTTPException(404, "unknown player")
    return s.engine.pstats.profile(pid)


@app.post("/api/session/{sid}/personalize")
async def personalize(sid: str, body: PersonalizeRequest):
    s = get_session(sid)
    if body.insight_id not in s.team.explained:
        raise HTTPException(404, "moment not explained yet")
    return await s.team.personalize(body.insight_id, body.viewer.model_dump())


@app.post("/api/session/{sid}/spotlight")
async def spotlight(sid: str, body: SpotlightRequest):
    s = get_session(sid)
    if body.player_id not in s.engine.pstats.p:
        raise HTTPException(404, "unknown player")
    profile = s.engine.pstats.profile(body.player_id)
    return {"profile": profile, "spotlight": await s.team.spotlight(profile, body.viewer.model_dump())}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
