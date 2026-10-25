"""
Match Intelligence orchestrator: the Explain stage.

A team of specialised agents (Microsoft Agent Framework) works on every moment the
stats engine detects:

  Tactical Analyst  -> why the moment matters, using the match state and storyline
  Broadcast Narrator-> overlay caption, commentary, casual-fan and analyst versions
  Fact Checker      -> rejects narration that contradicts the data
  Localizer         -> the same narration in other languages
  Recap Writer      -> half-time and full-time stories
  Personalizer      -> re-shapes a moment for one viewer (on demand)
  Player Spotlight  -> a followed player's match so far (on demand)

The orchestrator owns the shared match memory (the storyline so far), hands each
agent's output to the next, checks every number against the source data, retries the
narrator with the fact checker's feedback, and falls back to the engine's factual
headline if an agent fails or times out, so the broadcast overlay is never empty
and never wrong. Every agent call is recorded in a trace.
"""
import asyncio
import json
import logging
import re
import time

from agent_framework import Agent

from . import prompts
from .phrasebook import phrases_for
from .offline import _analyst as fallback_explanation, _narrator as fallback_narration
from .schemas import (Explanation, Localization, Narration, PersonalizedView, PlayerSpotlight,
                      Recap, Verdict)

logging.getLogger("agent_framework").setLevel(logging.ERROR)

LANGUAGE_NAMES = {"en": "English", "fr": "French", "es": "Spanish", "pt": "Portuguese",
                  "de": "German", "it": "Italian", "ar": "Arabic", "zh": "Chinese (Simplified)",
                  "ja": "Japanese", "ko": "Korean", "hi": "Hindi", "sw": "Swahili", "nl": "Dutch",
                  "tr": "Turkish", "pl": "Polish", "id": "Indonesian", "yo": "Yoruba", "ha": "Hausa",
                  "bn": "Bengali", "ur": "Urdu", "am": "Amharic", "ru": "Russian", "uk": "Ukrainian",
                  "el": "Greek", "sv": "Swedish", "no": "Norwegian", "da": "Danish", "fi": "Finnish",
                  "ms": "Malay", "th": "Thai", "vi": "Vietnamese", "fil": "Filipino", "he": "Hebrew",
                  "fa": "Persian", "ro": "Romanian", "hu": "Hungarian", "cs": "Czech", "hr": "Croatian",
                  "sr": "Serbian", "ta": "Tamil", "pa": "Punjabi", "zu": "Zulu"}
ALL_LANGUAGES = [c for c in LANGUAGE_NAMES if c != "en"]
LANGUAGES_PER_CALL = 6   # bigger sets are split into parallel batches
NUM_RE = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?")
MAX_CAPTION = 80


