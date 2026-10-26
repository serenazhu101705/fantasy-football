"""Idempotent guarded patch for existing Fantasy Coach player analysis."""
from pathlib import Path
import re

root = Path(__file__).resolve().parent
project = Path.cwd()
analysis = project / 'app/player_analysis.py'
api = project / 'app/api.py'
if not analysis.exists() or not api.exists():
    raise SystemExit('Run from fantasy_football repo root (need app/player_analysis.py and app/api.py).')
original = analysis.read_text()
api_text = api.read_text()
if 'def summarize_player(' not in original or 'def clear_analysis_cache(' not in original:
    raise SystemExit('Unrecognized player_analysis.py; no changes made.')
if 'summarize_player(profile, client, model' not in api_text:
    raise SystemExit('Unrecognized player API integration; no changes made.')

replacement = '''def summarize_player(profile, client, model, *, league=None):
    # The extra context comes from a verified team schedule, not Claude guessing.
    from analyst_context import analyst_context
    facts = _facts(profile)
    facts['schedule_interpretation'] = analyst_context(profile, league=league)
    body = json.dumps(facts, sort_keys=True, separators=(",", ":"), default=str)
    cache_key = sha256(f"analyst-v1:{model}:{body}".encode()).hexdigest()
    with _CACHE_LOCK:
        cached = _SUMMARY_CACHE.get(cache_key)
        if cached and monotonic() - cached[0] < _TTL:
            return {**cached[1], "cached": True, "input_tokens": 0, "output_tokens": 0,
                    "note": "Cached analyst report; no new Claude call."}
    prompt = (
        "You are an evidence-first fantasy football analyst writing for a full-PPR manager. "
        "Analyze instead of listing statistics. Use only the supplied factual input. "
        "Write 4 compact sections with headings: Season Outlook, Last Game Analysis, "
        "Next Game Preview, Fantasy Verdict. Aim for 220-320 words, with clear takeaways. "
        "SEASON: explain trajectory, consistency, workload evidence, and whether scoring "
        "seems sustainable; use recent completed games without counting BYE weeks as games. "
        "LAST GAME: explain why performance exceeded/fell short of projection and what "
        "matters for next week; avoid making unsupported causal claims. "
        "NEXT GAME: discuss verified opponent and projection if actually upcoming; "
        "otherwise say opponent/projection are unavailable. Provide a reasoned qualitative "
        "range of outcomes, not fabricated numerical predictions. For RB/WR, emphasize "
        "snap share, carries/routes, targets, red zone and backfield/receiver competition "
        "ONLY where verified inputs support them. Never invent expected snap share. "
        "VERDICT: concise start/flex/bench/watch recommendation, explaining confidence and "
        "major risk. Do not treat a bye week as zero performance, missed game, injury, or "
        "unavailable stats. No speculative injury reports, opponent defense rankings, "
        "future TDs, target shares or coaching decisions. If verified information is "
        "insufficient, explicitly state what evidence would change your take. "
        "Do not reproduce long stat lines. Use 2-4 key numbers at most per section. "
        "Input: " + body
    )
    params = {"model": model, "max_tokens": 1100,
              "messages": [{"role": "user", "content": prompt}]}
    if str(model).startswith("claude-sonnet-5-5"):
        params["thinking"] = {"type": "between_tools"}
    response = client.messages.create(**params)
    analysis = "".join(getattr(block, "text", "") for block in response.content
                       if getattr(block, "type", "") == "text").strip()
    if not analysis:
        stop_reason = str(getattr(response, "stop_reason", "unknown"))
        raise ValueError(f"Claude returned no visible player analysis (stop_reason={stop_reason}).")
    result = {"analysis": analysis, "source": "Claude analyst commentary grounded in ESPN and verified team schedule",
              "cached": False,
              "input_tokens": getattr(getattr(response, "usage", None), "input_tokens", 0),
              "output_tokens": getattr(getattr(response, "usage", None), "output_tokens", 0)}
    with _CACHE_LOCK:
        if len(_SUMMARY_CACHE) >= 128:
            _SUMMARY_CACHE.clear()
        _SUMMARY_CACHE[cache_key] = (monotonic(), result)
    return result


'''
pattern = r'def summarize_player\(.*?(?=def clear_analysis_cache\()'
if 'analyst-v1' not in original:
    new, n = re.subn(pattern, lambda _: replacement, original, count=1, flags=re.S)
    if n != 1:
        raise SystemExit('Failed to locate analysis function. No changes made.')
else:
    new = original
if 'summarize_player(profile, client, model, league=league)' not in api_text:
    newapi = api_text.replace('summarize_player(profile, client, model)',
                              'summarize_player(profile, client, model, league=league)')
else:
    newapi = api_text
destination = project / 'app/analyst_context.py'
if not (root / 'app/analyst_context.py').exists():
    raise SystemExit('Missing app/analyst_context.py. Extract the COMPLETE corrected ZIP into repo root.')
if destination.exists() and destination.read_text() != (root / 'app/analyst_context.py').read_text():
    raise SystemExit('Existing analyst_context.py differs; do not overwrite. Review the diff.')
# Do all validation before mutating any files.
compile(new, str(analysis), 'exec')
compile(newapi, str(api), 'exec')
context = (root / 'app/analyst_context.py').read_text()
compile(context, 'analyst_context.py', 'exec')
# Backup modified source files before changing anything.
if new != original:
    backup = analysis.with_suffix('.py.pre_analyst_backup')
    if not backup.exists():
        backup.write_text(original)
    analysis.write_text(new)
    print('Updated app/player_analysis.py')
if newapi != api_text:
    backup = api.with_suffix('.py.pre_analyst_backup')
    if not backup.exists():
        backup.write_text(api_text)
    api.write_text(newapi)
    print('Updated app/api.py')
destination = project / 'app/analyst_context.py'
if destination.exists() and destination.read_text() != context:
    raise SystemExit('Existing app/analyst_context.py differs. Refusing to overwrite it.')
# The source module arrives at the correct path from ZIP extraction. Leave it intact.
if not destination.exists():
    destination.write_text(context)
print('Verified app/analyst_context.py')
print('Done. See INSTALL.md for email dependency fix and tests.')
