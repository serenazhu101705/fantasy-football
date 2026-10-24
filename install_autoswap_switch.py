"""Apply the Auto Swap control to an existing Fantasy Coach checkout.

Run from repo root: python3 install_autoswap_switch.py
Edits existing files in place, with matching anchors and idempotence. Keeps
user modifications intact instead of replacing their complete App.tsx.
"""
from pathlib import Path

ROOT = Path.cwd()


def edit(rel: str, replacements: list[tuple[str, str]], marker: str):
    target = ROOT / rel
    if not target.is_file():
        raise SystemExit(f"Missing {rel}; run from fantasy_football repository root.")
    current = target.read_text()
    if marker in current:
        print(f"Already installed: {rel}")
        return
    output = current
    for old, new in replacements:
        matches = output.count(old)
        if matches != 1:
            raise SystemExit(f"Cannot safely patch {rel}: expected anchor once, got {matches}: {old[:85]!r}. No changes written to this file.")
        output = output.replace(old, new, 1)
    target.write_text(output)
    print(f"Updated {rel}")


def backend():
    api_source = (ROOT / "app/api.py").read_text()
    if 'allow_headers=["Content-Type", "X-Fantasy-Session", "X-Fantasy-Mobile-Key"],' in api_source:
        old_headers = 'allow_headers=["Content-Type", "X-Fantasy-Session", "X-Fantasy-Mobile-Key"],'
        new_headers = 'allow_headers=["Content-Type", "X-Fantasy-Session", "X-Fantasy-Mobile-Key", "X-Fantasy-Automation"],'
    else:
        old_headers = 'allow_headers=["Content-Type", "X-Fantasy-Session"],'
        new_headers = 'allow_headers=["Content-Type", "X-Fantasy-Session", "X-Fantasy-Automation"],'
    edit("app/api.py", [
        ('class ConfirmLineupRequest(BaseModel):', '''class AutoSwapToggleRequest(BaseModel):
    enabled: bool


class ConfirmLineupRequest(BaseModel):'''),
        ('    @api.post("/api/sessions", status_code=201)', '''    # Owner-only Auto Swap switch. Cloud password middleware authenticates
    # requests. The custom header prevents cross-site form submissions.
    @api.get("/api/automation/auto-swap")
    def get_autoswap_setting():
        from auto_swap_control import AutoSwapControlError, website_status
        try:
            return website_status()
        except AutoSwapControlError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None

    @api.post("/api/automation/auto-swap")
    def set_autoswap_setting(body: AutoSwapToggleRequest, request: Request):
        from urllib.parse import urlsplit
        from auto_swap_control import AutoSwapControlError, website_set
        if request.headers.get("X-Fantasy-Automation") != "owner":
            raise HTTPException(status_code=403, detail="Missing owner confirmation header.")
        origin = request.headers.get("origin", "")
        if origin:
            parts = urlsplit(origin)
            if parts.scheme not in ("http", "https") or parts.netloc.lower() != request.headers.get("host", "").lower():
                raise HTTPException(status_code=403, detail="Cross-site automation changes are not allowed.")
        try:
            return website_set(body.enabled)
        except AutoSwapControlError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None

    @api.post("/api/sessions", status_code=201)'''),
        (old_headers, new_headers),
    ], '@api.get("/api/automation/auto-swap")')


