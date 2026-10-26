"""Guarded incremental installer. Run from the fantasy_football repository root.

Does NOT overwrite existing player_analysis.py or any Auto Swap/email code.
Re-run safe; aborts on unrecognized frontend/backend shapes.
"""
from pathlib import Path
import sys
root = Path.cwd()
api = root / 'app/api.py'
web = root / 'web/src/App.tsx'
module = root / 'app/weekly_analysis_cache.py'
if not api.is_file() or not web.is_file() or not module.is_file():
    sys.exit('Missing files. Unzip the complete patch into fantasy_football/ and run from there.')
a = api.read_text()
w = web.read_text()
if 'def get_saved_player_analysis(' in a and 'Saved Week' in w:
    print('Already installed. No files changed.')
    sys.exit(0)
if 'def get_saved_player_analysis(' in a or 'Saved Week' in w:
    sys.exit('Partial install detected. No files changed; inspect git diff.')
api_anchor = '    @api.post("/api/players/{player_id}/analysis")\n'
api_fn = '''    @api.get("/api/players/{player_id}/analysis")
    def get_saved_player_analysis(player_id: int):
        """Read-only: never spends tokens or generates an analysis."""
        from weekly_analysis_cache import get_report
        with host.lock:
            engine = host.ensure_ready()
            league, _ = _espn_context(engine)
            current_week = int(engine.APP_CONTEXT.get('current_week') or getattr(league, 'current_week', 1))
            season = int(getattr(league, 'year', 2026))
            league_id = str(getattr(league, 'league_id', 'default'))
        try:
            cached = get_report(season, current_week, player_id, league_id)
            return {'exists': cached is not None, 'report': cached, 'week': current_week}
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f'Saved analysis unavailable ({type(exc).__name__}).') from None

'''
if a.count(api_anchor) != 1 or a.count('            return summarize_player(profile, client, model') != 1:
    sys.exit('Backend API differs from expected version; no files changed.')
a = a.replace(api_anchor, api_fn + api_anchor, 1)
old = '            return summarize_player(profile, client, model, league=league)' if '            return summarize_player(profile, client, model, league=league)' in a else '            return summarize_player(profile, client, model)'
if a.count(old) != 1:
    sys.exit('Analysis call does not match expected shape; no files changed.')
new = '''            from weekly_analysis_cache import get_report, save_report
            current_week = int(engine.APP_CONTEXT.get('current_week') or getattr(league, 'current_week', 1))
            season = int(getattr(league, 'year', 2026))
            league_id = str(getattr(league, 'league_id', 'default'))
            # Never spend Claude tokens if a player already has a report this week.
            cached = get_report(season, current_week, player_id, league_id)
            if cached is not None:
                return cached
            result = summarize_player(profile, client, model, league=league)
            save_report(season, current_week, player_id, result, league_id)
            return result'''
# Keep older versions compatible with 3-argument summarize_player.
if old.endswith('model)'):
    new = new.replace('summarize_player(profile, client, model, league=league)', 'summarize_player(profile, client, model)')
a = a.replace(old, new, 1)
# Important: load saved analysis when drawer opens; no paid POST until button click.
needle = '    return ()=>{cancelled=true}\n  },[selected.id,week])'
if w.count(needle) != 1:
    sys.exit('Frontend player drawer differs from expected version; no files changed.')
replacement = '''    request<{exists:boolean,report:PlayerAnalysis|null,week:number}>(`/api/players/${selected.id}/analysis`)
      .then(state=>{if(!cancelled && state.exists && state.report)setAnalysis(state.report)})
      .catch(err=>{if(!cancelled)setAnalysisError(err instanceof Error?err.message:'Saved report unavailable')})
    return ()=>{cancelled=true}
  },[selected.id,week])'''
w = w.replace(needle, replacement, 1)
# A useful indicator that communicates expiry on weekly rollover.
old_usage = "{analysis.cached?'Cached · no new API charge':`${(analysis.input_tokens+analysis.output_tokens).toLocaleString()} tokens · 1 Claude call`}"
new_usage = "{analysis.cached?'Saved Week analysis · no new Claude charge':`${(analysis.input_tokens+analysis.output_tokens).toLocaleString()} tokens · 1 Claude call · saved until next fantasy week`}"
if w.count(old_usage) != 1:
    sys.exit('Frontend summary footer differs from expected version; no files changed.')
w = w.replace(old_usage,new_usage,1)
# Targeted, reversible backups; validate both files before modifying either.
compile(a,str(api),'exec')
for original, output in ((api,a),(web,w)):
    backup = original.with_name(original.name + '.before_weekly_cache')
    if not backup.exists():
        backup.write_text(original.read_text())
    original.write_text(output)
    print('Updated', original.relative_to(root))
print('Saved reports load without Claude; first explicit click generates once per player/week.')
