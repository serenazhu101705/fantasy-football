import io
import json
import os
import re

from contextlib import redirect_stdout
from difflib import get_close_matches

from anthropic import Anthropic
from dotenv import load_dotenv

from decision_engine import (
    build_player_snapshot,
    compare_players,
    compare_players_ros,
)
from main import main as build_fantasy_context
from player_enrichment import enrich_players
from fantasypros_client import get_consensus_rankings
from player_value import add_player_values
from matchup_context import get_matchup_context
from shadow_predictor import get_shadow_prediction
from free_fantasy_sources import get_free_fallback_signals
from actual_performance import get_actual_performance


# ==================================================
# Environment / Claude
# ==================================================

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
if not ANTHROPIC_API_KEY:
    raise RuntimeError("ANTHROPIC_API_KEY is not set in .env")

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
CLAUDE_EFFORT = os.getenv("CLAUDE_EFFORT", "medium").lower()

if CLAUDE_EFFORT not in {"low", "medium", "high", "xhigh", "max"}:
    raise RuntimeError(
        "CLAUDE_EFFORT must be one of: low, medium, high, xhigh, max"
    )

client = Anthropic(api_key=ANTHROPIC_API_KEY)


# ==================================================
# State
# ==================================================

APP_CONTEXT = None
CHAT_HISTORY = []
EXTERNAL_PLAYER_CACHE = {}

MAX_HISTORY_MESSAGES = 2
MAX_TOOL_ROUNDS = 8
SEASON = 2026


# ==================================================
# Prompt
# ==================================================

SYSTEM_PROMPT = """
You are Serena's fantasy-football decision assistant.

Use the fantasy tools for every current-data claim about projections, rankings,
injuries, practices, availability, usage, waivers, lineups, matchup strength,
market value, or the shadow ML forecast.

Decision philosophy:
- Hard facts and eligibility are authoritative: OUT, IR, bye, roster status,
  legal lineup slots, and supplied provider data must never be invented or
  overridden.
- weekly_score, ros_score, optimizer output, and deterministic comparisons are
  ADVISORY summaries. They are useful evidence, not the answer. Do not blindly
  follow their winner or score gap.
- Make the final fantasy judgment by synthesizing the evidence that matters in
  this specific situation: projections, ECR/rankings, role/volume, injury and
  practice risk, matchup, market/ROS signals, and the shadow ML forecast.
- The importance of each signal may change by situation. For example, a Friday
  DNP can matter more than a small projection edge; a large projection gap can
  matter more than a modest matchup difference.

Provider rules:
- FantasyPros ECR/projections are useful expert/forecast evidence when present, but they are not required for Claude to make a decision when strong factual fallback evidence exists.
- actual_performance comes from nflverse completed-game statistics and is factual, independent of FantasyPros, and should be used heavily for role, volume, production, and trend analysis.
- When FantasyPros is missing or rate-limited, do NOT treat the player as unevaluable. Synthesize actual_performance + ESPN + Sleeper + FantasyCalc + LeagueLogs + matchup + shadow ML as available, and lower confidence appropriately.
- FantasyCalc is a FREE market-derived redraft value/rank based on real trades.
  Treat it as an independent ROS/market signal. It is NOT FantasyPros ECR and
  is NOT a weekly projection.
- Sleeper supplies free player/status/injury/practice/depth-chart metadata. It
  is NOT a projection or ECR provider.
- If FantasyPros is missing or rate-limited, use the free fallback evidence
  instead of pretending the missing FantasyPros field exists.
- If providers conflict on a current injury/status fact, state the conflict and
  be cautious; do not silently choose one as fact. The core can_play_this_week
  hard-eligibility flag remains binding for lineup legality.
- If comparison_status=FALLBACK_SUPPORTED, you may still make a recommendation
  from the fallback evidence, but explicitly note that a primary FantasyPros
  input is missing. If comparison_status=INCOMPLETE_DATA, be more cautious.
- The rolling ML model is SHADOW ONLY: zero live decision weight. It may inform
  your judgment, but it never changes weekly_score or the optimizer yet.
- Matchup context is descriptive PPR points allowed to the position, not a
  projection.
- Never turn an injury/workload multiplier directly into a fantasy-points
  projection unless a tool explicitly provides an adjusted projection.

Behavior:
- For direct two-player weekly questions, prefer compare_players_weekly.
- For direct two-player ROS/trade questions, prefer compare_players_ros.
- For an ML question, prefer get_shadow_prediction.
- Never fill a missing current player-specific fact from general football
  memory. General strategy is fine; current player claims must come from tools.
- Do not make meta-comments about whether tools were called on earlier turns.
- Lead with the recommendation, then the most decision-relevant evidence and
  uncertainty. Be concise unless the user asks for detail.
"""


# ==================================================
# Helpers
# ==================================================

