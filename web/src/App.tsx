import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { Activity, ArrowRight, Bot, Check, ChevronDown, CircleAlert, ClipboardList, Clock3, LayoutDashboard, MessageCircle, RefreshCw, Send, ShieldCheck, Sparkles, TrendingUp, Trophy, Users, Zap } from 'lucide-react'
import { ApiError, post, request } from './api'
import type { CoachReply, LeagueData, LineupData, Player, WaiverData } from './api'

type Tab = 'overview' | 'roster' | 'waivers' | 'lineup'
type Message = { id: number, role: 'user' | 'assistant', text: string, usage?: CoachReply }
const SUGGESTIONS = [
  'Who should I start this week?',
  'What is my biggest roster weakness?',
  'Best WR waiver pickup for ROS',
  'Which bench player should I stash?',
]
const POSITION_FILTERS = ['ALL', 'QB', 'RB', 'WR', 'TE']

function points(value?: number | null) { return typeof value === 'number' ? value.toFixed(1) : '—' }
function moneyTokens(reply?: CoachReply) { return reply ? `${(reply.input_tokens + reply.output_tokens).toLocaleString()} tokens · ${reply.api_calls} call${reply.api_calls === 1 ? '' : 's'}` : '' }

function RichText({ text }: { text: string }) {
  return <div className="richtext">{text.split('\n').map((line, index) => {
    const parts = line.split(/(\*\*[^*]+\*\*)/g)
    return <div className={line.startsWith('- ') ? 'rich-bullet' : ''} key={index}>{line ? parts.map((part, j) => part.startsWith('**') && part.endsWith('**') ? <strong key={j}>{part.slice(2,-2)}</strong> : <span key={j}>{part}</span>) : <span>&nbsp;</span>}</div>
  })}</div>
}

function PlayerRow({ player, showSlot = false }: { player: Player, showSlot?: boolean }) {
  const status = player.on_bye_week ? 'BYE' : player.injury_status
  return <div className="player-row">
    <div className={`position pos-${(player.position || '').replace('/', '')}`}>{showSlot && player.lineup_slot ? player.lineup_slot : player.position || '?'}</div>
    <div className="player-title"><span className="player-name">{player.name}</span><span className="subtext">{player.team || 'NFL'}{player.opponent ? ` · vs ${player.opponent}` : ''}</span></div>
    {status && status !== 'ACTIVE' && <span className="injury">{status}</span>}
    <div className="projection"><strong>{points(player.lineup_projection ?? player.espn_projection ?? player.fantasypros_projection)}</strong><span className="subtext">{player.lineup_projection_sources && player.lineup_projection_sources.length > 1 ? 'avg proj.' : 'proj.'}</span></div>
  </div>
}

function NavButton({ active, icon, label, onClick }: { active: boolean, icon: ReactNode, label: string, onClick: () => void }) {
  return <button className={`nav-button ${active ? 'active' : ''}`} onClick={onClick}>{icon}<span>{label}</span>{active && <ArrowRight size={15} className="nav-arrow" />}</button>
}

