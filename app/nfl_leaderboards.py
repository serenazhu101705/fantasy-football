"""Verified NFL-wide season leaderboards from the existing nflverse weekly data.

No Claude or FantasyPros calls. The data loader is intentionally imported lazily
because app startup and pure-parser tests must not download nflverse datasets.
"""
from __future__ import annotations

import math
import re
from typing import Any


METRICS = {
    "passing_yards": ("Passing yards", "passing_yards", "yds"),
    "passing_tds": ("Passing touchdowns", "passing_tds", "TD"),
    "rushing_yards": ("Rushing yards", "rushing_yards", "yds"),
    "rushing_tds": ("Rushing touchdowns", "rushing_tds", "TD"),
    "receiving_yards": ("Receiving yards", "receiving_yards", "yds"),
    "receiving_tds": ("Receiving touchdowns", "receiving_tds", "TD"),
    "receptions": ("Receptions", "receptions", "receptions"),
    "targets": ("Targets", "targets", "targets"),
    "completions": ("Pass completions", "completions", "completions"),
    "carries": ("Rushing attempts", "carries", "attempts"),
    "passing_interceptions": ("Interceptions thrown", "interceptions", "INT"),
    "passing_attempts": ("Pass attempts", "attempts", "attempts"),
    "defensive_sacks": ("Defensive sacks", "def_sacks", "sacks"),
    "defensive_interceptions": ("Defensive interceptions", "def_interceptions", "INT"),
}

_CATEGORY_ALIASES = {
    "passing_yards": (r"pass(?:ing)?\s+yards?", r"yards?\s+pass(?:ed|ing)?", r"pass(?:ing)?\s+yds?"),
    "passing_tds": (r"pass(?:ing)?\s+(?:tds?|touchdowns?)", r"touchdown\s+passes?"),
    "rushing_yards": (r"rush(?:ing)?\s+yards?", r"yards?\s+rush(?:ed|ing)?", r"rush(?:ing)?\s+yds?"),
    "rushing_tds": (r"rush(?:ing)?\s+(?:tds?|touchdowns?)" ,),
    "receiving_yards": (r"receiv(?:ing|er)?\s+yards?", r"yards?\s+receiv(?:ed|ing)?", r"receiv(?:ing)?\s+yds?"),
    "receiving_tds": (r"receiv(?:ing)?\s+(?:tds?|touchdowns?)",),
    "receptions": (r"\breceptions?\b", r"\bcatches\b"),
    "targets": (r"\btargets?\b",),
    "completions": (r"\b(?:pass\s+)?completions?\b",),
    "carries": (r"\bcarries\b", r"\brushing\s+attempts?\b"),
    "passing_interceptions": (r"\b(?:passing\s+)?interceptions?\s+thrown\b", r"\binterceptions?\s+thrown\b", r"\bpassing\s+interceptions?\b"),
    "passing_attempts": (r"\b(?:passing|pass)\s+attempts?\b",),
    "defensive_sacks": (r"\bsacks?\b",),
    "defensive_interceptions": (r"\binterceptions?\b",),
}

_LEADERSHIP = re.compile(
    r"\b(?:who|which|what)\b.*\b(?:le+a+d+s?|leading|leader|most|top|highest|best|rank)|"
    r"\b(?:le+a+d+s?|leading|leaders?|leaderboard|most|top|highest|rankings?)\b", re.I
)



def parse_leaderboard_request(question: str) -> dict | None:
    """Recognize league-wide *NFL* statistical questions without guessing players.

    ESPN fantasy-league 'most points' is deliberately not handled here.
    """
    text = re.sub(r"[^a-z0-9\s]", " ", str(question or "").lower())
    text = re.sub(r"\s+", " ", text).strip()
    if not _LEADERSHIP.search(text):
        return None
    category = None
    for metric, patterns in _CATEGORY_ALIASES.items():
        if any(re.search(pattern, text) for pattern in patterns):
            category = metric
            break
    if category is None:
        return None
    match = re.search(r"\b(?:top|first|leading)\s+(\d{1,2})\b", text)
    count = min(max(int(match.group(1)), 1), 20) if match else 5
    # Seasons can be explicitly named. A 'this season' query means the app's
    # configured current season; historical seasons are not silently substituted.
    season = re.search(r"\b20(?:1\d|2\d|3\d)\b", text)
    return {"metric": category, "limit": count,
            "requested_season": int(season.group()) if season else None}