def normalize_name(value):
    if value is None:
        return ""
    name = str(value).lower().strip()
    name = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", name)
    name = re.sub(r"[^a-z0-9]+", " ", name)
    return " ".join(name.split())


def json_text(value):
    return json.dumps(
        value,
        default=str,
        separators=(",", ":"),
    )


def _non_none(mapping):
    return {
        key: value
        for key, value in mapping.items()
        if value is not None
    }


def _current_week():
    return int(APP_CONTEXT.get("current_week"))


def _league():
    return APP_CONTEXT.get("league")


def _league_team_count():
    league = _league()
    teams = getattr(league, "teams", None) if league is not None else None
    try:
        return len(teams) if teams else 12
    except TypeError:
        return 12


def _free_fallback_context(player):
    """Free independent evidence used when FantasyPros is missing/limited."""
    try:
        return get_free_fallback_signals(
            player,
            num_teams=_league_team_count(),
            num_qbs=1,
            ppr=1.0,
        )
    except Exception as exc:
        return {
            "fantasycalc": {
                "available": False,
                "source": "FantasyCalc",
                "reason": str(exc),
            },
            "sleeper": {
                "available": False,
                "source": "Sleeper",
                "reason": str(exc),
            },
        }


def _actual_performance_context(player):
    """Completed-game nflverse evidence, independent of FantasyPros."""
    try:
        return get_actual_performance(
            player,
            season=SEASON,
            week=_current_week(),
        )
    except Exception as exc:
        return {
            "available": False,
            "source": "nflverse",
            "reason": str(exc),
        }


# ==================================================
# Player pool / arbitrary ESPN lookup
# ==================================================

def get_all_known_players():
    if APP_CONTEXT is None:
        return []

    players = []
    seen = set()

    groups = [
        APP_CONTEXT.get("roster") or [],
        APP_CONTEXT.get("waiver_candidates") or [],
        list(EXTERNAL_PLAYER_CACHE.values()),
    ]

    for group in groups:
        for player in group:
            key = player.get("espn_id") or (
                normalize_name(player.get("name")),
                player.get("position"),
            )
            if key in seen:
                continue
            seen.add(key)
            players.append(player)

    return players


def _find_loaded_player(requested_name):
    players = get_all_known_players()
    requested = normalize_name(requested_name)

    if not requested:
        return None

    for player in players:
        if normalize_name(player.get("name")) == requested:
            return player

    partial = []
    for player in players:
        player_name = normalize_name(player.get("name"))
        if requested in player_name or player_name in requested:
            partial.append(player)

    if len(partial) == 1:
        return partial[0]

    lookup = {
        normalize_name(player.get("name")): player
        for player in players
    }
    fuzzy = get_close_matches(
        requested,
        list(lookup.keys()),
        n=1,
        cutoff=0.82,
    )
    return lookup[fuzzy[0]] if fuzzy else None


def _resolve_espn_name(requested_name):
    """Resolve a natural player name against ESPN's league-wide player map."""

    league = _league()
    player_map = getattr(league, "player_map", {}) or {}

    if requested_name in player_map:
        return requested_name

    requested = normalize_name(requested_name)
    normalized_lookup = {
        normalize_name(name): name
        for name in player_map.keys()
    }

    if requested in normalized_lookup:
        return normalized_lookup[requested]

    fuzzy = get_close_matches(
        requested,
        list(normalized_lookup.keys()),
        n=1,
        cutoff=0.80,
    )

    if fuzzy:
        return normalized_lookup[fuzzy[0]]

    return None


def _current_week_projection(player_obj, week):
    stats = getattr(player_obj, "stats", {}) or {}
    week_stats = (
        stats.get(week)
        or stats.get(str(week))
        or {}
    )
    return week_stats.get("projected_points")


def _raw_player_from_espn(player_obj, week):
    on_team_id = getattr(player_obj, "onTeamId", None)

    try:
        rostered = on_team_id is not None and int(on_team_id) > 0
    except (TypeError, ValueError):
        rostered = bool(on_team_id)

    return {
        "espn_id": getattr(player_obj, "playerId", None),
        "name": getattr(player_obj, "name", None),
        "position": getattr(player_obj, "position", None),
        "nfl_team": getattr(player_obj, "proTeam", None),
        "lineup_slot": getattr(player_obj, "lineupSlot", "FA"),
        "injury_status": getattr(player_obj, "injuryStatus", None),
        "espn_projected_points": _current_week_projection(player_obj, week),
        "espn_on_bye": False,
        "rostered": rostered,
        "fantasy_team_id": on_team_id,
        "external_lookup": True,
    }



def _id_key(value):
    if value is None:
        return None
    try:
        return str(int(float(value)))
    except (TypeError, ValueError):
        return str(value).strip()


def _ranking_name(row):
    return (
        row.get("player_name")
        or row.get("name")
        or row.get("short_name")
        or row.get("player_name_full")
    )


