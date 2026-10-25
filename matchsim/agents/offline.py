"""
Offline stand-in for the language model.

It answers each agent with simple, rule-based JSON built from the input data, so the
whole multi-agent pipeline (orchestration, verification, retries, fallbacks, overlays,
personalization) can run and be tested without Azure. Real explanations, natural
commentary and real translations need the Foundry model.
"""
import json

from agent_framework import BaseChatClient, ChatResponse, Message

NAMES = {"fr": "FR", "es": "ES", "pt": "PT", "de": "DE", "it": "IT", "ar": "AR", "zh": "ZH",
         "ja": "JA", "ko": "KO", "hi": "HI", "sw": "SW", "nl": "NL", "tr": "TR"}


def _parse(text):
    role, _, rest = text.partition("\n")
    role = role.replace("ROLE:", "").strip()
    payload = rest.split("INPUT:", 1)[-1]
    try:
        return role, json.loads(payload)
    except json.JSONDecodeError:
        return role, {}


def _analyst(d):
    ins = d.get("moment", {})
    t = ins.get("type", "")
    team = ins.get("team_name") or "The game"
    data = ins.get("data", {})
    why = {
        "goal": f"The goal is the {data.get('scoreline_effect', 'goal').replace('_', ' ')} and changes the game state.",
        "momentum_shift": f"{team} now hold {round(100 * data.get('threat_share', 0))}% of the attacking threat.",
        "pressing_surge": f"{team} are pressing at {data.get('pressures_per_min_last_5')} per minute, well above their average of {data.get('match_average')}.",
        "pressing_drop": f"{team} have eased off to {data.get('pressures_per_min_last_5')} pressures per minute.",
        "phase_change": f"Turnovers are running at {data.get('turnovers_per_min')} per minute.",
        "big_chance_missed": f"A chance worth {data.get('xg')} xG went begging.",
    }.get(t, ins.get("headline", ""))
    return {"why_it_matters": why, "tactical_context": "Rule-based summary (offline mode).",
            "significance": 5 - min(ins.get("priority", 3), 3), "tags": [t.split("_")[0] or "moment"]}


def _narrator(d):
    ins = d.get("moment", {})
    head = ins.get("headline", "")
    why = d.get("explanation", {}).get("why_it_matters", head)
    return {"caption": head[:70], "commentary": f"{ins.get('clock', '')}: {head}.",
            "fan_take": why, "analyst_take": why}


def _localizer(d):
    n = d.get("narration", {})
    return {"items": [{"language": code, "caption": f"[{NAMES.get(code, code.upper())}] {n.get('caption', '')}",
                       "commentary": f"[{NAMES.get(code, code.upper())}] {n.get('commentary', '')}",
                       "fan_take": f"[{NAMES.get(code, code.upper())}] {n.get('fan_take', '')}"}
                      for code in d.get("languages", [])]}


def _recap(d):
    s = d.get("summary", {})
    moments = [f"{m.get('clock')} {m.get('caption')}" for m in d.get("key_moments", [])][:6]
    pom = s.get("data", {}).get("player_of_the_match") or {}
    return {"headline": s.get("headline", "Recap"), "summary": s.get("headline", ""),
            "key_moments": moments, "tactical_story": "Rule-based recap (offline mode).",
            "player_of_the_match": pom.get("name", "") if d.get("stage") == "full_time" else ""}


def _personalizer(d):
    m = d.get("moment", {}).get("narration", {})
    return {"caption": m.get("caption", ""), "text": m.get("fan_take" if d.get("viewer", {}).get("mode") == "casual" else "analyst_take", ""),
            "why_you_care": "Matches your viewing preferences."}


def _spotlight(d):
    p = d.get("player", {})
    stats = []
    for items in p.get("sections", {}).values():
        for it in items:
            if it.get("rank") and it["rank"] <= 3 and it.get("value"):
                stats.append(f"{it['stat']} {it['value']} ({it['rank']} of {it['of']})")
    return {"headline": f"{p.get('name', 'Player')}: rating {p.get('rating')}",
            "text": f"{p.get('name')} has played {p.get('minutes_played')} minutes.", "standout_stats": stats[:5]}


HANDLERS = {"analyst": _analyst, "narrator": _narrator, "verifier": lambda d: {"approved": True, "problems": []},
            "localizer": _localizer, "recap": _recap, "personalizer": _personalizer, "spotlight": _spotlight}


class OfflineChatClient(BaseChatClient):
    OTEL_PROVIDER_NAME = "offline"

    def _inner_get_response(self, *, messages, stream, options, **kwargs):
        role, data = _parse(messages[-1].text)
        body = HANDLERS.get(role, lambda d: {})(data)

        async def respond():
            return ChatResponse(messages=[Message(role="assistant", contents=[json.dumps(body)])])
        return respond()
