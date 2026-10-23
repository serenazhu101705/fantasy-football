import io
import json
import os
import re

from contextlib import redirect_stdout
from difflib import get_close_matches, SequenceMatcher

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
from league_settings import get_league_scoring_context, reception_points_or_default
from recommendation_journal import choice_instruction, extract_choice, create_record, save_record
from league_intelligence import (matchup_strategy, standings as league_standings,
    faab as league_faab, schedule as league_schedule, team_roster as league_team_roster)


# ==================================================
# Environment / Claude
# ==================================================

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
if not ANTHROPIC_API_KEY:
    raise RuntimeError("ANTHROPIC_API_KEY is not set in .env")

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
CLAUDE_EFFORT = os.getenv("CLAUDE_EFFORT", "medium").lower()

# Adaptive thinking uses the same output-token budget as the visible answer.
# 1400 was too small for richer waiver comparisons, so use a larger dynamic
# budget while keeping ordinary questions cheaper. These can be overridden in
# .env without changing code.
CLAUDE_MAX_TOKENS = int(os.getenv("CLAUDE_MAX_TOKENS", "2400"))
CLAUDE_SIMPLE_MAX_TOKENS = int(os.getenv("CLAUDE_SIMPLE_MAX_TOKENS", "1600"))
CLAUDE_WAIVER_MAX_TOKENS = int(os.getenv("CLAUDE_WAIVER_MAX_TOKENS", "2800"))
CLAUDE_RETRY_MAX_TOKENS = int(os.getenv("CLAUDE_RETRY_MAX_TOKENS", "4200"))
CLAUDE_SIMPLE_EFFORT = os.getenv("CLAUDE_SIMPLE_EFFORT", "low").lower()
CLAUDE_DECISION_EFFORT = os.getenv("CLAUDE_DECISION_EFFORT", CLAUDE_EFFORT).lower()

for _effort_name, _effort_value in {
    "CLAUDE_EFFORT": CLAUDE_EFFORT,
    "CLAUDE_SIMPLE_EFFORT": CLAUDE_SIMPLE_EFFORT,
    "CLAUDE_DECISION_EFFORT": CLAUDE_DECISION_EFFORT,
}.items():
    if _effort_value not in {"low", "medium", "high", "xhigh", "max"}:
        raise RuntimeError(
            f"{_effort_name} must be one of: low, medium, high, xhigh, max"
        )

client = Anthropic(api_key=ANTHROPIC_API_KEY)


# ==================================================
# State
# ==================================================

APP_CONTEXT = None
CHAT_HISTORY = []
EXTERNAL_PLAYER_CACHE = {}
DECISION_CONTEXT = {
    "players": [],
    "horizon": None,
    "acquisition": False,
    "position": None,
    "open_spot": False,
    "requested_add_count": 1,
    "waiver_drop_target": None,
}

MAX_HISTORY_MESSAGES = 2
MAX_TOOL_ROUNDS = 4
SEASON = 2026
APP_VERSION = "v7.15"


