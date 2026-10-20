"""Resolve every pre-target-week player week into a factual participation state.

The goal is to avoid treating an absent nflverse player-stat row as mysterious
"missing data".  For each prior regular-season week we cross-check:

- nflverse schedule (bye / game existence / result)
- nflverse weekly roster status
- nflverse official injury + practice report
- nflverse snap counts
- the player's nflverse stat row

Only a true unresolved source gap is labeled DATA_GAP.

This module is independent of FantasyPros and is safe to use when FantasyPros
is unavailable or rate-limited.
"""

from pathlib import Path
from datetime import datetime, timezone
import math
import re
import unicodedata

import nflreadpy as nfl
import pandas as pd
import polars as pl


CACHE_DIR = Path("data/cache/nflverse/week_resolution")
CACHE_TTL_HOURS = 6

_DATASET_MEMORY_CACHE = {}

TEAM_ALIASES = {
    "ARZ": "ARI", "ARI": "ARI",
    "GBP": "GB", "GB": "GB",
    "JAC": "JAX", "JAX": "JAX",
    "KCC": "KC", "KC": "KC",
    "LVR": "LV", "OAK": "LV", "LV": "LV",
    "LA": "LAR", "LAR": "LAR",
    "NEP": "NE", "NE": "NE",
    "NOS": "NO", "NO": "NO",
    "SFO": "SF", "SF": "SF",
    "TBB": "TB", "TB": "TB",
    "WAS": "WSH", "WSH": "WSH",
}

ROSTER_STATUS_LABELS = {
    "ACT": "ACTIVE",
    "INA": "INACTIVE",
    "RES": "RESERVE",
    "PUP": "PUP",
    "RSN": "NFI_RESERVE",
    "SUS": "SUSPENDED",
    "DEV": "PRACTICE_SQUAD",
    "EXE": "EXEMPT",
    "CUT": "CUT",
    "UFA": "FREE_AGENT",
}


def normalize_name(value):
    if value is None:
        return ""
    name = unicodedata.normalize("NFKD", str(value))
    name = "".join(ch for ch in name if not unicodedata.combining(ch))
    name = name.lower()
    name = re.sub(r"[^a-z0-9\s]", "", name)
    name = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", name)
    return re.sub(r"\s+", " ", name).strip()


def normalize_team(value):
    if value is None:
        return None
    team = str(value).upper().strip().replace("@", "")
    if not team or team in {"FA", "NONE", "NAN"}:
        return None
    return TEAM_ALIASES.get(team, team)


def _safe_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _id_string(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "nan"}:
        return None
    # ESPN IDs sometimes arrive as 12345.0 after dataframe conversion.
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    return text


def _cache_fresh(path):
    if not path.exists():
        return False
    age_seconds = datetime.now(timezone.utc).timestamp() - path.stat().st_mtime
    return age_seconds <= CACHE_TTL_HOURS * 3600


def _call_loader(loader, season):
    """Support nflreadpy versions that accept positional or keyword seasons."""
    try:
        return loader(seasons=[int(season)])
    except TypeError:
        return loader([int(season)])


def _load_dataset(kind, season):
    key = (kind, int(season))
    if key in _DATASET_MEMORY_CACHE:
        return _DATASET_MEMORY_CACHE[key]

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{kind}_{int(season)}.parquet"

    loaders = {
        "schedules": nfl.load_schedules,
        "weekly_rosters": nfl.load_rosters_weekly,
        "injuries": nfl.load_injuries,
        "snap_counts": nfl.load_snap_counts,
    }
    loader = loaders[kind]

    raw = None
    if _cache_fresh(path):
        raw = pl.read_parquet(path)
    else:
        try:
            raw = _call_loader(loader, season)
            if raw is not None:
                raw.write_parquet(path)
        except Exception:
            # Stale data is much better than losing the resolver entirely.
            if path.exists():
                raw = pl.read_parquet(path)
            else:
                raise

    df = pd.DataFrame(raw.to_dicts())
    _DATASET_MEMORY_CACHE[key] = df
    return df


def _regular_only(df):
    if df is None or df.empty:
        return df
    for column in ("game_type", "season_type"):
        if column in df.columns:
            values = df[column].astype(str).str.upper()
            if values.isin(["REG"]).any():
                return df[values == "REG"].copy()
    return df


