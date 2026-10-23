"""Deterministic front-door route for NFL-wide leaderboard questions.

Call this from the HTTP chat adapter *before* any Claude conversation routing.
An NFL-wide statistical question should never end up at a generic news-only
lookup merely because of stale dialog state or missed intent classification.
"""
from __future__ import annotations

from nfl_leaderboards import format_leaderboard_answer, parse_leaderboard_request
from nfl_source_lookup import verified_leaderboard

GUARD_VERSION = "v7.26"


def maybe_answer_nfl_leaderboard(question: str, *, season: int = 2026, lookup=None) -> dict | None:
    """Return a compatible /api/chat response or None for non-leaderboard queries.

    The caller owns conversation history. Never route statistical questions to
    RSS news; it cannot establish a leaderboard. No Claude calls are made.
    """
    parsed = parse_leaderboard_request(question)
    if parsed is None:
        return None
    result = (lookup or verified_leaderboard)(question, season=season)
    answer = format_leaderboard_answer(result)
    if result.get('status') != 'available':
        attempted = result.get('attempted_sources') or []
        if attempted:
            answer += '\n\n**Structured sources attempted:** ' + ', '.join(attempted) + '.'
        errors = result.get('source_errors') or {}
        details = [f'{name}: {reason}' for name, reason in errors.items() if reason]
        if details:
            answer += '\n\n' + '\n'.join(details)
    return {'answer': answer, 'api_calls': 0, 'input_tokens': 0,
            'output_tokens': 0, 'prefetched': True,
            'intent': 'nfl_leaderboard', 'leaderboard_status': result.get('status'),
            'leaderboard_stat': parsed['metric'],
            'sources_attempted': result.get('attempted_sources') or []}
