#!/usr/bin/env python3
"""Append finished NBA games to data/gamelog.js (player rows AND team rows).

Source: ESPN's public (unofficial) JSON endpoints. No API key needed.

Daily use (what the scheduled job runs):
    python update_data.py --days 3

Backfill a date range (also fills in team rows for games whose player rows are already saved):
    python update_data.py --from 2025-10-21 --to 2026-10-21 --sleep 0.3

Rows are de-duplicated on (date, team, player) and (date, team), so re-running is safe.
Regular season (type 2), playoffs (3) and play-in (5) games are kept; All-Star and other non-NBA-team games are not.
Games from before SEASON_START are pruned to the last --keep-prior games per player / per team,
because the 20-game trends never need more than that.

The data file holds: "rows" (one per player per game) and "teams" (one per team per game).
"""
import argparse
import json
import re
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
DATA = Path(__file__).parent / "data" / "gamelog.js"
ET = ZoneInfo("America/New_York")
SEASON_START = "2026-09-01"  # games before this date count as "last season"

# ESPN abbreviations that differ from the ones used on the site
TEAM_FIX = {"GS": "GSW", "NY": "NYK", "NO": "NOP", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS", "PHO": "PHX"}

TEAM_SUM_KEYS = ["reb", "ast", "stl", "blk", "tov", "fgm", "fga", "tpm", "tpa", "ftm", "fta"]

# Only real NBA teams. This keeps out All-Star game teams (STARS, STRIPES, WORLD) and any other special games.
VALID_TEAMS = {
    "ATL", "BOS", "BKN", "CHA", "CHI", "CLE", "DAL", "DEN", "DET", "GSW", "HOU", "IND", "LAC", "LAL", "MEM",
    "MIA", "MIL", "MIN", "NOP", "NYK", "OKC", "ORL", "PHI", "PHX", "POR", "SAC", "SAS", "TOR", "UTA", "WAS",
}


def norm_team(abbr):
    return TEAM_FIX.get(abbr, abbr)


def get_json(url, tries=3):
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "nba-trends-site/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except Exception as e:  # network hiccup, 5xx, bad JSON
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise last


def load():
    text = DATA.read_text(encoding="utf-8")
    body = text[text.index("=") + 1:].strip().rstrip(";")
    data = json.loads(body)
    data.setdefault("teams", [])
    return data


def save(data):
    def block(rows):
        return ",\n".join("  " + json.dumps(r, separators=(",", ":")) for r in rows)
    out = 'window.NBA_LOG = {"updated":%s,"rows":[\n%s\n],"teams":[\n%s\n]};\n' % (
        json.dumps(data["updated"]), block(data["rows"]), block(data.get("teams", [])))
    DATA.write_text(out, encoding="utf-8")


def prune_prior(rows, keep, key="p"):
    """Keep every current-season row; for older rows keep only the last `keep` per player (or team)."""
    if keep <= 0:
        return rows
    prior = [r for r in rows if r["d"] < SEASON_START]
    current = [r for r in rows if r["d"] >= SEASON_START]
    grouped = {}
    for r in prior:  # rows are already date-sorted
        grouped.setdefault(r[key], []).append(r)
    kept = [r for lst in grouped.values() for r in lst[-keep:]]
    kept.sort(key=lambda r: r["d"])
    return kept + current


def parse_boxscore(summary, date_str, scores):
    """scores: {team_abbr: (points, 'home'|'away')} for this game. Returns list of player rows."""
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


def team_rows(player_rows, date_str, scores):
    """One row per team: points from the final score, everything else summed over that team's players."""
    out = []
    for team, (pf, side) in scores.items():
        mine = [r for r in player_rows if r["t"] == team]
        if not mine:  # box score missing for this team: skip rather than store partial totals
            continue
        opp_team = next(t for t in scores if t != team)
        pa = scores[opp_team][0]
        row = {"d": date_str, "t": team, "o": opp_team, "h": 1 if side == "home" else 0,
               "r": f"{'W' if pf > pa else 'L'} {pf}-{pa}", "pf": pf, "pa": pa}
        for k in TEAM_SUM_KEYS:
            row[k] = sum(r[k] for r in mine)
        out.append(row)
    return out


def day_range(args):
    if args.date_from:
        start = date.fromisoformat(args.date_from)
        end = date.fromisoformat(args.date_to) if args.date_to else datetime.now(ET).date()
    else:
        end = datetime.now(ET).date()
        start = end - timedelta(days=args.days)
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3, help="look back this many days (daily mode)")
    ap.add_argument("--from", dest="date_from", help="backfill start date YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", help="backfill end date YYYY-MM-DD (default today)")
    ap.add_argument("--types", default="2,3,5",
                    help="ESPN season types to keep: 2=regular season, 3=playoffs, 5=play-in")
    ap.add_argument("--sleep", type=float, default=0.0, help="pause between requests (be polite on backfills)")
    ap.add_argument("--keep-prior", type=int, default=25, help="games kept per player/team from before the season start")
    args = ap.parse_args()
    allowed = {int(t) for t in args.types.split(",")}

    data = load()
    # Self-clean: drop anything saved earlier for non-NBA teams (e.g. All-Star game teams)
    n_before = len(data["rows"]) + len(data["teams"])
    data["rows"] = [r for r in data["rows"] if r["t"] in VALID_TEAMS]
    data["teams"] = [r for r in data["teams"] if r["t"] in VALID_TEAMS]
    removed = n_before - len(data["rows"]) - len(data["teams"])
    if removed:
        print(f"Removed {removed} rows from non-NBA teams (All-Star etc.).")
    seen = {(r["d"], r["t"], r["p"]) for r in data["rows"]}
    tseen = {(r["d"], r["t"]) for r in data["teams"]}
    added, added_t, failures = 0, 0, 0

    for day in day_range(args):
        ds = f"{day:%Y-%m-%d}"
        try:
            board = get_json(f"{BASE}/scoreboard?dates={day:%Y%m%d}")
        except Exception as e:
            print(f"{ds}: scoreboard failed ({e})")
            failures += 1
            continue
        day_added, day_t, games = 0, 0, 0
        for ev in board.get("events", []):
            if not ev.get("status", {}).get("type", {}).get("completed"):
                continue
            stype = ev.get("season", {}).get("type")
            if stype is not None and stype not in allowed:
                continue
            try:
                comp = ev["competitions"][0]
                scores = {norm_team(c["team"]["abbreviation"]): (int(float(c["score"])), c["homeAway"])
                          for c in comp["competitors"]}
                if not all(t in VALID_TEAMS for t in scores) or len(scores) != 2:
                    continue  # All-Star / exhibition games
                if args.sleep:
                    time.sleep(args.sleep)
                summary = get_json(f"{BASE}/summary?event={ev['id']}")
                all_rows = parse_boxscore(summary, ds, scores)
                new_rows = [r for r in all_rows if (r["d"], r["t"], r["p"]) not in seen]
                new_teams = [r for r in team_rows(all_rows, ds, scores) if (r["d"], r["t"]) not in tseen]
            except Exception as e:
                print(f"{ds} event {ev.get('id')}: failed ({e})")
                failures += 1
                continue
            for r in new_rows:
                seen.add((r["d"], r["t"], r["p"]))
            for r in new_teams:
                tseen.add((r["d"], r["t"]))
            data["rows"].extend(new_rows)
            data["teams"].extend(new_teams)
            day_added += len(new_rows)
            day_t += len(new_teams)
            games += 1
        added += day_added
        added_t += day_t
        if games:
            print(f"{ds}: {games} games, +{day_added} player rows, +{day_t} team rows")
        if args.sleep:
            time.sleep(args.sleep)

    if added or added_t or removed:
        data["rows"].sort(key=lambda r: r["d"])  # stable: keeps order within a day
        data["teams"].sort(key=lambda r: r["d"])
        data["rows"] = prune_prior(data["rows"], args.keep_prior, "p")
        data["teams"] = prune_prior(data["teams"], args.keep_prior, "t")
        if added or added_t:
            data["updated"] = datetime.now(ET).strftime("%Y-%m-%d")
        save(data)
    print(f"Done. {added} new player-games and {added_t} new team-games added. {failures} failures. "
          f"File now has {len(data['rows'])} player rows and {len(data['teams'])} team rows.")
    # Partial failures are logged and skipped (re-running fills the gaps). Only fail the job
    # when nothing worked, so the workflow still commits whatever was saved.
    return 1 if (failures and not (added or added_t)) else 0


if __name__ == "__main__":
    sys.exit(main())
