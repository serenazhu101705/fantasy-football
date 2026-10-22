"""Frozen Claude recommendation receipts. No ESPN/Claude calls are made here.

A receipt is generated during the *same* Claude API call as the normal answer.
Only explicitly recorded choices may be evaluated later; the evaluator never
attempts to infer a choice from free-form explanations after the fact.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

JOURNAL_DIR = Path("data/decisions/claude")
_RECEIPT = re.compile(r'\n?FC_CHOICE_JSON:\s*(\{[^\n]*\})\s*$', re.IGNORECASE)


def _comparison(prefetched):
    if not isinstance(prefetched, dict):
        return None
    if prefetched.get("intent") == "weekly_compare":
        evidence = prefetched.get("comparison") or {}
        kind = "weekly"
    elif prefetched.get("intent") == "ros_compare":
        evidence = prefetched.get("comparison") or {}
        kind = "ros"
    elif prefetched.get("intent") == "waiver_player_compare":
        evidence = prefetched
        # An unspecified overall hold is not a weekly start/sit recommendation.
        kind = "ros" if evidence.get("horizon") == "ros" else (
            "weekly" if evidence.get("horizon") == "weekly" else "overall_hold"
        )
    else:
        return None
    a, b = evidence.get("player_a") or {}, evidence.get("player_b") or {}
    if not (isinstance(a, dict) and isinstance(b, dict)):
        return None
    names = [a.get("name"), b.get("name")]
    if not all(isinstance(n, str) and n.strip() for n in names):
        return None
    if names[0].casefold() == names[1].casefold():
        return None
    return kind, (a, b)


def choice_instruction(prefetched):
    """Small, machine-checkable receipt from Claude's *existing* paid call."""
    if os.getenv("FANTASY_JOURNAL", "1").strip().lower() in {"0", "false", "no", "off"}:
        return ""
    pair = _comparison(prefetched)
    if pair is None:
        return ""
    _, (a, b) = pair
    names = [a["name"], b["name"]]
    return (
        "\n\nFor the offline recommendation audit, finish your response with exactly one "
        'line: FC_CHOICE_JSON: {"choice": "FULL_NAME"}. '
        "FULL_NAME must be exactly one of " + json.dumps(names, ensure_ascii=False) +
        '. If you cannot choose, use {"choice": null}. '
        "This is an audit receipt, not additional fantasy analysis."
    )


def extract_choice(response_text, prefetched):
    """Return (visible_answer, valid_choice). Never guess from prose."""
    match = _RECEIPT.search(response_text)
    visible = response_text[:match.start()].rstrip() if match else response_text
    pair = _comparison(prefetched)
    if not match or pair is None:
        return visible, None
    try:
        payload = json.loads(match.group(1))
    except (ValueError, TypeError):
        return visible, None
    if not isinstance(payload, dict) or not isinstance(payload.get("choice"), str):
        return visible, None
    choices = {p["name"].casefold(): p["name"] for p in pair[1]}
    return visible, choices.get(payload["choice"].strip().casefold())


def _safe_number(value):
    try:
        val = float(value)
        if val != val or abs(val) == float("inf"):
            return None
        return val
    except (TypeError, ValueError):
        return None


def create_record(*, season, week, question, answer, prefetched, turn_context,
                  league_scoring, choice, api_calls, input_tokens, output_tokens,
                  recorded_at=None):
    """Build a small immutable decision-time record, not the entire evidence blob."""
    pair = _comparison(prefetched)
    players = []
    if pair:
        for p in pair[1]:
            players.append({
                "name": p["name"],
                "position": p.get("position"),
                "espn_projection": _safe_number(p.get("espn_projection")),
                "fantasypros_projection": _safe_number(p.get("fantasypros_projection")),
                "weekly_score_advisory": _safe_number(p.get("weekly_score_advisory")),
            })
    label = pair[0] if pair else (prefetched or {}).get("intent") or turn_context.get("effective_intent")
    score_points = (league_scoring or {}).get("reception_points")
    record = {
        "schema_version": 1,
        "record_id": uuid4().hex,
        "recorded_at_utc": (recorded_at or datetime.now(timezone.utc)).isoformat(),
        "season": int(season), "week": int(week),
        "type": label,
        "question": question,
        "answer": answer,
        "candidate_players": players,
        "claude_choice": choice if pair else None,
        "choice_verification": "receipt" if choice else "unverified",
        "scoring": {
            "label": (league_scoring or {}).get("scoring"),
            "reception_points": _safe_number(score_points),
        },
        "usage": {"api_calls": api_calls, "input_tokens": input_tokens, "output_tokens": output_tokens},
        "evaluation_scope": "weekly_head_to_head" if pair and pair[0] == "weekly" else "not_weekly_scored",
    }
    return record


def save_record(record, root=None):
    """Atomic write; never overwrite an earlier decision or an evaluation."""
    if os.getenv("FANTASY_JOURNAL", "1").strip().lower() in {"0", "false", "no", "off"}:
        return None
    directory = Path(root) if root is not None else JOURNAL_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'{record["season"]}_week{record["week"]}_{record["record_id"]}.json'
    temp = path.with_suffix(".tmp")
    try:
        with temp.open("x", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        temp.replace(path)
    finally:
        if temp.exists():
            temp.unlink()
    return path
