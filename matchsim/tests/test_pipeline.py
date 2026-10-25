"""
Tests for the simulator, the stats engine and the agent team.

Run:  python -m pytest -q tests
The agent tests use fault-injecting stand-in models, so no Azure access is needed.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_framework import ChatResponse, Message  # noqa: E402

from agents.offline import OfflineChatClient, _parse  # noqa: E402
from agents.orchestrator import MatchIntelligence, ungrounded_numbers  # noqa: E402
from engine.stats_engine import MatchEngine  # noqa: E402
from generate_match import Match  # noqa: E402


def simulate(seed):
    m = Match(seed)
    m.run()
    return m.metadata(), m.events, m.ground_truth()


def run_engine(meta, events):
    engine, insights, state = MatchEngine(meta), [], None
    for ev in events:
        for item in engine.ingest(ev):
            if item["kind"] == "state":
                state = item
            else:
                insights.append((item, state))
    return engine, insights


# ---------------------------------------------------------------- simulator + engine
def test_simulator_is_reproducible():
    a, b = simulate(11), simulate(11)
    assert a[1] == b[1]


def test_engine_reports_every_goal_and_final_score():
    for seed in (3, 4, 5):
        meta, events, truth = simulate(seed)
        engine, insights = run_engine(meta, events)
        goals = [i for i, _ in insights if i["type"] == "goal"]
        assert len(goals) == sum(truth["final_score"].values())
        assert engine.score == truth["final_score"]


def test_player_profiles_cover_everyone_who_played():
    meta, events, _ = simulate(6)
    engine, _ = run_engine(meta, events)
    report = engine.final_report()
    assert len(report["players"]) >= 22
    first = report["players"][0]
    assert set(first["sections"]) == {"Shooting", "Physical performance", "Passing", "Possession",
                                      "Defending", "Discipline"}


def test_grounding_check():
    source = {"xg": 0.31, "distance_m": 10234, "possession": 0.62, "clock": "45+2"}
    assert ungrounded_numbers(["xG 0.31, 62% of the ball, 10.2 km"], source) == []
    assert ungrounded_numbers(["a 0.87 xG chance"], source) == ["0.87"]


# ---------------------------------------------------------------- fault injection
class FaultyClient(OfflineChatClient):
    """Stand-in model that misbehaves in configurable ways."""

    def __init__(self, invent_numbers=0, reject=0, crash=None, crash_times=0, garbage=None):
        super().__init__()
        self.invent_numbers = invent_numbers  # narrator invents a number this many times
        self.reject = reject                  # fact checker rejects this many times
        self.crash, self.crash_times = crash, crash_times
        self.garbage = garbage                # role that returns non-JSON once
        self.calls = {}

    def _inner_get_response(self, *, messages, stream, options, **kwargs):
        role, _ = _parse(messages[-1].text)
        self.calls[role] = self.calls.get(role, 0) + 1
        if role == self.crash and self.calls[role] <= self.crash_times:
            raise RuntimeError("simulated 429 rate limit")
        if role == self.garbage and self.calls[role] == 1:
            async def junk():
                return ChatResponse(messages=[Message(role="assistant", contents=["not json at all"])])
            return junk()
        if role == "narrator" and self.invent_numbers:
            self.invent_numbers -= 1
            async def invented():
                body = {"caption": "A 97-pass move!", "commentary": "Wow.", "fan_take": "Great.",
                        "analyst_take": "That was 97 passes."}
                return ChatResponse(messages=[Message(role="assistant", contents=[json.dumps(body)])])
            return invented()
        if role == "verifier" and self.reject:
            self.reject -= 1
            async def rejected():
                body = {"approved": False, "problems": ["says the wrong team scored"]}
                return ChatResponse(messages=[Message(role="assistant", contents=[json.dumps(body)])])
            return rejected()
        return super()._inner_get_response(messages=messages, stream=stream, options=options, **kwargs)


def first_goal(seed=7):
    meta, events, _ = simulate(seed)
    _, insights = run_engine(meta, events)
    return meta, next((i, s) for i, s in insights if i["type"] == "goal")


def explain_with(client, **kw):
    meta, (insight, state) = first_goal()
    team = MatchIntelligence(client, meta, languages=["fr"], **kw)
    team_sleep = asyncio.sleep
    async def no_wait(_):  # skip back-off delays in tests
        await team_sleep(0)
    asyncio.sleep = no_wait
    try:
        return team, asyncio.run(team.explain(insight, state))
    finally:
        asyncio.sleep = team_sleep


def test_happy_path_is_verified():
    team, m = explain_with(OfflineChatClient())
    assert m["verified"] and m["source"] == "ai"
    assert "fr" in m["translations"]
    agents = {t["agent"] for t in team.trace}
    assert {"TacticalAnalyst", "BroadcastNarrator", "FactChecker", "Localizer"} <= agents


def test_invented_number_triggers_retry_then_passes():
    team, m = explain_with(FaultyClient(invent_numbers=1))
    assert m["verified"] and m["narrator_attempts"] == 2
    assert any(t["status"] == "retry" for t in team.trace)
    assert "97" not in m["narration"]["caption"]


def test_persistent_invention_falls_back_to_facts():
    _, m = explain_with(FaultyClient(invent_numbers=5))
    assert not m["verified"] and m["source"] == "fallback"
    assert "97" not in json.dumps(m["narration"])
    assert m["narration"]["caption"]


def test_fact_checker_rejection_is_fed_back():
    team, m = explain_with(FaultyClient(reject=1))
    assert m["verified"] and m["narrator_attempts"] == 2


def test_rate_limit_errors_are_retried():
    team, m = explain_with(FaultyClient(crash="analyst", crash_times=2))
    assert m["source"] == "ai"
    statuses = [t["status"] for t in team.trace if t["agent"] == "TacticalAnalyst"]
    assert statuses == ["error", "error", "ok"]


def test_dead_analyst_still_produces_an_overlay():
    _, m = explain_with(FaultyClient(crash="analyst", crash_times=99))
    assert m["source"] == "fallback" and m["narration"]["caption"]


def test_invalid_json_is_retried():
    team, m = explain_with(FaultyClient(garbage="narrator"))
    assert m["verified"]
    assert [t["status"] for t in team.trace if t["agent"] == "BroadcastNarrator"][:2] == ["error", "ok"]


def test_personalize_and_spotlight():
    meta, events, _ = simulate(8)
    engine, insights = run_engine(meta, events)
    team = MatchIntelligence(OfflineChatClient(), meta)
    insight, state = next((i, s) for i, s in insights if i["type"] == "goal")

    async def go():
        await team.explain(insight, state)
        view = await team.personalize(insight["insight_id"], {"mode": "casual", "language": "en",
                                                              "favourite_club": insight["team_name"]})
        spot = await team.spotlight(engine.final_report()["players"][0], {"mode": "analyst", "language": "en"})
        return view, spot
    view, spot = asyncio.run(go())
    assert view["caption"] and spot["headline"]


# ---------------------------------------------------------------- languages + phrasebook
class CapturingClient(OfflineChatClient):
    """Records what each agent was sent."""

    def __init__(self):
        super().__init__()
        self.sent = []

    def _inner_get_response(self, *, messages, stream, options, **kwargs):
        self.sent.append(_parse(messages[-1].text))
        return super()._inner_get_response(messages=messages, stream=stream, options=options, **kwargs)


def test_all_languages_are_translated_in_parallel_batches():
    from agents.orchestrator import ALL_LANGUAGES, LANGUAGES_PER_CALL
    meta, (insight, state) = first_goal()
    client = CapturingClient()
    team = MatchIntelligence(client, meta, languages=["all"])
    m = asyncio.run(team.explain(insight, state))
    assert set(m["translations"]) == set(ALL_LANGUAGES)
    localizer_calls = [d for role, d in client.sent if role == "localizer"]
    assert len(localizer_calls) == -(-len(ALL_LANGUAGES) // LANGUAGES_PER_CALL)
    assert all(len(d["languages"]) <= LANGUAGES_PER_CALL for d in localizer_calls)


def test_phrasebook_reaches_narrator_and_localizer():
    meta, (insight, state) = first_goal()
    client = CapturingClient()
    team = MatchIntelligence(client, meta, languages=["fr", "de", "sw"])
    asyncio.run(team.explain(insight, state))
    narrator = next(d for role, d in client.sent if role == "narrator")
    localizer = next(d for role, d in client.sent if role == "localizer")
    assert any(p["phrase"] == "parking the bus" for p in narrator["phrasebook"])
    assert set(localizer["phrasebook"]) == {"fr", "de"}   # no entries for sw: model uses its own
    assert all("use_when" in p for p in localizer["phrasebook"]["de"])


# ---------------------------------------------------------------- recaps are fact-checked too
def half_time_insight(seed=7):
    meta, events, _ = simulate(seed)
    _, insights = run_engine(meta, events)
    return meta, next(i for i, _ in insights if i["type"] == "half_time")


class RecapFaults(FaultyClient):
    def __init__(self, bad_recaps=0, **kw):
        super().__init__(**kw)
        self.bad_recaps = bad_recaps

    def _inner_get_response(self, *, messages, stream, options, **kwargs):
        role, _ = _parse(messages[-1].text)
        if role == "recap" and self.bad_recaps:
            self.bad_recaps -= 1
            async def invented():
                body = {"headline": "A 77% possession masterclass", "summary": "They had 77% of the ball.",
                        "key_moments": [], "tactical_story": "", "player_of_the_match": ""}
                return ChatResponse(messages=[Message(role="assistant", contents=[json.dumps(body)])])
            return invented()
        return super()._inner_get_response(messages=messages, stream=stream, options=options, **kwargs)


def test_recap_with_invented_number_is_retried():
    meta, ht = half_time_insight()
    team = MatchIntelligence(RecapFaults(bad_recaps=1), meta)
    r = asyncio.run(team.recap(ht))
    assert r["verified"] and r["source"] == "ai" and "77" not in json.dumps(r["recap"])


def test_unverifiable_recap_falls_back_to_real_numbers():
    meta, ht = half_time_insight()
    team = MatchIntelligence(RecapFaults(bad_recaps=5), meta)
    r = asyncio.run(team.recap(ht))
    assert not r["verified"] and r["source"] == "fallback"
    assert "Possession" in r["recap"]["summary"] and "77" not in json.dumps(r["recap"])
