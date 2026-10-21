#!/usr/bin/env python3
"""Append newly finished NBA games to data/gamelog.js.

Source: ESPN's public (unofficial) JSON endpoints. No API key needed.
Usage:  python update_data.py [--days 3]

Looks back --days days (US Eastern dates), so a missed run is backfilled.
Rows are de-duplicated on (date, team, player), so re-running is safe.
"""
import argparse
import json
import re
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
DATA = Path(__file__).parent / "data" / "gamelog.js"
ET = ZoneInfo("America/New_York")

# ESPN abbreviations that differ from the ones used on the site
TEAM_FIX = {"GS": "GSW", "NY": "NYK", "NO": "NOP", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS", "PHO": "PHX"}


def norm_team(abbr):
    return TEAM_FIX.get(abbr, abbr)


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "nba-trends-site/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def load():
    text = DATA.read_text(encoding="utf-8")
    body = text[text.index("=") + 1:].strip().rstrip(";")
    return json.loads(body)


def save(data):
    lines = ",\n".join("  " + json.dumps(r, separators=(",", ":")) for r in data["rows"])
    out = 'window.NBA_LOG = {"updated":%s,"rows":[\n%s\n]};\n' % (json.dumps(data["updated"]), lines)
    DATA.write_text(out, encoding="utf-8")


def parse_boxscore(summary, date_str, scores):
    """scores: {team_abbr: (points, 'home'|'away')} for this game. Returns list of rows."""
    rows = []
    for entry in summary.get("boxscore", {}).get("players", []):
        team = norm_team(entry["team"]["abbreviation"])
        if team not in scores:
            continue
        mine = scores[team][0]
        opp_team = next(t for t in scores if t != team)
        opp = scores[opp_team][0]
        result = f"{'W' if mine > opp else 'L'} {mine}-{opp}"
        for grp in entry.get("statistics", []):
            idx = {label: i for i, label in enumerate(grp.get("labels", []))}
            for a in grp.get("athletes", []):
                st = a.get("stats") or []
                if a.get("didNotPlay") or not st:
                    continue

                def raw(label):
                    i = idx.get(label)
                    return st[i] if i is not None and i < len(st) else "0"

                def num(label):
                    try:
                        return int(float(raw(label)))
                    except ValueError:
                        return 0

                def split(label):
                    m = re.match(r"(\d+)-(\d+)", str(raw(label)))
                    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)

                if raw("MIN") in ("0", "--", ""):
                    continue
                fgm, fga = split("FG")
                tpm, tpa = split("3PT")
                ftm, fta = split("FT")
                rows.append({
                    "d": date_str, "p": a["athlete"]["displayName"], "t": team, "o": opp_team,
                    "h": 1 if scores[team][1] == "home" else 0, "r": result,
                    "pts": num("PTS"), "reb": num("REB"), "ast": num("AST"),
                    "stl": num("STL"), "blk": num("BLK"), "tov": num("TO"),
                    "fgm": fgm, "fga": fga, "tpm": tpm, "tpa": tpa, "ftm": ftm, "fta": fta,
                })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    args = ap.parse_args()

    data = load()
    seen = {(r["d"], r["t"], r["p"]) for r in data["rows"]}
    today = datetime.now(ET).date()
    added = 0

    for back in range(args.days, -1, -1):
        day = today - timedelta(days=back)
        board = get_json(f"{BASE}/scoreboard?dates={day:%Y%m%d}")
        for ev in board.get("events", []):
            if not ev.get("status", {}).get("type", {}).get("completed"):
                continue
            comp = ev["competitions"][0]
            scores = {norm_team(c["team"]["abbreviation"]): (int(float(c["score"])), c["homeAway"])
                      for c in comp["competitors"]}
            summary = get_json(f"{BASE}/summary?event={ev['id']}")
            new_rows = [r for r in parse_boxscore(summary, f"{day:%Y-%m-%d}", scores)
                        if (r["d"], r["t"], r["p"]) not in seen]
            for r in new_rows:
                seen.add((r["d"], r["t"], r["p"]))
            data["rows"].extend(new_rows)
            added += len(new_rows)
            print(f"{day} {'/'.join(scores)}: +{len(new_rows)} rows")

    if added:
        data["rows"].sort(key=lambda r: r["d"])  # stable: keeps order within a day
        data["updated"] = datetime.now(ET).strftime("%Y-%m-%d")
        save(data)
    print(f"Done. {added} new player-games added.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