def _find_ranking_row(rows, player):
    wanted_id = _id_key(player.get("fantasypros_id"))
    wanted_name = normalize_name(player.get("name"))

    if wanted_id:
        for row in rows:
            row_id = _id_key(
                row.get("player_id")
                or row.get("id")
                or row.get("fpid")
            )
            if row_id == wanted_id:
                return row

    if wanted_name:
        by_name = [
            row
            for row in rows
            if normalize_name(_ranking_name(row)) == wanted_name
        ]
        if len(by_name) == 1:
            return by_name[0]

    return None


def _recover_external_rankings(player):
    """Best-effort cached bulk ranking recovery for an arbitrary player.

    This is especially useful after FantasyPros has opened the per-run 429
    circuit breaker: get_consensus_rankings can still serve a cached position
    list, and name matching can recover a player even if provider IDs differ.
    """

    position = str(player.get("position") or "").upper()
    if position not in {"QB", "RB", "WR", "TE"}:
        return player

    changed = False

    if player.get("ecr") is None:
        try:
            result = get_consensus_rankings(
                season=SEASON,
                week=_current_week(),
                position=position,
                scoring="PPR",
            )
            row = _find_ranking_row(result.get("players", []), player)
            if row:
                player["ecr"] = row.get("rank_ecr")
                player["rank_min"] = row.get("rank_min")
                player["rank_max"] = row.get("rank_max")
                player["rank_std"] = row.get("rank_std")
                player["pos_rank"] = row.get("pos_rank")
                player["start_sit_grade"] = row.get("start_sit_grade")
                player["opponent"] = row.get("player_opponent")
                changed = True
        except Exception:
            pass

    if player.get("ros_ecr") is None:
        try:
            result = get_consensus_rankings(
                season=SEASON,
                week=_current_week(),
                position=position,
                scoring="PPR",
                ranking_type="ROS",
            )
            row = _find_ranking_row(result.get("players", []), player)
            if row:
                player["ros_ecr"] = row.get("rank_ecr")
                player["ros_min"] = row.get("rank_min")
                player["ros_max"] = row.get("rank_max")
                player["ros_std"] = row.get("rank_std")
                player["ros_pos_rank"] = row.get("pos_rank")
                changed = True
        except Exception:
            pass

    if changed:
        try:
            add_player_values(
                [player],
                current_week=_current_week(),
            )
        except Exception:
            pass

    return player


def lookup_external_player(requested_name):
    """Fetch and enrich a player anywhere in ESPN's player universe.

    This is called only when the player was not already in the roster / waiver
    data, so normal start-sit questions stay fast and cheap.
    """

    cache_key = normalize_name(requested_name)
    if cache_key in EXTERNAL_PLAYER_CACHE:
        return EXTERNAL_PLAYER_CACHE[cache_key]

    league = _league()
    resolved_name = _resolve_espn_name(requested_name)

    if resolved_name is None:
        return None

    try:
        espn_player = league.player_info(name=resolved_name)
    except Exception:
        return None

    if espn_player is None:
        return None

    if isinstance(espn_player, list):
        if not espn_player:
            return None
        espn_player = espn_player[0]

    raw_player = _raw_player_from_espn(
        espn_player,
        _current_week(),
    )

    if not raw_player.get("name"):
        return None

    # Fully enrich only the requested outside player. This avoids doing heavy
    # API work for the whole NFL player pool.
    try:
        enriched = enrich_players(
            players=[raw_player],
            season=SEASON,
            week=_current_week(),
            include_player_specific=True,
            include_injuries=True,
        )
    except TypeError:
        # Backward compatibility with an older enrich_players signature.
        enriched = enrich_players(
            players=[raw_player],
            season=SEASON,
            week=_current_week(),
            include_player_specific=True,
        )
    except Exception:
        enriched = [raw_player]

    player = enriched[0]
    player = _recover_external_rankings(player)
    EXTERNAL_PLAYER_CACHE[cache_key] = player
    EXTERNAL_PLAYER_CACHE[normalize_name(player.get("name"))] = player
    return player


def find_player(requested_name, allow_external=True):
    player = _find_loaded_player(requested_name)
    if player is not None:
        return player

    if allow_external:
        return lookup_external_player(requested_name)

    return None


# ==================================================
# Compact player context
# ==================================================

