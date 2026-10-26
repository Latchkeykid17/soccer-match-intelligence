"""
Data export: match data as tables for spreadsheets (CSV) and pipelines (JSON / JSON Lines).

Tables
  players   one row per player who played: every profile stat, rating, minutes
  teams     one row per team: totals and rates
  moments   one row per detected moment: engine headline, AI caption, fan and analyst text
  timeline  one row per minute: score, possession, xG, shots, momentum, phase
  events    the raw synthetic event feed, one row per event

CSV files are written as UTF-8 with a byte-order mark so Excel shows accented names correctly.
"""
import csv
import io
import json
import zipfile

from engine.player_stats import SECTIONS
from engine.stats_engine import MatchEngine

TABLES = ("players", "teams", "moments", "timeline", "events")
FORMATS = ("csv", "json", "jsonl")
SIDES = ("home", "away")


def _flat(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def run_full_match(meta, events):
    """Replay the whole feed through a fresh engine (takes well under a second)."""
    engine, insights, states = MatchEngine(meta), [], []
    for ev in events:
        for item in engine.ingest(ev):
            (states if item["kind"] == "state" else insights).append(item)
    return engine, insights, states


def players_rows(engine):
    rows = []
    for prof in engine.pstats.all_profiles():
        row = {"player_id": prof["player_id"], "name": prof["name"], "number": prof["number"],
               "position": prof["position"], "team": prof["team_name"],
               "minutes_played": prof["minutes_played"], "rating": prof["rating"]}
        for items in prof["sections"].values():
            for it in items:
                row[it["stat"]] = it["value"]
        rows.append(row)
    return rows


def teams_rows(engine):
    rows = []
    for side in SIDES:
        s = engine.team_summary(side)
        rows.append({"side": side, "team": engine.team_names[side],
                     "opponent": engine.team_names["away" if side == "home" else "home"], **s})
    return rows


def moments_rows(insights, explained):
    rows = []
    for ins in insights:
        m = explained.get(ins["insight_id"], {})
        n, e = m.get("narration", {}), m.get("explanation", {})
        rows.append({"insight_id": ins["insight_id"], "clock": ins["clock"], "match_seconds": ins["match_seconds"],
                     "period": ins["period"], "type": ins["type"], "priority": ins["priority"],
                     "team": ins.get("team_name"), "player": ins.get("player_name"),
                     "score_home": ins["score"]["home"], "score_away": ins["score"]["away"],
                     "engine_headline": ins["headline"],
                     "ai_caption": n.get("caption", ""), "commentary": n.get("commentary", ""),
                     "fan_take": n.get("fan_take", ""), "analyst_take": n.get("analyst_take", ""),
                     "why_it_matters": e.get("why_it_matters", ""), "significance": e.get("significance", ""),
                     "fact_checked": m.get("verified", "") if m else "", "source": m.get("source", "engine"),
                     "languages": ",".join(sorted(m.get("translations", {}))) if m else "",
                     "evidence": json.dumps(ins.get("data", {}), ensure_ascii=False)})
    return rows


def timeline_rows(states, names):
    rows = []
    for s in states:
        row = {"minute": s["minute"], "period": s["period"], "match_seconds": s["match_seconds"],
               "score_home": s["score"]["home"], "score_away": s["score"]["away"],
               "phase": s["phase"], "momentum_home_share": s["momentum_home_share"]}
        for side in SIDES:
            t, l5 = s["teams"][side], s["last_5_min"][side]
            for k in ("possession", "xg", "shots", "shots_on_target", "passes", "pass_accuracy", "pressures"):
                row[f"{side}_{k}"] = t.get(k)
            row[f"{side}_xg_last_5"] = l5.get("xg")
            row[f"{side}_pressures_last_5"] = l5.get("pressures")
        rows.append(row)
    return rows


def events_rows(events):
    rows = []
    for ev in events:
        if ev["type"] == "physical_update":
            continue  # bulky GPS snapshots; physical totals are in the players table
        row = {k: _flat(v) for k, v in ev.items() if k != "score"}
        row["score_home"], row["score_away"] = ev["score"]["home"], ev["score"]["away"]
        rows.append(row)
    return rows


def build_table(name, engine, insights, states, events, explained):
    if name == "players":
        return players_rows(engine)
    if name == "teams":
        return teams_rows(engine)
    if name == "moments":
        return moments_rows(insights, explained)
    if name == "timeline":
        return timeline_rows(states, engine.team_names)
    if name == "events":
        return events_rows(events)
    raise ValueError(f"unknown table {name}")


def serialise(rows, fmt):
    """Return (bytes, media type)."""
    if fmt == "json":
        return json.dumps(rows, ensure_ascii=False, indent=1).encode("utf-8"), "application/json"
    if fmt == "jsonl":
        return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode("utf-8"), "application/x-ndjson"
    columns = []
    for r in rows:
        for k in r:
            if k not in columns:
                columns.append(k)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: _flat(r.get(k)) for k in columns})
    return ("﻿" + buf.getvalue()).encode("utf-8"), "text/csv; charset=utf-8"


def data_dictionary(meta):
    lines = [f"# Data dictionary: {meta['home']['name']} v {meta['away']['name']} ({meta['match_id']})", "",
             "All data is synthetic: clubs, players and events are fictional.",
             "Coordinates are metres on a 105 x 68 pitch. Shares (possession, accuracy) are 0-1.", "",
             "## players.csv", "One row per player who played.", "",
             "| Column | Meaning |", "|---|---|",
             "| player_id, name, number, position, team | Who the player is |",
             "| minutes_played | Minutes on the pitch |", "| rating | Match rating, 3-10 |"]
    for section, stats in SECTIONS:
        for label, _, higher in stats:
            lines.append(f"| {label} | {section}; {'higher' if higher else 'lower'} is better |")
    lines += ["", "## teams.csv", "One row per team: goals, shots, xG, xGOT, possession, passes, pass accuracy, "
              "final-third entries, pressures, tackles, interceptions, turnovers, fouls, cards, corners, saves, "
              "aerial duels, recoveries, distance and sprints.", "",
              "## moments.csv", "One row per moment the engine detected. `engine_headline` is the factual "
              "headline; `ai_caption`, `fan_take`, `analyst_take` and `why_it_matters` come from the AI agents "
              "(empty if the match wasn't AI-explained). `fact_checked` is true when the narration passed the "
              "number check and the Fact Checker. `evidence` holds the engine's structured data as JSON.", "",
              "## timeline.csv", "One row per minute: score, phase (balanced, chaos, control_home, control_away), "
              "`momentum_home_share` (home team's share of attacking threat over the last 5 minutes) and "
              "running team stats.", "",
              "## events.jsonl", "The raw event feed, one JSON object per line, ready for pipelines "
              "(Fabric, Databricks, pandas). Field definitions are in matchsim/README.md."]
    return "\n".join(lines) + "\n"


def bundle(meta, engine, insights, states, events, explained):
    """A ZIP with every table as CSV, the events as JSON Lines, and a data dictionary."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name in ("players", "teams", "moments", "timeline"):
            body, _ = serialise(build_table(name, engine, insights, states, events, explained), "csv")
            z.writestr(f"{name}.csv", body)
        body, _ = serialise(events_rows(events), "jsonl")
        z.writestr("events.jsonl", body)
        z.writestr("metadata.json", json.dumps(meta, ensure_ascii=False, indent=1))
        z.writestr("DATA_DICTIONARY.md", data_dictionary(meta))
    return out.getvalue()