def ui():
    edit("web/src/App.tsx", [
        ("type Message = { id: number, role: 'user' | 'assistant', text: string, usage?: CoachReply }", '''type Message = { id: number, role: 'user' | 'assistant', text: string, usage?: CoachReply }
type AutoSwapStatus = { enabled: boolean, configurable: boolean, note: string }'''),
        ("  const [lineupPreview,setLineupPreview] = useState<LineupPreview | null>(null)", '''  const [lineupPreview,setLineupPreview] = useState<LineupPreview | null>(null)
  const [autoSwapStatus,setAutoSwapStatus] = useState<AutoSwapStatus | null>(null)
  const [autoSwapBusy,setAutoSwapBusy] = useState(false)
  const [autoSwapLoading,setAutoSwapLoading] = useState(false)
  const [autoSwapError,setAutoSwapError] = useState('')'''),
        ('  async function submitEmail(event:FormEvent){', '''  // A server-backed switch: no optimistic change, no ephemeral localStorage.
  useEffect(() => {
    if (tab !== 'lineup') return
    let cancelled = false
    setAutoSwapLoading(true)
    setAutoSwapError('')
    request<AutoSwapStatus>('/api/automation/auto-swap')
      .then(status => { if (!cancelled) setAutoSwapStatus(status) })
      .catch(err => { if (!cancelled) { setAutoSwapStatus(null); setAutoSwapError(err instanceof Error ? err.message : 'Could not read Auto Swap setting') } })
      .finally(() => { if (!cancelled) setAutoSwapLoading(false) })
    return () => { cancelled = true }
  }, [tab])

  async function changeAutoSwap() {
    if (!autoSwapStatus?.configurable || autoSwapBusy) return
    const next = !autoSwapStatus.enabled
    if (next && !window.confirm('Enable automatic ESPN lineup changes? GitHub Actions may replace OUT, IR, suspended or bye-week starters before kickoff, without asking again. Healthy starters, waivers and locked players are not meant to change.')) return
    setAutoSwapBusy(true)
    setAutoSwapError('')
    try {
      const status = await request<AutoSwapStatus>('/api/automation/auto-swap', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Fantasy-Automation': 'owner' },
        body: JSON.stringify({ enabled: next }),
      })
      setAutoSwapStatus(status)
    } catch (err) {
      setAutoSwapError(err instanceof Error ? err.message : 'Could not update Auto Swap; setting unchanged')
    } finally {
      setAutoSwapBusy(false)
    }
  }

  async function submitEmail(event:FormEvent){'''),
        ("        {tab==='lineup' && <section className=\"panel full-panel\"><div className=\"panel-head\"><div><span className=\"panel-kicker\">TRANSPARENT OPTIMIZER</span>", '''        {tab==='lineup' && <section className="panel full-panel autoswap-section" aria-label="Automatic lineup substitutions">
          <div className="autoswap-control">
            <div className="autoswap-copy">
              <span className="panel-kicker">AUTOMATION · OWNER ONLY</span>
              <h2>Auto Swap</h2>
              <p>Automatically replace verified OUT, IR, suspended or bye-week starters with eligible bench players before kickoff.</p>
            </div>
            <button type="button" role="switch" aria-label="Auto Swap" aria-checked={autoSwapStatus?.enabled ?? false}
              className={`autoswap-switch ${autoSwapStatus?.enabled ? 'is-on' : ''}`}
              disabled={autoSwapLoading || autoSwapBusy || !autoSwapStatus?.configurable}
              onClick={()=>void changeAutoSwap()}>
              <span className="autoswap-knob"/>
            </button>
          </div>
          <div className="autoswap-foot">
            <span className={`autoswap-indicator ${autoSwapStatus?.enabled ? 'enabled' : ''}`}>
              {autoSwapBusy ? 'Saving…' : autoSwapLoading ? 'Checking…' : autoSwapStatus?.enabled ? 'ON · Scheduled ESPN changes authorized' : 'OFF · No scheduled auto-swaps authorized'}
            </span>
            <p>{autoSwapStatus?.note || 'Auto Swap status is unavailable.'} Checks run on the existing GitHub schedule, not continuously. Turning OFF prevents future checks, but cannot undo an ESPN change already made.</p>
            {autoSwapError && <p className="detail-error" role="alert">{autoSwapError}</p>}
          </div>
        </section>}

        {tab==='lineup' && <section className="panel full-panel"><div className="panel-head"><div><span className="panel-kicker">TRANSPARENT OPTIMIZER</span>'''),
        ("          ['auto_swap','Automatically replace OUT / IR / suspended / bye starters on ESPN (advanced)'],\n", ''),
    ], 'type AutoSwapStatus =')
    edit("web/src/styles.css", [
        ("/* v7.14: aligned lineup comparisons, ESPN NFL game states, and player detail drawer */", '''/* Owner-controlled Auto Swap setting, persisted in GitHub Actions variables. */
.autoswap-section{padding:20px 22px!important}
.autoswap-control{display:flex;justify-content:space-between;align-items:center;gap:24px}
.autoswap-copy{flex:1;min-width:0}.autoswap-copy h2{font-size:21px;margin:7px 0 6px}
.autoswap-copy p,.autoswap-foot p{font-size:12px;color:#a6b8c7;line-height:1.6;margin:0}
.autoswap-switch{flex:none;width:62px;height:34px;border-radius:100px;padding:4px;background:#374657;border:1px solid #6a7c8b;display:flex;align-items:center;cursor:pointer;transition:background .2s,border-color .2s}
.autoswap-switch.is-on{background:#b7f779;border-color:#b7f779}
.autoswap-switch:disabled{opacity:.45;cursor:not-allowed}
.autoswap-knob{width:24px;height:24px;border-radius:50%;background:#eff6fa;transition:transform .2s;box-shadow:0 1px 5px #0005}
.autoswap-switch.is-on .autoswap-knob{transform:translateX(27px);background:#203221}
.autoswap-switch:focus-visible{outline:3px solid #d4fbaf;outline-offset:4px}
.autoswap-foot{border-top:1px solid #314154;padding-top:13px;margin-top:16px;display:flex;flex-direction:column;gap:8px}
.autoswap-indicator{font-size:11px;font-weight:800;color:#a9b6c4;letter-spacing:.25px}
.autoswap-indicator.enabled{color:#b7f779}
@media(max-width:520px){.autoswap-section{padding:17px 14px!important}.autoswap-control{gap:14px}.autoswap-copy h2{font-size:19px}}
/* v7.14: aligned lineup comparisons, ESPN NFL game states, and player detail drawer */'''),
    ], 'Owner-controlled Auto Swap setting')