def _shadow_context(player):
    result = get_shadow_prediction(
        player,
        season=SEASON,
        week=_current_week(),
    )

    if not result.get("available"):
        return _non_none({
            "available": False,
            "reason": result.get("reason"),
        })

    prediction = result.get("prediction_ppr")
    espn = player.get("espn_projected_points")
    fantasypros = player.get("fp_ppr")

    source_values = []
    for value in (espn, fantasypros):
        try:
            if value is not None:
                source_values.append(float(value))
        except (TypeError, ValueError):
            pass

    consensus = (
        sum(source_values) / len(source_values)
        if source_values
        else None
    )

    vs_consensus = (
        prediction - consensus
        if prediction is not None and consensus is not None
        else None
    )

    if vs_consensus is None:
        disagreement = None
        model_lean = None
    else:
        magnitude = abs(vs_consensus)
        if magnitude < 0.75:
            disagreement = "LOW"
        elif magnitude < 2.0:
            disagreement = "MODERATE"
        else:
            disagreement = "HIGH"

        if vs_consensus > 0.5:
            model_lean = "HIGHER_THAN_CONSENSUS"
        elif vs_consensus < -0.5:
            model_lean = "LOWER_THAN_CONSENSUS"
        else:
            model_lean = "IN_LINE_WITH_CONSENSUS"

    return _non_none({
        "available": True,
        "prediction_ppr": prediction,
        "consensus_projection": (
            round(consensus, 3)
            if consensus is not None
            else None
        ),
        "vs_consensus": (
            round(vs_consensus, 3)
            if vs_consensus is not None
            else None
        ),
        "disagreement_level": disagreement,
        "model_lean": model_lean,
        "vs_espn": (
            round(prediction - float(espn), 3)
            if prediction is not None and espn is not None
            else None
        ),
        "vs_fantasypros": (
            round(prediction - float(fantasypros), 3)
            if prediction is not None and fantasypros is not None
            else None
        ),
        "player_history_games_used": result.get("player_history_games_used"),
        "model_training_rows": result.get("model_training_rows"),
        "trained_through_week": result.get("trained_through_week"),
        "live_decision_weight": result.get("live_decision_weight", 0.0),
        "availability_adjusted": result.get("availability_adjusted", False),
    })


def compact_player(
    player,
    include_shadow=True,
    include_matchup=True,
    include_free=True,
    include_actual=True,
):
    if not player:
        return None

    availability = player.get("availability") or {}
    usage = player.get("recent_usage") or {}
    leaguelogs = (
        player.get("signals", {})
        .get("leaguelogs", {})
    )

    result = _non_none({
        "name": player.get("name"),
        "position": player.get("position"),
        "team": player.get("nfl_team"),
        "rostered": player.get("rostered"),
        "fantasy_team_id": player.get("fantasy_team_id"),
        "lineup_slot": player.get("lineup_slot"),
        "opponent": player.get("opponent"),
        "projection_source": player.get("projection_source"),
        "espn_projection": player.get("espn_projected_points"),
        "fantasypros_projection": player.get("fp_ppr"),
        "weekly_ecr": (
            player.get("ecr")
            if player.get("ecr") is not None
            else player.get("pos_rank")
        ),
        "ecr_best": player.get("rank_min"),
        "ecr_worst": player.get("rank_max"),
        "ecr_sd": player.get("rank_std"),
        "start_sit_grade": player.get("start_sit_grade"),
        # Advisory composite scores: useful evidence, never a command to Claude.
        "weekly_score_advisory": player.get("weekly_score"),
        "fantasypros_ros_rank": player.get("ros_pos_rank"),
        "ros_score_advisory": player.get("ros_score"),
        "leaguelogs_ros_rank": leaguelogs.get("ros_pos_rank"),
        "leaguelogs_market_value": leaguelogs.get("market_value"),
        "recent_ppr": usage.get("ppr_points_per_game"),
        "carries_per_game": usage.get("carries_per_game"),
        "targets_per_game": usage.get("targets_per_game"),
        "snap_share": usage.get("snap_share"),
        "route_participation": usage.get("route_participation"),
        "injury_status": player.get("injury_status"),
        "can_play_this_week": availability.get("can_play_this_week", True),
        "weekly_multiplier": availability.get("weekly_multiplier"),
        "risk_level": availability.get("risk_level"),
        "play_probability": availability.get("play_probability"),
        "practice_summary": availability.get("practice_summary"),
        "availability_reason": availability.get("reason"),
        "on_bye_week": player.get("is_on_bye") or player.get("espn_on_bye"),
        "weekly_data_complete": player.get("weekly_data_complete"),
        "weekly_missing_data": player.get("weekly_missing_data") or None,
        "ros_data_complete": player.get("ros_data_complete"),
        "ros_missing_data": player.get("ros_missing_data") or None,
    })

    # Factual completed-game evidence is intentionally separate from projections
    # and rankings so Claude can still evaluate a player when FantasyPros fails.
    if include_actual:
        result["actual_performance"] = _actual_performance_context(player)

    if include_matchup:
        result["matchup"] = get_matchup_context(
            player,
            season=SEASON,
            week=_current_week(),
        )

    if include_shadow:
        result["ml_shadow"] = _shadow_context(player)

    if include_free:
        result["free_fallback"] = _free_fallback_context(player)

    return result


