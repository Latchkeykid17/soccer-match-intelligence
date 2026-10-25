"""
Print a player's full match profile, like a stats app.

Usage (run run_engine.py on the match first):
  python player_card.py matches/match_00007              # player of the match
  python player_card.py matches/match_00007 "Keller"     # search by name
  python player_card.py matches/match_00007 --list       # everyone, by rating
"""
import json
import sys


def show(p):
    print(f"\n{p['name']}  #{p['number']}  {p['position']}  {p['team_name']}")
    print(f"Minutes {p['minutes_played']}   Rating {p['rating']}")
    for section, items in p["sections"].items():
        print(f"\n  {section:<34}{'':>8}  Rank")
        for it in items:
            v = "-" if it["value"] is None else it["value"]
            if isinstance(v, float) and it["stat"].endswith("%"):
                v = f"{v}%"
            rank = f"{it['rank']} of {it['of']}" if it["rank"] else ""
            print(f"  {it['stat']:<34}{str(v):>8}  {rank}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    report = json.load(open(f"{sys.argv[1]}/player_stats.json"))
    players = report["players"]
    if len(sys.argv) > 2 and sys.argv[2] == "--list":
        for p in players:
            print(f"{p['rating']:>4}  {p['name']:<22} {p['position']:<3} {p['team_name']}  ({p['minutes_played']} min)")
        return
    if len(sys.argv) > 2:
        found = [p for p in players if sys.argv[2].lower() in p["name"].lower()]
        if not found:
            print("No player matches that name. Try --list.")
            return
        for p in found:
            show(p)
    else:
        show(players[0])


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        pass
