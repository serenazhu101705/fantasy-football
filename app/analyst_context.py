"""Ground player analysis in verified game status, avoiding BYE/DNP mixups.

No guessing snap percentages, game outcomes or injury reasons. This module is
only called when the user explicitly requests a paid player breakdown.
"""
from __future__ import annotations


def _team(value):
    try:
        from matchup_live import _team as normalized
        return normalized(value)
    except ImportError:
        return str(value or '').strip().upper()


def _game(league, week, team, status_loader=None):
    if league is None or not team or not isinstance(week, int) or not 1 <= week <= 18:
        return {'state': 'UNKNOWN', 'reason': 'schedule unavailable'}
    if status_loader is None:
        from matchup_live import nfl_game_statuses
        status_loader = nfl_game_statuses
    try:
        status = status_loader(league, week)
    except Exception:
        return {'state': 'UNKNOWN', 'reason': 'schedule lookup failed'}
    games = status.get('games') or {}
    if status.get('status') != 'available' or not games:
        return {'state': 'UNKNOWN', 'reason': 'schedule unavailable'}
    normalized = _team(team)
    match = games.get(normalized)
    if match is None:
        return {'state': 'BYE', 'reason': 'verified no NFL game for team this week'}
    return {'state': match.get('state') or 'UNKNOWN',
            'opponent': match.get('opponent'), 'kickoff': match.get('kickoff')}


def analyst_context(profile, league=None, status_loader=None):
    """Create a source-labelled interpretation layer for one player profile.

    A blank fantasy game log is never proof of a bye. It is a bye only when
    the authoritative team schedule has games but omits the player's NFL team.
    """
    current = int(profile.get('week') or 0)
    team = profile.get('nfl_team')
    raw_games = profile.get('recent_games') or []
    historical = {g.get('week'): g for g in raw_games
                  if isinstance(g, dict) and isinstance(g.get('week'), int)}
    # Bounded lookback keeps one analysis request from fetching a full season.
    lookback = list(range(max(1, current - 5), current)) if current else []
    weeks = []
    for week in lookback:
        game = historical.get(week)
        schedule = _game(league, week, team, status_loader)
        if schedule['state'] == 'BYE':
            label = 'BYE'
        elif game and game.get('fantasy_points') is not None:
            label = 'PLAYED' if schedule['state'] == 'FINAL' else 'RECORDED_SCORE_UNVERIFIED_FINAL'
        elif schedule['state'] == 'FINAL':
            label = 'NO_PLAYER_SCORE_IN_COMPLETED_TEAM_GAME'  # may be DNP or missing data
        elif schedule['state'] in ('LIVE', 'SCHEDULED'):
            label = schedule['state']
        else:
            label = 'UNKNOWN'
        weeks.append({'week': week, 'classification': label,
                      'team_game': schedule, 'fantasy_points': game.get('fantasy_points') if game else None})
    next_game = _game(league, current, team, status_loader)
    # ESPN may not advance fantasy current_week immediately after a final.
    # Provide next week's official schedule only if this week's game is final.
    if next_game['state'] == 'FINAL' and current < 18:
        next_game = {'week': current + 1, **_game(league, current + 1, team, status_loader)}
    else:
        next_game = {'week': current, **next_game}
    return {'last_five_weeks': weeks, 'upcoming_game': next_game,
            'interpretation_rules': [
                'A verified BYE means no NFL game, not zero production or missing data.',
                'Absent fantasy points during a FINAL team game are unknown: never assume injury or a DNP.',
                'Only calculate games-played consistency from verified scored games; exclude bye weeks.',
                'If upcoming game already FINAL, there is no upcoming projection for that week.',
                'No snap/route/target share estimate unless input includes verified usage metrics.',
            ]}