def _debug_enabled():
    return os.getenv("FANTASY_DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}


def _trace(section, **fields):
    """Small opt-in trace. Deliberately exclude keys, cookies and raw prompts."""
    if not _debug_enabled():
        return
    parts = [f"{key}={json_text(value)}" for key, value in fields.items()]
    print(f"[{section}] " + " ".join(parts))


def _evidence_sources(prefetched):
    """Summarize sources represented in the compact evidence, without claiming freshness."""
    available = set()

    def walk(value):
        if isinstance(value, dict):
            if value.get("source") == "nflverse" and value.get("available"):
                available.add("nflverse")
            for key, inner in value.items():
                if inner is not None and key in {"espn_projection", "espn_projected_points"}:
                    available.add("ESPN projections")
                if inner is not None and key in {"fantasypros_projection", "fantasypros_ros_rank", "weekly_ecr"}:
                    available.add("FantasyPros")
                if key in {"fantasycalc", "sleeper"} and isinstance(inner, dict) and inner.get("available"):
                    available.add("FantasyCalc" if key == "fantasycalc" else "Sleeper")
                if key.startswith("leaguelogs_") and inner is not None:
                    available.add("LeagueLogs")
                walk(inner)
        elif isinstance(value, list):
            for entry in value:
                walk(entry)

    walk(prefetched or {})
    return sorted(available)


def _has_ros_cue(user_message):
    """Match full words; 'ros' inside 'roster' must not imply ROS intent."""
    text = normalize_name(user_message)
    return bool(
        re.search(r"\b(?:ros|rest of (?:the )?season|long term|longterm|keeper|trade)\b", text)
        or ("season" in text.split() and ({"rest", "reset"} & set(text.split())))
    )


def _asks_league_scoring(user_message):
    text = normalize_name(user_message)
    return bool(
        re.search(r"\b(scoring|ppr|half ppr|points per reception)\b", text)
        and re.search(r"\b(league|format|settings|we|our|my)\b", text)
    )


def _is_roster_review(user_message):
    text = normalize_name(user_message)
    return bool(
        re.search(r"\b(?:roster|my team|given my team)\b", text)
        and re.search(r"\b(?:best move|improve|upgrade|weakness|analy[sz]e|review|recommend)\b", text)
    )


# ==================================================
# Prompt
# ==================================================

SYSTEM_PROMPT = """
You are Serena's fantasy-football decision assistant. Use app evidence for every
current player/league claim; never invent current facts.

Core rules:
- OUT/IR/bye/roster status/lineup legality and supplied provider facts are binding.
- weekly_score, ros_score, optimizer output, and deterministic winners are ADVISORY.
  The weekly optimizer primarily uses ESPN + FantasyPros projected points (using
  available sources), with weekly_score only a small tie-breaker. For starting
  lineup advice, use that same projection baseline. If recommending a different
  lineup, explicitly identify the changed player, why, and the injury/role/news
  evidence strong enough to override the baseline; never silently contradict it.
  Make the final judgment from projections, rankings, actual role/volume/production,
  health/practice, matchup, market signals, and shadow ML as relevant.
- nflverse actual_performance is factual completed-game evidence. Explained BYE,
  inactive/injury, reserve, suspension, or zero-stat weeks are not missing; only
  DATA_GAP is unresolved. Averages are per confirmed game played.
- FantasyPros is useful but optional. If it is unavailable, use ESPN + nflverse +
  Sleeper + FantasyCalc + LeagueLogs + matchup + shadow ML and lower confidence.
- LEAGUE CONTEXT is authoritative. Never ask the user whether the league is PPR,
  half-PPR, standard, team count, or other settings already present there. Use the
  ESPN-derived scoring format automatically.
- FantasyCalc is market value, not ECR/projection. Sleeper is status/depth-chart
  metadata, not ECR/projection. Shadow ML is advisory with zero live weight.
- If current-status providers conflict, state the conflict; do not silently choose.
- For waivers, NO_THRESHOLD_MOVE never means no players are available. Evaluate the
  returned candidate_pool directly, especially with an empty roster spot.
- For standalone waiver-move questions, do NOT reuse earlier player matchups.
  Only suggest adds drawn from the supplied actual waiver candidate_pool;
  never suggest adding an existing player in roster_players. If no move is
  justified, say HOLD rather than inventing a transaction or FAAB price.
- When PREFETCHED APP EVIDENCE contains roster_context, you DO have current roster
  context. Never say you cannot see the roster; use its position counts, unavailable
  players, and focus-position players to judge roster need.
- Follow-up context may be resolved deterministically by the app. If prefetched evidence
  says the user is continuing a comparison between named players or changing the horizon
  to ROS/long-term, continue that same decision without asking the user to repeat names.
- When evidence intent=waiver_swap, the named drop_target is owned; compare HOLD against only actual candidate_pool adds. Another mentioned player may be BACKGROUND, not an available add.
- For an open bench spot between two named waiver players, recommend the better add; do
  not switch back to the general waiver pool unless the user asks for other candidates.
- Treat a surname from the immediately established comparison as that same player; do not
  re-ask which player the user means unless the app evidence itself marks the name ambiguous.
- For WEEKLY MATCHUP STRATEGY, use ESPN's actual matchup, both starting lineups and
  projections. A positive projected_margin means favored BY POINTS, not a calibrated
  win probability. Extra points needed assumes the opponent meets their projection.
  Do not invent odds or guarantee that a lineup swap is legal after kickoff.
- For league FAAB, other rosters, standings, and schedule, use current ESPN
  context. Unprocessed/pending waiver claims are not reliably available here;
  NEVER say there are zero claims merely because they cannot be retrieved.
- Do not convert an injury/workload multiplier directly into projected points.

Use PREFETCHED APP EVIDENCE when present; do not call a tool again for facts already
there unless the evidence is insufficient. Lead with the recommendation, give only
the most decision-relevant evidence, mention material uncertainty, and stay concise.
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


def _league_settings_context():
    context = APP_CONTEXT.get("league_settings") if APP_CONTEXT else None
    if context:
        return context
    league = _league()
    if league is not None:
        try:
            return get_league_scoring_context(league)
        except Exception:
            pass
    return {
        "source": "fallback",
        "scoring_label": "UNKNOWN",
        "reception_points": 1.0,
        "fantasypros_scoring": "PPR",
        "fantasypros_scoring_approximate": True,
        "team_count": 12,
    }


def _league_team_count():
    value = (_league_settings_context() or {}).get("team_count")
    try:
        return int(value) if value else 12
    except (TypeError, ValueError):
        return 12


def _league_reception_points():
    return reception_points_or_default(_league_settings_context(), default=1.0)


def _league_fantasypros_scoring():
    return (_league_settings_context() or {}).get("fantasypros_scoring") or "PPR"


def _compact_league_context():
    ctx = _league_settings_context() or {}
    return _non_none({
        "scoring": ctx.get("scoring_label"),
        "reception_points": ctx.get("reception_points"),
        "fantasypros_scoring": ctx.get("fantasypros_scoring"),
        "fantasypros_scoring_approximate": ctx.get("fantasypros_scoring_approximate"),
        "team_count": ctx.get("team_count"),
        "faab": ctx.get("faab"),
        "acquisition_budget": ctx.get("acquisition_budget"),
    })


def _free_fallback_context(player):
    """Free independent evidence used when FantasyPros is missing/limited."""
    try:
        return get_free_fallback_signals(
            player,
            num_teams=_league_team_count(),
            num_qbs=1,
            ppr=_league_reception_points(),
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
            reception_points=_league_reception_points(),
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
    """Resolve a player from the already-loaded roster/waiver pool.

    This resolver is deliberately more typo-tolerant than the league-wide ESPN
    resolver because the candidate universe is small and fantasy-relevant.  It
    prefers exact full names, then exact surnames, then a conservative fuzzy
    surname/full-name match.  This lets common inputs such as ``barnard`` resolve
    to Germie Bernard without spending Claude calls guessing unrelated NFL names.
    """
    players = get_all_known_players()
    requested = normalize_name(requested_name)

    if not requested:
        return None

    # 1) Exact normalized full name.
    exact = [
        player for player in players
        if normalize_name(player.get("name")) == requested
    ]
    if len(exact) == 1:
        return exact[0]

    # 2) Exact surname.  Only accept it when unique in the loaded fantasy pool.
    requested_parts = requested.split()
    if len(requested_parts) == 1 and len(requested) >= 4:
        surname_matches = []
        for player in players:
            parts = normalize_name(player.get("name")).split()
            if parts and parts[-1] == requested:
                surname_matches.append(player)
        if len(surname_matches) == 1:
            return surname_matches[0]

    # 3) Safe partial full-name matching.
    partial = []
    for player in players:
        player_name = normalize_name(player.get("name"))
        if requested in player_name or player_name in requested:
            partial.append(player)
    if len(partial) == 1:
        return partial[0]

    # 4) A one-word typo should be compared with loaded surnames, not with every
    # NFL full name.  This is what turns "barnard" -> "Bernard" safely.
    if len(requested_parts) == 1 and len(requested) >= 5:
        surname_lookup = {}
        for player in players:
            parts = normalize_name(player.get("name")).split()
            if not parts:
                continue
            surname_lookup.setdefault(parts[-1], []).append(player)

        close_surnames = get_close_matches(
            requested,
            list(surname_lookup.keys()),
            n=2,
            cutoff=0.84,
        )
        if close_surnames:
            best = close_surnames[0]
            matches = surname_lookup.get(best) or []
            # Do not guess if that surname itself maps to multiple loaded players.
            if len(matches) == 1:
                # If there is a nearly identical second surname, leave it ambiguous.
                if len(close_surnames) == 1:
                    return matches[0]
                best_score = SequenceMatcher(None, requested, close_surnames[0]).ratio()
                second_score = SequenceMatcher(None, requested, close_surnames[1]).ratio()
                if best_score - second_score >= 0.08:
                    return matches[0]

    # 5) Fuzzy full-name matching is useful for small typos such as "Tree Tucker".
    # Keep the threshold high so a vague one-word query cannot jump to a random name.
    if len(requested_parts) >= 2:
        lookup = {
            normalize_name(player.get("name")): player
            for player in players
        }
        fuzzy = get_close_matches(
            requested,
            list(lookup.keys()),
            n=2,
            cutoff=0.90,
        )
        if fuzzy:
            if len(fuzzy) == 1:
                return lookup[fuzzy[0]]
            best_score = SequenceMatcher(None, requested, fuzzy[0]).ratio()
            second_score = SequenceMatcher(None, requested, fuzzy[1]).ratio()
            if best_score - second_score >= 0.06:
                return lookup[fuzzy[0]]

    return None


def _resolve_espn_name(requested_name):
    """Resolve a natural player name against ESPN's league-wide player map.

    League-wide fuzzy guessing is intentionally conservative.  A single vague or
    misspelled surname such as "Barnard" must never silently become an unrelated
    player.  Loaded roster/waiver players are handled first by _find_loaded_player().
    """
    league = _league()
    player_map = getattr(league, "player_map", {}) or {}

    if requested_name in player_map:
        return requested_name

    requested = normalize_name(requested_name)
    if not requested:
        return None

    normalized_lookup = {
        normalize_name(name): name
        for name in player_map.keys()
    }

    # Exact full name.
    if requested in normalized_lookup:
        return normalized_lookup[requested]

    requested_parts = requested.split()

    # Exact surname is okay only when globally unique.
    if len(requested_parts) == 1 and len(requested) >= 4:
        surname_matches = []
        for normalized, original in normalized_lookup.items():
            parts = normalized.split()
            if parts and parts[-1] == requested:
                surname_matches.append(original)
        if len(surname_matches) == 1:
            return surname_matches[0]
        return None

    # For a full name only, allow a very close typo with a clear margin over the
    # runner-up.  This handles "Germie Barnard" without random global guesses.
    if len(requested_parts) >= 2:
        fuzzy = get_close_matches(
            requested,
            list(normalized_lookup.keys()),
            n=2,
            cutoff=0.90,
        )
        if fuzzy:
            if len(fuzzy) == 1:
                return normalized_lookup[fuzzy[0]]
            best_score = SequenceMatcher(None, requested, fuzzy[0]).ratio()
            second_score = SequenceMatcher(None, requested, fuzzy[1]).ratio()
            if best_score - second_score >= 0.06:
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
                scoring=_league_fantasypros_scoring(),
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
                scoring=_league_fantasypros_scoring(),
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
            scoring=_league_fantasypros_scoring(),
            reception_points=_league_reception_points(),
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
    if abs(_league_reception_points() - 1.0) > 1e-9:
        return {
            "available": False,
            "reason": (
                "Current shadow model is trained on full-PPR targets; disabled for "
                f"{(_league_settings_context() or {}).get('scoring_label')} so it is not "
                "compared against differently scored league projections."
            ),
        }

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
    fantasypros = player.get("fp_points") if player.get("fp_points") is not None else player.get("fp_ppr")

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
        "fantasypros_projection": player.get("fp_points") if player.get("fp_points") is not None else player.get("fp_ppr"),
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
        "recent_fantasy_points": usage.get("fantasy_points_per_game") if usage.get("fantasy_points_per_game") is not None else usage.get("ppr_points_per_game"),
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


def _compact_actual_for_decision(player):
    """Actual stats without full week-by-week logs for routine decisions."""
    actual = _actual_performance_context(player)
    if not actual.get("available"):
        return actual

    coverage = actual.get("week_coverage") or {}
    return _non_none({
        "available": True,
        "source": "nflverse",
        "through_week": actual.get("through_week"),
        "games_played": actual.get("games_played"),
        "season": actual.get("season"),
        "last_3": actual.get("last_3_played_games"),
        "last_3_vs_season": actual.get("last_3_vs_season"),
        "coverage_complete": coverage.get("coverage_complete"),
        "unresolved_weeks": coverage.get("unresolved_weeks") or None,
    })


def _compact_free_for_decision(player):
    """Keep only decision-relevant free fallback fields."""
    fallback = _free_fallback_context(player)
    fantasycalc = fallback.get("fantasycalc") or {}
    sleeper = fallback.get("sleeper") or {}

    fc = _non_none({
        "available": fantasycalc.get("available"),
        "redraft_value": fantasycalc.get("redraft_value"),
        "overall_rank": fantasycalc.get("overall_rank"),
        "position_rank": fantasycalc.get("position_rank"),
        "trend_30_day": fantasycalc.get("trend_30_day"),
    })
    sl = _non_none({
        "available": sleeper.get("available"),
        "status": sleeper.get("status"),
        "injury_status": sleeper.get("injury_status"),
        "injury_body_part": sleeper.get("injury_body_part"),
        "practice_participation": sleeper.get("practice_participation"),
        "depth_chart_order": sleeper.get("depth_chart_order"),
        "depth_chart_position": sleeper.get("depth_chart_position"),
    })
    return {"fantasycalc": fc, "sleeper": sl}


def _compact_matchup_for_decision(player):
    matchup = get_matchup_context(
        player,
        season=SEASON,
        week=_current_week(),
    )
    if not matchup.get("available"):
        return _non_none({
            "available": False,
            "opponent": matchup.get("opponent"),
            "reason": matchup.get("reason"),
        })
    return _non_none({
        "available": True,
        "opponent": matchup.get("opponent"),
        "label": matchup.get("label"),
        "relative_to_league_pct": matchup.get("relative_to_league_pct"),
        "easiest_matchup_rank": matchup.get("easiest_matchup_rank"),
        "defenses_ranked": matchup.get("defenses_ranked"),
        "sample_games": matchup.get("sample_games"),
    })


def _comparison_player(player, include_shadow=False, include_matchup=True):
    # Start from cheap/core fields. Rich evidence is added back in compact form.
    compact = compact_player(
        player,
        include_shadow=False,
        include_matchup=False,
        include_free=False,
        include_actual=False,
    )

    wanted = {
        "name", "position", "team", "opponent",
        "espn_projection", "fantasypros_projection", "weekly_ecr",
        "weekly_score_advisory", "fantasypros_ros_rank", "ros_score_advisory",
        "leaguelogs_ros_rank", "leaguelogs_market_value",
        "recent_fantasy_points", "carries_per_game", "targets_per_game",
        "injury_status", "can_play_this_week", "weekly_multiplier",
        "risk_level", "play_probability", "practice_summary",
        "weekly_data_complete", "weekly_missing_data",
        "ros_data_complete", "ros_missing_data",
    }
    result = {key: value for key, value in compact.items() if key in wanted}

    result["actual_performance"] = _compact_actual_for_decision(player)
    result["free_fallback"] = _compact_free_for_decision(player)
    if include_matchup:
        result["matchup"] = _compact_matchup_for_decision(player)
    if include_shadow:
        result["ml_shadow"] = _shadow_context(player)
    return result

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
        "league_settings": _compact_league_context(),
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

    first_context = _comparison_player(first, include_shadow=False, include_matchup=False)
    second_context = _comparison_player(second, include_shadow=False, include_matchup=False)
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


def _lineup_projected_points(player):
    from lineup_optimizer import projected_points
    return projected_points(player)


def _lineup_projection_sources(player):
    from lineup_optimizer import projection_sources
    return projection_sources(player)


def tool_get_optimal_lineup():
    optimized = APP_CONTEXT.get("optimized") or {}
    lineup = optimized.get("lineup") or {}

    return {
        "week": _current_week(),
        "lineup": {
            str(slot): (
                None
                if player is None
                else {
                    **compact_player(player, include_shadow=False, include_matchup=False, include_free=False, include_actual=False),
                    "lineup_projection": (
                        round(_lineup_projected_points(player), 2)
                        if _lineup_projection_sources(player) else None
                    ),
                    "lineup_projection_sources": list(_lineup_projection_sources(player)),
                }
            )
            for slot, player in lineup.items()
        },
        "projected_total": optimized.get("projected_total"),
        "projection_complete": optimized.get("projection_complete"),
        "projection_coverage": optimized.get("projection_coverage"),
        "selection_method": optimized.get("selection_method"),
        "weekly_lineup_value": optimized.get("lineup_score"),
    }


def _normalize_waiver_position(position):
    if position is None:
        return None

    value = str(position).strip().upper()
    aliases = {
        "": None,
        "ALL": None,
        "ANY": None,
        "FLEX": None,
        "DST": "D/ST",
        "DEF": "D/ST",
        "DEFENSE": "D/ST",
    }
    return aliases.get(value, value)


def _waiver_candidate_sort_key(player):
    def number(key):
        try:
            value = player.get(key)
            return float(value) if value is not None else -1.0
        except (TypeError, ValueError):
            return -1.0

    # The prescreen score is specifically designed to rank the already-loaded
    # waiver shortlist. The remaining fields are deterministic tie-breakers.
    return (
        number("waiver_prescreen_score"),
        number("weekly_score"),
        number("ros_score"),
        number("fp_points") if player.get("fp_points") is not None else number("fp_ppr"),
    )


def _compact_waiver_actual(player):
    return _compact_actual_for_decision(player)


def _compact_waiver_candidate(player, include_detail=True):
    core = compact_player(
        player,
        include_shadow=False,
        include_matchup=False,
        include_free=False,
        include_actual=False,
    )
    wanted = {
        "name", "position", "team", "opponent",
        "espn_projection", "fantasypros_projection", "weekly_ecr",
        "weekly_score_advisory", "fantasypros_ros_rank", "ros_score_advisory",
        "leaguelogs_ros_rank", "leaguelogs_market_value",
        "recent_fantasy_points", "carries_per_game", "targets_per_game",
        "injury_status", "can_play_this_week", "risk_level",
        "play_probability", "practice_summary", "on_bye_week",
        "weekly_data_complete", "ros_data_complete",
    }
    payload = {k: v for k, v in core.items() if k in wanted}
    payload["waiver_prescreen_score"] = player.get("waiver_prescreen_score")

    if include_detail:
        payload["actual_performance"] = _compact_waiver_actual(player)
        payload["matchup"] = _compact_matchup_for_decision(player)
        payload["free_fallback"] = _compact_free_for_decision(player)

    return payload

def tool_get_waiver_recommendations(position=None, limit=5):
    """Return actionable transactions AND the actual loaded waiver candidates.

    The old version returned only candidates[:8] when no transaction cleared the
    add/drop threshold. Because waiver_candidates is grouped QB -> RB -> WR -> TE,
    that hid every WR and TE from Claude. This function always exposes a
    position-filterable candidate pool, independent of the transaction threshold.
    """

    transactions = APP_CONTEXT.get("waiver_transactions") or []
    # ESPN's loaded roster is the authoritative ownership source. Even if a
    # stale precomputed waiver pool mistakenly includes a player on this team,
    # never surface that player as an add (or count them as a free agent).
    roster = APP_CONTEXT.get("roster") or []
    owned_names = {normalize_name(p.get("name")) for p in roster}
    owned_ids = {str(p.get("espn_id")) for p in roster if p.get("espn_id") is not None}
    candidates = [
        p for p in (APP_CONTEXT.get("waiver_candidates") or [])
        if p.get("rostered") is not True
        and normalize_name(p.get("name")) not in owned_names
        and (p.get("espn_id") is None or str(p.get("espn_id")) not in owned_ids)
    ]
    eligible_names = {normalize_name(p.get("name")) for p in candidates}

    requested_position = _normalize_waiver_position(position)
    allowed_positions = {"QB", "RB", "WR", "TE", "K", "D/ST"}
    if requested_position is not None and requested_position not in allowed_positions:
        return {
            "success": False,
            "error": f"Unsupported position: {position}",
            "supported_positions": sorted(allowed_positions),
        }

    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(limit, 12))

    # Count what is actually loaded before filtering. This prevents Claude from
    # interpreting a truncated/general list as evidence that a position is empty.
    position_counts = {}
    for player in candidates:
        pos = player.get("position") or "UNKNOWN"
        position_counts[pos] = position_counts.get(pos, 0) + 1

    filtered_candidates = [
        player
        for player in candidates
        if requested_position is None
        or player.get("position") == requested_position
    ]
    filtered_candidates = sorted(
        filtered_candidates,
        key=_waiver_candidate_sort_key,
        reverse=True,
    )

    # For a requested position, give Claude rich evidence for the actual top
    # candidates. For a general waiver query, return a BALANCED pool instead of
    # taking a global slice that could again hide whole positions.
    include_detail = requested_position is not None

    if requested_position is not None:
        selected_candidates = filtered_candidates[:limit]
    else:
        per_position_limit = min(limit, 3)
        selected_candidates = []
        for pos in ("QB", "RB", "WR", "TE"):
            position_group = sorted(
                [p for p in candidates if p.get("position") == pos],
                key=_waiver_candidate_sort_key,
                reverse=True,
            )
            selected_candidates.extend(position_group[:per_position_limit])

    candidate_pool = [
        _compact_waiver_candidate(player, include_detail=include_detail)
        for player in selected_candidates
    ]

    actionable = {"STRONG_ADD", "ADD", "STASH", "STREAMER"}
    moves = []
    seen = set()

    for transaction in transactions:
        category = transaction.get("recommendation")
        if category not in actionable:
            continue

        add_player = transaction.get("add") or {}
        # A stale transaction must not resurrect an owned or unavailable add.
        if normalize_name(add_player.get("name")) not in eligible_names:
            continue
        if requested_position is not None and add_player.get("position") != requested_position:
            continue

        drop_player = transaction.get("drop") or {}
        key = add_player.get("espn_id") or normalize_name(add_player.get("name"))
        if key in seen:
            continue
        seen.add(key)

        moves.append(_non_none({
            "category": category,
            "add": compact_player(
                add_player,
                include_shadow=False,
                include_matchup=False,
                include_free=False,
                include_actual=False,
            ),
            "drop": (
                compact_player(
                    drop_player,
                    include_shadow=False,
                    include_matchup=False,
                    include_free=False,
                    include_actual=False,
                )
                if drop_player else None
            ),
            "weekly_lineup_delta": transaction.get("weekly_lineup_delta"),
            "projected_points_delta": transaction.get("projected_points_delta"),
            "ros_delta": transaction.get("ros_delta"),
            "market_value_delta": transaction.get("market_value_delta"),
            "roster_balance_delta": transaction.get("roster_balance_delta"),
            "depth_risk": transaction.get("depth_risk"),
            "reason": transaction.get("recommendation_reason"),
        }))

        if len(moves) >= limit:
            break

    return {
        "success": True,
        "requested_position": requested_position or "ALL",
        "candidate_source": (
            "Current ESPN free-agent pool shortlisted by the app; candidates are "
            "loaded independently of whether an add/drop transaction clears the "
            "deterministic threshold."
        ),
        "loaded_position_counts": position_counts,
        "matching_candidates_loaded": len(filtered_candidates),
        "candidate_pool_returned": len(candidate_pool),
        "candidate_pool_scope": (
            "Top requested-position candidates"
            if requested_position is not None
            else "Balanced top candidates across QB/RB/WR/TE"
        ),
        "candidate_pool": candidate_pool,
        "transaction_model_result": (
            "ACTIONABLE_MOVES" if moves else "NO_THRESHOLD_MOVE"
        ),
        "moves": moves,
        "interpretation": (
            "NO_THRESHOLD_MOVE does NOT mean there are no available players. "
            "When the user has an empty bench spot, evaluate candidate_pool directly "
            "because no drop is required."
        ),
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
        "fantasypros_projection": player.get("fp_points") if player.get("fp_points") is not None else player.get("fp_ppr"),
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
        "league_settings": _compact_league_context(),
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
        "description": (
            "Inspect the current ESPN waiver/free-agent shortlist. Can filter by "
            "position (for example WR) and returns the actual candidate pool even "
            "when no deterministic add/drop transaction clears the threshold. Use "
            "the position filter whenever the user asks for a specific position."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "position": {
                    "type": "string",
                    "enum": ["QB", "RB", "WR", "TE", "K", "D/ST", "ALL"],
                    "description": "Optional position filter. Use ALL if no position is requested.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 12,
                    "description": "Maximum candidates/moves to return; default 5.",
                },
            },
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
    """Supply only relevant tools; use full-word checks to avoid accidental routes."""
    text = normalize_name(user_message)

    def pick(*names):
        wanted = set(names)
        return [tool for tool in TOOLS if tool["name"] in wanted]

    if _is_matchup_strategy_query(user_message) or _is_league_data_query(user_message):
        return []
    if _asks_league_scoring(user_message):
        return pick("get_system_status")
    if _is_roster_review(user_message):
        return pick("get_roster", "get_waiver_recommendations", "get_optimal_lineup")
    if _is_waiver_replacement_request(user_message):
        return pick("get_roster", "get_waiver_recommendations", "get_player_details")
    if _looks_like_waiver_acquisition(user_message) or _mentions_open_spot(user_message):
        return pick("get_waiver_recommendations", "get_roster", "get_player_details")
    if any(term in text for term in ("ml", "model", "predict", "prediction", "shadow")):
        return pick("get_shadow_prediction", "get_player_details")
    if any(term in text for term in ("actual stats", "actual data", "stats", "statistics", "fallback data", "fallback", "usage", "targets", "carries", "production")):
        return pick("get_player_details")
    if _has_ros_cue(user_message):
        return pick("compare_players_ros", "get_player_details")
    if "lineup" in text or "optimize my team" in text:
        return pick("get_roster", "get_optimal_lineup", "get_player_details")
    if re.search(r"\b(start|sit|flex|bench|this week)\b", text):
        return pick("compare_players_weekly", "get_player_details", "get_optimal_lineup")
    if "team" in text:
        return pick("get_roster", "get_optimal_lineup", "get_player_details")
    if any(term in text for term in ("system", "status", "live decision weight")):
        return pick("get_system_status", "get_shadow_prediction")
    return TOOLS



# ==================================================
# Cheap intent prefetch
# ==================================================

def _looks_like_waiver_acquisition(user_message):
    """Recognize waiver/add intent even when the user does not say "waiver".

    Examples we want to catch cheaply:
      - "I want to take 2 WRs"
      - "best move for my empty roster spot"
      - "who should I grab at receiver"
    """
    text = normalize_name(user_message)

    direct_terms = (
        "waiver", "pickup", "pick up", "free agent", "streamer",
        "add ", "drop ", "claim ", "grab ",
    )
    if any(term in text for term in direct_terms):
        return True

    position_terms = (
        " qb", " qbs", "quarterback",
        " rb", " rbs", "running back",
        " wr", " wrs", "wide receiver", "receiver",
        " te", " tes", "tight end",
        " kicker", " defense", " dst", " d st",
    )
    acquisition_terms = (
        "take ", "want ", "need ", "get ", "target ",
        "best move", "roster spot", "empty spot", "open spot",
        "empty bench", "open bench",
    )

    return (
        any(term in f" {text}" for term in position_terms)
        and any(term in text for term in acquisition_terms)
    )


def _is_waiver_replacement_request(user_message):
    """A named roster-player drop vs the *waiver pool*, not a named-player matchup.

    This must run before the generic acquisition-comparison route. A mention of
    another player as injury/backfield context does not make him a waiver add.
    """
    text = normalize_name(user_message)
    pool = bool(re.search(r"\b(waivers?|free agents?|available (?:rbs?|running backs?|players?)|waiver (?:rbs?|running backs?|players?)|pickup|pick up)\b", text))
    decision = bool(re.search(r"\b(drop|cut|replace|hold|keep|swap)\b", text))
    return pool and decision


def _is_fresh_waiver_request(user_message, resolved_names):
    """A standalone search of the waiver pool must not inherit a prior matchup.

    Short contextual turns ("what about him on waivers?", "instead?") can still
    follow a prior acquisition discussion. Explicitly naming a player also
    preserves that player for targeted questions.
    """
    if resolved_names or _intent_for_message(user_message) != "waiver":
        return False
    text = normalize_name(user_message)
    if re.search(r"\b(?:instead|that one|those|same|for him|for her|what about|how about)\b", text):
        return False
    return True


def _waiver_drop_name(user_message, resolved_names):
    """Find the player after 'drop/cut/replace' without confusing context names."""
    text = normalize_name(user_message)
    candidates = list(resolved_names or [])
    match = re.search(r"\b(?:drop|cut|replace)\s+(?:my\s+)?", text)
    if not match:
        return candidates[0] if len(candidates) == 1 else None
    after = f" {text[match.end():]} "
    for name in candidates:
        words = normalize_name(name).split()
        aliases = [" ".join(words)]
        if words and len(words[-1]) >= 4:
            aliases.append(words[-1])
        if any(after.startswith(f" {alias} ") for alias in aliases):
            return name
    return candidates[0] if len(candidates) == 1 else None


def _is_matchup_strategy_query(user_message):
    text = normalize_name(user_message)
    return bool(re.search(
        r"\b(?:am i favored|are we favored|win this week|beat my opponent|"
        r"beat my projection|beat my projections|projected to win|"
        r"how much (?:do i|should i|would i) (?:need|have) to (?:score|win|beat)|"
        r"how (?:can|do) i win|my matchup|opponent matchup|"
        r"what do i need to do to win|what is my opponent projected|"
        r"who am i playing this week|who do i play this week)\b", text
    ))


def _is_league_data_query(user_message):
    text = normalize_name(user_message)
    return bool(re.search(
        r"\b(?:faab|waiver budget|waiver priority|standings|league schedule|"
        r"other teams roster|other team roster|opponents roster|opponent roster|"
        r"opponent s roster|opponents lineup|opponent lineup|pending claims|"
        r"pending waivers|recent transactions|league transactions|"
        r"how many wins|who has the most points)\b", text
    ))


def _intent_for_message(user_message):
    text = normalize_name(user_message)
    if _is_matchup_strategy_query(user_message):
        return "matchup_strategy"
    if _is_league_data_query(user_message):
        return "league_data"
    if _asks_league_scoring(user_message):
        return "league_info"
    if _is_roster_review(user_message):
        return "roster_review"
    if _is_waiver_replacement_request(user_message):
        return "waiver_swap"
    if _looks_like_waiver_acquisition(user_message) or _mentions_open_spot(user_message):
        return "waiver"
    if _has_ros_cue(user_message):
        return "ros"
    if re.search(r"\b(ml|model|predict|prediction|shadow)\b", text):
        return "ml"
    if any(term in text for term in ("actual stats", "actual data", "stats", "statistics", "usage", "targets", "carries", "production")):
        return "stats"
    if "lineup" in text or "optimize my team" in text:
        return "lineup"
    if re.search(r"\b(start|sit|flex|bench|this week)\b", text):
        return "weekly"
    return None


def _extract_requested_position(user_message):
    text = f" {normalize_name(user_message)} "
    checks = (
        ("D/ST", (" d st ", " dst ", " defense ", " defenses ", " defence ")),
        ("QB", (" qb ", " qbs ", " quarterback ", " quarterbacks ")),
        ("RB", (" rb ", " rbs ", " running back ", " running backs ")),
        ("WR", (" wr ", " wrs ", " wide receiver ", " wide receivers ", " receiver ", " receivers ")),
        ("TE", (" te ", " tes ", " tight end ", " tight ends ")),
        ("K", (" kicker ", " kickers ", " k ")),
    )
    for position, terms in checks:
        if any(term in text for term in terms):
            return position
    return None


def _extract_requested_add_count(user_message):
    """Best-effort count for requests like "take 2 WRs". Defaults to one."""
    text = normalize_name(user_message)
    word_numbers = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    }

    patterns = (
        r"\b([1-5])\s+(?:qbs?|rbs?|wrs?|tes?|wide receivers?|running backs?|quarterbacks?|tight ends?)\b",
        r"\b(one|two|three|four|five)\s+(?:qbs?|rbs?|wrs?|tes?|wide receivers?|running backs?|quarterbacks?|tight ends?)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        raw = match.group(1)
        if raw.isdigit():
            return int(raw)
        return word_numbers.get(raw, 1)

    return 1


def _all_resolvable_names():
    names = []
    seen = set()

    for player in get_all_known_players():
        name = player.get("name")
        key = normalize_name(name)
        if key and key not in seen:
            seen.add(key)
            names.append(name)

    league = _league()
    player_map = getattr(league, "player_map", {}) or {}
    for name in player_map.keys():
        key = normalize_name(name)
        if key and key not in seen:
            seen.add(key)
            names.append(name)

    return names


def _extract_player_names(user_message, max_players=2):
    """Resolve player mentions cheaply without spending a Claude tool-selection call.

    Resolution order matters for both continuity and cost:
      1. players already in the current decision context,
      2. loaded roster/waiver players,
      3. the wider ESPN name map.

    A surname that was already established in the conversation (for example
    "Washington" after "Malik Washington") is therefore resolved to that same
    player rather than treated as globally ambiguous.
    """
    text = normalize_name(user_message)
    padded = f" {text} "
    found = []
    used = set()

    def mention_index(name):
        """Keep comparison order as the user typed it, even for surnames/typos."""
        parts = normalize_name(name).split()
        candidates = [" ".join(parts)]
        if parts and len(parts[-1]) >= 4:
            candidates.append(parts[-1])
        locations = [padded.find(f" {value} ") for value in candidates]
        locations = [pos for pos in locations if pos >= 0]
        if locations:
            return min(locations)
        # Used for ordering only: conservative resolution above has already
        # established which real player this typo denotes.
        for match in re.finditer(r"\b[a-z]{5,}\b", text):
            if parts and SequenceMatcher(None, match.group(0), parts[-1]).ratio() >= 0.84:
                return match.start()
        return len(text)

    def add_name(name):
        key = normalize_name(name)
        if key and key not in used:
            used.add(key)
            found.append(name)
            found.sort(key=mention_index)
        return len(found) >= max_players

    # 1) Conversation-local aliases.  Once Malik Washington has been established,
    # a later "Worthy or Washington" should not forget which Washington was meant.
    context_names = list(DECISION_CONTEXT.get("players") or [])
    context_hits = []
    for name in context_names:
        norm = normalize_name(name)
        parts = norm.split()
        if not parts:
            continue
        candidates = [norm]
        if len(parts[-1]) >= 4:
            candidates.append(parts[-1])
        best_pos = None
        for candidate in candidates:
            pos = padded.find(f" {candidate} ")
            if pos >= 0 and (best_pos is None or pos < best_pos):
                best_pos = pos
        if best_pos is not None:
            context_hits.append((best_pos, name))

    for _, name in sorted(context_hits):
        if add_name(name):
            return found

    loaded_names = []
    loaded_seen = set()
    for player in get_all_known_players():
        name = player.get("name")
        key = normalize_name(name)
        if key and key not in loaded_seen:
            loaded_seen.add(key)
            loaded_names.append(name)

    all_names = _all_resolvable_names()

    def add_full_name_hits(names):
        hits = []
        for name in names:
            norm = normalize_name(name)
            if len(norm) < 4:
                continue
            pos = padded.find(f" {norm} ")
            if pos >= 0:
                hits.append((pos, -len(norm), name))
        for _, _, name in sorted(hits):
            if add_name(name):
                return True
        return False

    if add_full_name_hits(loaded_names):
        return found
    if add_full_name_hits(all_names):
        return found

    def add_unique_surname_hits(names):
        surname_map = {}
        for name in names:
            norm = normalize_name(name)
            parts = norm.split()
            if not parts:
                continue
            surname = parts[-1]
            if len(surname) < 4:
                continue
            surname_map.setdefault(surname, []).append(name)

        hits = []
        for surname, matches in surname_map.items():
            unique = []
            seen_names = set()
            for name in matches:
                key = normalize_name(name)
                if key not in seen_names:
                    seen_names.add(key)
                    unique.append(name)
            if len(unique) != 1:
                continue
            pos = padded.find(f" {surname} ")
            if pos >= 0:
                hits.append((pos, unique[0]))

        for _, name in sorted(hits):
            if add_name(name):
                return True
        return False

    if add_unique_surname_hits(loaded_names):
        return found
    if add_unique_surname_hits(all_names):
        return found

    # 4) Conservative typo recovery for individual words against LOADED surnames.
    # This catches "barnard" -> Bernard cheaply.  We do not do this against the
    # entire NFL map because that caused unrelated guesses in v7.4.
    loaded_surname_lookup = {}
    for name in loaded_names:
        parts = normalize_name(name).split()
        if parts:
            loaded_surname_lookup.setdefault(parts[-1], []).append(name)

    stop_words = {
        "or", "vs", "versus", "for", "long", "term", "rest", "season",
        "stash", "wr", "rb", "qb", "te", "the", "of", "a", "an", "i",
        "mean", "meant", "thinking", "week", "this", "who", "better",
    }
    typo_hits = []
    for token_match in re.finditer(r"\b[a-z][a-z0-9']{4,}\b", text):
        token = token_match.group(0)
        if token in stop_words or token in loaded_surname_lookup:
            continue
        close = get_close_matches(
            token,
            list(loaded_surname_lookup.keys()),
            n=2,
            cutoff=0.84,
        )
        if not close:
            continue
        best = close[0]
        mapped = loaded_surname_lookup.get(best) or []
        if len(mapped) != 1:
            continue
        if len(close) > 1:
            best_score = SequenceMatcher(None, token, close[0]).ratio()
            second_score = SequenceMatcher(None, token, close[1]).ratio()
            if best_score - second_score < 0.08:
                continue
        typo_hits.append((token_match.start(), mapped[0]))

    for _, name in sorted(typo_hits):
        if add_name(name):
            break

    return found


def _reset_decision_context():
    DECISION_CONTEXT.clear()
    DECISION_CONTEXT.update({
        "players": [],
        "horizon": None,
        "acquisition": False,
        "position": None,
        "open_spot": False,
        "requested_add_count": 1,
        "waiver_drop_target": None,
    })


def _explicit_horizon(user_message):
    """Return an explicitly requested decision horizon, if any."""
    text = normalize_name(user_message)
    tokens = set(text.split())

    # Be tolerant of common typing errors such as "rest tof the season" or
    # "reset of the season".  If the user mentions season together with rest/reset,
    # the intended horizon is clearly ROS.
    if (
        _has_ros_cue(user_message)
        or any(term in text for term in ("stash", "hold long", "for the season"))
        or ("season" in tokens and ("rest" in tokens or "reset" in tokens))
    ):
        return "ros"
    if any(term in text for term in (
        "this week", "start", "sit", "lineup", "today", "tonight",
    )):
        return "weekly"
    return None


def _mentions_open_spot(user_message):
    text = normalize_name(user_message)
    return any(
        phrase in text
        for phrase in (
            "empty bench", "empty spot", "open bench", "open spot",
            "empty roster spot", "open roster spot", "free bench spot",
        )
    )


def _merge_corrected_player(previous_players, new_player):
    """Replace the likely mistaken player while preserving the other comparison side."""
    previous_players = list(previous_players or [])[:4]
    if not previous_players:
        return [new_player]

    new_norm = normalize_name(new_player)
    new_surname = new_norm.split()[-1] if new_norm.split() else new_norm

    # If this player was already in context, keep the existing comparison intact.
    for existing in previous_players:
        if normalize_name(existing) == new_norm:
            return previous_players

    similarities = []
    for index, existing in enumerate(previous_players):
        existing_norm = normalize_name(existing)
        parts = existing_norm.split()
        existing_surname = parts[-1] if parts else existing_norm
        score = max(
            SequenceMatcher(None, new_norm, existing_norm).ratio(),
            SequenceMatcher(None, new_surname, existing_surname).ratio(),
        )
        similarities.append((score, index))

    # A correction usually resembles the mistaken name (Barner/Barnard/Bernard).
    best_score, replace_index = max(similarities)
    if best_score < 0.55:
        # If no previous player resembles the correction, replace the most recently
        # mentioned side rather than throwing away the entire comparison.
        replace_index = len(previous_players) - 1

    merged = list(previous_players)
    merged[replace_index] = new_player

    deduped = []
    seen = set()
    for name in merged:
        key = normalize_name(name)
        if key and key not in seen:
            seen.add(key)
            deduped.append(name)
    return deduped


def _is_correction_turn(user_message):
    text = normalize_name(user_message)
    return any(
        phrase in text
        for phrase in (
            "i mean", "i meant", "meant ", "sorry", "actually", "correction",
        )
    )


def _derive_turn_context(user_message):
    """Resolve cheap conversational state before Claude sees the turn."""
    context = dict(DECISION_CONTEXT)
    previous_players = list(DECISION_CONTEXT.get("players") or [])
    context["players"] = list(previous_players)

    names = _extract_player_names(user_message, max_players=4)
    if _is_matchup_strategy_query(user_message) or _is_league_data_query(user_message):
        # New league-wide questions must not inherit unrelated A-vs-B decisions.
        context.update({"players": [], "horizon": None, "acquisition": False,
                        "position": None, "open_spot": False,
                        "requested_add_count": 1, "waiver_drop_target": None})
        previous_players = []
        names = []
    if _is_fresh_waiver_request(user_message, names):
        # Fresh standalone waiver searches override all stale comparison state.
        # Merely setting explicit_intent='waiver' is insufficient: the generic
        # acquisition-compare branch otherwise sees two carried-over names.
        context.update({
            "players": [], "horizon": None, "acquisition": True,
            "position": None, "open_spot": False,
            "requested_add_count": 1, "waiver_drop_target": None,
        })
        previous_players = []
    if len(names) >= 2:
        context["players"] = names
    elif len(names) == 1:
        # A correction such as "I meant Germie Bernard" should repair the mistaken
        # side of the previous A-vs-B comparison, not erase the other player.
        if _is_correction_turn(user_message) and len(previous_players) >= 2:
            context["players"] = _merge_corrected_player(previous_players, names[0])
        else:
            # If the single name is merely shorthand for one side of an established
            # comparison, keep the pair; otherwise start a new single-player context.
            prior_keys = {normalize_name(x) for x in previous_players}
            if normalize_name(names[0]) in prior_keys and len(previous_players) >= 2:
                context["players"] = previous_players
            else:
                context["players"] = names

    horizon = _explicit_horizon(user_message)
    if horizon is not None:
        context["horizon"] = horizon

    position = _extract_requested_position(user_message)
    if position is not None:
        context["position"] = position

    requested_count = _extract_requested_add_count(user_message)
    if requested_count > 1:
        context["requested_add_count"] = requested_count

    if _looks_like_waiver_acquisition(user_message):
        context["acquisition"] = True

    if _mentions_open_spot(user_message):
        context["open_spot"] = True
        context["acquisition"] = True

    explicit_intent = _intent_for_message(user_message)
    if explicit_intent in {"matchup_strategy", "league_data"}:
        context["horizon"] = None
        context["acquisition"] = False
        context["position"] = None
        context["players"] = []
    players = context.get("players") or []

    # Keep drop-vs-available-waivers anchored across corrections and background
    # injury news. Otherwise 'Jacobs is coming back' becomes a nonsensical
    # proposal to *add Jacobs* even if the user says Jacobs is unavailable.
    drop_target = context.get("waiver_drop_target")
    if explicit_intent == "waiver_swap":
        drop_target = _waiver_drop_name(user_message, names) or drop_target
    elif drop_target:
        # An explicit new A-or-B comparison or roster review starts a new task.
        new_comparison = bool(
            len(names) >= 2
            and re.search(r"\b(?:or|vs|versus|compare)\b", normalize_name(user_message))
        )
        new_pool_request = bool(
            explicit_intent == "waiver"
            and re.search(r"\b(?:best|top|different|another)\b", normalize_name(user_message))
            and not re.search(r"\b(?:instead|better than|for him|replace)\b", normalize_name(user_message))
        )
        new_weekly_question = bool(
            explicit_intent == "weekly"
            and re.search(r"\b(?:who should i start|who do i start|start sit|my starters)\b", normalize_name(user_message))
        )
        if new_comparison or new_pool_request or new_weekly_question or explicit_intent in {"roster_review", "lineup", "league_info", "matchup_strategy", "league_data"}:
            drop_target = None

    if drop_target:
        context["waiver_drop_target"] = drop_target
        context["players"] = [drop_target]
        loaded_drop = _find_loaded_player(drop_target)
        context["position"] = context.get("position") or (loaded_drop or {}).get("position")
        context["acquisition"] = True
        context["effective_intent"] = "waiver_swap"
        context["current_names"] = names
        return context
    context["waiver_drop_target"] = None

    # If a terse A-or-B prompt names two players that are both already loaded as
    # unrostered/free agents, infer an acquisition comparison without a Claude round.
    if len(players) >= 2 and not context.get("acquisition"):
        loaded = [_find_loaded_player(name) for name in players[:2]]
        if all(
            player is not None and player.get("rostered") is False
            for player in loaded
        ):
            context["acquisition"] = True

    if len(players) >= 2 and context.get("acquisition"):
        if context.get("horizon") == "ros" or explicit_intent == "ros":
            effective_intent = "ros_acquisition_compare"
        elif context.get("horizon") == "weekly" or explicit_intent == "weekly":
            effective_intent = "weekly_acquisition_compare"
        else:
            effective_intent = "acquisition_compare"
    elif explicit_intent == "ros" and len(players) >= 2:
        effective_intent = "ros"
    elif explicit_intent == "weekly" and len(players) >= 2:
        effective_intent = "weekly"
    elif len(players) >= 2 and re.search(r"\b(?:or|vs|versus|compare)\b", normalize_name(user_message)):
        # Plain A-or-B comparisons should prefetch even when no horizon is given.
        effective_intent = "ros" if context.get("horizon") == "ros" else "weekly"
    else:
        effective_intent = explicit_intent

    context["effective_intent"] = effective_intent
    context["current_names"] = names
    return context


def _persist_turn_context(turn_context):
    """Persist only tiny structured context; never store bulky provider payloads."""
    for key in (
        "players", "horizon", "acquisition", "position",
        "open_spot", "requested_add_count", "waiver_drop_target",
    ):
        if key in turn_context:
            value = turn_context[key]
            if key == "players":
                value = list(value or [])[:4]
            DECISION_CONTEXT[key] = value


def _update_turn_context_from_tool(turn_context, tool_name, tool_input, result):
    """Learn tiny conversational state from tools Claude actually used.

    This is essential when Claude resolves a surname that the cheap local parser did
    not. Without it, the answer can mention Tucker/Worthy correctly but the next turn
    forgets them because DECISION_CONTEXT never learned the tool-resolved names.
    """
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    result = result if isinstance(result, dict) else {}

    if tool_name in {"compare_players_weekly", "compare_players_ros"}:
        names = []
        for key in ("player_a", "player_b"):
            value = result.get(key)
            if isinstance(value, dict):
                name = value.get("name")
                if name:
                    names.append(name)
        if len(names) < 2:
            names = [
                tool_input.get("player_a"),
                tool_input.get("player_b"),
            ]
            names = [name for name in names if name]

        if len(names) >= 2:
            turn_context["players"] = names[:2]

        turn_context["horizon"] = (
            "ros" if tool_name == "compare_players_ros" else "weekly"
        )

        # If both resolved players are currently unrostered, remember that this
        # is an acquisition choice so later "long term" / "empty spot" turns
        # remain about these same waiver candidates.
        resolved = [find_player(name, allow_external=False) for name in names[:2]]
        if len(resolved) == 2 and all(
            player is not None and player.get("rostered") is False
            for player in resolved
        ):
            turn_context["acquisition"] = True

    elif tool_name == "get_player_details":
        player_payload = result.get("player") or {}
        name = player_payload.get("name") or tool_input.get("player_name")
        if name:
            players = list(turn_context.get("players") or [])
            if normalize_name(name) not in {normalize_name(x) for x in players}:
                players.append(name)
            turn_context["players"] = players[-4:]

    elif tool_name == "get_waiver_recommendations":
        turn_context["acquisition"] = True
        position = tool_input.get("position")
        if position and position != "ALL":
            turn_context["position"] = position


def _prefetch_acquisition_compare(turn_context):
    """Compact evidence for choosing between named waiver/bench targets."""
    names = list(turn_context.get("players") or [])[:2]
    if len(names) < 2:
        return None

    first = find_player(names[0])
    second = find_player(names[1])
    if first is None or second is None:
        return None

    horizon = turn_context.get("horizon") or "overall_hold"
    include_matchup = horizon != "ros"
    first_context = _comparison_player(
        first,
        include_shadow=False,
        include_matchup=include_matchup,
    )
    second_context = _comparison_player(
        second,
        include_shadow=False,
        include_matchup=include_matchup,
    )

    mode = "ros" if horizon == "ros" else "weekly"
    status = _comparison_status(first_context, second_context, mode=mode)

    return {
        "intent": "waiver_player_compare",
        "week": _current_week(),
        "horizon": horizon,
        "open_spot": bool(turn_context.get("open_spot")),
        "players": names,
        "comparison_status": status,
        "player_a": first_context,
        "player_b": second_context,
        "instruction": (
            "Choose between these named players for the user's roster spot. "
            + (
                "Prioritize rest-of-season role, actual usage/production, health, and "
                "market/ROS evidence; current-week matchup is intentionally omitted."
                if horizon == "ros"
                else "Balance sustainable role/market value with current production and weekly outlook."
            )
            + " Do not replace them with a different waiver candidate unless asked."
        ),
    }


def _compact_roster_need(position=None):
    """Compact roster context for waiver decisions without sending the whole roster blob."""
    roster = APP_CONTEXT.get("roster") or []

    counts = {}
    unavailable = []
    for p in roster:
        pos = p.get("position") or "UNKNOWN"
        counts[pos] = counts.get(pos, 0) + 1
        availability = p.get("availability") or {}
        if not availability.get("can_play_this_week", True):
            unavailable.append(_non_none({
                "name": p.get("name"),
                "position": pos,
                "reason": availability.get("reason") or p.get("injury_status"),
            }))

    result = {
        "roster_size": len(roster),
        "position_counts": counts,
        "unavailable": unavailable,
    }

    if position:
        focus = [p for p in roster if p.get("position") == position]
        result["focus_position"] = position
        result["focus_players"] = [
            _non_none({
                "name": p.get("name"),
                "slot": p.get("lineup_slot"),
                "injury_status": p.get("injury_status"),
                "can_play": (p.get("availability") or {}).get("can_play_this_week"),
                "risk": (p.get("availability") or {}).get("risk_level"),
                "bye": bool(p.get("is_on_bye") or p.get("espn_on_bye")),
                "espn_proj": p.get("espn_projected_points"),
                "fp_proj": p.get("fp_points") if p.get("fp_points") is not None else p.get("fp_ppr"),
            })
            for p in focus
        ]

    return result


def _prefetch_evidence(user_message, turn_context=None):
    """Do deterministic obvious tool work before Claude to avoid a tool-selection API call."""
    turn_context = turn_context or _derive_turn_context(user_message)
    intent = turn_context.get("effective_intent") or _intent_for_message(user_message)

    try:
        if intent == "matchup_strategy":
            context = APP_CONTEXT or {}
            league, team = context.get("league"), context.get("team")
            if league is None or team is None:
                return {"intent": "matchup_strategy", "status": "unavailable", "reason": "ESPN context missing"}
            try:
                result = matchup_strategy(league, team.team_id, _current_week())
            except Exception as exc:
                return {"intent": "matchup_strategy", "status": "unavailable",
                        "reason": f"ESPN matchup could not be loaded ({type(exc).__name__})"}
            # Compact evidence: never send 2 full 17-player rosters for one question.
            if result.get("status") == "available":
                for key in ("my_team", "opponent"):
                    side = result.get(key) or {}
                    side["roster"] = [_non_none({"name": p.get("name"), "position": p.get("position"),
                        "slot": p.get("slot"), "proj": p.get("projected_points"),
                        "actual": p.get("actual_points"), "injury": p.get("injury_status")})
                        for p in (side.get("roster") or []) if p.get("starter")]
                result["possible_lineup_upgrades"] = result.get("possible_lineup_upgrades", [])[:4]
            roster_names = {normalize_name(p.get("name")) for p in (context.get("roster") or [])}
            pool = [p for p in (context.get("waiver_candidates") or [])
                    if normalize_name(p.get("name")) not in roster_names]
            # The loaded pool is evidence of availability, not a guarantee of
            # future waiver priority or successful claims.
            def waiver_week_projection(p):
                for key in ("espn_projected_points", "fp_points", "fp_ppr"):
                    try:
                        value = p.get(key)
                        if value is not None:
                            return float(value)
                    except (ValueError, TypeError):
                        pass
                return -1.0
            pool.sort(key=waiver_week_projection, reverse=True)
            shortlist = [_non_none({"name": p.get("name"), "position": p.get("position"),
                         "espn_projection": p.get("espn_projected_points"),
                         "fp_projection": p.get("fp_points"),
                         "injury_status": p.get("injury_status")}) for p in pool[:5]]
            return {"intent": "matchup_strategy", "matchup": result,
                    "available_waiver_shortlist": shortlist,
                    "instruction": "Answer projection lead/deficit and points-to-beat numerically from ESPN. "
                    "Use both lineups and explain highest leverage legal possibilities. "
                    "Suggest waiver/FAAB moves only from available_waiver_shortlist and verify a legal drop; "
                    "waiver bids and expected win probability aren't calculated here."}

        if intent == "league_data":
            context = APP_CONTEXT or {}
            league, team = context.get("league"), context.get("team")
            if league is None or team is None:
                return {"intent": "league_data", "status": "unavailable"}
            text = normalize_name(user_message)
            result = {"intent": "league_data", "week": _current_week()}
            if any(x in text for x in ("faab", "budget", "waiver priority")):
                result["faab"] = league_faab(league)
            elif "pending" in text and ("claim" in text or "waiver" in text):
                result["pending_claims"] = {"status": "not_supported", "note": "Private unprocessed claims are not available from this ESPN integration."}
            elif "roster" in text or "lineup" in text:
                try:
                    opponent = None
                    if "opponent" in text:
                        strategy = matchup_strategy(league, team.team_id, _current_week())
                        opponent = (strategy.get("opponent") or {}).get("team_id")
                    if opponent is None:
                        for t in league.teams:
                            candidate = normalize_name(getattr(t, "team_name", ""))
                            if candidate and candidate in text:
                                opponent = t.team_id
                                break
                    if opponent is not None:
                        result["roster"] = league_team_roster(league, opponent, _current_week())
                    else:
                        result["available_teams"] = [{"name": t.team_name, "id": t.team_id}
                                                     for t in league.teams]
                except Exception as exc:
                    result["status"] = "unavailable"
                    result["error"] = f"ESPN roster details unavailable ({type(exc).__name__})"
            elif "standings" in text or "wins" in text or "most points" in text:
                result["standings"] = league_standings(league)
            elif "schedule" in text or "play next" in text:
                rows = league_schedule(league, team.team_id).get("schedule", [])
                result["schedule"] = [r for r in rows if r["week"] >= _current_week()][:5]
            else:
                result["standings"] = league_standings(league)
            return result

        if intent == "league_info":
            return {"intent": "league_info", "league_context": _compact_league_context()}

        if intent == "roster_review":
            roster = APP_CONTEXT.get("roster") or []
            pool = APP_CONTEXT.get("waiver_candidates") or []
            shortlist = []
            for position in ("RB", "WR", "TE", "QB"):
                group = sorted(
                    (p for p in pool if p.get("position") == position),
                    key=_waiver_candidate_sort_key, reverse=True,
                )
                shortlist.extend(_compact_waiver_candidate(p, include_detail=False) for p in group[:2])
            return {
                "intent": "roster_review", "week": _current_week(),
                "roster_context": _compact_roster_need(),
                "roster_players": [_non_none({
                    "name": p.get("name"), "position": p.get("position"),
                    "slot": p.get("lineup_slot"),
                    "espn_projection": p.get("espn_projected_points"),
                    "weekly_score_advisory": p.get("weekly_score"),
                    "ros_score_advisory": p.get("ros_score"),
                    "injury_status": p.get("injury_status"),
                }) for p in roster],
                "waiver_shortlist": shortlist,
                "instruction": "Review my real roster and available waiver candidates. Avoid assuming an add is automatically justified; explain the best move or recommend holding.",
            }

        if intent == "waiver_swap":
            drop_name = turn_context.get("waiver_drop_target")
            if not drop_name:
                return None
            roster = APP_CONTEXT.get("roster") or []
            owned = next((p for p in roster if normalize_name(p.get("name")) == normalize_name(drop_name)), None)
            drop_player = owned or find_player(drop_name)
            position = (
                turn_context.get("position")
                or (drop_player or {}).get("position")
                or _extract_requested_position(user_message)
            )
            # The requested position is the one being replaced, not a contextual
            # second player (such as a backfield teammate returning from injury).
            position = position or (drop_player or {}).get("position")
            waiver = tool_get_waiver_recommendations(position=position, limit=7)
            pool = [
                p for p in (waiver.get("candidate_pool") or [])
                if normalize_name(p.get("name")) != normalize_name(drop_name)
            ]
            # Offer deterministic drop-specific comparisons if ESPN confirms the
            # drop player is actually in the loaded roster. Keep payload compact.
            evaluations = []
            if owned and pool:
                from waiver_advisor import evaluate_transaction
                indexed = {normalize_name(p.get("name")): p for p in (APP_CONTEXT.get("waiver_candidates") or [])}
                for candidate in pool[:5]:
                    add = indexed.get(normalize_name(candidate.get("name")))
                    if add is None:
                        continue
                    try:
                        result = evaluate_transaction(roster, add, owned)
                        evaluations.append(_non_none({
                            "add": add.get("name"), "drop": owned.get("name"),
                            "valid": result.get("valid"),
                            "weekly_lineup_delta": result.get("weekly_lineup_delta"),
                            "projected_points_delta": result.get("projected_points_delta"),
                            "ros_delta": result.get("ros_delta"),
                            "market_value_delta": result.get("market_value_delta"),
                        }))
                    except Exception as exc:
                        _trace("SWAP_EVAL", error_type=type(exc).__name__)
            return {
                "intent": "waiver_swap", "week": _current_week(),
                "drop_target": drop_name,
                "drop_roster_verification": "confirmed_owned" if owned else "not_in_loaded_roster_user_says_owned",
                "drop_player": _comparison_player(drop_player, include_matchup=True) if drop_player else {"name": drop_name, "data_unavailable": True},
                "requested_position": position,
                "waivers": {
                    "candidate_pool": pool,
                    "matching_candidates_loaded": waiver.get("matching_candidates_loaded"),
                    "transaction_model_result": waiver.get("transaction_model_result"),
                    "drop_specific_evaluations": evaluations,
                },
                "roster_context": _compact_roster_need(position),
                "instruction": (
                    "The user ALREADY OWNS the drop_target and wants to decide whether to KEEP that player "
                    "or DROP him for one of candidate_pool, which are the actual currently available ESPN waiver options. "
                    "Do NOT suggest adding drop_target. Do NOT suggest acquiring another named player mentioned "
                    "as background context (e.g., Josh Jacobs) unless that player is in candidate_pool. "
                    "If a player is absent from the loaded roster, say ESPN ownership could not be verified; "
                    "do not contradict the user's ownership statement. Compare the best available candidate "
                    "against holding drop_target, factoring its handcuff role, teammate return, and ROS upside. "
                    "Do not claim the user's quoted projection applies to a specific player if its referent is ambiguous. "
                    "If the swap does not clearly improve the roster, recommend HOLD."
                ),
            }

        if intent in {"acquisition_compare", "weekly_acquisition_compare", "ros_acquisition_compare"}:
            comparison = _prefetch_acquisition_compare(turn_context)
            if comparison is not None:
                return comparison

        if intent == "waiver":
            position = _extract_requested_position(user_message) or turn_context.get("position")
            requested_count = max(
                _extract_requested_add_count(user_message),
                int(turn_context.get("requested_add_count") or 1),
            )
            # Specific-position requests deserve enough rich evidence to choose the
            # requested number of adds, with a few alternatives for comparison.
            waiver = tool_get_waiver_recommendations(
                position=position,
                limit=max(5, min(7, requested_count + 3)) if position else 2,
            )
            normalized_message = normalize_name(user_message)
            open_spot = bool(turn_context.get("open_spot")) or any(
                phrase in normalized_message
                for phrase in ("empty bench", "empty spot", "open bench", "open spot")
            )
            compact_waiver = {
                "requested_position": waiver.get("requested_position"),
                "matching_candidates_loaded": waiver.get("matching_candidates_loaded"),
                "candidate_pool": waiver.get("candidate_pool") or [],
                "transaction_model_result": waiver.get("transaction_model_result"),
            }
            if open_spot:
                # No drop is required. Compare direct-add impact for candidates
                # rather than showing add/drop model transactions.
                from waiver_advisor import evaluate_open_slot_add
                known = {normalize_name(p.get("name")): p for p in (APP_CONTEXT.get("waiver_candidates") or [])}
                compact_waiver["open_spot_adds"] = [
                    evaluate_open_slot_add(
                        APP_CONTEXT.get("roster") or [], known[normalize_name(p.get("name"))]
                    )
                    for p in compact_waiver["candidate_pool"]
                    if normalize_name(p.get("name")) in known
                ][:7]
            else:
                compact_waiver["moves"] = (waiver.get("moves") or [])[:3]
            return {
                "intent": "waiver",
                "week": _current_week(),
                "requested_add_count": requested_count,
                "user_reports_open_spot": open_spot,
                "waivers": compact_waiver,
                "roster_context": _compact_roster_need(position),
                "roster_players": [_non_none({
                    "name": p.get("name"),
                    "position": p.get("position"),
                    "slot": p.get("lineup_slot"),
                    "espn_projection": p.get("espn_projected_points"),
                    "ros_score_advisory": p.get("ros_score"),
                }) for p in (APP_CONTEXT.get("roster") or [])],
                "instruction": (
                    "This is a waiver-wire roster-improvement request, NOT an old "
                    "start/sit or A-vs-B comparison. Use the currently loaded roster "
                    "and the actual available candidate_pool to evaluate possible "
                    "add/drop moves. NEVER suggest acquiring a player already in "
                    "roster_players, and ONLY recommend adding a player in candidate_pool. "
                    "If no candidate improves the roster, explicitly recommend HOLD. "
                    "When the user asks for multiple adds, choose the requested number "
                    "from candidate_pool. Do not invent waiver prices or availability."
                ),
            }

        names = _extract_player_names(user_message, max_players=2)
        if not names:
            names = list(turn_context.get("players") or [])[:2]

        if intent == "weekly" and len(names) >= 2:
            result = tool_compare_players_weekly(names[0], names[1])
            return {
                "intent": "weekly_compare",
                "week": _current_week(),
                "comparison": {
                    key: result.get(key)
                    for key in (
                        "success", "comparison_status", "player_a", "player_b",
                        "advisory_score_comparison",
                    )
                },
            }

        if intent == "ros" and len(names) >= 2:
            result = tool_compare_players_ros(names[0], names[1])
            return {
                "intent": "ros_compare",
                "comparison": {
                    key: result.get(key)
                    for key in (
                        "success", "comparison_status", "player_a", "player_b",
                        "advisory_score_comparison",
                    )
                },
            }

        if intent == "ml" and names:
            return {
                "intent": "shadow_ml",
                "week": _current_week(),
                "result": tool_get_shadow_prediction(names[0]),
            }

        if intent == "stats" and names:
            player = find_player(names[0])
            if player is not None:
                return {
                    "intent": "actual_stats",
                    "week": _current_week(),
                    "player": player.get("name"),
                    "actual_performance": _actual_performance_context(player),
                    "current_status": _non_none({
                        "injury_status": player.get("injury_status"),
                        "availability": player.get("availability"),
                        "free_fallback": _compact_free_for_decision(player),
                    }),
                }

        if intent == "lineup":
            return {
                "intent": "lineup",
                "week": _current_week(),
                "optimized": tool_get_optimal_lineup(),
            }

    except Exception as exc:
        # Prefetch is an optimization only. Normal Claude tool use remains the
        # correctness fallback if local intent parsing/tool execution fails.
        _trace("PREFETCH", error_type=type(exc).__name__)
        return None

    return None


def _needs_chat_history(user_message):
    text = normalize_name(user_message)
    followups = (
        "what about", "how about", "instead", "same question", "that one",
        "those", "earlier", "last answer", "your answer", "why did", "why was",
    )
    if any(term in text for term in followups):
        return True
    if text.startswith(("no ", "yes ", "but ", "and ")):
        return True
    # Very short turns are often follow-ups such as "what about adams?"
    return len(text.split()) <= 4


def _effort_for_message(user_message):
    intent = _intent_for_message(user_message)
    # Very short context-only follow-ups such as "I am thinking long term" still
    # become real decisions after structured context carryover, so medium is useful.
    if intent in {"weekly", "ros", "waiver", "lineup", "matchup_strategy", "league_data"}:
        return CLAUDE_DECISION_EFFORT
    if DECISION_CONTEXT.get("players") and _needs_chat_history(user_message):
        return CLAUDE_DECISION_EFFORT
    return CLAUDE_SIMPLE_EFFORT

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
        return tool_get_waiver_recommendations(
            position=tool_input.get("position"),
            limit=tool_input.get("limit", 5),
        )
    if tool_name == "get_shadow_prediction":
        return tool_get_shadow_prediction(tool_input.get("player_name", ""))
    if tool_name == "get_system_status":
        return tool_get_system_status()

    return {"error": f"Unknown tool: {tool_name}"}


# ==================================================
# Claude tool loop
# ==================================================

def _is_waiver_query(user_message):
    text = normalize_name(user_message)
    return any(
        term in text
        for term in (
            "waiver", "pickup", "pick up", "add", "drop",
            "free agent", "streamer",
        )
    )


def _response_token_budget(user_message):
    intent = _intent_for_message(user_message)
    if intent in {"waiver", "waiver_swap"}:
        return max(CLAUDE_MAX_TOKENS, CLAUDE_WAIVER_MAX_TOKENS)
    if intent in {"weekly", "ros", "lineup"}:
        return CLAUDE_MAX_TOKENS
    if DECISION_CONTEXT.get("players") and _needs_chat_history(user_message):
        return min(CLAUDE_MAX_TOKENS, 1800)
    return min(CLAUDE_MAX_TOKENS, CLAUDE_SIMPLE_MAX_TOKENS)


def create_claude_message(messages, tools, max_tokens, effort):
    kwargs = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "system": SYSTEM_PROMPT,
        "messages": messages,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": effort},
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = {"type": "auto"}
    return client.messages.create(**kwargs)

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


def ask_claude(user_message, *, return_details=False):
    turn_context = _derive_turn_context(user_message)
    prefetched = _prefetch_evidence(user_message, turn_context=turn_context)
    if prefetched is not None:
        prefetched["league_context"] = _compact_league_context()

    # If deterministic prefetch already resolved the players/horizon, old prose history
    # is redundant and expensive. Keep history only when Claude truly needs it to infer
    # the follow-up itself.
    use_history = _needs_chat_history(user_message) and prefetched is None
    history = CHAT_HISTORY[-MAX_HISTORY_MESSAGES:] if use_history else []

    if prefetched is not None:
        context_note = _non_none({
            "players": turn_context.get("players") or None,
            "horizon": turn_context.get("horizon"),
            "acquisition": turn_context.get("acquisition"),
            "open_spot": turn_context.get("open_spot"),
            "position": turn_context.get("position"),
            "waiver_drop_target": turn_context.get("waiver_drop_target"),
        })
        current_content = (
            user_message
            + "\n\nCONVERSATION DECISION CONTEXT:\n"
            + json_text(context_note)
            + "\n\nPREFETCHED APP EVIDENCE (generated by the fantasy app; current factual input):\n"
            + json_text(prefetched)
            + choice_instruction(prefetched)
        )
        # The obvious factual work is already done locally. Omitting tool schemas
        # here avoids paying for a tool-selection round and repeated schemas.
        selected_tools = []
    else:
        current_content = (
            user_message
            + "\n\nLEAGUE CONTEXT (ESPN-derived; authoritative):\n"
            + json_text(_compact_league_context())
        )
        selected_tools = select_tools_for_message(user_message)

    working_messages = history + [
        {"role": "user", "content": current_content}
    ]

    _trace(
        "ROUTER",
        intent=turn_context.get("effective_intent") or _intent_for_message(user_message),
        players=turn_context.get("players") or [],
        horizon=turn_context.get("horizon"),
        position=turn_context.get("position"),
        waiver_drop_target=turn_context.get("waiver_drop_target"),
        requested_add_count=turn_context.get("requested_add_count"),
        open_spot=turn_context.get("open_spot"),
        prefetch=prefetched is not None,
        league=(_compact_league_context() or {}).get("scoring"),
        tools=[tool["name"] for tool in selected_tools],
        candidate_count=(len((prefetched.get("waivers") or {}).get("candidate_pool") or []) if prefetched else 0),
        evidence_keys=sorted(prefetched.keys()) if prefetched else [],
        data_sources=_evidence_sources(prefetched) if prefetched and _debug_enabled() else [],
    )

    token_budget = _response_token_budget(user_message)
    effort = _effort_for_message(user_message)

    total_input_tokens = 0
    total_output_tokens = 0
    api_calls = 0
    final_response = None
    tools_called = []

    for _ in range(MAX_TOOL_ROUNDS):
        response = create_claude_message(
            working_messages,
            selected_tools,
            max_tokens=token_budget,
            effort=effort,
        )
        api_calls += 1

        usage = getattr(response, "usage", None)
        if usage is not None:
            total_input_tokens += getattr(usage, "input_tokens", 0) or 0
            total_output_tokens += getattr(usage, "output_tokens", 0) or 0

        # Cost-aware retry: if Claude used the whole budget only for hidden
        # thinking and produced no visible answer, retry once with more room.
        # If usable visible text exists, keep it rather than paying for a full
        # duplicate call merely to finish a sentence or two.
        if response.stop_reason == "max_tokens":
            partial = extract_text(response)
            if partial:
                final_response = response
                break

            retry_budget = max(CLAUDE_RETRY_MAX_TOKENS, token_budget * 2)
            print(
                f"[Claude] No visible answer before token cap; retrying once "
                f"with {retry_budget:,} tokens..."
            )
            response = create_claude_message(
                working_messages,
                selected_tools,
                max_tokens=retry_budget,
                effort=effort,
            )
            api_calls += 1

            usage = getattr(response, "usage", None)
            if usage is not None:
                total_input_tokens += getattr(usage, "input_tokens", 0) or 0
                total_output_tokens += getattr(usage, "output_tokens", 0) or 0

        final_response = response

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
            tools_called.append(block.name)
            try:
                result = run_tool(block.name, block.input)
                _update_turn_context_from_tool(
                    turn_context,
                    block.name,
                    block.input,
                    result,
                )
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

    raw_text = extract_text(final_response)
    response_text, recorded_choice = extract_choice(raw_text, prefetched)
    if not response_text:
        raise RuntimeError("Claude returned no text response.")

    if final_response.stop_reason == "max_tokens":
        response_text += "\n\n[Answer reached the response-token cap.]"

    print("\nCoach: " + response_text)
    print(
        "\n"
        f"[Claude usage: {total_input_tokens:,} input / "
        f"{total_output_tokens:,} output tokens across {api_calls} API call"
        f"{'' if api_calls == 1 else 's'}; "
        f"prefetch={'yes' if prefetched is not None else 'no'}; effort={effort}]"
    )

    _trace(
        "CLAUDE", api_calls=api_calls, input_tokens=total_input_tokens,
        output_tokens=total_output_tokens, tools_called=tools_called,
    )

    # Store only the human-visible conversation, never bulky prefetched JSON.
    CHAT_HISTORY.append({"role": "user", "content": user_message})
    CHAT_HISTORY.append({"role": "assistant", "content": response_text})
    _persist_turn_context(turn_context)

    # Save the recommendation Claude *actually made*, independently of
    # the deterministic optimizer. Saving is best-effort and never triggers
    # additional Claude calls or changes the visible answer.
    try:
        record = create_record(
            season=SEASON, week=_current_week(),
            question=user_message, answer=response_text,
            prefetched=prefetched, turn_context=turn_context,
            league_scoring=_compact_league_context(),
            choice=recorded_choice, api_calls=api_calls,
            input_tokens=total_input_tokens, output_tokens=total_output_tokens,
        )
        path = save_record(record)
        if path is not None:
            _trace("JOURNAL", file=path.name, choice=recorded_choice, scope=record["evaluation_scope"])
    except Exception as exc:
        _trace("JOURNAL", error_type=type(exc).__name__)

    if return_details:
        return {
            "answer": response_text,
            "api_calls": api_calls,
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "prefetched": prefetched is not None,
            "intent": turn_context.get("effective_intent") or _intent_for_message(user_message),
        }
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

    scoring = _compact_league_context()
    print(
        "League scoring: "
        f"{scoring.get('scoring', 'UNKNOWN')} "
        f"({scoring.get('reception_points')} pts/reception)."
    )


# ==================================================
# CLI
# ==================================================

def main():
    load_app_context()

    print(f"\nFantasy Coach {APP_VERSION} ready ({MODEL}, effort={CLAUDE_EFFORT}).")
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
                _reset_decision_context()
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