# ---------------------------------------------------------------- grounding check
def _collect_numbers(obj, out):
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, (int, float)):
        v = float(obj)
        out.update({v, round(v), round(v, 1), round(v, 2)})
        if 0 < abs(v) <= 1:
            out.update({round(v * 100), round(v * 100, 1)})       # shares as percentages
        if abs(v) >= 1000:
            out.update({round(v / 1000, 1), round(v / 1000, 2)})  # metres as km
        if abs(v) >= 60:
            out.update({round(v / 60), int(v // 60), int(v // 60) + 1})  # seconds as minutes
    elif isinstance(obj, str):
        for m in NUM_RE.findall(obj):
            _collect_numbers(float(m.replace(",", ".")), out)
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _collect_numbers(v, out)


def ungrounded_numbers(texts, source):
    """Numbers in `texts` that do not appear (allowing rounding) anywhere in `source`."""
    allowed = set()
    _collect_numbers(source, allowed)
    bad = []
    for text in texts:
        for m in NUM_RE.findall(text or ""):
            v = float(m.replace(",", "."))
            if v <= 2 and v.is_integer() and "." not in m and "," not in m:
                continue  # small whole numbers: "1v1", "2nd half"...
            if not any(abs(v - a) <= max(0.051, 0.01 * abs(a)) for a in allowed):
                bad.append(m)
    return sorted(set(bad))


# ---------------------------------------------------------------- orchestrator
class MatchIntelligence:
    def __init__(self, client, metadata, languages=(), timeout_s=45, max_attempts=3):
        self.meta = metadata
        self.team_names = {s: metadata[s]["name"] for s in ("home", "away")}
        if list(languages) == ["all"]:
            languages = ALL_LANGUAGES
        self.languages = [l for l in languages if l and l != "en"]
        self.timeout_s = timeout_s
        self.max_attempts = max_attempts
        mk = lambda name, text: Agent(client, instructions=text, name=name)
        self.agents = {
            "analyst": mk("TacticalAnalyst", prompts.ANALYST),
            "narrator": mk("BroadcastNarrator", prompts.NARRATOR),
            "verifier": mk("FactChecker", prompts.VERIFIER),
            "localizer": mk("Localizer", prompts.LOCALIZER),
            "recap": mk("RecapWriter", prompts.RECAP),
            "personalizer": mk("Personalizer", prompts.PERSONALIZER),
            "spotlight": mk("PlayerSpotlight", prompts.SPOTLIGHT),
        }
        self.storyline = []   # shared match memory
        self.explained = {}   # insight_id -> explained moment
        self.trace = []
        self._personal_cache = {}

    # ---------------------------------------------------------- plumbing
    def _log(self, role, ref, started, status, attempt, note=""):
        self.trace.append({"agent": self.agents[role].name, "ref": ref, "status": status,
                           "attempt": attempt + 1, "ms": round((time.monotonic() - started) * 1000),
                           "note": note[:200], "at": time.time()})

    async def _call(self, role, payload, schema, ref=None):
        """Run one agent with structured output; retry with back-off; None if it keeps failing."""
        prompt = f"ROLE: {role}\nINPUT:\n{json.dumps(payload, ensure_ascii=False, default=str)}"
        for attempt in range(self.max_attempts):
            started = time.monotonic()
            try:
                resp = await asyncio.wait_for(
                    self.agents[role].run(prompt, options={"response_format": schema}), self.timeout_s)
                value = resp.value if isinstance(resp.value, schema) else schema.model_validate_json(resp.text)
                self._log(role, ref, started, "ok", attempt)
                return value
            except Exception as exc:  # timeouts, rate limits, invalid JSON, network
                self._log(role, ref, started, "error", attempt, f"{type(exc).__name__}: {exc}")
                if attempt + 1 < self.max_attempts:
                    await asyncio.sleep(min(2 ** (attempt + 1), 10))
        return None

    def _state_view(self, state):
        if not state:
            return {}
        n = self.team_names
        keys = ("goals", "xg", "shots", "shots_on_target", "big_chances", "possession",
                "pass_accuracy", "pressures", "final_third_entries", "red_cards")
        return {"minute": state.get("minute"), "score": {n[s]: state["score"][s] for s in n},
                "phase": state.get("phase"),
                "attacking_threat_share_last_5_min": {n["home"]: state.get("momentum_home_share"),
                                                      n["away"]: round(1 - state.get("momentum_home_share", 0.5), 3)},
                "teams": {n[s]: {k: state["teams"][s].get(k) for k in keys} for s in n},
                "last_5_min": {n[s]: state.get("last_5_min", {}).get(s) for s in n}}

    @staticmethod
    def _moment_view(ins):
        keep = ("insight_id", "type", "priority", "clock", "team_name", "player_name", "headline", "data")
        view = {k: ins.get(k) for k in keep}
        view["score"] = ins.get("score")
        return view

    # ---------------------------------------------------------- live moments
    async def explain(self, insight, state=None):
        """Run one detected moment through the agent team."""
        ref = insight["insight_id"]
        light = insight["priority"] >= 3   # minor moments skip the analyst and fact checker
        ctx = {"teams": self.team_names, "moment": self._moment_view(insight),
               "match_state": self._state_view(state), "storyline_so_far": self.storyline[-6:]}

        explanation = None if light else await self._call("analyst", ctx, Explanation, ref)
        source = "ai"
        if explanation is None:
            explanation = Explanation(**fallback_explanation(ctx))
            source = "ai_light" if light else "fallback"

        narration, problems, verified, attempts = None, [], False, 0
        for attempts in range(1, 3):
            payload = {**ctx, "explanation": explanation.model_dump(), "phrasebook": phrases_for(["en"])["en"]}
            if problems:
                payload["feedback"] = problems
            narration = await self._call("narrator", payload, Narration, ref)
            if narration is None:
                break
            texts = [narration.caption, narration.commentary, narration.fan_take, narration.analyst_take]
            problems = [f"number not in the data: {n}" for n in ungrounded_numbers(texts, ctx)]
            if not problems and not light:
                verdict = await self._call("verifier", {"source_data": ctx, "narration": narration.model_dump()},
                                           Verdict, ref)
                if verdict is not None and not verdict.approved:
                    problems = verdict.problems or ["rejected by fact checker"]
            if not problems:
                verified = True
                break
            self.trace.append({"agent": "Orchestrator", "ref": ref, "status": "retry",
                               "note": "; ".join(problems)[:200], "at": time.time()})
        if not verified:
            narration = Narration(**fallback_narration({**ctx, "explanation": explanation.model_dump()}))
            source = "fallback"
        if len(narration.caption) > MAX_CAPTION:
            narration.caption = narration.caption[:MAX_CAPTION - 1].rstrip() + "…"

        translations = await self.translate(
            narration.caption, narration.commentary, narration.fan_take, ref,
            context={"type": insight["type"], "score_state": insight.get("data", {}).get("score_state"),
                     "data": insight.get("data")})

        self.storyline.append({"clock": insight["clock"], "type": insight["type"], "caption": narration.caption,
                               "why": explanation.why_it_matters[:200], "significance": explanation.significance})
        moment = {"insight_id": ref, "match_id": insight["match_id"], "type": insight["type"],
                  "priority": insight["priority"], "clock": insight["clock"], "match_seconds": insight["match_seconds"],
                  "score": insight["score"], "team": insight["team"], "team_name": insight["team_name"],
                  "player_id": insight["player_id"], "player_name": insight["player_name"],
                  "headline": insight["headline"], "data": insight["data"], "overlay": insight["overlay"],
                  "explanation": explanation.model_dump(), "narration": narration.model_dump(),
                  "translations": translations, "verified": verified, "source": source,
                  "narrator_attempts": attempts}
        self.explained[ref] = moment
        return moment

    async def translate(self, caption, commentary, fan_take, ref=None, context=None):
        """Localize into every chosen language, in parallel batches of LANGUAGES_PER_CALL."""
        if not self.languages:
            return {}
        batches = [self.languages[i:i + LANGUAGES_PER_CALL]
                   for i in range(0, len(self.languages), LANGUAGES_PER_CALL)]

        async def one(batch):
            payload = {"languages": batch,
                       "language_names": {l: LANGUAGE_NAMES.get(l, l) for l in batch},
                       "phrasebook": phrases_for(batch),
                       "moment_context": context or {},
                       "narration": {"caption": caption, "commentary": commentary, "fan_take": fan_take}}
            loc = await self._call("localizer", payload, Localization, ref)
            return [] if loc is None else loc.items

        results = await asyncio.gather(*(one(b) for b in batches))
        return {i.language: i.model_dump(exclude={"language"})
                for items in results for i in items if i.language in self.languages}

    async def recap(self, summary_insight):
        """Half-time or full-time recap from the engine summary and the storyline."""
        stage = summary_insight["type"]
        data = summary_insight["data"]
        n = self.team_names
        summary = {"headline": summary_insight["headline"], "clock": summary_insight["clock"],
                   "teams": {n[s]: data["teams"][s] for s in n},
                   "xg_tells_different_story": data.get("xg_tells_different_story"),
                   "top_players": data.get("top_players"), "player_of_the_match": data.get("player_of_the_match")}
        key = [m for m in self.storyline if m["significance"] >= 3] or self.storyline
        payload = {"stage": stage, "teams": n, "summary": summary, "key_moments": key[-10:]}
        recap, problems, verified = None, [], False
        for _ in range(2):  # same checks as live moments: numbers in code, claims by the Fact Checker
            attempt = dict(payload, feedback=problems) if problems else payload
            recap = await self._call("recap", attempt, Recap, stage)
            if recap is None:
                break
            texts = [recap.headline, recap.summary, recap.tactical_story, recap.player_of_the_match, *recap.key_moments]
            problems = [f"number not in the data: {x}" for x in ungrounded_numbers(texts, payload)]
            if not problems:
                verdict = await self._call("verifier", {"source_data": payload, "narration": recap.model_dump()},
                                           Verdict, stage)
                if verdict is not None and not verdict.approved:
                    problems = verdict.problems or ["rejected by fact checker"]
            if not problems:
                verified = True
                break
            self.trace.append({"agent": "Orchestrator", "ref": stage, "status": "retry",
                               "note": "; ".join(problems)[:200], "at": time.time()})
        source = "ai"
        if not verified:
            recap = self._factual_recap(summary_insight, key)
            source = "fallback"
        translations = await self.translate(recap.headline, recap.summary, recap.tactical_story, stage)
        return {"stage": stage, "recap": recap.model_dump(), "translations": translations, "source": source,
                "verified": verified, "score": summary_insight["score"]}

    def _factual_recap(self, summary_insight, key):
        """Plain recap built only from engine numbers, used if the AI recap can't be verified."""
        n, t = self.team_names, summary_insight["data"]["teams"]
        pct = lambda v: f"{round(100 * v)}%" if v is not None else "-"
        line = (f"Possession {n['home']} {pct(t['home']['possession'])}, {n['away']} {pct(t['away']['possession'])}. "
                f"Shots {t['home']['shots']}-{t['away']['shots']}, on target {t['home']['shots_on_target']}-"
                f"{t['away']['shots_on_target']}, xG {t['home']['xg']}-{t['away']['xg']}.")
        pom = (summary_insight["data"].get("player_of_the_match") or {}).get("name", "")
        return Recap(headline=summary_insight["headline"], summary=line,
                     key_moments=[f"{m['clock']} {m['caption']}" for m in key[-6:]], tactical_story="",
                     player_of_the_match=pom)

    # ---------------------------------------------------------- on demand, per viewer
    async def personalize(self, insight_id, viewer):
        """viewer: {"mode": "casual"|"analyst", "language": "en", "favourite_club": ..., "favourite_player": ..., "focus_metric": ...}"""
        key = (insight_id, json.dumps(viewer, sort_keys=True))
        if key in self._personal_cache:
            return self._personal_cache[key]
        moment = self.explained[insight_id]
        payload = {"viewer": {**viewer, "language_name": LANGUAGE_NAMES.get(viewer.get("language", "en"), "English")},
                   "teams": self.team_names,
                   "moment": {k: moment[k] for k in ("clock", "type", "score", "team_name", "player_name",
                                                     "headline", "data", "explanation", "narration")}}
        view = await self._call("personalizer", payload, PersonalizedView, insight_id)
        if view is None:
            text = moment["narration"]["fan_take" if viewer.get("mode") == "casual" else "analyst_take"]
            view = PersonalizedView(caption=moment["narration"]["caption"], text=text, why_you_care="")
        self._personal_cache[key] = view.model_dump()
        return self._personal_cache[key]

    async def spotlight(self, player_profile, viewer):
        payload = {"viewer": {**viewer, "language_name": LANGUAGE_NAMES.get(viewer.get("language", "en"), "English")},
                   "player": player_profile}
        result = await self._call("spotlight", payload, PlayerSpotlight, player_profile.get("player_id"))
        if result is None:
            return {"headline": f"{player_profile['name']}: rating {player_profile['rating']}", "text": "",
                    "standout_stats": []}
        return result.model_dump()

    def trace_summary(self):
        calls = [t for t in self.trace if t["agent"] != "Orchestrator"]
        ok = sum(t["status"] == "ok" for t in calls)
        return {"agent_calls": len(calls), "succeeded": ok, "errors": len(calls) - ok,
                "narrator_retries": sum(t["status"] == "retry" for t in self.trace),
                "fallbacks": sum(m["source"] == "fallback" for m in self.explained.values()),
                "avg_ms": round(sum(t["ms"] for t in calls) / len(calls)) if calls else 0}
