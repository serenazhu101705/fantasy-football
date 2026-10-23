"""Guarded ESPN lineup preview and opt-in writes.

Uses ESPN's private, undocumented roster transaction API. Default is read-only.
All writes require ESPN_WRITES_ENABLED=1, an explicit fresh preview token and
fully unlocked players. Auto-swap uses a separate opt-in and limited criteria.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import os
import secrets
from threading import RLock
from time import monotonic

import requests

# ESPN football slot IDs; RB1/RB2 and WR1/WR2 are the same ESPN slot ID.
SLOTS = {"QB": 0, "RB": 2, "WR": 4, "TE": 6, "D/ST": 16, "K": 17,
         "FLEX": 23, "BE": 20, "IR": 21}
TARGET_SLOTS = {"QB": "QB", "RB1": "RB", "RB2": "RB", "WR1": "WR",
                "WR2": "WR", "TE": "TE", "FLEX": "FLEX", "D/ST": "D/ST", "K": "K"}
ALLOWED = {"QB": {"QB"}, "RB": {"RB"}, "WR": {"WR"}, "TE": {"TE"},
           "FLEX": {"RB", "WR", "TE"}, "D/ST": {"D/ST"}, "K": {"K"}}
PREVIEWS: dict[str, tuple[float, tuple, dict]] = {}
LOCK = RLock()
PREVIEW_TTL = 180


def _current_slot(player):
    slot = str(getattr(player, "lineupSlot", "") or "").upper()
    if slot in ("RB/WR/TE", "FLEX"):
        return "FLEX"
    if slot in ("DST", "D/ST"):
        return "D/ST"
    if slot in ("BE", "BN"):
        return "BE"
    return slot


def _roster_signature(team):
    return tuple(sorted((int(p.playerId), _current_slot(p)) for p in team.roster))


def _lock_check(player, league, week, game_status):
    """Fail closed if we cannot verify kickoff has not happened."""
    if bool(getattr(player, "on_bye_week", False)):
        return
    pro_team = str(getattr(player, "proTeam", "") or "").upper()
    aliases = {"JAX": "JAC", "WSH": "WAS", "LA": "LAR"}
    game = (game_status.get("games") or {}).get(aliases.get(pro_team, pro_team))
    if not game:
        raise ValueError(f"Cannot verify kickoff/lock for {getattr(player, 'name', 'player')}.")
    if game.get("state") != "SCHEDULED":
        raise ValueError(f"Player game is already live, final or unverified: {getattr(player, 'name', 'player')}.")
    kickoff = game.get("kickoff")
    if not kickoff:
        raise ValueError("Kickoff time unavailable; not safe to move lineup.")
    try:
        kickoff_at = datetime.fromisoformat(str(kickoff).replace("Z", "+00:00"))
        if kickoff_at.tzinfo is None or kickoff_at <= datetime.now(timezone.utc):
            raise ValueError("Kickoff has started or timestamp is ambiguous.")
    except ValueError:
        raise ValueError("Cannot verify kickoff/lock time for lineup move.") from None


def _target_lineup(optimal):
    source = optimal.get("lineup") or {}
    if set(source) != set(TARGET_SLOTS):
        raise ValueError("Expected standard 9-slot optimizer lineup; custom leagues require another adapter.")
    result = {}
    for key, slot in TARGET_SLOTS.items():
        obj = source[key]
        if not isinstance(obj, dict) or obj.get("espn_id") is None:
            raise ValueError(f"No ESPN player ID for {key}; lineup cannot be applied.")
        pid = int(obj["espn_id"])
        if pid in result:
            raise ValueError("Optimizer selected the same player twice.")
        if str(obj.get("position") or "").upper() not in ALLOWED[slot]:
            raise ValueError(f"Ineligible optimizer selection for {key}.")
        result[pid] = slot
    return result


def calculate_preview(league, team, optimal, *, game_status=None):
    """Never sends a write; validates actual ESPN roster membership and locks."""
    if team.team_id not in {t.team_id for t in league.teams}:
        raise ValueError("Team is not in authenticated league.")
    desired_active = _target_lineup(optimal)
    current = {int(p.playerId): p for p in team.roster}
    if not set(desired_active).issubset(current):
        raise ValueError("Optimizer includes a player no longer on your ESPN team. Refresh first.")
    actual_active = [p for p in team.roster if _current_slot(p) not in ("BE", "IR")]
    expected = Counter({"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "D/ST": 1, "K": 1})
    active_counts = Counter(_current_slot(p) for p in actual_active)
    if active_counts != expected:
        raise ValueError("Unsupported or incomplete ESPN starting slots; won't risk a write.")
    target = {pid: desired_active.get(pid, "IR" if _current_slot(p) == "IR" else "BE") for pid,p in current.items()}
    # Do not activate IR as an implicit lineup change; requires a distinct ESPN IR action.
    if any(_current_slot(current[pid]) == "IR" for pid in desired_active):
        raise ValueError("Optimal player is on IR; activate via ESPN first.")
    changes = []
    game_status = game_status or {"games": {}}
    for pid, p in current.items():
        old, new = _current_slot(p), target[pid]
        if old == new:
            continue
        if old not in SLOTS or new not in SLOTS:
            raise ValueError("Unknown ESPN lineup slot; not safe to change.")
        _lock_check(p, league, int(getattr(league,"current_week", 0)), game_status)
        changes.append({"player_id": pid, "name": p.name, "from": old, "to": new,
                        "from_slot_id": SLOTS[old], "to_slot_id": SLOTS[new]})
    return {"week": int(league.current_week), "team_id": int(team.team_id),
            "projected_total": optimal.get("projected_total"), "changes": changes,
            "change_count": len(changes), "writes_enabled": os.getenv("ESPN_WRITES_ENABLED") == "1",
            "note": "Preview only; nothing has been submitted to ESPN."}


def create_preview(league, team, optimal, *, game_status):
    preview = calculate_preview(league, team, optimal, game_status=game_status)
    token = secrets.token_urlsafe(24)
    with LOCK:
        for key,(created, _, _) in list(PREVIEWS.items()):
            if monotonic()-created > PREVIEW_TTL:
                PREVIEWS.pop(key, None)
        PREVIEWS[token] = (monotonic(), _roster_signature(team), preview)
    return {**preview, "confirmation_token": token, "expires_in_seconds": PREVIEW_TTL}


def submit_preview(league, team, optimal, token, *, game_status, sender=None):
    """The only code path allowed to send a real ESPN lineup transaction."""
    if os.getenv("ESPN_WRITES_ENABLED") != "1":
        raise ValueError("Lineup writes are disabled. Set ESPN_WRITES_ENABLED=1 locally to opt in.")
    with LOCK:
        record = PREVIEWS.pop(token, None)  # one use even on failure
    if record is None or monotonic() - record[0] > PREVIEW_TTL:
        raise ValueError("Preview expired or already used. Generate a new preview.")
    prior_signature, old_preview = record[1], record[2]
    if _roster_signature(team) != prior_signature:
        raise ValueError("ESPN roster changed after preview; refresh and try again.")
    fresh = calculate_preview(league, team, optimal, game_status=game_status)
    if fresh["changes"] != old_preview["changes"] or fresh["week"] != old_preview["week"]:
        raise ValueError("Lineup or week changed since preview. Generate a new preview.")
    if not fresh["changes"]:
        return {"applied": True, "verified": True, "changes": [], "note": "Already optimal; no ESPN write needed."}
    cookie_s2 = os.getenv("ESPN_S2")
    swid = os.getenv("ESPN_SWID")
    league_id = os.getenv("ESPN_LEAGUE_ID")
    if not all((cookie_s2, swid, league_id)):
        raise ValueError("Missing ESPN credentials in server configuration.")
    season = int(getattr(league, "year", 2026))
    url = f"https://lm-api-writes.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{int(league_id)}/transactions/"
    payload = {"isLeagueManager": False, "teamId": int(team.team_id), "type": "ROSTER",
               "memberId": swid, "scoringPeriodId": fresh["week"], "executionType": "EXECUTE",
               "items": [{"playerId": row["player_id"], "type": "LINEUP",
                          "fromLineupSlotId": row["from_slot_id"], "toLineupSlotId": row["to_slot_id"]}
                         for row in fresh["changes"]]}
    # No retries on a mutation. A timed-out response can still mean ESPN applied it.
    if sender is None:
        def sender(url, payload, s2, swid):
            return requests.post(url, json=payload, cookies={"espn_s2": s2, "SWID": swid},
                                 headers={"Origin": "https://fantasy.espn.com",
                                          "Referer": "https://fantasy.espn.com/"}, timeout=12)
    try:
        response = sender(url, payload, cookie_s2, swid)
    except Exception:
        raise ValueError("ESPN write result is unknown (network error). Refresh your ESPN roster before retrying.") from None
    if not (200 <= int(getattr(response, "status_code", 0)) < 300):
        raise ValueError(f"ESPN rejected lineup update (HTTP {getattr(response, 'status_code', '?')}).")
    # ESPN's response is not authoritative for the actual roster. Caller must
    # reload ESPN roster before confirming changes have taken effect.
    return {"applied": True, "verified": False, "changes": fresh["changes"],
            "note": "ESPN accepted the transaction. Refresh ESPN to verify it took effect."}


def automatic_injury_bye_lineup(team, optimal):
    """Replace only unambiguously OUT/IR/SUSPENDED/bye starters.

    Does not shuffle healthy starters merely to raise projections. The selected
    replacement must come from the deterministic optimizer and currently be on
    this team's bench. If no safe replacement exists, do nothing.
    """
    original = list(team.roster)
    starters = [p for p in original if _current_slot(p) not in ("BE", "IR")]
    if Counter(_current_slot(p) for p in starters) != Counter(
            {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "D/ST": 1, "K": 1}):
        raise ValueError("Cannot auto-swap a nonstandard ESPN lineup.")
    candidates = (optimal.get("lineup") or {}).values()
    eligible_bench = {int(p.playerId): p for p in original if _current_slot(p) == "BE"}
    ordered = {"QB": [], "RB": [], "WR": [], "TE": [], "FLEX": [], "D/ST": [], "K": []}
    for p in original:
        slot = _current_slot(p)
        if slot in ordered:
            ordered[slot].append(p)
    desired = {}
    for slot in ("QB", "RB", "WR", "TE", "FLEX", "D/ST", "K"):
        for i,p in enumerate(ordered[slot]):
            key = (f"{slot}{i+1}" if slot in ("RB", "WR") else slot)
            desired[key] = {"espn_id": int(p.playerId), "position": p.position, "name": p.name}
    used = {obj["espn_id"] for obj in desired.values()}
    for key, p in list(desired.items()):
        raw = next((q for q in original if int(q.playerId) == p["espn_id"]), None)
        injured = str(getattr(raw, "injuryStatus", "") or "").upper() in \
                  ("OUT", "INJURY_RESERVE", "IR", "SUSPENSION", "SUSPENDED")
        if not (injured or bool(getattr(raw, "on_bye_week", False))):
            continue
        base = TARGET_SLOTS[key]
        replacement = next((c for c in candidates
                            if isinstance(c,dict) and c.get("espn_id") is not None
                            and int(c["espn_id"]) in eligible_bench
                            and int(c["espn_id"]) not in used
                            and str(c.get("position") or "").upper() in ALLOWED[base]), None)
        if not replacement:
            raise ValueError(f"No safe bench replacement for {getattr(raw, 'name', key)}.")
        used.remove(p["espn_id"])
        new_id = int(replacement["espn_id"])
        used.add(new_id)
        desired[key] = {"espn_id": new_id, "position": replacement["position"],
                        "name": replacement.get("name")}
    if set(desired) != set(TARGET_SLOTS):
        raise ValueError("Unable to create a complete safe starting lineup.")
    return {"lineup": desired, "projected_total": optimal.get("projected_total")}