def _prepare_schedule(season):
    df = _regular_only(_load_dataset("schedules", season)).copy()
    if df.empty:
        return df
    df["season"] = pd.to_numeric(df.get("season"), errors="coerce")
    df["week"] = pd.to_numeric(df.get("week"), errors="coerce")
    df["home_team_norm"] = df.get("home_team", pd.Series(index=df.index, dtype=object)).apply(normalize_team)
    df["away_team_norm"] = df.get("away_team", pd.Series(index=df.index, dtype=object)).apply(normalize_team)
    return df


def _prepare_rosters(season):
    df = _regular_only(_load_dataset("weekly_rosters", season)).copy()
    if df.empty:
        return df
    df["season"] = pd.to_numeric(df.get("season"), errors="coerce")
    df["week"] = pd.to_numeric(df.get("week"), errors="coerce")
    df["name_norm"] = df.get("full_name", pd.Series(index=df.index, dtype=object)).apply(normalize_name)
    df["team_norm"] = df.get("team", pd.Series(index=df.index, dtype=object)).apply(normalize_team)
    return df


def _prepare_injuries(season):
    df = _regular_only(_load_dataset("injuries", season)).copy()
    if df.empty:
        return df
    df["season"] = pd.to_numeric(df.get("season"), errors="coerce")
    df["week"] = pd.to_numeric(df.get("week"), errors="coerce")
    df["name_norm"] = df.get("full_name", pd.Series(index=df.index, dtype=object)).apply(normalize_name)
    df["team_norm"] = df.get("team", pd.Series(index=df.index, dtype=object)).apply(normalize_team)
    if "date_modified" in df.columns:
        df["date_modified_parsed"] = pd.to_datetime(df["date_modified"], errors="coerce", utc=True)
    return df