def runner():
    edit(".github/workflows/fantasy-coach-emails.yml", [
        ("      ESPN_WRITES_ENABLED: '0'\n      ESPN_AUTO_SWAPS_ENABLED: '0'", '''      # A single durable Actions repository variable is the owner switch.
      # Default to zero even if the variable was never created.
      FANTASY_AUTO_SWAP_ENABLED: ${{ vars.FANTASY_AUTO_SWAP_ENABLED == '1' && '1' || '0' }}
      ESPN_WRITES_ENABLED: ${{ vars.FANTASY_AUTO_SWAP_ENABLED == '1' && '1' || '0' }}
      ESPN_AUTO_SWAPS_ENABLED: ${{ vars.FANTASY_AUTO_SWAP_ENABLED == '1' && '1' || '0' }}'''),
        ("      - name: Send due digest emails\n        env:\n          HOME:", "      - name: Send due digest emails\n        env:\n          GITHUB_TOKEN: ${{ github.token }}\n          HOME:"),
    ], 'A single durable Actions repository variable')
    edit("app/github_email_bootstrap.py", [
        ('    if os.getenv("ESPN_WRITES_ENABLED") == "1" or os.getenv("ESPN_AUTO_SWAPS_ENABLED") == "1":\n        raise RuntimeError("Automatic ESPN writes must be disabled for the email-only runner.")', '''    enabled = os.getenv("FANTASY_AUTO_SWAP_ENABLED", "0")
    if enabled not in ("0", "1"):
        raise RuntimeError("Auto Swap requires an explicit 0/1 setting.")
    if (os.getenv("ESPN_WRITES_ENABLED", "0") != enabled or
            os.getenv("ESPN_AUTO_SWAPS_ENABLED", "0") != enabled):
        raise RuntimeError("Auto Swap and ESPN write safety flags must agree.")'''),
        ('            return "existing"', '''            # Always honor the current owner switch, including turning it OFF.
            db.execute("UPDATE subscribers SET auto_swap=? WHERE email=?",
                       (int(enabled), email))
            return "existing"'''),
        ('            VALUES (?,1,?,?,?,1,1,1,1,0,?)""",',
         '            VALUES (?,1,?,?,?,1,1,1,1,?,?)""",'),
        ('             _token_hash(secrets.token_urlsafe(30)), now.isoformat()))',
         '             _token_hash(secrets.token_urlsafe(30)), int(enabled), now.isoformat()))'),
        ('    print(f"Personal Fantasy Coach email state: {status}. No automatic ESPN writes enabled.")',
         '    print(f"Personal Fantasy Coach email state: {status}. Owner Auto Swap preference synced.")'),
    ], 'Owner Auto Swap preference synced')
    edit("app/run_digest_jobs.py", [
        ('    if (authorized_auto and os.getenv("ESPN_WRITES_ENABLED") == "1"\n            and os.getenv("ESPN_AUTO_SWAPS_ENABLED") == "1"):', '''    # The Actions variable is checked twice, including right before the
    # transaction. An unavailable GitHub API fails closed (no ESPN write).
    from auto_swap_control import worker_is_enabled
    if authorized_auto and worker_is_enabled():'''),
        ('            if preview["changes"]:\n                response = submit_preview(league, team, restricted, preview["confirmation_token"], game_status=status)', '''            if preview["changes"] and worker_is_enabled():
                response = submit_preview(league, team, restricted, preview["confirmation_token"], game_status=status)'''),
    ], 'The Actions variable is checked twice')


if __name__ == '__main__':
    backend()
    runner()
    ui()
    print("\nDone. Review `git diff` before committing. No secrets were written to source.")