export default function App() {
  const [tab, setTab] = useState<Tab>('overview')
  const [league, setLeague] = useState<LeagueData | null>(null)
  const [roster, setRoster] = useState<Player[]>([])
  const [lineup, setLineup] = useState<LineupData | null>(null)
  const [waivers, setWaivers] = useState<WaiverData | null>(null)
  const [position, setPosition] = useState('ALL')
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const [chatError, setChatError] = useState('')
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const chatBottom = useRef<HTMLDivElement | null>(null)
  const chatCounter = useRef(0)

  const loadDashboard = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [leagueData, rosterData, lineupData] = await Promise.all([
        request<LeagueData>('/api/league'),
        request<{players: Player[]}>('/api/roster'),
        request<LineupData>('/api/lineup'),
      ])
      setLeague(leagueData)
      setRoster(rosterData.players || [])
      setLineup(lineupData)
    } catch (err) { setError(err instanceof Error ? err.message : 'Could not load fantasy data') }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { void loadDashboard() }, [loadDashboard])
  useEffect(() => {
    let cancelled = false
    request<WaiverData>(`/api/waivers?position=${encodeURIComponent(position)}&limit=5`)
      .then(data => { if (!cancelled) setWaivers(data) })
      .catch(err => { if (!cancelled) setError(err instanceof Error ? err.message : 'Waiver data unavailable') })
    return () => { cancelled = true }
  }, [position, league])
  useEffect(() => { chatBottom.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages, sending])

  async function send(event?: FormEvent, suggested?: string) {
    event?.preventDefault()
    const content = (suggested || input).trim()
    if (!content || sending) return
    setChatError('')
    setInput('')
    setMessages(previous => [...previous, { id: ++chatCounter.current, role: 'user', text: content }])
    setSending(true)
    try {
      let sid = sessionId
      if (!sid) {
        sid = (await post<{session_id: string}>('/api/sessions')).session_id
        setSessionId(sid)
      }
      let reply: CoachReply
      try { reply = await post<CoachReply>('/api/chat', { message: content }, sid) }
      catch (err) {
        if (err instanceof ApiError && err.status === 404) {
          sid = (await post<{session_id: string}>('/api/sessions')).session_id
          setSessionId(sid)
          reply = await post<CoachReply>('/api/chat', { message: content }, sid)
        } else throw err
      }
      setMessages(previous => [...previous, { id: ++chatCounter.current, role: 'assistant', text: reply.answer, usage: reply }])
    } catch (err) { setChatError(err instanceof Error ? err.message : 'Coach could not respond') }
    finally { setSending(false) }
  }

  async function refresh() {
    if (refreshing || sending) return
    setRefreshing(true)
    setError('')
    try {
      await post('/api/refresh')
      setSessionId(null)
      setMessages([])
      await loadDashboard()
    } catch (err) { setError(err instanceof Error ? err.message : 'Refresh failed') }
    finally { setRefreshing(false) }
  }

  const starters = roster.filter(p => p.lineup_slot && !['BE', 'IR'].includes(p.lineup_slot))
  const bench = roster.filter(p => p.lineup_slot === 'BE')
  const rosterTabs: { key: Tab, label: string }[] = [
    { key:'overview', label:'Overview' }, { key:'roster', label:'My Roster' },
    { key:'waivers', label:'Waiver Wire' }, { key:'lineup', label:'Lineup' },
  ]

  return <div className="shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-logo"><Trophy size={22} strokeWidth={2.3}/></div><div><strong>Fantasy<span>Coach</span></strong><small>YOUR FANTASY INTELLIGENCE</small></div></div>
      <div className="nav-label">WORKSPACE</div>
      <nav aria-label="Main navigation">
        <NavButton active={tab==='overview'} icon={<LayoutDashboard size={18}/>} label="Overview" onClick={()=>setTab('overview')}/>
        <NavButton active={tab==='roster'} icon={<Users size={18}/>} label="My Roster" onClick={()=>setTab('roster')}/>
        <NavButton active={tab==='waivers'} icon={<TrendingUp size={18}/>} label="Waiver Wire" onClick={()=>setTab('waivers')}/>
        <NavButton active={tab==='lineup'} icon={<ClipboardList size={18}/>} label="Optimal Lineup" onClick={()=>setTab('lineup')}/>
      </nav>
      <div className="sidebar-bottom"><div className="sidebar-note"><ShieldCheck size={17}/><span>League data from ESPN<br/>Advice powered by Claude</span></div><div className="account"><span className="avatar">FC</span><span><strong>Personal workspace</strong><small>Local development mode</small></span></div></div>
    </aside>

    <main className="main">
      <header className="topbar"><div className="breadcrumb">WORKSPACE <span>/</span> {rosterTabs.find(t=>t.key===tab)?.label.toUpperCase()}</div>
        <div className="top-actions"><span className={`online-dot ${error ? 'offline' : ''}`}/><span className="connection">{error ? 'Check connection' : 'ESPN connected'}</span><button className="refresh-button" onClick={refresh} disabled={refreshing || sending} title="Reload ESPN fantasy data"><RefreshCw size={16} className={refreshing ? 'rotating' : ''}/><span>Refresh</span></button></div>
      </header>
      <div className="content">
        <section className="page-intro"><div className="eyebrow"><span className="eyebrow-line"/> DECISIONS, NOT GUESSWORK</div><div className="intro-row"><div><h1>{tab === 'overview' ? 'Your fantasy command center.' : tab === 'roster' ? 'Your roster.' : tab === 'waivers' ? 'The waiver wire.' : 'Your optimal lineup.'}</h1><p>{tab === 'overview' ? 'Make sharper start/sit calls, uncover waiver value, and know exactly where your team stands.' : tab === 'roster' ? 'Every player, role, and projection in one place.' : tab === 'waivers' ? 'Explore the actual available player pool before making a move.' : 'A deterministic starting point—not a substitute for your judgment.'}</p></div><div className="week-chip"><Activity size={16}/><span>WEEK {league?.week || '—'}</span><span className="chip-divider"/>{league?.scoring?.scoring || '—'}</div></div></section>

        {error && <div className="banner-error"><CircleAlert size={18}/><span>{error}</span><button onClick={()=>void loadDashboard()}>Retry</button></div>}
        {loading && <div className="loading-line"><RefreshCw size={15} className="rotating"/> Loading your league data…</div>}

        <section className="stats-grid">
          <div className="stat"><span className="stat-icon blue"><Users size={18}/></span><span className="stat-name">YOUR ROSTER</span><strong>{league?.roster_size ?? '—'}<small> players</small></strong><span className="stat-foot">ESPN verified</span></div>
          <div className="stat"><span className="stat-icon lime"><Zap size={18}/></span><span className="stat-name">CURRENT WEEK</span><strong>{league?.week ?? '—'}<small> / 18</small></strong><span className="stat-foot">2026 NFL season</span></div>
          <div className="stat"><span className="stat-icon purple"><TrendingUp size={18}/></span><span className="stat-name">LINEUP PROJECTION</span><strong>{points(lineup?.projected_total)}<small> pts</small></strong><span className="stat-foot">Deterministic optimizer</span></div>
        </section>

        {tab==='overview' && <section className="columns">
          <div className="panel"><div className="panel-head"><div><span className="panel-kicker">SQUAD CHECK</span><h2>Starting roster</h2></div><button className="subtle-link" onClick={()=>setTab('roster')}>View all <ArrowRight size={14}/></button></div><div className="player-list">{starters.length ? starters.slice(0,9).map((p,i)=><PlayerRow key={`${p.name}-${i}`} player={p} showSlot/>) : <div className="empty">Roster loads from your ESPN league.</div>}</div></div>
          <div className="right-stack"><div className="hero-tile"><span className="hero-eyebrow"><Sparkles size={16}/> YOUR AI COACH</span><h2>Have a tough call?<br/><em>Ask away.</em></h2><p>Get recommendations grounded in your real roster, opponent matchups, and player usage.</p><button onClick={()=>document.querySelector<HTMLTextAreaElement>('#coach-input')?.focus()}>Ask Fantasy Coach <ArrowRight size={16}/></button><div className="hero-orbit orbit-one"/><div className="hero-orbit orbit-two"/></div>
            <div className="panel waiver-peek"><div className="panel-head"><div><span className="panel-kicker">OPPORTUNITY RADAR</span><h2>Waiver watchlist</h2></div><button className="subtle-link" onClick={()=>setTab('waivers')}>Explore <ArrowRight size={14}/></button></div><div className="player-list">{(waivers?.candidate_pool || []).slice(0,3).map((p,i)=><PlayerRow key={`${p.name}-${i}`} player={p}/>)}</div></div></div>
        </section>}

        {tab==='roster' && <section className="columns"><div className="panel"><div className="panel-head"><div><span className="panel-kicker">ACTIVE SLOTS</span><h2>Starting lineup</h2></div><span className="count-pill">{starters.length} starters</span></div><div className="player-list">{starters.map((p,i)=><PlayerRow key={`${p.name}-${i}`} player={p} showSlot/>)}</div></div><div className="panel"><div className="panel-head"><div><span className="panel-kicker">DEPTH CHART</span><h2>Bench & IR</h2></div><span className="count-pill">{bench.length} bench</span></div><div className="player-list">{[...bench,...roster.filter(p=>p.lineup_slot==='IR')].map((p,i)=><PlayerRow key={`${p.name}-${i}`} player={p} showSlot/>)}</div></div></section>}

        {tab==='waivers' && <section className="panel full-panel"><div className="panel-head"><div><span className="panel-kicker">ESPN FREE AGENT POOL</span><h2>Players available now</h2></div><div className="filter-bar">{POSITION_FILTERS.map(pos=><button key={pos} onClick={()=>setPosition(pos)} className={position===pos ? 'selected' : ''}>{pos}</button>)}</div></div><p className="panel-description">These are candidates returned by your existing fantasy engine. An optimizer's “no threshold move” does not mean the waiver pool is empty.</p><div className="player-list">{(waivers?.candidate_pool || []).map((p,i)=><PlayerRow key={`${p.name}-${i}`} player={p}/>)}{!waivers?.candidate_pool?.length && <div className="empty">No candidates returned for this filter.</div>}</div></section>}

        {tab==='lineup' && <section className="panel full-panel"><div className="panel-head"><div><span className="panel-kicker">DETERMINISTIC BASELINE</span><h2>Projected optimal lineup</h2></div><span className="count-pill">{points(lineup?.projected_total)} pts</span></div><p className="panel-description">Starters are selected using available ESPN and FantasyPros projections in your league scoring format, with the advisory score breaking close ties. Claude uses this baseline and explains any evidence-based disagreement. {lineup?.projection_complete === false ? `Provider projections are incomplete (${lineup?.projection_coverage ?? 'unknown coverage'}); totals exclude unavailable projections.` : ''}</p><div className="player-list">{Object.entries(lineup?.lineup || {}).map(([slot,p],i)=>p ? <PlayerRow key={`${slot}-${i}`} player={{...p,lineup_slot:slot}} showSlot/> : <div className="player-row" key={slot}><span className="position">{slot}</span><span className="subtext">No eligible player</span></div>)}</div></section>}

        <section className="chat-panel" aria-label="Fantasy Coach chat"><div className="chat-heading"><div className="coach-avatar"><Bot size={23}/></div><div><div className="coach-label"><h2>Ask Fantasy Coach</h2><span className="available"><span/> Ready to analyze</span></div><p>Personalized strategy, powered by your league data.</p></div><span className="chat-model">CLAUDE · v7.10</span></div>
          <div className="chat-body">{messages.length===0 ? <div className="chat-welcome"><div className="chat-welcome-icon"><MessageCircle size={21}/></div><h3>What are we figuring out today?</h3><p>Compare players, explore waivers, or ask for the best move for your roster.</p><div className="suggestions">{SUGGESTIONS.map(s=><button onClick={()=>void send(undefined,s)} key={s} disabled={sending}>{s}<ArrowRight size={13}/></button>)}</div></div> : <div className="messages">{messages.map(message=><div className={`message ${message.role}`} key={message.id}><div className="message-avatar">{message.role === 'assistant' ? <Bot size={17}/> : <span>YOU</span>}</div><div className="message-main"><div className="message-author">{message.role === 'assistant' ? 'Fantasy Coach' : 'You'}</div><RichText text={message.text}/>{message.usage && <span className="usage"><Check size={12}/>{moneyTokens(message.usage)}</span>}</div></div>)}{sending && <div className="typing"><span/><span/><span/> Analyzing league data…</div>}<div ref={chatBottom}/></div>}</div>
          {chatError && <div className="chat-error"><CircleAlert size={15}/>{chatError}</div>}
          <form className="composer" onSubmit={e=>void send(e)}><textarea id="coach-input" rows={2} maxLength={2000} placeholder="Ask about your lineup, trades, waivers, or player value…" value={input} onChange={e=>setInput(e.target.value)} onKeyDown={e=>{if(e.key==='Enter' && !e.shiftKey){e.preventDefault();void send()}}} disabled={sending}/><button aria-label="Send message" type="submit" disabled={sending || !input.trim()}>{sending ? <Clock3 size={19}/> : <Send size={19}/>}</button></form><div className="composer-foot"><span><ShieldCheck size={12}/> ESPN-derived scoring · No data guesswork</span><span>ENTER TO SEND <ChevronDown size={12}/> SHIFT+ENTER FOR NEW LINE</span></div>
        </section>
        <footer>FANTASY COACH · LOCAL DEVELOPMENT <span>Built to support better decisions, not guarantee outcomes.</span></footer>
      </div>
    </main>
  </div>
}