def _prepare_snaps(season):
    df = _regular_only(_load_dataset("snap_counts", season)).copy()
    if df.empty:
        return df
    df["season"] = pd.to_numeric(df.get("season"), errors="coerce")
    df["week"] = pd.to_numeric(df.get("week"), errors="coerce")
    df["name_norm"] = df.get("player", pd.Series(index=df.index, dtype=object)).apply(normalize_name)
    df["team_norm"] = df.get("team", pd.Series(index=df.index, dtype=object)).apply(normalize_team)
    for col in ("offense_snaps", "defense_snaps", "st_snaps"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    return df


def _stat_identity(player, stat_rows):
    gsis_ids = set()
    teams_by_week = {}

    if stat_rows is not None and not stat_rows.empty:
        if "player_id" in stat_rows.columns:
            gsis_ids = {
                _id_string(value)
                for value in stat_rows["player_id"].dropna().tolist()
                if _id_string(value)
            }
        team_col = next((c for c in ("team", "recent_team") if c in stat_rows.columns), None)
        if team_col:
            for _, row in stat_rows.iterrows():
                try:
                    wk = int(row["week"])
                except Exception:
                    continue
                team = normalize_team(row.get(team_col))
                if team:
                    teams_by_week[wk] = team

    return {
        "name": normalize_name(player.get("name")),
        "position": str(player.get("position") or "").upper(),
        "espn_id": _id_string(player.get("espn_id")),
        "current_team": normalize_team(player.get("nfl_team")),
        "gsis_ids": gsis_ids,
        "teams_by_week": teams_by_week,
    }


def _pick_roster_row(rosters, identity, week):
    if rosters is None or rosters.empty:
        return None

    pool = rosters[rosters["week"] == int(week)].copy()
    if pool.empty:
        return None

    espn_id = identity["espn_id"]
    if espn_id and "espn_id" in pool.columns:
        matched = pool[pool["espn_id"].apply(_id_string) == espn_id]
        if not matched.empty:
            pool = matched
        else:
            pool = pool.iloc[0:0]

    if pool.empty and identity["gsis_ids"] and "gsis_id" in rosters.columns:
        base = rosters[rosters["week"] == int(week)].copy()
        matched = base[base["gsis_id"].apply(_id_string).isin(identity["gsis_ids"])]
        if not matched.empty:
            pool = matched

    if pool.empty:
        base = rosters[rosters["week"] == int(week)].copy()
        matched = base[base["name_norm"] == identity["name"]]
        if identity["position"] and "position" in matched.columns:
            same_pos = matched[matched["position"].astype(str).str.upper() == identity["position"]]
            if not same_pos.empty:
                matched = same_pos
        pool = matched

    if pool.empty:
        return None

    expected_team = identity["teams_by_week"].get(int(week)) or identity["current_team"]
    if expected_team:
        same_team = pool[pool["team_norm"] == expected_team]
        if not same_team.empty:
            pool = same_team

    # If a player appears multiple times during a transaction week, prefer a
    # football roster state over cut/free-agent rows.
    if "status" in pool.columns:
        priority = {"ACT": 0, "INA": 1, "RES": 2, "PUP": 3, "SUS": 4, "DEV": 5, "CUT": 8, "UFA": 9}
        pool = pool.copy()
        pool["_status_priority"] = pool["status"].astype(str).str.upper().map(priority).fillna(6)
        pool = pool.sort_values("_status_priority")

    return pool.iloc[0]


def _pick_injury_row(injuries, identity, week, roster_row=None):
    if injuries is None or injuries.empty:
        return None

    pool = injuries[injuries["week"] == int(week)].copy()
    if pool.empty:
        return None

    if identity["gsis_ids"] and "gsis_id" in pool.columns:
        matched = pool[pool["gsis_id"].apply(_id_string).isin(identity["gsis_ids"])]
        if not matched.empty:
            pool = matched
        else:
            pool = pool.iloc[0:0]

    if pool.empty:
        base = injuries[injuries["week"] == int(week)].copy()
        pool = base[base["name_norm"] == identity["name"]]

    if pool.empty:
        return None

    expected_team = None
    if roster_row is not None:
        expected_team = normalize_team(roster_row.get("team"))
    expected_team = expected_team or identity["teams_by_week"].get(int(week)) or identity["current_team"]
    if expected_team:
        same_team = pool[pool["team_norm"] == expected_team]
        if not same_team.empty:
            pool = same_team

    if "date_modified_parsed" in pool.columns and pool["date_modified_parsed"].notna().any():
        pool = pool.sort_values("date_modified_parsed")

    return pool.iloc[-1]


def _pick_snap_row(snaps, identity, week, roster_row=None):
    if snaps is None or snaps.empty:
        return None

    pool = snaps[snaps["week"] == int(week)].copy()
    if pool.empty:
        return None

    if roster_row is not None and "pfr_id" in roster_row.index and "pfr_player_id" in pool.columns:
        pfr_id = _id_string(roster_row.get("pfr_id"))
        if pfr_id:
            matched = pool[pool["pfr_player_id"].apply(_id_string) == pfr_id]
            if not matched.empty:
                pool = matched
            else:
                pool = pool.iloc[0:0]

    if pool.empty:
        base = snaps[snaps["week"] == int(week)].copy()
        pool = base[base["name_norm"] == identity["name"]]

    if pool.empty:
        return None

    expected_team = None
    if roster_row is not None:
        expected_team = normalize_team(roster_row.get("team"))
    expected_team = expected_team or identity["teams_by_week"].get(int(week)) or identity["current_team"]
    if expected_team:
        same_team = pool[pool["team_norm"] == expected_team]
        if not same_team.empty:
            pool = same_team

    # In the unlikely event of duplicate rows, aggregate snap counts.
    row = pool.iloc[0].copy()
    for col in ("offense_snaps", "defense_snaps", "st_snaps"):
        if col in pool.columns:
            row[col] = float(pd.to_numeric(pool[col], errors="coerce").fillna(0).sum())
    return row


def _schedule_game(schedule, team, season, week):
    team = normalize_team(team)
    if schedule is None or schedule.empty or not team:
        return None

    games = schedule[
        (schedule["season"] == int(season))
        & (schedule["week"] == int(week))
        & ((schedule["home_team_norm"] == team) | (schedule["away_team_norm"] == team))
    ]
    if games.empty:
        return None
    return games.iloc[0]


def _game_completed(game):
    if game is None:
        return False
    for home_col, away_col in (("home_score", "away_score"), ("home_points", "away_points")):
        if home_col in game.index and away_col in game.index:
            home = _safe_float(game.get(home_col))
            away = _safe_float(game.get(away_col))
            if home is not None and away is not None:
                return True
    result = str(game.get("result") or "").strip()
    if result and result.lower() not in {"nan", "none"}:
        return True
    return False


def _injury_payload(row):
    if row is None:
        return None
    payload = {
        "report_status": row.get("report_status"),
        "primary_injury": row.get("report_primary_injury"),
        "secondary_injury": row.get("report_secondary_injury"),
        "practice_status": row.get("practice_status"),
        "practice_injury": row.get("practice_primary_injury"),
    }
    if "date_modified" in row.index:
        value = row.get("date_modified")
        if value is not None and str(value).lower() not in {"nan", "nat", "none"}:
            payload["updated"] = str(value)
    return {k: v for k, v in payload.items() if v is not None and str(v).lower() not in {"nan", "none", ""}}


def _snap_payload(row):
    if row is None:
        return None
    payload = {}
    for field in ("offense_snaps", "offense_pct", "st_snaps", "st_pct"):
        if field in row.index:
            value = _safe_float(row.get(field))
            if value is not None:
                payload[field] = round(value, 3)
    return payload or None


def _roster_payload(row):
    if row is None:
        return None
    status = str(row.get("status") or "").upper() or None
    return {
        "status": status,
        "status_label": ROSTER_STATUS_LABELS.get(status, status),
        "team": normalize_team(row.get("team")),
    }


def _status_text(value):
    return str(value or "").strip().upper()


def _is_injury_out(injury_row):
    if injury_row is None:
        return False
    report = _status_text(injury_row.get("report_status"))
    return report in {"OUT", "O"} or "OUT" in report


def _has_injury_reason(injury_row):
    if injury_row is None:
        return False
    fields = (
        "report_primary_injury",
        "report_secondary_injury",
        "practice_primary_injury",
        "practice_secondary_injury",
    )
    return any(str(injury_row.get(field) or "").strip() for field in fields)


def resolve_week_history(player, season, target_week, stat_rows=None):
    """Resolve Weeks 1..target_week-1 for one fantasy player.

    Returns one row per elapsed regular-season week.  Missing player-stat rows
    are explained whenever the free nflverse sources can determine why.
    """
    season = int(season)
    target_week = int(target_week)
    identity = _stat_identity(player, stat_rows)

    source_errors = {}
    datasets = {}
    for kind, prepare in (
        ("schedules", _prepare_schedule),
        ("weekly_rosters", _prepare_rosters),
        ("injuries", _prepare_injuries),
        ("snap_counts", _prepare_snaps),
    ):
        try:
            datasets[kind] = prepare(season)
        except Exception as exc:
            datasets[kind] = pd.DataFrame()
            source_errors[kind] = str(exc)

    stat_by_week = {}
    if stat_rows is not None and not stat_rows.empty:
        for _, row in stat_rows.iterrows():
            try:
                wk = int(row["week"])
            except Exception:
                continue
            stat_by_week[wk] = row

    resolved = []
    last_known_team = None

    for week in range(1, target_week):
        stat_row = stat_by_week.get(week)
        roster_row = _pick_roster_row(datasets["weekly_rosters"], identity, week)
        injury_row = _pick_injury_row(datasets["injuries"], identity, week, roster_row)
        snap_row = _pick_snap_row(datasets["snap_counts"], identity, week, roster_row)

        team = None
        if stat_row is not None:
            team = normalize_team(stat_row.get("team") or stat_row.get("recent_team"))
        if team is None and roster_row is not None:
            team = normalize_team(roster_row.get("team"))
        if team is None:
            team = identity["teams_by_week"].get(week)
        if team is None:
            team = last_known_team or identity["current_team"]
        if team:
            last_known_team = team

        game = _schedule_game(datasets["schedules"], team, season, week)
        game_completed = _game_completed(game)

        opponent = None
        if game is not None and team:
            home = normalize_team(game.get("home_team"))
            away = normalize_team(game.get("away_team"))
            opponent = away if home == team else home

        roster_status = _status_text(roster_row.get("status")) if roster_row is not None else ""
        offense_snaps = _safe_float(snap_row.get("offense_snaps")) if snap_row is not None else None
        st_snaps = _safe_float(snap_row.get("st_snaps")) if snap_row is not None else None

        reason = None
        counts_as_game_played = False

        if stat_row is not None:
            state = "PLAYED"
            counts_as_game_played = True
            reason = "nflverse has a completed player-stat row for this week."

        elif team and game is None and not datasets["schedules"].empty:
            state = "BYE"
            reason = "The player's team had no regular-season game on the nflverse schedule."

        elif game is not None and not game_completed:
            state = "GAME_NOT_COMPLETED"
            reason = "A game is on the schedule, but nflverse does not show a completed result yet."

        elif offense_snaps is not None and offense_snaps > 0:
            state = "PLAYED_ZERO_FANTASY_STATS"
            counts_as_game_played = True
            reason = (
                "Snap counts show offensive participation, but no player-stat row was produced; "
                "this is treated as a played game with zero tracked fantasy production."
            )

        elif st_snaps is not None and st_snaps > 0:
            state = "PLAYED_SPECIAL_TEAMS_ONLY"
            counts_as_game_played = True
            reason = "Snap counts show special-teams participation but no offensive fantasy stat row."

        elif roster_status in {"RES", "PUP", "RSN"}:
            state = "INACTIVE_RESERVE"
            reason = f"Weekly roster status was {ROSTER_STATUS_LABELS.get(roster_status, roster_status)}."

        elif roster_status == "SUS":
            state = "SUSPENDED"
            reason = "Weekly roster status was suspended."

        elif roster_status == "DEV":
            state = "PRACTICE_SQUAD"
            reason = "Weekly roster status was practice squad."

        elif roster_status == "EXE":
            state = "EXEMPT"
            reason = "Weekly roster status was exempt."

        elif roster_status == "INA":
            if _has_injury_reason(injury_row) or _is_injury_out(injury_row):
                state = "INACTIVE_INJURY"
                reason = "Weekly roster status was inactive and the official injury report supplies an injury context."
            else:
                state = "INACTIVE_OTHER"
                reason = "Weekly roster status was inactive; no injury reason was present in the available injury report."

        elif _is_injury_out(injury_row):
            state = "INACTIVE_INJURY"
            reason = "The official nflverse injury report listed the player OUT and no participation was recorded."

        elif roster_status == "ACT" and game_completed:
            state = "ACTIVE_NO_SNAPS"
            reason = (
                "Weekly roster data lists the player active, but no player-stat row or snap-count participation "
                "was found for the completed game."
            )

        else:
            state = "DATA_GAP"
            reason = (
                "The week could not be fully explained from schedule, weekly roster, injury report, "
                "snap-count, and player-stat sources."
            )

        item = {
            "week": week,
            "state": state,
            "team": team,
            "opponent": opponent,
            "counts_as_game_played": counts_as_game_played,
            "reason": reason,
        }

        roster_payload = _roster_payload(roster_row)
        injury_payload = _injury_payload(injury_row)
        snap_payload = _snap_payload(snap_row)
        if roster_payload:
            item["weekly_roster"] = roster_payload
        if injury_payload:
            item["injury_report"] = injury_payload
        if snap_payload:
            item["snaps"] = snap_payload

        if stat_row is not None:
            ppr = _safe_float(stat_row.get("fantasy_points_ppr"))
            if ppr is not None:
                item["ppr"] = round(ppr, 3)

        resolved.append(item)

    unresolved = [item["week"] for item in resolved if item["state"] == "DATA_GAP"]
    explained = len(resolved) - len(unresolved)

    return {
        "available": True,
        "source": "nflverse multi-source week resolver",
        "through_week": target_week - 1,
        "weeks_expected": max(target_week - 1, 0),
        "weeks_explained": explained,
        "coverage_complete": len(unresolved) == 0,
        "unresolved_weeks": unresolved,
        "weeks": resolved,
        "source_errors": source_errors or None,
        "note": (
            "An absent player-stat row is not automatically called missing: schedule, weekly roster, "
            "official injury/practice report, and snap counts are cross-checked first."
        ),
    }
