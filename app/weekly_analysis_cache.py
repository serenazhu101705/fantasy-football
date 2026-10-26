"""Explicit-request-only, per-season/week/player Claude report cache.

Uses durable Upstash Redis REST if configured; otherwise local SQLite.
On Render Free, use Upstash: local disk disappears after instance restarts.
No ESPN cookies or Claude prompts are stored here, just the finished report.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
from threading import RLock
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

_LOCK = RLock()


def _key(season: int, week: int, player_id: int, league_id: str) -> str:
    if not (2000 <= int(season) <= 2200 and 1 <= int(week) <= 18 and int(player_id) > 0):
        raise ValueError("Invalid player analysis cache key")
    import hashlib
    league_hash = hashlib.sha256(str(league_id).encode()).hexdigest()[:12]
    return f"fantasy-coach:analyst:v2:{league_hash}:{int(season)}:{int(week)}:{int(player_id)}"


def _redis_enabled():
    return bool(os.getenv('UPSTASH_REDIS_REST_URL') or os.getenv('UPSTASH_REDIS_REST_TOKEN'))


def _redis(command):
    url = os.getenv('UPSTASH_REDIS_REST_URL', '').strip().rstrip('/')
    token = os.getenv('UPSTASH_REDIS_REST_TOKEN', '').strip()
    if not url.startswith('https://') or not token:
        raise RuntimeError('Configure both UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN')
    req = Request(url, data=json.dumps(command).encode(), method='POST', headers={
        'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'})
    try:
        with urlopen(req, timeout=8) as response:
            payload = json.load(response)
    except (OSError, HTTPError, URLError, TimeoutError, ValueError):
        raise RuntimeError('Persistent analysis cache unavailable; no Claude call was made') from None
    if not isinstance(payload, dict) or payload.get('error'):
        raise RuntimeError('Persistent analysis cache unavailable; no Claude call was made')
    return payload.get('result')


def _sqlite_path():
    return Path(os.getenv('FANTASY_ANALYSIS_CACHE_DB', 'data/player_analysis_cache.sqlite3'))


def _connect():
    path = _sqlite_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15)
    db.execute('CREATE TABLE IF NOT EXISTS player_analysis_cache (cache_key TEXT PRIMARY KEY, season INTEGER NOT NULL, week INTEGER NOT NULL, report TEXT NOT NULL)')
    return db


def get_report(season: int, week: int, player_id: int, league_id: str = 'default'):
    key = _key(season, week, player_id, league_id)
    if _redis_enabled():
        raw = _redis(['GET', key])
    else:
        with _LOCK, _connect() as db:
            # Old weeks never get served and are discarded from local storage.
            db.execute('DELETE FROM player_analysis_cache WHERE season != ? OR week != ?', (season, week))
            row = db.execute('SELECT report FROM player_analysis_cache WHERE cache_key=?', (key,)).fetchone()
            raw = row[0] if row else None
    if raw is None:
        return None
    try:
        report = json.loads(raw)
        if not isinstance(report, dict) or not isinstance(report.get('analysis'), str):
            raise ValueError('Invalid stored report')
    except (ValueError, TypeError):
        raise RuntimeError('Saved analyst report unreadable; no Claude call was made') from None
    return {**report, 'cached': True, 'input_tokens': 0, 'output_tokens': 0,
            'note': f'Saved Week {week} analysis · no Claude charge'}


def save_report(season: int, week: int, player_id: int, report: dict, league_id: str = 'default'):
    key = _key(season, week, player_id, league_id)
    value = json.dumps({'analysis': report['analysis'], 'source': report.get('source', 'Claude')})
    if _redis_enabled():
        # Expire stored entries automatically. Weekly namespace prevents reuse next week.
        _redis(['SET', key, value, 'EX', 1209600])
    else:
        with _LOCK, _connect() as db:
            db.execute('DELETE FROM player_analysis_cache WHERE season != ? OR week != ?', (season, week))
            db.execute('INSERT OR REPLACE INTO player_analysis_cache (cache_key,season,week,report) VALUES (?,?,?,?)', (key, season, week, value))
