import { useCallback, useEffect, useState } from 'react'
import { CheckCircle2, Globe2, LockKeyhole, Settings2, ShieldAlert, Smartphone, Wifi } from 'lucide-react'
import App from './App'
import { clearAPIConnection, connectionReady, isIOSApp, normalizeAPIURL, readAPIURL, readAPIToken, saveAPIConnection } from './mobileConnection'

export default function MobileApp() {
  const mobile = isIOSApp()
  const [connected, setConnected] = useState(() => !mobile || connectionReady())
  const [editing, setEditing] = useState(false)
  const [url, setURL] = useState(readAPIURL)
  const [token, setToken] = useState(readAPIToken)
  const [allowLANHTTP, setAllowLANHTTP] = useState(false)
  const [testing, setTesting] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const sync = useCallback(() => setConnected(connectionReady()), [])
  useEffect(() => {
    window.addEventListener('fantasycoach-mobile-connected', sync)
    return () => window.removeEventListener('fantasycoach-mobile-connected', sync)
  }, [sync])

  if (!mobile) return <App />
  const showSetup = !connected || editing

  async function testConnection() {
    setError(''); setSuccess(''); setTesting(true)
    try {
      const base = normalizeAPIURL(url, allowLANHTTP)
      const accessKey = token.trim()
      if (accessKey.length < 16) throw new Error('Enter the access key from FANTASY_MOBILE_ACCESS_KEY on your Mac.')
      const controller = new AbortController()
      const timeout = setTimeout(() => controller.abort(), 12000)
      let result: Response
      try {
        result = await fetch(base + '/api/health', {
          headers: { Accept: 'application/json', 'X-Fantasy-Mobile-Key': accessKey },
          signal: controller.signal,
        })
      } finally { clearTimeout(timeout) }
      if (result.status === 401 || result.status === 403) throw new Error('Access key rejected. Check FANTASY_MOBILE_ACCESS_KEY on your backend.')
      if (!result.ok) throw new Error(`The API returned HTTP ${result.status}.`)
      const health = await result.json().catch(() => ({})) as { status?: string }
      if (health.status !== 'ok') throw new Error('This address did not respond as the Fantasy Coach API.')
      saveAPIConnection(base, accessKey)
      setSuccess('Connected to Fantasy Coach.')
      setEditing(false)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Unable to reach the API.')
    } finally { setTesting(false) }
  }
  return <div className="ios-app-shell">
    {showSetup ? <main className="ios-connect-page">
      <div className="ios-connect-card">
        <div className="ios-connect-icon"><Smartphone size={32} /></div>
        <div className="ios-overline">FANTASY COACH FOR IPHONE</div>
        <h1>Connect your fantasy data</h1>
        <p>The iPhone app uses the same FastAPI backend as your web dashboard. ESPN and Claude credentials remain on the backend—not in this app.</p>
        <label htmlFor="ios-url"><Globe2 size={15}/> Backend URL</label>
        <input id="ios-url" type="url" autoCapitalize="none" spellCheck={false} autoCorrect="off" placeholder="https://your-private-api.example" value={url} onChange={e=>setURL(e.target.value)} />
        <label htmlFor="ios-token"><LockKeyhole size={15}/> Mobile API access key</label>
        <input id="ios-token" type="password" autoCapitalize="none" spellCheck={false} autoComplete="off" placeholder="Key from FANTASY_MOBILE_ACCESS_KEY" value={token} onChange={e=>setToken(e.target.value)} />
        <label className="ios-dev-checkbox"><input type="checkbox" checked={allowLANHTTP} onChange={e=>setAllowLANHTTP(e.target.checked)} /> Allow private-LAN HTTP for development only</label>
        {error && <div role="alert" className="ios-connect-error"><ShieldAlert size={15}/>{error}</div>}
        {success && <div className="ios-connect-success"><CheckCircle2 size={15}/>{success}</div>}
        <button className="ios-connect-button" type="button" disabled={testing} onClick={() => void testConnection()}>{testing ? 'Checking connection…' : 'Connect to Fantasy Coach'} <Wifi size={17}/></button>
        {connected && <button className="ios-disconnect-button" type="button" onClick={()=>setEditing(false)}>Cancel</button>}
        <p className="ios-connect-disclaimer">For your iPhone, use a private HTTPS endpoint whenever possible. GitHub Actions only runs email jobs; it does not host the Fantasy Coach API. This app cannot reach a backend that's powered off.</p>
      </div>
    </main> : <>
      <button type="button" className="ios-server-settings" aria-label="Change API connection" title="API connection" onClick={()=>setEditing(true)}><Settings2 size={17}/></button>
      <App />
    </>}
    {editing && connected && <button type="button" className="ios-disconnect" onClick={()=>{ clearAPIConnection(); setEditing(false) }}>Forget connection</button>}
  </div>
}