def _as_number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def aggregate_leaders(rows: list[dict], metric: str, *, season: int, limit: int = 5) -> dict:
    """Pure, easy-to-test aggregation of explicit player-week observations."""
    if metric not in METRICS:
        raise ValueError(f"Unsupported NFL statistic: {metric}")
    label, column, unit = METRICS[metric]
    totals: dict[str, dict] = {}
    weeks: set[int] = set()
    observations = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            if row.get("season") is not None and int(row["season"]) != season:
                continue
            week = int(row["week"])
        except (ValueError, TypeError, KeyError):
            continue
        if week < 1 or week > 22:
            continue
        value = _as_number(row.get(column))
        if value is None:
            continue
        name = str(row.get("player_display_name") or row.get("player_name") or "").strip()
        if not name:
            continue
        player_id = str(row.get("player_id") or "").strip()
        # nflverse player IDs are stable across teams, preventing traded players
        # from being counted as two separate leaderboard entries.
        key = player_id or name.casefold()
        current = totals.setdefault(key, {"name": name, "team": None, "total": 0.0,
                                          "games": set(), "last_week": -1})
        current["total"] += value
        current["games"].add(week)
        if week >= current["last_week"]:
            current["last_week"] = week
            current["name"] = name
            current["team"] = row.get("recent_team") or row.get("team") or current["team"]
        weeks.add(week)
        observations += 1
    leaders = sorted(totals.values(), key=lambda p: (-p["total"], p["name"]))[:max(1, min(int(limit), 20))]
    return {"status": "available" if leaders else "unavailable", "season": season,
            "stat": metric, "stat_label": label, "unit": unit,
            "through_week": max(weeks) if weeks else None,
            "weeks_available": sorted(weeks), "sample_rows": observations,
            "source": "nflverse weekly player statistics",
            "note": "Totals use only player-week rows available to the app; the feed may lag recent or live games. This is the NFL, not your fantasy league.",
            "leaders": [{"rank": i, "name": p["name"], "team": p["team"],
                         "value": round(p["total"], 2), "games": len(p["games"])}
                        for i, p in enumerate(leaders, 1)]}


def get_nfl_leaderboard(question: str, *, season: int = 2026) -> dict:
    parsed = parse_leaderboard_request(question)
    if parsed is None:
        return {"status": "unsupported", "reason": "Not an NFL statistical-leaderboard question."}
    requested = parsed.get("requested_season")
    if requested is not None and requested != season:
        return {"status": "unavailable", "season": requested,
                "reason": f"The current data loader contains {season} only, not {requested}; no historical result was substituted."}
    try:
        from nfl_data import get_weekly_player_stats
        df = get_weekly_player_stats()
        columns = set(getattr(df, "columns", []))
        required = {"week", "player_display_name", parsed["metric"]}
        if not required <= columns:
            return {"status": "unavailable", "season": season,
                    "reason": "The loaded nflverse table is missing the requested statistic or identifiers."}
        keep = [col for col in ("season", "week", "player_id", "player_display_name",
                                "recent_team", "team", parsed["metric"]) if col in columns]
        # Select just the requested stat instead of serializing full game records.
        rows = df.select(keep).to_dicts()
        return aggregate_leaders(rows, parsed["metric"], season=season, limit=parsed["limit"])
    except Exception as exc:
        # Do not report an invented leader when upstream data is unavailable.
        return {"status": "unavailable", "season": season,
                "reason": f"NFL weekly statistics couldn't be loaded ({type(exc).__name__})."}


def format_leaderboard_answer(result: dict) -> str:
    if result.get("status") != "available":
        return "I couldn't verify that NFL leaderboard from the currently available data. " + str(result.get("reason") or "")
    leaders = result.get("leaders") or []
    if not leaders:
        return "No verified player records were available for that statistic."
    top = leaders[0]
    value = top["value"]
    fmt = f"{int(value):,}" if float(value).is_integer() else f"{value:,.1f}"
    lead = f"**{top['name']}** leads the NFL in {result['stat_label'].lower()} with **{fmt} {result['unit']}**"
    if top.get("team"):
        lead += f" ({top['team']})"
    week = result.get("through_week")
    lead += (f", through available Week {week} data for {result['season']}."
             if week is not None else f", according to available {result['season']} data.")
    others = []
    for row in leaders[1:]:
        val = row["value"]
        score = f"{int(val):,}" if float(val).is_integer() else f"{val:,.1f}"
        others.append(f"{row['rank']}. {row['name']} ({row['team'] or 'team unknown'}): {score}")
    if others:
        lead += "\n\n**Other leaders:**\n" + "\n".join(others)
    source = result.get("source") or "nflverse weekly player statistics"
    caveat = ("Figures reflect loaded weeks and may lag games in progress." if result.get("through_week") is not None
              else "ESPN did not specify the latest completed week; totals may be delayed.")
    lead += f"\n\n*Source: {source}. {caveat}*"
    return lead