def _comparison_player(player, include_shadow=False):
    compact = compact_player(
        player,
        include_shadow=include_shadow,
        include_matchup=True,
        include_free=True,
    )

    wanted = {
        "name",
        "position",
        "team",
        "opponent",
        "espn_projection",
        "fantasypros_projection",
        "weekly_ecr",
        "weekly_score_advisory",
        "fantasypros_ros_rank",
        "ros_score_advisory",
        "leaguelogs_ros_rank",
        "leaguelogs_market_value",
        "recent_ppr",
        "carries_per_game",
        "targets_per_game",
        "injury_status",
        "can_play_this_week",
        "weekly_multiplier",
        "risk_level",
        "play_probability",
        "practice_summary",
        "weekly_data_complete",
        "weekly_missing_data",
        "ros_data_complete",
        "ros_missing_data",
        "actual_performance",
        "matchup",
        "ml_shadow",
        "free_fallback",
    }

    return {
        key: value
        for key, value in compact.items()
        if key in wanted
    }


def _fallback_available(player_context, source):
    return bool(
        (player_context.get("free_fallback") or {})
        .get(source, {})
        .get("available")
    )


def _actual_stats_available(player_context):
    return bool(
        (player_context.get("actual_performance") or {})
        .get("available")
    )


def _comparison_status(first_context, second_context, mode):
    if mode == "weekly":
        primary_complete = bool(first_context.get("weekly_data_complete")) and bool(
            second_context.get("weekly_data_complete")
        )
        if primary_complete:
            return "COMPLETE_PRIMARY_DATA"

        sleeper_support = _fallback_available(first_context, "sleeper") or _fallback_available(
            second_context, "sleeper"
        )
        fantasycalc_support = _fallback_available(first_context, "fantasycalc") and _fallback_available(
            second_context, "fantasycalc"
        )
        actual_stats_support = _actual_stats_available(first_context) and _actual_stats_available(
            second_context
        )
        if actual_stats_support or sleeper_support or fantasycalc_support:
            return "FALLBACK_SUPPORTED"
        return "INCOMPLETE_DATA"

    primary_complete = bool(first_context.get("ros_data_complete")) and bool(
        second_context.get("ros_data_complete")
    )
    if primary_complete:
        return "COMPLETE_PRIMARY_DATA"

    # A ROS recommendation can still be supported by factual player performance
    # plus independent market/health evidence even when FantasyPros ROS ECR is
    # missing. FantasyCalc remains market value, not ECR.
    actual_stats_support = _actual_stats_available(first_context) and _actual_stats_available(
        second_context
    )
    fantasycalc_support = _fallback_available(first_context, "fantasycalc") and _fallback_available(
        second_context, "fantasycalc"
    )
    if actual_stats_support or fantasycalc_support:
        return "FALLBACK_SUPPORTED"

    return "INCOMPLETE_DATA"


def _compact_baseline(result):
    if not isinstance(result, dict):
        return result

    wanted = {
        "recommended_player",
        "winner",
        "reason",
        "decision_strength",
        "weekly_score_difference",
        "ros_score_difference",
        "score_difference",
        "evidence",
    }

    compact = {
        key: value
        for key, value in result.items()
        if key in wanted and value is not None
    }

    return compact or result


# ==================================================
# Tools
# ==================================================

def tool_get_roster():
    roster = APP_CONTEXT.get("roster") or []
    league = _league()

    return {
        "league": league.settings.name if league is not None else None,
        "week": _current_week(),
        # Keep roster-wide responses smaller. Claude can call player_details for
        # shadow predictions on individual players if needed.
        "players": [
            compact_player(
                p,
                include_shadow=False,
                include_matchup=False,
                include_free=False,
                include_actual=False,
            )
            for p in roster
        ],
    }


def tool_get_player_details(player_name):
    player = find_player(player_name)

    if player is None:
        return {
            "found": False,
            "requested_name": player_name,
            "message": "Player could not be resolved in ESPN's player map.",
        }

    return {
        "found": True,
        "player": compact_player(player, include_shadow=True),
        "advisory_quantitative_snapshot": build_player_snapshot(player),
    }


def tool_compare_players_weekly(player_a, player_b):
    first = find_player(player_a)
    second = find_player(player_b)

    missing = []
    if first is None:
        missing.append(player_a)
    if second is None:
        missing.append(player_b)

    if missing:
        return {
            "success": False,
            "missing_players": missing,
        }

    first_context = _comparison_player(first, include_shadow=True)
    second_context = _comparison_player(second, include_shadow=True)
    status = _comparison_status(first_context, second_context, mode="weekly")

    advisory = None
    if status == "COMPLETE_PRIMARY_DATA":
        advisory = _compact_baseline(compare_players(first, second))

    return {
        "success": True,
        "comparison_type": "THIS_WEEK",
        "comparison_status": status,
        "player_a": first_context,
        "player_b": second_context,
        "advisory_score_comparison": advisory,
        "decision_instruction": (
            "Make the final start/sit judgment from the supplied evidence, including "
            "actual_performance from completed nflverse games. The deterministic score "
            "comparison is advisory only. If FantasyPros is missing, use actual role, "
            "volume, production, health, ESPN, market, matchup, and ML evidence rather "
            "than treating the player as unevaluable."
        ),
    }


