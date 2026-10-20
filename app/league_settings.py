"""Small, authoritative league-context adapter for ESPN fantasy settings.

The rest of the app should consume this module instead of hard-coding PPR.
The espn-api Settings object exposes ``scoring_format`` as a list of scoring
rules. We primarily need points per reception so FantasyPros/FantasyCalc and
our nflverse actual-production summaries use the same reception format as the
ESPN league.
"""

from __future__ import annotations

import math
import re


def _safe_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _norm(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _find_reception_points(scoring_format):
    """Return ESPN points awarded per reception, if identifiable.

    espn-api exposes scoring_format as rows like:
      {"abbr": "RETD", "label": "TD Reception", "id": 43, "points": 6.0}

    Reception itself is commonly ESPN stat id 53 (and older mappings may expose
    41), but label/abbr matching is preferred so we never confuse receiving TDs
    with catches.
    """
    if isinstance(scoring_format, str):
        text = _norm(scoring_format)
        if "half" in text and "ppr" in text:
            return 0.5
        if "ppr" in text:
            return 1.0
        if "standard" in text or text == "std":
            return 0.0
        return None

    if isinstance(scoring_format, dict):
        rows = []
        for key, value in scoring_format.items():
            if isinstance(value, dict):
                row = dict(value)
                row.setdefault("id", key)
                rows.append(row)
            else:
                rows.append({"id": key, "points": value})
    elif isinstance(scoring_format, (list, tuple)):
        rows = list(scoring_format)
    else:
        rows = []

    # First pass: strong semantic match.
    for row in rows:
        if not isinstance(row, dict):
            continue
        abbr = str(row.get("abbr") or "").upper().strip()
        label = _norm(row.get("label"))
        if "touchdown" in label or label.startswith("td ") or abbr in {"RETD", "RECTD"}:
            continue
        semantic_match = (
            abbr in {"REC", "RECP", "RECS"}
            or label in {"reception", "receptions", "receiving reception", "receiving receptions"}
            or ("reception" in label and "yard" not in label)
        )
        if semantic_match:
            points = _safe_float(row.get("points"))
            if points is not None:
                return points

    # Second pass: known ESPN reception stat ids, but only if no contradictory
    # touchdown-style label/abbr is present.
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            stat_id = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        if stat_id not in {41, 53}:
            continue
        abbr = str(row.get("abbr") or "").upper().strip()
        label = _norm(row.get("label"))
        if "touchdown" in label or abbr in {"RETD", "RECTD"}:
            continue
        points = _safe_float(row.get("points"))
        if points is not None:
            return points

    return None


def _classify_reception_scoring(points):
    if points is None:
        return "UNKNOWN", "PPR", True
    if abs(points - 0.0) < 1e-9:
        return "STANDARD", "STD", False
    if abs(points - 0.5) < 1e-9:
        return "HALF_PPR", "HALF", False
    if abs(points - 1.0) < 1e-9:
        return "PPR", "PPR", False

    # FantasyPros exposes STD/HALF/PPR only. Pick the nearest reception format
    # but mark it approximate so Claude never presents it as exact.
    choices = [(0.0, "STD"), (0.5, "HALF"), (1.0, "PPR")]
    _, fp = min(choices, key=lambda pair: abs(points - pair[0]))
    return "CUSTOM", fp, True


def get_league_scoring_context(league):
    settings = getattr(league, "settings", None)
    scoring_format = getattr(settings, "scoring_format", None) if settings is not None else None
    reception_points = _find_reception_points(scoring_format)
    label, fantasypros_scoring, approximate = _classify_reception_scoring(reception_points)

    team_count = getattr(settings, "team_count", None) if settings is not None else None
    if team_count is None:
        try:
            team_count = len(getattr(league, "teams", []) or [])
        except Exception:
            team_count = None

    faab = getattr(settings, "faab", None) if settings is not None else None
    acquisition_budget = getattr(settings, "acquisition_budget", None) if settings is not None else None

    result = {
        "source": "ESPN league.settings.scoring_format",
        "scoring_label": label,
        "reception_points": reception_points,
        "fantasypros_scoring": fantasypros_scoring,
        "fantasypros_scoring_approximate": approximate,
        "team_count": team_count,
        "faab": faab,
        "acquisition_budget": acquisition_budget,
    }

    # This app can exactly adapt the catch component of nflverse scoring. If the
    # ESPN league has other custom scoring rules, ESPN's own projections/points
    # remain authoritative and our nflverse-derived fantasy points are a useful
    # role/production signal rather than an exact replay of every custom rule.
    result["nflverse_points_scope"] = (
        "reception-adjusted standard NFL fantasy scoring"
        if reception_points is not None
        else "PPR fallback because reception scoring could not be identified"
    )
    return result


def reception_points_or_default(context, default=1.0):
    value = (context or {}).get("reception_points")
    value = _safe_float(value)
    return default if value is None else value
