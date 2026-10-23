"""Read-only, low-cost browsing views over data already loaded from ESPN.

The paginated browser never changes the smaller waiver shortlist passed to
Claude. It can make one on-demand ESPN-only pool read, cached for the current
loaded league session, and does not make FantasyPros or Claude calls.
"""
from __future__ import annotations

import math
import re

SUPPORTED_POSITIONS = {"ALL", "QB", "RB", "WR", "TE", "K", "D/ST"}


def _number(value):
    try:
        return float(value) if value is not None else -1.0
    except (ValueError, TypeError):
        return -1.0


def _normalized_name(name):
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def browse_pool(context, *, fetcher=None, max_size=400):
    """Load up to max_size ESPN free agents once; merge existing enrichment.

    If ESPN rejects a large size, reuse the smaller enriched pool and clearly
    disclose that limitation instead of presenting it as an exhaustive list.
    """
    if "_browser_free_agents" in context and fetcher is None:
        return context["_browser_free_agents"], context.get("_browser_scope", "cached_espn_pool")
    loaded = context.get("free_agents") or context.get("waiver_candidates") or []
    source = "loaded_espn_free_agents" if "free_agents" in context else "enriched_waiver_shortlist_only"
    league = context.get("league")
    if league is not None:
        try:
            if fetcher is None:
                from espn_client import get_free_agents
                fetcher = get_free_agents
            fetched = fetcher(league, size=max_size, week=context.get("current_week"))
            if isinstance(fetched, list):
                enriched = {str(p.get("espn_id")):p for p in loaded if p.get("espn_id") is not None}
                loaded = [{**row, **enriched.get(str(row.get("espn_id")), {})} for row in fetched]
                source = "on_demand_espn_pool"
        except Exception:
            source = "espn_browse_fallback_to_loaded_pool"
    if fetcher is None or league is not None:
        # API reads hold the engine lock; a second pagination click shares this.
        context["_browser_free_agents"] = loaded
        context["_browser_scope"] = source
    return loaded, source


def waiver_browser(context, *, position="ALL", page=1, page_size=10):
    """A genuine page of the ESPN free-agent set, not a top-5 repeat.

    The app caches one expanded ESPN browse request when possible, with no
    paid Claude or FantasyPros requests. Its size can be provider-limited.
    """
    requested = (position or "ALL").upper().strip()
    if requested not in SUPPORTED_POSITIONS:
        raise ValueError("Unsupported waiver position")
    if not isinstance(page, int) or page < 1 or not isinstance(page_size, int) or not 1 <= page_size <= 50:
        raise ValueError("Invalid pagination")

    source, scope = browse_pool(context)
    roster = context.get("roster") or []
    owned_ids = {str(p.get("espn_id")) for p in roster if p.get("espn_id") is not None}
    owned_names = {_normalized_name(p.get("name")) for p in roster}
    filtered = []
    seen = set()
    for p in source:
        pid = p.get("espn_id")
        name = _normalized_name(p.get("name"))
        key = str(pid) if pid is not None else name
        if (not name or key in seen or p.get("rostered") is True
                or (pid is not None and str(pid) in owned_ids) or name in owned_names):
            continue
        seen.add(key)
        if requested != "ALL" and str(p.get("position") or "").upper() != requested:
            continue
        filtered.append(p)

    # Position-prescreen value can be unavailable for K/DST: projections still
    # provide an ordering. A stable name tie-breaker ensures repeatable pages.
    filtered.sort(key=lambda p: (
        -_number(p.get("waiver_prescreen_score")),
        -_number(p.get("espn_projection")),
        str(p.get("name") or "").lower(),
    ))
    total = len(filtered)
    total_pages = max(1, math.ceil(total / page_size))
    selected = filtered[(page - 1) * page_size:page * page_size]
    pool = [{
        "espn_id": p.get("espn_id"),
        "name": p.get("name"),
        "position": p.get("position"),
        "team": p.get("team") or p.get("nfl_team"),
        "opponent": p.get("opponent"),
        "espn_projection": p.get("espn_projected_points") if p.get("espn_projected_points") is not None else p.get("espn_projection"),
        "fantasypros_projection": p.get("fp_points") if p.get("fp_points") is not None else p.get("fantasypros_projection"),
        "injury_status": p.get("injury_status"),
        "on_bye_week": p.get("on_bye_week") or p.get("espn_on_bye"),
        "percent_owned": p.get("percent_owned"),
    } for p in selected]
    from waiver_explanations import browser_explanations
    analysis = browser_explanations(context, selected)
    return {
        "requested_position": requested,
        "candidate_pool": pool,
        "candidate_explanations": analysis,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "scope": scope,
        "loaded_pool_size": len(source),
        "note": ("Browsing up to 400 ESPN free agents returned by the provider, "
                 "with existing enriched projections for those already loaded. "
                 "ESPN may return fewer, and this isn't guaranteed to include every NFL player. "
                 "The deterministic advisor keeps a separate small shortlist. "
                 "Provider fallback: loaded candidates only." if scope.endswith("fallback_to_loaded_pool")
                 else "Browsing ESPN free agents (up to 400, subject to ESPN's available pool). "
                      "Additional players may have only ESPN projections; no extra FantasyPros or Claude calls. "
                      "The deterministic advisor uses a separate small shortlist."),
    }