def tool_compare_players_ros(player_a, player_b):
    first = find_player(player_a)
    second = find_player(player_b)

    missing = []
    if first is None:
        missing.append(player_a)
    if second is None:
        missing.append(player_b)

    if missing:
        return {
            "success": False,
            "missing_players": missing,
        }

    first_context = _comparison_player(first, include_shadow=False)
    second_context = _comparison_player(second, include_shadow=False)
    status = _comparison_status(first_context, second_context, mode="ros")

    advisory = None
    if status == "COMPLETE_PRIMARY_DATA":
        advisory = _compact_baseline(compare_players_ros(first, second))

    return {
        "success": True,
        "comparison_type": "REST_OF_SEASON",
        "comparison_status": status,
        "player_a": first_context,
        "player_b": second_context,
        "advisory_score_comparison": advisory,
        "decision_instruction": (
            "Make the final ROS/trade judgment from the evidence. When FantasyPros "
            "ROS ECR is missing, actual_performance provides factual production/role "
            "evidence and FantasyCalc provides market evidence; neither should be called "
            "ECR. The deterministic ROS score is advisory and is intentionally suppressed "
            "unless primary ROS inputs are complete for both players."
        ),
    }


def tool_get_optimal_lineup():
    optimized = APP_CONTEXT.get("optimized") or {}
    lineup = optimized.get("lineup") or {}

    return {
        "week": _current_week(),
        "lineup": {
            str(slot): (
                None
                if player is None
                else compact_player(player, include_shadow=False, include_matchup=False, include_free=False, include_actual=False)
            )
            for slot, player in lineup.items()
        },
        "projected_total": optimized.get("projected_total"),
        "weekly_lineup_value": optimized.get("lineup_score"),
    }


def tool_get_waiver_recommendations():
    transactions = APP_CONTEXT.get("waiver_transactions") or []
    candidates = APP_CONTEXT.get("waiver_candidates") or []

    actionable = {"STRONG_ADD", "ADD", "STASH", "STREAMER"}
    moves = []
    seen = set()

    for transaction in transactions:
        category = transaction.get("recommendation")
        if category not in actionable:
            continue

        add_player = transaction.get("add") or {}
        drop_player = transaction.get("drop") or {}

        key = add_player.get("espn_id") or normalize_name(add_player.get("name"))
        if key in seen:
            continue
        seen.add(key)

        moves.append(_non_none({
            "category": category,
            "add": compact_player(add_player, include_shadow=False, include_matchup=False, include_free=False, include_actual=False),
            "drop": compact_player(drop_player, include_shadow=False, include_matchup=False, include_free=False, include_actual=False) if drop_player else None,
            "weekly_lineup_delta": transaction.get("weekly_lineup_delta"),
            "projected_points_delta": transaction.get("projected_points_delta"),
            "ros_delta": transaction.get("ros_delta"),
            "market_value_delta": transaction.get("market_value_delta"),
            "roster_balance_delta": transaction.get("roster_balance_delta"),
            "depth_risk": transaction.get("depth_risk"),
            "reason": transaction.get("recommendation_reason"),
        }))

        if len(moves) >= 5:
            break

    if not moves:
        return {
            "recommendation": "NO_MOVE",
            "moves": [],
            "top_loaded_candidates": [
                compact_player(p, include_shadow=False, include_matchup=False, include_free=False, include_actual=False)
                for p in candidates[:8]
            ],
        }

    return {
        "recommendation": "ACTIONABLE_MOVES",
        "moves": moves,
    }


def tool_get_shadow_prediction(player_name):
    player = find_player(player_name)

    if player is None:
        return {
            "found": False,
            "requested_name": player_name,
        }

    return {
        "found": True,
        "player": player.get("name"),
        "espn_projection": player.get("espn_projected_points"),
        "fantasypros_projection": player.get("fp_ppr"),
        "weekly_ecr": player.get("ecr"),
        "availability": player.get("availability"),
        "matchup": get_matchup_context(
            player,
            season=SEASON,
            week=_current_week(),
        ),
        "ml_shadow": _shadow_context(player),
        "warning": "Shadow prediction is not yet used by weekly_score or optimizer.",
    }


