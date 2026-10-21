#!/usr/bin/env python3
"""Append finished NBA games to data/gamelog.js.

Source: ESPN's public (unofficial) JSON endpoints. No API key needed.

Daily use (what the scheduled job runs):
    python update_data.py --days 3

One-time backfill of last season, so 5/10/20-game trends work right away:
    python update_data.py --from 2025-10-21 --to 2026-04-30 --sleep 0.3

Rows are de-duplicated on (date, team, player), so re-running is safe.
Only regular-season games are kept unless you pass --types 2,3 (adds playoffs).
Games from before SEASON_START are pruned to each player's last --keep-prior games,
because the 20-game trends never need more than that.
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
    return json.loads(body)


def save(data):
    lines = ",\n".join("  " + json.dumps(r, separators=(",", ":")) for r in data["rows"])
    out = 'window.NBA_LOG = {"updated":%s,"rows":[\n%s\n]};\n' % (json.dumps(data["updated"]), lines)
    DATA.write_text(out, encoding="utf-8")


def prune_prior(rows, keep):
    """Keep every current-season row; for older rows keep only each player's last `keep` games."""
    if keep <= 0:
        return rows
    prior = [r for r in rows if r["d"] < SEASON_START]
    current = [r for r in rows if r["d"] >= SEASON_START]
    by_player = {}
    for r in prior:  # rows are already date-sorted
        by_player.setdefault(r["p"], []).append(r)
    kept = [r for lst in by_player.values() for r in lst[-keep:]]
    kept.sort(key=lambda r: r["d"])
    return kept + current


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
    ap.add_argument("--types", default="2", help="ESPN season types to keep: 2=regular season, 3=playoffs")
    ap.add_argument("--sleep", type=float, default=0.0, help="pause between requests (be polite on backfills)")
    ap.add_argument("--keep-prior", type=int, default=25, help="games kept per player from before the season start")
    args = ap.parse_args()
    allowed = {int(t) for t in args.types.split(",")}

    data = load()
    seen = {(r["d"], r["t"], r["p"]) for r in data["rows"]}
    added, failures = 0, 0

    for day in day_range(args):
        ds = f"{day:%Y-%m-%d}"
        try:
            board = get_json(f"{BASE}/scoreboard?dates={day:%Y%m%d}")
        except Exception as e:
            print(f"{ds}: scoreboard failed ({e})")
            failures += 1
            continue
        day_added, games = 0, 0
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
                if args.sleep:
                    time.sleep(args.sleep)
                summary = get_json(f"{BASE}/summary?event={ev['id']}")
                new_rows = [r for r in parse_boxscore(summary, ds, scores)
                            if (r["d"], r["t"], r["p"]) not in seen]
            except Exception as e:
                print(f"{ds} event {ev.get('id')}: failed ({e})")
                failures += 1
                continue
            for r in new_rows:
                seen.add((r["d"], r["t"], r["p"]))
            data["rows"].extend(new_rows)
            day_added += len(new_rows)
            games += 1
        added += day_added
        if games:
            print(f"{ds}: {games} games, +{day_added} rows")
        if args.sleep:
            time.sleep(args.sleep)

    if added:
        data["rows"].sort(key=lambda r: r["d"])  # stable: keeps order within a day
        data["rows"] = prune_prior(data["rows"], args.keep_prior)
        data["updated"] = datetime.now(ET).strftime("%Y-%m-%d")
        save(data)
    print(f"Done. {added} new player-games added. {failures} failures. File now has {len(data['rows'])} rows.")
    # Partial failures are logged and skipped (re-running fills the gaps). Only fail the job
    # when nothing worked, so the workflow still commits whatever was saved.
    return 1 if (failures and not added) else 0


if __name__ == "__main__":
    sys.exit(main())
