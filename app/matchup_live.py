"""ESPN-style matchup presentation and factual per-player statistics.

No Claude or FantasyPros calls. Live status is grounded in ESPN's public NFL
scoreboard; fantasy points/stat lines remain sourced from the fantasy league.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from math import isfinite
from threading import RLock
from time import monotonic
from urllib.request import Request, urlopen
import json
import re

_SCOREBOARD_CACHE: dict[tuple[int, int], tuple[float, dict]] = {}
_STATUS_LOCK = RLock()
_SCOREBOARD_TTL = 35
_BIO_CACHE: dict[int, tuple[float, dict]] = {}
_BIO_TTL = 6 * 3600

# ESPN NFL scoreboard abbreviations differ from some espn-api fantasy abbreviations.
_ALIASES = {"WSH": "WAS", "JAX": "JAC", "LA": "LAR", "OAK": "LV", "SD": "LAC"}
_SLOT_ORDER = {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "RB/WR/TE": 4,
               "FLEX": 4, "RB/WR": 4, "WR/TE": 4, "OP": 5, "SUPERFLEX": 5,
               "D/ST": 6, "DST": 6, "K": 7}
_FLEX_ALIASES = {"FLEX": "RB/WR/TE", "RB/WR/TE": "RB/WR/TE", "RB/WR": "RB/WR/TE", "WR/TE": "RB/WR/TE"}
_STAT_NAMES = {
    "passingYards": "Passing yards", "passingTouchdowns": "Passing TD", "passingInterceptions": "Interceptions",
    "passingCompletions": "Completions", "passingAttempts": "Pass attempts",
    "rushingAttempts": "Carries", "rushingYards": "Rushing yards", "rushingTouchdowns": "Rushing TD",
    "receivingReceptions": "Receptions", "receivingTargets": "Targets", "receivingYards": "Receiving yards",
    "receivingTouchdowns": "Receiving TD", "lostFumbles": "Lost fumbles", "fumblesLost": "Lost fumbles",
    "madeFieldGoalsFrom50Plus": "FG made 50+", "madeExtraPoints": "Extra points made",
}


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return round(value, 2) if isfinite(value) else None


def _team(value):
    raw = str(value or "").upper().strip()
    return _ALIASES.get(raw, raw)


def _slot(value):
    raw = str(value or "").upper()
    return _FLEX_ALIASES.get(raw, raw)


def head_to_head(my_side, opponent_side):
    """Match equivalent ESPN starter slots, with distinct RB1/RB2 and WR1/WR2.

    Missing projections are not zero; ties and unavailable data are explicit.
    """
    mine = defaultdict(list)
    theirs = defaultdict(list)
    for player in (my_side or {}).get("roster", []):
        if player.get("starter") and _slot(player.get("slot")) not in {"BE", "IR", "FA", "BN"}:
            mine[_slot(player.get("slot"))].append(player)
    for player in (opponent_side or {}).get("roster", []):
        if player.get("starter") and _slot(player.get("slot")) not in {"BE", "IR", "FA", "BN"}:
            theirs[_slot(player.get("slot"))].append(player)
    keys = sorted(set(mine) | set(theirs), key=lambda k: (_SLOT_ORDER.get(k, 99), k))
    result = []
    for key in keys:
        for index in range(max(len(mine[key]), len(theirs[key]))):
            my_player = mine[key][index] if index < len(mine[key]) else None
            their_player = theirs[key][index] if index < len(theirs[key]) else None
            a = _number(my_player.get("projected_points")) if my_player else None
            b = _number(their_player.get("projected_points")) if their_player else None
            diff = round(a - b, 2) if a is not None and b is not None else None
            advantage = "me" if diff is not None and diff > 0 else "opponent" if diff is not None and diff < 0 else "tie" if diff == 0 else "unknown"
            result.append({"slot": f"{key}{index + 1}" if max(len(mine[key]), len(theirs[key])) > 1 else key,
                           "position_slot": key, "my_player": my_player, "opponent_player": their_player,
                           "projection_margin": diff, "projected_advantage": advantage})
    return result


def _download_scoreboard(year, week):
    url = ("https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
           f"?dates={int(year)}&seasontype=2&week={int(week)}&limit=100")
    req = Request(url, headers={"User-Agent": "FantasyCoach/1.0 (local dashboard)", "Accept": "application/json"})
    with urlopen(req, timeout=5) as response:
        return json.loads(response.read())


def nfl_game_statuses(league, week, fetcher=None):
    """One cached public ESPN request; unknown rather than guessed on failures.

    During games, caller should refetch its matchup strategy ~60 seconds apart.
    This is not play-by-play and may lag the broadcast.
    """
    year = int(getattr(league, "year", 0) or getattr(league, "season", 0) or 2026)
    key = (year, week)
    with _STATUS_LOCK:
        cached = _SCOREBOARD_CACHE.get(key)
        if fetcher is None and cached and monotonic() - cached[0] < _SCOREBOARD_TTL:
            return cached[1]
    result = {"source": "ESPN NFL public scoreboard", "status": "unavailable", "games": {},
              "as_of": datetime.now(timezone.utc).isoformat(),
              "note": "NFL live game status unavailable. Player fantasy points may still be available."}
    try:
        data = (fetcher or _download_scoreboard)(year, week)
        games = {}
        for event in data.get("events", []):
            competition = (event.get("competitions") or [{}])[0]
            competitors = competition.get("competitors", [])
            if len(competitors) < 2:
                continue
            teams = [_team(c.get("team", {}).get("abbreviation")) for c in competitors]
            if not all(teams):
                continue
            status = event.get("status", {})
            detail = status.get("type", {})
            state = detail.get("state")
            game_state = "LIVE" if state == "in" else "FINAL" if state == "post" else "SCHEDULED" if state == "pre" else "UNKNOWN"
            meta = {"state": game_state, "detail": detail.get("shortDetail") or detail.get("description"),
                    "kickoff": event.get("date"), "opponent": None}
            for ix, name in enumerate(teams):
                games[name] = {**meta, "opponent": teams[1 - ix]}
        result.update({"status": "available" if games else "unavailable", "games": games,
                       "note": "Scores and stat lines may update later than the broadcast." if games else result["note"]})
    except Exception:
        pass
    if fetcher is None:
        with _STATUS_LOCK:
            _SCOREBOARD_CACHE[key] = (monotonic(), result)
    return result


def enrich_live_players(my_side, their_side, game_info):
    for side in (my_side, their_side):
        for player in (side or {}).get("roster", []):
            team = _team(player.get("nfl_team"))
            game = (game_info.get("games") or {}).get(team)
            if player.get("on_bye"):
                player["game_status"] = "BYE"
            elif game:
                player["game_status"] = game["state"]
                player["game_status_detail"] = game.get("detail")
                player["game_kickoff"] = game.get("kickoff")
                player["opponent"] = player.get("opponent") or game.get("opponent")
            else:
                player["game_status"] = "UNKNOWN"


def player_public_bio(player_id, fetcher=None):
    """Cached public ESPN athlete bio. Best-effort; never use an inferred age/team."""
    if not isinstance(player_id, int) or player_id <= 0:
        return {}
    with _STATUS_LOCK:
        cached = _BIO_CACHE.get(player_id)
        if fetcher is None and cached and monotonic() - cached[0] < _BIO_TTL:
            return cached[1]
    bio = {}
    try:
        if fetcher is None:
            url = f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/athletes/{player_id}"
            request = Request(url, headers={"User-Agent": "FantasyCoach/1.0", "Accept": "application/json"})
            with urlopen(request, timeout=3) as response:
                data = json.load(response)
        else:
            data = fetcher(player_id)
        if isinstance(data, dict):
            athlete = data.get("athlete", data)
            if not isinstance(athlete, dict):
                athlete = {}
            college = athlete.get("college") or {}
            experience = athlete.get("experience") or {}
            bio = {"height": athlete.get("displayHeight"), "weight": athlete.get("displayWeight"),
                   "age": athlete.get("age"),
                   "college": college.get("name") if isinstance(college, dict) else None,
                   "years_pro": experience.get("years") if isinstance(experience, dict) else None,
                   "jersey": athlete.get("jersey"),
                   "source": "ESPN public NFL athlete profile"}
            bio = {k: v for k, v in bio.items() if v is not None}
    except Exception:
        pass
    if fetcher is None:
        with _STATUS_LOCK:
            _BIO_CACHE[player_id] = (monotonic(), bio)
    return bio


def _clean_breakdown(value):
    if not isinstance(value, dict):
        return []
    pairs = []
    # espn-api occasionally leaves derived ESPN stat IDs (47, 48, 210, etc.)
    # in the breakdown. They are scoring counters, not standalone NFL stats.
    # Prefer verified base-stat IDs only; unknown IDs are never shown raw.
    numeric = {
        "0": "Pass attempts", "1": "Completions", "3": "Passing yards",
        "4": "Passing TD", "20": "Interceptions", "23": "Carries",
        "24": "Rushing yards", "25": "Rushing TD", "41": "Receptions",
        "42": "Receiving yards", "43": "Receiving TD", "58": "Targets",
        "59": "Receiving yards after catch", "60": "Receiving yards per reception",
        "72": "Fumbles lost", "86": "Extra points made", "99": "Defensive sacks",
        "210": "Games played", "213": "Receiving first downs",
    }
    # ESPN duplicates certain counters under different IDs; avoid counting twice.
    alternate_ids = {"22": "3", "40": "24", "53": "41", "61": "42"}
    present = {str(k) for k in value}
    for key, raw in value.items():
        val = _number(raw)
        if val is None or (abs(val) < 0.001):
            continue
        code = str(key)
        if code in alternate_ids and alternate_ids[code] in present:
            continue
        if code.isdigit():
            label = numeric.get(code) or (numeric.get(alternate_ids[code]) if code in alternate_ids else None)
            if label is None:
                continue
        else:
            label = _STAT_NAMES.get(code)
            if not label:
                label = re.sub(r"([a-z])([A-Z])", r"\1 \2", code).replace("_", " ").capitalize()
        pairs.append({"key": code, "label": label, "value": val})
    # Keep named/base football stats over duplicated derived ESPN IDs.
    unique = {}
    for pair in pairs:
        label = pair["label"]
        if label not in unique or (unique[label]["key"].isdigit() and not pair["key"].isdigit()):
            unique[label] = pair
    return list(unique.values())[:32]


def _completed_espn_fantasy_scores(league, player_id, current_week, *, get_scores=None,
                                   known_weeks=(), max_games=5):
    """Read actual fantasy points from completed ESPN fantasy box scores.

    ESPN's loaded Player.stats dict may only contain the latest week even
    though the authenticated league has historical weekly box-score data.
    Look up the exact ESPN player ID (never a fuzzy name match). Historical
    results are shared via league_intelligence's cache. No Claude or
    FantasyPros calls. A missing response must never become a zero score.
    """
    from league_intelligence import _get_box_scores
    fetch = get_scores or _get_box_scores
    known = set(known_weeks)
    result = []
    # Historical weeks are final in the league's scheduling model. The
    # current week is handled by the separate live/actual panel.
    weeks = list(range(max(1, int(current_week) - max_games), int(current_week)))
    for week in reversed(weeks):
        if week in known:
            continue
        try:
            boxes = fetch(league, week)
        except Exception:
            continue
        found = None
        for box in boxes:
            for side in ("home", "away"):
                for player in (getattr(box, f"{side}_lineup", None) or []):
                    if str(getattr(player, "playerId", "")) != str(player_id):
                        continue
                    actual = _number(getattr(player, "points", None))
                    if actual is None:
                        continue
                    found = {
                        "week": week, "fantasy_points": actual,
                        "fantasy_points_source": "ESPN Fantasy league box score",
                        "projected_points": _number(getattr(player, "projected_points", None)),
                        "stats": _clean_breakdown(getattr(player, "breakdown", None)),
                        "source": "ESPN Fantasy completed-week box score",
                    }
                    break
                if found:
                    break
            if found:
                break
        if found is not None:
            result.append(found)
    return result


def player_profile(league, player_id, week, *, free_agent=None):
    """Find a player by exact ESPN ID, including loaded ESPN free agents.

    A player on waivers is not in any fantasy team roster or matchup; the
    browser pool supplies their authoritative ESPN player ID and name. We
    never guess one player's biography or fantasy points from another name.
    """
    from league_intelligence import _get_box_scores, _week
    week = _week(league, week)
    player = None
    box_player = None
    roster_player = None
    try:
        for box in _get_box_scores(league, week):
            for side in ("home", "away"):
                for p in (getattr(box, f"{side}_lineup", None) or []):
                    if getattr(p, "playerId", None) == player_id:
                        box_player = p
                        player = p
                        break
                if player is not None:
                    break
            if player is not None:
                break
    except Exception:
        pass
    for team in getattr(league, "teams", []) or []:
        roster_player = next((p for p in getattr(team, "roster", []) or [] if getattr(p, "playerId", None) == player_id), None)
        if roster_player is not None:
            break
    player = box_player or roster_player
    if player is None and isinstance(free_agent, dict) and str(free_agent.get("espn_id")) == str(player_id):
        from types import SimpleNamespace
        player = SimpleNamespace(
            playerId=player_id, name=free_agent.get("name", "Unknown"),
            proTeam=free_agent.get("nfl_team") or free_agent.get("team"),
            position=free_agent.get("position"),
            injuryStatus=free_agent.get("injury_status") or "UNKNOWN",
            espn_projected_points=free_agent.get("espn_projected_points") if free_agent.get("espn_projected_points") is not None else free_agent.get("espn_projection"),
            stats={}, total_points=None, avg_points=None)
    if player is None:
        raise ValueError("Player is not in the loaded league rosters or matchup.")
    # The roster Player generally carries a fuller week-by-week stat history,
    # whereas a BoxPlayer is authoritative for this week's live scoring.
    stats = getattr(roster_player, "stats", None) or getattr(player, "stats", None) or {}
    current = stats.get(week, stats.get(str(week), {})) if isinstance(stats, dict) else {}
    if not isinstance(current, dict):
        current = {}
    history = []
    if isinstance(stats, dict):
        for k, row in stats.items():
            try:
                w = int(k)
            except (ValueError, TypeError):
                continue
            if not 1 <= w < week or not isinstance(row, dict):
                continue
            breakdown = row.get("breakdown") or {}
            if not breakdown and _number(row.get("points")) in (None, 0):
                continue
            history.append({"week": w, "fantasy_points": _number(row.get("points")),
                            "fantasy_points_source": "ESPN Fantasy league score",
                            "projected_points": _number(row.get("projected_points")),
                            "stats": _clean_breakdown(breakdown)})
    history.sort(key=lambda x: x["week"], reverse=True)
    season_totals = defaultdict(float)
    stat_weeks = 0
    for row in history:
        if row["stats"]:
            stat_weeks += 1
        for stat in row["stats"]:
            season_totals[stat["key"]] += stat["value"]
    # Totals only reflect the weekly breakdowns ESPN returned, not necessarily
    # the full season. Never label sparse data as complete season statistics.
    season_stats = _clean_breakdown(dict(season_totals))
    # ESPN Fantasy league roster stats can omit most of the season. Prefer
    # independently published NFL season totals; never present 1-week data as
    # an entire season. Failure to retrieve public stats preserves the fallback.
    from espn_player_stats import public_player_data
    year = int(getattr(league, "year", 0) or getattr(league, "season", 0) or 2026)
    public = public_player_data(player_id, year)
    full_stats = public["season_stats"]
    full_source = public["stats_source"] if full_stats else "unavailable"
    verified_weeks = []
    # NFLverse is already used by the existing coach; its explicit season/week
    # columns prevent accidental substitution of ESPN career statistics.
    # It is a best-effort source, not a prerequisite for opening a profile.
    try:
        from league_settings import get_league_scoring_context, reception_points_or_default
        from season_nfl_stats import current_season_player_stats
        reception_points = reception_points_or_default(get_league_scoring_context(league), default=1.0)
        verified = current_season_player_stats(
            getattr(player, "name", ""), getattr(player, "position", None),
            getattr(player, "proTeam", None), year, week, reception_points=reception_points)
    except Exception:
        verified = {"season_stats": [], "weeks_covered": [], "recent_games": [], "stats_source": "unavailable"}
    if verified.get("season_stats"):
        full_stats = verified["season_stats"]
        full_source = verified["stats_source"]
        verified_weeks = verified["weeks_covered"]
    if full_stats:
        season_stats = full_stats

    # Preserve official ESPN points wherever available, then fill remaining
    # weeks from nflverse if it has verified player-week rows. A public NFL
    # gamelog does NOT carry league fantasy scores and must never supersede
    # either of these.
    by_week = {g["week"]: dict(g) for g in (verified.get("recent_games") or [])
               if isinstance(g.get("week"), int)}
    for espn_game in history:
        w = espn_game["week"]
        merged = dict(by_week.get(w, {}))
        if espn_game.get("fantasy_points") is not None:
            merged["fantasy_points"] = espn_game["fantasy_points"]
            merged["fantasy_points_source"] = "ESPN Fantasy league score"
        if espn_game.get("stats") and not merged.get("stats"):
            merged["stats"] = espn_game["stats"]
        if espn_game.get("projected_points") is not None:
            merged["projected_points"] = espn_game["projected_points"]
        merged["week"] = w
        by_week[w] = merged

    # The critical fallback: fetch the exact historical player points from
    # ESPN fantasy box scores. These are actual league-scored points, not
    # approximations and not an NFL-only gamelog with an empty score field.
    scored_weeks = {w for w, row in by_week.items() if row.get("fantasy_points") is not None}
    desired = min(5, max(0, week - 1))
    if len([w for w in scored_weeks if week - 5 <= w < week]) < desired:
        for espn_game in _completed_espn_fantasy_scores(
                league, player_id, week, known_weeks=scored_weeks):
            w = espn_game["week"]
            merged = dict(by_week.get(w, {}))
            merged.update({k: v for k, v in espn_game.items() if v is not None and
                           (k not in {"stats"} or v or not merged.get("stats"))})
            by_week[w] = merged

    # The league can still report Week 7 as current after Monday's final.
    # Include this week in the *completed* game log only when the player's
    # actual NFL game is explicitly FINAL, never merely because points > 0.
    try:
        team_status = (nfl_game_statuses(league, week).get("games") or {}).get(
            _team(getattr(player, "proTeam", None)), {})
    except Exception:
        team_status = {}
    if team_status.get("state") == "FINAL":
        current_score = (_number(getattr(box_player, "points", None)) if box_player is not None
                         else _number(current.get("points")))
        if current_score is not None:
            completed = dict(by_week.get(week, {}))
            completed.update({"week": week, "fantasy_points": current_score,
                              "fantasy_points_source": "ESPN Fantasy final-week score",
                              "projected_points": (_number(getattr(box_player, "projected_points", None))
                                                   if box_player is not None else _number(current.get("projected_points"))),
                              "opponent": team_status.get("opponent"),
                              "source": "ESPN Fantasy completed game"})
            if not completed.get("stats"):
                completed["stats"] = _clean_breakdown(
                    getattr(box_player, "breakdown", None) if box_player is not None
                    else current.get("breakdown"))
            by_week[week] = completed

    history = sorted(by_week.values(), key=lambda g: g.get("week", 0), reverse=True)
    if not history and public.get("recent_games"):
        # Truly unsupported player: retain dated NFL gamelogs, but clearly
        # label missing fantasy scores instead of fabricating them.
        history = [{**g, "week": None} for g in public["recent_games"]]
    season_points = _number(getattr(roster_player or player, "total_points", None))
    season_avg = _number(getattr(roster_player or player, "avg_points", None))
    season_games = len(history)
    # box-score objects may only carry partial stats; avoid replacing ESPN's season averages.
    current_points = (_number(getattr(box_player, "points", None)) if box_player is not None
                      else _number(current.get("points")))
    current_proj = (_number(getattr(box_player, "projected_points", None)) if box_player is not None
                    else (_number(current.get("projected_points")) if current.get("projected_points") is not None
                          else _number(getattr(player, "espn_projected_points", None))))
    return {"espn_id": player_id, "name": getattr(player, "name", "Unknown"),
            "nfl_team": getattr(player, "proTeam", None), "position": getattr(player, "position", None),
            "injury_status": getattr(player, "injuryStatus", None), "week": week,
            "season_fantasy_points": season_points, "season_fantasy_ppg": season_avg,
            "season_games_in_loaded_stats": season_games,
            "available_weeks_stat_totals": season_stats,
            "stat_coverage_weeks": len(verified_weeks) if verified_weeks else stat_weeks,
            "season_stats_as_of_week": max(verified_weeks) if verified_weeks else None,
            "season_stats_source": full_source if full_stats else ("Partial ESPN Fantasy weekly breakdowns" if stat_weeks else "unavailable"),
            "season_stats_complete": bool(full_stats),
            "bio": player_public_bio(player_id),
            "current_week": {"projected_points": current_proj,
                             "fantasy_points": current_points,
                             "stats": _clean_breakdown((getattr(box_player, "breakdown", None) if box_player is not None else None) or current.get("breakdown")),
                             "projected_stats": _clean_breakdown((getattr(box_player, "projected_breakdown", None) if box_player is not None else None) or current.get("projected_breakdown"))},
            "last_completed_game": history[0] if history else None,
            "recent_games": history[:5],
            "source": ("ESPN free-agent pool + public NFL stats" if free_agent and not (box_player or roster_player)
                       else "ESPN fantasy player data"),
            "note": "Recent fantasy points use historical ESPN Fantasy scores when available, followed by verified nflverse weekly scoring. Public NFL gamelogs never masquerade as fantasy scores."}


def clear_live_cache():
    with _STATUS_LOCK:
        _SCOREBOARD_CACHE.clear()
        _BIO_CACHE.clear()