def tool_get_system_status():
    league = _league()
    return {
        "league": league.settings.name if league is not None else None,
        "week": _current_week(),
        "loaded_players": len(get_all_known_players()),
        "external_player_cache": len(EXTERNAL_PLAYER_CACHE),
        "decision_architecture": {
            "hard_constraints": "authoritative",
            "provider_facts": "authoritative evidence",
            "weekly_score": "advisory",
            "ros_score": "advisory",
            "optimizer": "advisory baseline",
            "claude_role": "final contextual judgment",
        },
        "free_fallbacks": {
            "nflverse_actual_performance": "completed-game production/usage/trends; independent of FantasyPros",
            "fantasycalc": "redraft market value/rank; not ECR or projection",
            "sleeper": "player/injury/practice/depth-chart metadata",
        },
        "ml_model": {
            "mode": "SHADOW",
            "affects_live_weekly_score": False,
            "live_decision_weight": 0.0,
        },
    }


# ==================================================
# Claude schemas
# ==================================================

TOOLS = [
    {
        "name": "get_roster",
        "description": "Get Serena's current roster and current-week player data.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_player_details",
        "description": (
            "Get current data for any NFL fantasy player, including completed-game "
            "nflverse actual-performance stats. If the player is not already loaded, "
            "the app attempts an ESPN league-wide lookup and enriches that player."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "player_name": {"type": "string"},
            },
            "required": ["player_name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "compare_players_weekly",
        "description": (
            "Compare two players for this week's start/sit decision using actual "
            "completed-game nflverse performance, projections/rankings when available, "
            "health, matchup, free fallback evidence, shadow ML, and an advisory score."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "player_a": {"type": "string"},
                "player_b": {"type": "string"},
            },
            "required": ["player_a", "player_b"],
            "additionalProperties": False,
        },
    },
    {
        "name": "compare_players_ros",
        "description": (
            "Compare two players rest of season / for a trade using actual completed-game "
            "nflverse performance plus FantasyPros when available and FantasyCalc, "
            "Sleeper, and LeagueLogs fallback evidence. Deterministic ROS score is advisory."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "player_a": {"type": "string"},
                "player_b": {"type": "string"},
            },
            "required": ["player_a", "player_b"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_optimal_lineup",
        "description": "Get the project's current optimized starting lineup.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_waiver_recommendations",
        "description": "Get current waiver and add/drop recommendations.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_shadow_prediction",
        "description": (
            "Get our rolling ML model's shadow PPR forecast for a player and "
            "compare it with ESPN/FantasyPros. The shadow forecast is not live."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "player_name": {"type": "string"},
            },
            "required": ["player_name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_system_status",
        "description": "Get current week and ML live/shadow status.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
]



def select_tools_for_message(user_message):
    """Send Claude only the tool schemas likely to matter for this turn.

    Claude still chooses whether/which tool to call. This only reduces repeated
    schema tokens and cost on obvious question types.
    """

    text = normalize_name(user_message)

    def pick(*names):
        wanted = set(names)
        return [tool for tool in TOOLS if tool["name"] in wanted]

    if any(term in text for term in ("ml", "model", "predict", "prediction", "shadow")):
        return pick("get_shadow_prediction", "get_player_details")

    if any(term in text for term in ("actual stats", "actual data", "stats", "statistics", "fallback data", "fallback", "usage", "targets", "carries", "production")):
        return pick("get_player_details")

    if any(term in text for term in ("rest of season", "ros", "trade", "keeper", "long term", "longterm")):
        return pick("compare_players_ros", "get_player_details")

    if any(term in text for term in ("start", "sit", "flex", "bench", "this week")):
        return pick(
            "compare_players_weekly",
            "get_player_details",
            "get_optimal_lineup",
        )

    if any(term in text for term in ("waiver", "pickup", "pick up", "add", "drop", "free agent", "streamer")):
        return pick(
            "get_waiver_recommendations",
            "get_roster",
            "get_player_details",
        )

    if any(term in text for term in ("lineup", "starting lineup", "team")):
        return pick(
            "get_roster",
            "get_optimal_lineup",
            "get_player_details",
        )

    if any(term in text for term in ("system", "status", "live decision weight")):
        return pick("get_system_status", "get_shadow_prediction")

    return TOOLS


# ==================================================
# Dispatcher
# ==================================================

def run_tool(tool_name, tool_input):
    tool_input = tool_input if isinstance(tool_input, dict) else {}

    if tool_name == "get_roster":
        return tool_get_roster()
    if tool_name == "get_player_details":
        return tool_get_player_details(tool_input.get("player_name", ""))
    if tool_name == "compare_players_weekly":
        return tool_compare_players_weekly(
            tool_input.get("player_a", ""),
            tool_input.get("player_b", ""),
        )
    if tool_name == "compare_players_ros":
        return tool_compare_players_ros(
            tool_input.get("player_a", ""),
            tool_input.get("player_b", ""),
        )
    if tool_name == "get_optimal_lineup":
        return tool_get_optimal_lineup()
    if tool_name == "get_waiver_recommendations":
        return tool_get_waiver_recommendations()
    if tool_name == "get_shadow_prediction":
        return tool_get_shadow_prediction(tool_input.get("player_name", ""))
    if tool_name == "get_system_status":
        return tool_get_system_status()

    return {"error": f"Unknown tool: {tool_name}"}


# ==================================================
# Claude tool loop
# ==================================================

def create_claude_message(messages, tools):
    return client.messages.create(
        model=MODEL,
        max_tokens=1400,
        system=SYSTEM_PROMPT,
        messages=messages,
        tools=tools,
        tool_choice={"type": "auto"},
        thinking={"type": "adaptive"},
        output_config={"effort": CLAUDE_EFFORT},
    )


def serialize_content_blocks(blocks):
    serialized = []
    for block in blocks:
        if hasattr(block, "model_dump"):
            serialized.append(block.model_dump())
        else:
            serialized.append(block)
    return serialized


def extract_text(response):
    return "".join(
        block.text
        for block in response.content
        if block.type == "text"
    ).strip()


def ask_claude(user_message):
    working_messages = CHAT_HISTORY[-MAX_HISTORY_MESSAGES:] + [
        {"role": "user", "content": user_message}
    ]

    selected_tools = select_tools_for_message(user_message)

    total_input_tokens = 0
    total_output_tokens = 0
    api_calls = 0
    final_response = None

    for _ in range(MAX_TOOL_ROUNDS):
        response = create_claude_message(working_messages, selected_tools)
        final_response = response
        api_calls += 1

        usage = getattr(response, "usage", None)
        if usage is not None:
            total_input_tokens += getattr(usage, "input_tokens", 0) or 0
            total_output_tokens += getattr(usage, "output_tokens", 0) or 0

        tool_blocks = [
            block
            for block in response.content
            if block.type == "tool_use"
        ]

        if response.stop_reason != "tool_use" or not tool_blocks:
            break

        working_messages.append({
            "role": "assistant",
            "content": serialize_content_blocks(response.content),
        })

        tool_results = []

        for block in tool_blocks:
            try:
                result = run_tool(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json_text(result),
                })
            except Exception as exc:
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json_text({"error": str(exc)}),
                    "is_error": True,
                })

        working_messages.append({
            "role": "user",
            "content": tool_results,
        })

    if final_response is None:
        raise RuntimeError("Claude returned no response.")

    if final_response.stop_reason == "tool_use":
        raise RuntimeError("Claude exceeded the maximum number of tool rounds.")

    if final_response.stop_reason == "max_tokens":
        raise RuntimeError("Claude reached max_tokens before finishing.")

    response_text = extract_text(final_response)
    if not response_text:
        response_text = "Claude returned no text response."

    print("\nCoach: " + response_text)
    print(
        "\n"
        f"[Claude usage: {total_input_tokens:,} input / "
        f"{total_output_tokens:,} output tokens across {api_calls} API call"
        f"{'' if api_calls == 1 else 's'}]"
    )

    CHAT_HISTORY.append({"role": "user", "content": user_message})
    CHAT_HISTORY.append({"role": "assistant", "content": response_text})

    return response_text


# ==================================================
# App context
# ==================================================

def load_app_context():
    global APP_CONTEXT

    print("Loading fantasy data...")

    captured_output = io.StringIO()
    with redirect_stdout(captured_output):
        APP_CONTEXT = build_fantasy_context()

    if APP_CONTEXT is None:
        raise RuntimeError(
            "main.py returned None. Make sure main() returns the fantasy context."
        )

    league = APP_CONTEXT.get("league")
    league_name = (
        league.settings.name
        if league is not None
        else "Fantasy League"
    )

    print(
        f"Loaded {league_name}, "
        f"Week {APP_CONTEXT.get('current_week')}."
    )


# ==================================================
# CLI
# ==================================================

def main():
    load_app_context()

    print(f"\nFantasy Coach ready ({MODEL}, effort={CLAUDE_EFFORT}).")
    print(
        "Claude can use roster, arbitrary-player lookup, player comparisons, "
        "lineup, waivers, and shadow-ML tools."
    )
    print("Type 'refresh' to reload fantasy data.")
    print("Type 'quit' to exit.")

    while True:
        try:
            user_message = input("\nYou: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye.")
            break

        if not user_message:
            continue

        if user_message.lower() in {"quit", "exit", "q"}:
            print("Goodbye.")
            break

        if user_message.lower() == "refresh":
            try:
                EXTERNAL_PLAYER_CACHE.clear()
                CHAT_HISTORY.clear()
                load_app_context()
                print("Fantasy data refreshed. Conversation history cleared.")
            except Exception as exc:
                print(f"Refresh failed: {exc}")
            continue

        try:
            ask_claude(user_message)
        except Exception as exc:
            print("\nChatbot error:")
            print(f"  {exc}")


if __name__ == "__main__":
    main()
