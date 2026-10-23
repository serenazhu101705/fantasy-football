import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { Activity, ArrowRight, Bot, Check, ChevronDown, ChevronLeft, ChevronRight, CircleAlert, ClipboardList, Clock3, LayoutDashboard, MessageCircle, Newspaper, RefreshCw, Send, X, ShieldCheck, Sparkles, TrendingUp, Trophy, Users, Zap, Mail, Settings2 } from 'lucide-react'
import { ApiError, post, request } from './api'
import { orderRoster } from './rosterLayout'
import { PlayerHeadshot } from './PlayerHeadshot'
import type { CoachReply, LeagueData, LineupData, Player, WaiverBrowserData, MatchupStrategy,
  LineupPreview, ApplyLineupResponse, StandingsData, FaabData, ScheduleData, TransactionData, TeamRosterData, ClaimsData, LeagueMatchupsData, NewsBrowseData, RosterNewsData, MatchupPlayer, PlayerProfile, PlayerAnalysis, StatLine, WeeklyRecap, WeeklyRecapStatus } from './api' 

type Tab = 'overview' | 'roster' | 'waivers' | 'lineup' | 'matchup' | 'league' | 'feed' | 'settings'
type Message = { id: number, role: 'user' | 'assistant', text: string, usage?: CoachReply }
const SUGGESTIONS = [
  'What do I need to do to win this week?',
  'Am I favored and by how much?',
  'What is my biggest roster weakness?',
  'Best WR waiver pickup for ROS',
  'What waiver moves would improve my odds this week?',
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

function PlayerRow({ player, showSlot = false, onOpen }: { player: Player, showSlot?: boolean, onOpen?: (id:number,name:string)=>void }) {
  const status = player.on_bye_week ? 'BYE' : player.injury_status
  return <div className="player-row">
    <div className={`position pos-${(player.position || '').replace('/', '')}`}>{showSlot && player.lineup_slot ? player.lineup_slot : player.position || '?'}</div>
    <PlayerHeadshot id={player.espn_id} name={player.name}/>
    <div className="player-title"><button type="button" className="player-link player-name" disabled={!onOpen || player.espn_id == null} title={player.espn_id == null?'ESPN player ID unavailable':'Open player profile'} onClick={()=>{if(onOpen && player.espn_id != null)onOpen(player.espn_id,player.name)}}>{player.name}</button><span className="subtext">{player.team || 'NFL'}{player.opponent ? ` · vs ${player.opponent}` : ''}</span></div>
    {status && status !== 'ACTIVE' && <span className="injury">{status}</span>}
    <div className="projection"><strong>{points(player.lineup_projection ?? player.espn_projection ?? player.fantasypros_projection)}</strong><span className="subtext">{player.lineup_projection_sources && player.lineup_projection_sources.length > 1 ? 'avg proj.' : 'proj.'}</span></div>
  </div>
}

// All team roster views use this same ESPN-style positional layout and
// the same player drawer. Opponent roster payloads are mapped to Player here.
function RosterColumns({players, onOpen, title = 'Starting lineup'}: {players:Player[],onOpen:(id:number,name:string)=>void,title?:string}) {
  const ordered = orderRoster(players, p=>p.lineup_slot)
  const active = ordered.filter(p=>p.group==='starter')
  const reserves = ordered.filter(p=>p.group!=='starter')
  return <section className="columns roster-columns">
    <div className="panel"><div className="panel-head"><div><span className="panel-kicker">ACTIVE SLOTS · ESPN ORDER</span><h2>{title}</h2></div><span className="count-pill">{active.length} starters</span></div>
      <div className="player-list">{active.map(({row,label},i)=><PlayerRow key={`${row.espn_id||row.name}-${i}`} player={{...row,lineup_slot:label}} showSlot onOpen={onOpen}/>)}
        {!active.length && <div className="empty">No confirmed starters for this team.</div>}</div></div>
    <div className="panel"><div className="panel-head"><div><span className="panel-kicker">DEPTH CHART</span><h2>Bench & IR</h2></div><span className="count-pill">{reserves.filter(p=>p.group==='bench').length} bench</span></div>
      <div className="player-list">{reserves.map(({row,label},i)=><PlayerRow key={`${row.espn_id||row.name}-${i}`} player={{...row,lineup_slot:label}} showSlot onOpen={onOpen}/>)}
        {!reserves.length && <div className="empty">No bench or IR players returned.</div>}</div></div>
  </section>
}

function PageControls({page,totalPages,total,label,onChange}:{page:number,totalPages:number,total:number,label:string,onChange:(page:number)=>void}) {
  const pages = Math.max(1,totalPages)
  const current = Math.max(1,Math.min(pages,page))
  const visible = Array.from(new Set([1,pages,current-1,current,current+1].filter(n=>n>=1 && n<=pages))).sort((a,b)=>a-b)
  const first = total ? ((current-1)*10)+1 : 0
  const last = Math.min(current*10,total)
  const itemType = label === "News" ? "articles" : "players"
  return <nav className="pagination-bar" aria-label={`${label} pagination`}>
    <div className="pagination-summary"><span className="pagination-label">{label}</span>
      <strong>{total ? `${first}–${last}`:'0'}</strong><span>of {total.toLocaleString()} {itemType}</span>
    </div>
    <div className="pagination-navigation">
      <button type="button" className="pagination-direction" disabled={current===1} onClick={()=>onChange(current-1)} aria-label="Previous page"><ChevronLeft size={17}/><span>Previous</span></button>
      <div className="pagination-pages">{visible.map((p,i)=><span className="pagination-page-item" key={p}>
        {i>0 && p-visible[i-1]>1 && <span className="pagination-ellipsis">…</span>}
        <button type="button" className={`pagination-page ${p===current?'is-current':''}`} aria-current={p===current?'page':undefined} onClick={()=>onChange(p)}>{p}</button>
      </span>)}</div>
      <button type="button" className="pagination-direction" disabled={current===pages} onClick={()=>onChange(current+1)} aria-label="Next page"><span>Next</span><ChevronRight size={17}/></button>
    </div>
  </nav>
}

// Compact matchup cells: starter position is aligned on both fantasy teams.
function StarterCell({ player, winner, open }: {player: MatchupPlayer | null, winner:boolean, open:(id:number,name:string)=>void}) {
  if (!player) return <div className="starter-card starter-empty">No starter assigned</div>
  const state = player.game_status || 'UNKNOWN'
  return <div className={`starter-card ${winner ? 'starter-ahead' : ''}`}>
    <div className="starter-name-row">
      <button className="player-link" disabled={player.espn_id == null} onClick={()=>{if(player.espn_id != null)open(player.espn_id,player.name)}}>{player.name}</button>
      {winner && <span className="starter-check" title="Higher ESPN projected fantasy points"><Check size={13}/></span>}
    </div>
    <div className="starter-meta"><span>{player.nfl_team || '—'}{player.opponent ? ` vs ${player.opponent}`:''}</span>
      {state==='LIVE' ? <span className="game-pill playing"><span className="live-dot"/>IN PLAY {player.game_status_detail || ''}</span>
        : state==='FINAL' ? <span className="game-pill final">FINAL</span>
        : state==='SCHEDULED' ? <span className="game-pill scheduled">UPCOMING</span>
        : state==='BYE' ? <span className="game-pill">BYE</span> : null}
    </div>
    <div className="starter-scores"><strong>{points(player.actual_points)} <small>scored</small></strong><span>{points(player.projected_points)} projected</span></div>
  </div>
}

function StatGrid({ stats, empty = 'Stats not available from ESPN.' }: {stats?:StatLine[],empty?:string}) {
  return stats?.length ? <div className="stat-detail-grid">{stats.map((s,i)=><div key={`${s.key}-${i}`}><span>{s.label}</span><strong>{s.value.toLocaleString()}</strong></div>)}</div> : <p className="detail-muted">{empty}</p>
}

function PlayerDrawer({ selected, onClose, week }: {selected:{id:number,name:string},onClose:()=>void,week?:number}) {
  const [profile,setProfile] = useState<PlayerProfile | null>(null)
  const [error,setError] = useState('')
  const [analysis,setAnalysis] = useState<PlayerAnalysis | null>(null)
  const [analysisError,setAnalysisError] = useState('')
  const [analyzing,setAnalyzing] = useState(false)
  useEffect(()=>{
    let cancelled=false
    setProfile(null);setAnalysis(null);setError('');setAnalysisError('')
    request<PlayerProfile>(`/api/players/${selected.id}${week ? `?week=${week}` : ''}`)
      .then(data=>{if(!cancelled)setProfile(data)})
      .catch(err=>{if(!cancelled)setError(err instanceof Error?err.message:'Player data unavailable')})
    return ()=>{cancelled=true}
  },[selected.id,week])
  useEffect(()=>{
    const handler=(e:KeyboardEvent)=>{if(e.key==='Escape')onClose()}
    window.addEventListener('keydown',handler)
    return ()=>window.removeEventListener('keydown',handler)
  },[onClose])
  async function generate() {
    setAnalyzing(true);setAnalysisError('')
    try {
      const reply=await post<PlayerAnalysis>(`/api/players/${selected.id}/analysis${week ? `?week=${week}` : ''}`)
      setAnalysis(reply)
    } catch(err){setAnalysisError(err instanceof Error?err.message:'Claude analysis unavailable')}
    finally {setAnalyzing(false)}
  }
  return <div className="drawer-scrim" onMouseDown={onClose} role="presentation">
    <section className="player-drawer" role="dialog" aria-modal="true" aria-label={`${selected.name} player details`} onMouseDown={e=>e.stopPropagation()}>
      <div className="drawer-header"><div className="drawer-player-identity"><PlayerHeadshot id={selected.id} name={profile?.name || selected.name} size="lg"/><div className="drawer-player-label"><span className="panel-kicker">ESPN PLAYER INTELLIGENCE</span><h2>{profile?.name || selected.name}</h2><p>{profile ? `${profile.position || ''} · ${profile.nfl_team || 'Unknown team'} · ${profile.injury_status || 'Status unavailable'}` : 'Loading player data…'}</p></div></div><button className="drawer-close" onClick={onClose} aria-label="Close player details"><X size={19}/></button></div>
      {error && <div className="detail-error">{error}</div>}
      {profile && <div className="drawer-body">
        <div className="detail-metrics"><div><span>SEASON POINTS</span><strong>{points(profile.season_fantasy_points)}</strong></div><div><span>FANTASY PPG</span><strong>{points(profile.season_fantasy_ppg)}</strong></div><div><span>WEEK {profile.week} PROJECTION</span><strong>{points(profile.current_week.projected_points)}</strong></div><div><span>WEEK {profile.week} ACTUAL</span><strong>{points(profile.current_week.fantasy_points)}</strong></div></div>
        {profile.bio && Object.keys(profile.bio).some(k=>k!=='source') && <div className="bio-strip">{profile.bio.age != null && <span>Age {profile.bio.age}</span>}{profile.bio.height && <span>{profile.bio.height}</span>}{profile.bio.college && <span>{profile.bio.college}</span>}{profile.bio.years_pro != null && <span>{profile.bio.years_pro} seasons pro</span>}{profile.bio.jersey && <span>#{profile.bio.jersey}</span>}</div>}
        <h3>2026 season NFL stats <small className="detail-muted">{profile.season_stats_complete
          ? `${profile.season_stats_source}${profile.season_stats_as_of_week ? ` · through Week ${profile.season_stats_as_of_week}` : ''}`
          : profile.stat_coverage_weeks ? `Partial · ${profile.stat_coverage_weeks} ESPN Fantasy week(s) only` : 'Season totals unavailable'}</small></h3><StatGrid stats={profile.available_weeks_stat_totals} empty="No verified 2026 season statistics are available for this player." />
        <h3>This week's actual NFL stats</h3><StatGrid stats={profile.current_week.stats} empty="No verified stats yet for this week." />
        <h3>This week's projected NFL stats</h3><StatGrid stats={profile.current_week.projected_stats} empty="ESPN did not provide a projected stat breakdown." />
        <h3>Last completed game {profile.last_completed_game?.week ? `· Week ${profile.last_completed_game.week}` : profile.last_completed_game?.game_date ? `· ${new Date(profile.last_completed_game.game_date).toLocaleDateString()}` : ''}</h3>
        {profile.last_completed_game ? <><p className="detail-muted">{profile.last_completed_game.fantasy_points != null ? `${points(profile.last_completed_game.fantasy_points)} fantasy points` : 'NFL stats from ESPN game log · fantasy scoring not available for this entry'}</p><StatGrid stats={profile.last_completed_game.stats}/></> : <p className="detail-muted">No completed-game stat breakdown available.</p>}
        <h3>Recent performances</h3><div className="recent-game-list">{profile.recent_games.length ? profile.recent_games.map((g,i)=><details className="game-log-entry" key={`${g.week ?? g.game_date ?? i}-${i}`}>
          <summary><span className="game-log-date">{g.week ? `Week ${g.week}` : g.game_date ? new Date(g.game_date).toLocaleDateString() : 'NFL game'}{g.opponent ? ` · vs ${g.opponent}` : ''}</span>
            <span className={`game-log-pts ${g.fantasy_points == null?'unavailable':''}`}>{g.fantasy_points != null ? `${points(g.fantasy_points)} fantasy pts` : 'Fantasy score unavailable'}</span></summary>
          <div className="game-log-expanded">{g.projected_points != null && <p className="detail-muted">ESPN projected: {points(g.projected_points)} pts</p>}
            {g.fantasy_points_source && <p className="detail-muted">Points source: {g.fantasy_points_source}</p>}
            <StatGrid stats={g.stats} empty="No verified NFL stat breakdown for this game." />
          </div>
        </details>) : <p className="detail-muted">No verified recent weekly performances are available.</p>}</div>
        <div className="claude-player-card"><div><span className="panel-kicker">OPTIONAL · USES CLAUDE TOKENS</span><h3>Personalized player breakdown</h3><p>A short explanation of the last game, season performance, and this week's outlook, using ESPN facts.</p></div>
          {!analysis && <button className="button-action" disabled={analyzing} onClick={()=>void generate()}>{analyzing?'Analyzing…':'Analyze with Claude'} <Sparkles size={14}/></button>}
          {analysisError && <p className="detail-error">{analysisError}</p>}
          {analysis && <><RichText text={analysis.analysis}/><span className="analysis-usage">{analysis.cached?'Cached · no new API charge':`${(analysis.input_tokens+analysis.output_tokens).toLocaleString()} tokens · 1 Claude call`}</span></>}
        </div>
        <p className="detail-muted">{profile.note} Only available ESPN data is shown; missing stats are not treated as zero.</p>
      </div>}
    </section>
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
  const [lineupPreview,setLineupPreview] = useState<LineupPreview | null>(null)
  const [lineupActionError,setLineupActionError] = useState('')
  const [lineupActionMessage,setLineupActionMessage] = useState('')
  const [lineupActionLoading,setLineupActionLoading] = useState(false)
  const [email,setEmail] = useState('')
  const [emailPrefs,setEmailPrefs] = useState({thursday_lineup:true,monday_recap:true,injury_news:true,bye_alerts:true,auto_swap:false})
  const [emailSending,setEmailSending] = useState(false)
  const [emailMessage,setEmailMessage] = useState('')
  const [emailError,setEmailError] = useState('')

  const [waivers, setWaivers] = useState<WaiverBrowserData | null>(null)
  const [waiverPage, setWaiverPage] = useState(1)
  const [position, setPosition] = useState('ALL')
  const [strategy, setStrategy] = useState<MatchupStrategy | null>(null)
  const [matchupError, setMatchupError] = useState('')
  const [selectedPlayer, setSelectedPlayer] = useState<{id:number,name:string} | null>(null)
  const [standings, setStandings] = useState<StandingsData | null>(null)
  const [faab, setFaab] = useState<FaabData | null>(null)
  const [schedule, setSchedule] = useState<ScheduleData | null>(null)
  const [scheduleWeek, setScheduleWeek] = useState<number | 'all' | 'current'>('current')
  const [news, setNews] = useState<NewsBrowseData | null>(null)
  const [newsPage, setNewsPage] = useState(1)
  const [newsError, setNewsError] = useState('')
  const [feedSource, setFeedSource] = useState('ALL')
  const [feedTab, setFeedTab] = useState<'fantasy'|'team'>('fantasy')
  const [rosterNews, setRosterNews] = useState<RosterNewsData | null>(null)
  const [rosterNewsError, setRosterNewsError] = useState('')
  const [teamNewsPage, setTeamNewsPage] = useState(1)
  const [expandedNewsPlayers, setExpandedNewsPlayers] = useState<Record<string, boolean>>({})
  const [rosterNewsRefresh, setRosterNewsRefresh] = useState(0)
  const [recapWeek, setRecapWeek] = useState<number | null>(null)
  const [recapStatus, setRecapStatus] = useState<WeeklyRecapStatus | null>(null)
  const [recap, setRecap] = useState<WeeklyRecap | null>(null)
  const [recapError, setRecapError] = useState('')
  const [generatingRecap, setGeneratingRecap] = useState(false)
  const [transactions, setTransactions] = useState<TransactionData | null>(null)
  const [leagueErrors, setLeagueErrors] = useState<Record<string,string>>({})
  const [claims, setClaims] = useState<ClaimsData | null>(null)
  const [selectedTeam, setSelectedTeam] = useState<number | null>(null)
  const [opponentRoster, setOpponentRoster] = useState<TeamRosterData | null>(null)
  const [leagueSubTab, setLeagueSubTab] = useState<'standings'|'matchups'|'faab'|'schedule'|'activity'>('standings')
  const [leagueMatchups, setLeagueMatchups] = useState<LeagueMatchupsData | null>(null)
  const [leagueMatchupsWeek, setLeagueMatchupsWeek] = useState<number | null>(null)
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

  const openPlayer = useCallback((id:number,name:string)=>setSelectedPlayer({id,name}),[])

  async function loadLineupPreview() {
    setLineupActionLoading(true);setLineupActionError('');setLineupActionMessage('');setLineupPreview(null)
    try { setLineupPreview(await request<LineupPreview>('/api/lineup/preview')) }
    catch(err){setLineupActionError(err instanceof Error?err.message:'ESPN lineup preview unavailable')}
    finally{setLineupActionLoading(false)}
  }

  async function confirmLineupApply(){
    if(!lineupPreview || lineupActionLoading)return
    setLineupActionLoading(true);setLineupActionError('')
    try{
      const outcome=await post<ApplyLineupResponse>('/api/lineup/apply', {confirmation_token:lineupPreview.confirmation_token})
      setLineupActionMessage(outcome.note)
      setLineupPreview(null)
      if(outcome.verified){await loadDashboard()}
    }catch(err){setLineupActionError(err instanceof Error?err.message:'ESPN lineup update unavailable');setLineupPreview(null)}
    finally{setLineupActionLoading(false)}
  }

  async function submitEmail(event:FormEvent){
    event.preventDefault();setEmailSending(true);setEmailError('');setEmailMessage('')
    try{
      const result=await post<{message:string}>('/api/subscriptions',{email,...emailPrefs})
      setEmailMessage(result.message)
    }catch(err){setEmailError(err instanceof Error?err.message:'Could not subscribe')}
    finally{setEmailSending(false)}
  }


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
    if (!league || (tab !== 'overview' && tab !== 'matchup')) return
    let cancelled = false
    const load = () => { if (document.visibilityState !== 'hidden') {
      request<MatchupStrategy>('/api/strategy/week').then(data => {
        if (!cancelled) { setStrategy(data); setMatchupError('') }
      }).catch(err => {
        if (!cancelled) setMatchupError(err instanceof Error ? err.message : 'Matchup data unavailable')
      })
    }}
    load()
    // ESPN box scores and the NFL scoreboard have independent caches; never claim instant updates.
    const interval = tab === 'matchup' ? window.setInterval(load, 60_000) : null
    return () => { cancelled = true; if (interval != null) window.clearInterval(interval) }
  }, [league, tab])
  useEffect(() => {
    if (tab !== 'matchup' || !league) return
    let cancelled=false
    const wk = recapWeek ?? league.week
    const load=()=>{
      if (document.visibilityState !== 'hidden') {
        request<WeeklyRecapStatus>(`/api/recaps/week?week=${wk}`)
          .then(data=>{if(!cancelled){setRecapStatus(data);setRecapError('')}})
          .catch(err=>{if(!cancelled)setRecapError(err instanceof Error?err.message:'Recap status unavailable')})
      }
    }
    setRecapStatus(null)
    load()
    const interval=window.setInterval(load,60_000)
    return ()=>{cancelled=true;window.clearInterval(interval)}
  },[tab,league,recapWeek])

  async function generateRecap() {
    if(!league||generatingRecap)return
    setGeneratingRecap(true);setRecapError('')
    try{
      const week=recapWeek ?? league.week
      const result=await post<WeeklyRecap>(`/api/recaps/week?week=${week}`)
      setRecap(result)
    }catch(err){setRecapError(err instanceof Error?err.message:'Weekly recap unavailable')}
    finally{setGeneratingRecap(false)}
  }

  useEffect(() => {
    if (tab !== 'league') return
    let cancelled = false
    // ESPN endpoints are independent: a transaction-access failure must not hide standings.
    const load = <T,>(path:string, setter:(v:T)=>void, name:string) => request<T>(path)
      .then(data => { if (!cancelled) setter(data) })
      .catch(err => { if (!cancelled) setLeagueErrors(prev=>({...prev,[name]:err instanceof Error ? err.message : 'Unavailable'})) })
    void load<StandingsData>('/api/league/standings', setStandings, 'standings')
    void load<FaabData>('/api/league/faab', setFaab, 'faab')
    void load<ScheduleData>('/api/league/schedule', setSchedule, 'schedule')
    void load<ClaimsData>('/api/league/pending-claims', setClaims, 'claims')
    return () => { cancelled = true }
  }, [tab, league])
  useEffect(() => {
    if (tab !== 'feed' || feedTab !== 'fantasy') return
    let cancelled = false
    setNews(null)
    request<NewsBrowseData>(`/api/news/browse?page=${newsPage}&page_size=10&source=${encodeURIComponent(feedSource)}`)
      .then(data => { if (!cancelled) { setNews(data); setNewsError('') } })
      .catch(err => { if (!cancelled) setNewsError(err instanceof Error ? err.message : 'News feed unavailable') })
    return () => { cancelled = true }
  }, [tab, feedTab, newsPage, feedSource])

  useEffect(() => {
    if (tab !== 'feed' || feedTab !== 'team' || rosterNews || !league) return
    let cancelled=false
    request<RosterNewsData>(`/api/news/roster${rosterNewsRefresh ? '?refresh=true' : ''}`)
      .then(data=>{if(!cancelled){setRosterNews(data);setRosterNewsError('')}})
      .catch(err=>{if(!cancelled)setRosterNewsError(err instanceof Error?err.message:'Roster news unavailable')})
    return ()=>{cancelled=true}
  },[tab,feedTab,rosterNews,league,rosterNewsRefresh])

  useEffect(() => {
    if (tab !== 'league' || leagueSubTab !== 'activity' || transactions) return
    let cancelled = false
    request<TransactionData>('/api/league/transactions?limit=10')
      .then(data => { if (!cancelled) setTransactions(data) })
      .catch(err => { if (!cancelled) setLeagueErrors(prev=>({...prev,activity:err instanceof Error?err.message:'Unavailable'})) })
    return () => { cancelled = true }
  }, [tab, leagueSubTab, transactions])

  useEffect(() => {
    if (tab !== 'league' || leagueSubTab !== 'matchups' || !league) return
    let cancelled = false
    setLeagueMatchups(null)
    const week = leagueMatchupsWeek ?? league.week
    request<LeagueMatchupsData>(`/api/league/matchups?week=${week}`)
      .then(data => { if (!cancelled) setLeagueMatchups(data) })
      .catch(() => { if (!cancelled) setLeagueMatchups({week,matchups:[],status:'unavailable',note:'ESPN box scores are unavailable for the selected week.'}) })
    return () => { cancelled = true }
  }, [tab, leagueSubTab, league, leagueMatchupsWeek])

  useEffect(() => {
    if (selectedTeam === null || tab !== 'league' || selectedTeam === league?.my_team_id) {
      setOpponentRoster(null)
      return
    }
    let cancelled = false
    setOpponentRoster(null)
    request<TeamRosterData>(`/api/league/teams/${selectedTeam}/roster`)
      .then(data => { if (!cancelled) setOpponentRoster(data) }).catch(() => {})
    return () => { cancelled = true }
  }, [selectedTeam, tab, league])
  useEffect(() => {
    if (!league) return
    let cancelled = false
    setWaivers(null)
    request<WaiverBrowserData>(`/api/waivers/browse?position=${encodeURIComponent(position)}&page=${waiverPage}&page_size=10`)
      .then(data => { if (!cancelled) setWaivers(data) })
      .catch(err => { if (!cancelled) setError(err instanceof Error ? err.message : 'Waiver data unavailable') })
    return () => { cancelled = true }
  }, [position, waiverPage, league])
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
      setStrategy(null)
      setStandings(null)
      setFaab(null)
      setSchedule(null)
      setNews(null)
      setRosterNews(null)
      setTeamNewsPage(1)
      setNewsPage(1)
      setWaiverPage(1)
      setRecapStatus(null)
      setRecap(null)
      setTransactions(null)
      setOpponentRoster(null)
      setSelectedTeam(null)
      setLeagueErrors({})
      setLeagueMatchups(null)
      setLeagueMatchupsWeek(null)
      await loadDashboard()
    } catch (err) { setError(err instanceof Error ? err.message : 'Refresh failed') }
    finally { setRefreshing(false) }
  }

  const starters = orderRoster(roster, p=>p.lineup_slot).filter(p=>p.group==='starter').map(p=>({...p.row,lineup_slot:p.label}))

  const rosterTabs: { key: Tab, label: string }[] = [
    { key:'overview', label:'Overview' }, { key:'roster', label:'My Roster' },
    { key:'waivers', label:'Waiver Wire' }, { key:'lineup', label:'Lineup' },
    { key:'matchup', label:'Weekly Matchup' }, { key:'league', label:'League Explorer' }, { key:'feed', label:'News Feed' },
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
        <NavButton active={tab==='matchup'} icon={<Activity size={18}/>} label="Weekly Matchup" onClick={()=>setTab('matchup')}/>
        <NavButton active={tab==='league'} icon={<Users size={18}/>} label="League Explorer" onClick={()=>setTab('league')}/>
        <NavButton active={tab==='feed'} icon={<Newspaper size={18}/>} label="News Feed" onClick={()=>setTab('feed')}/>
        <NavButton active={tab==='settings'} icon={<Settings2 size={18}/>} label="Email & Automation" onClick={()=>setTab('settings')}/>
      </nav>
      <div className="sidebar-bottom"><div className="sidebar-note"><ShieldCheck size={17}/><span>League data from ESPN<br/>Advice powered by Claude</span></div><div className="account"><span className="avatar">FC</span><span><strong>Personal workspace</strong><small>Local development mode</small></span></div></div>
    </aside>

    <main className="main">
      <header className="topbar"><div className="breadcrumb">WORKSPACE <span>/</span> {rosterTabs.find(t=>t.key===tab)?.label.toUpperCase()}</div>
        <div className="top-actions"><span className={`online-dot ${error ? 'offline' : ''}`}/><span className="connection">{error ? 'Check connection' : league ? 'ESPN connected' : 'Connecting to ESPN'}</span><button className="refresh-button" onClick={refresh} disabled={refreshing || sending} title="Reload ESPN fantasy data"><RefreshCw size={16} className={refreshing ? 'rotating' : ''}/><span>Refresh</span></button></div>
      </header>
      <div className="content">
        <section className="page-intro"><div className="eyebrow"><span className="eyebrow-line"/> DECISIONS, NOT GUESSWORK</div><div className="intro-row"><div><h1>{tab === 'overview' ? 'Your fantasy command center.' : tab === 'roster' ? 'Your roster.' : tab === 'waivers' ? 'The waiver wire.' : tab === 'matchup' ? 'Your path to victory.' : tab === 'league' ? 'Your entire league.' : tab === 'feed' ? 'The fantasy news feed.' : tab === 'settings' ? 'Email & automation.' : 'Your optimal lineup.'}</h1><p>{tab === 'overview' ? 'Make sharper start/sit calls, uncover waiver value, and know exactly where your team stands.' : tab === 'roster' ? 'Every player, role, and projection in one place.' : tab === 'waivers' ? 'Explore the actual available player pool before making a move.' : tab === 'matchup' ? 'Your ESPN matchup, projection gap, and potential lineup gains.' : tab === 'league' ? 'Records, FAAB, schedules, league moves, and opponent rosters.' : tab === 'feed' ? 'Fresh NFL headlines and fantasy analysis from ESPN and RotoWire.' : tab === 'settings' ? 'Confirm newsletter preferences and configure opt-in lineup automation.' : 'A deterministic starting point—not a substitute for your judgment.'}</p></div><div className="week-chip"><Activity size={16}/><span>WEEK {league?.week || '—'}</span><span className="chip-divider"/>{league?.scoring?.scoring || '—'}</div></div></section>

        {error && <div className="banner-error"><CircleAlert size={18}/><span>{error}</span><button onClick={()=>void loadDashboard()}>Retry</button></div>}
        {loading && <div className="loading-line"><RefreshCw size={15} className="rotating"/> Loading your league data…</div>}

        <section className="stats-grid">
          <div className="stat"><span className="stat-icon blue"><Users size={18}/></span><span className="stat-name">YOUR ROSTER</span><strong>{league?.roster_size ?? '—'}<small> players</small></strong><span className="stat-foot">ESPN verified</span></div>
          <div className="stat"><span className="stat-icon lime"><Zap size={18}/></span><span className="stat-name">CURRENT WEEK</span><strong>{league?.week ?? '—'}<small> / 18</small></strong><span className="stat-foot">2026 NFL season</span></div>
          <div className="stat"><span className="stat-icon purple"><TrendingUp size={18}/></span><span className="stat-name">LINEUP PROJECTION</span><strong>{points(lineup?.projected_total)}<small> pts</small></strong><span className="stat-foot">Deterministic optimizer</span></div>
        </section>

        {tab==='overview' && strategy?.status==='available' && <section className="matchup-mini" aria-label="Current matchup">
          <div><span className="panel-kicker">YOUR WEEK {strategy.week} MATCHUP</span><strong>{strategy.my_team?.name} <span>vs</span> {strategy.opponent?.name}</strong>
            <p>{strategy.projected_margin == null ? 'ESPN projections unavailable' : strategy.projected_margin >= 0 ? `Projected ahead by ${points(strategy.projected_margin)} points` : `Projected behind by ${points(-strategy.projected_margin)} points`}</p>
          </div>
          <div><span className="panel-kicker">POINTS TO CLOSE</span><strong>{points(strategy.extra_points_above_my_projection_to_take_lead)} pts</strong><p>Above your current projection, assuming opponent meets theirs</p></div>
          <button className="button-action" onClick={()=>setTab('matchup')}>Analyze matchup <ArrowRight size={15}/></button>
        </section>}

        {tab==='overview' && <section className="columns">
          <div className="panel"><div className="panel-head"><div><span className="panel-kicker">SQUAD CHECK</span><h2>Starting roster</h2></div><button className="subtle-link" onClick={()=>setTab('roster')}>View all <ArrowRight size={14}/></button></div><div className="player-list">{starters.length ? starters.slice(0,9).map((p,i)=><PlayerRow key={`${p.name}-${i}`} player={p} showSlot onOpen={openPlayer}/>) : <div className="empty">Roster loads from your ESPN league.</div>}</div></div>
          <div className="right-stack"><div className="hero-tile"><span className="hero-eyebrow"><Sparkles size={16}/> YOUR AI COACH</span><h2>Have a tough call?<br/><em>Ask away.</em></h2><p>Get recommendations grounded in your real roster, opponent matchups, and player usage.</p><button onClick={()=>document.querySelector<HTMLTextAreaElement>('#coach-input')?.focus()}>Ask Fantasy Coach <ArrowRight size={16}/></button><div className="hero-orbit orbit-one"/><div className="hero-orbit orbit-two"/></div>
            <div className="panel waiver-peek"><div className="panel-head"><div><span className="panel-kicker">OPPORTUNITY RADAR</span><h2>Waiver watchlist</h2></div><button className="subtle-link" onClick={()=>setTab('waivers')}>Explore <ArrowRight size={14}/></button></div><div className="player-list">{(waivers?.candidate_pool || []).slice(0,3).map((p,i)=><PlayerRow key={`${p.name}-${i}`} player={p} onOpen={openPlayer}/>)}</div></div></div>
        </section>}

        {tab==='roster' && <RosterColumns players={roster} onOpen={openPlayer}/>}

        {tab==='waivers' && <section className="panel full-panel"><div className="panel-head"><div><span className="panel-kicker">ESPN FREE AGENT POOL</span><h2>Players available now</h2></div><div className="filter-bar">{POSITION_FILTERS.map(pos=><button key={pos} onClick={()=>{setPosition(pos);setWaiverPage(1)}} className={position===pos ? 'selected' : ''}>{pos}</button>)}</div></div><p className="panel-description">Browse 10 players at a time from the expanded ESPN free-agent pool, not just the five-player Claude shortlist. Select a player to open their profile. No Claude calls are needed.</p><div className="player-list">{(waivers?.candidate_pool || []).map((p,i)=><PlayerRow key={`${p.espn_id||p.name}-${i}`} player={p} onOpen={openPlayer}/>)}{!waivers && <div className="empty">Loading available players…</div>}{waivers && !waivers.candidate_pool.length && <div className="empty">No more players loaded for this filter.</div>}</div>{waivers && <><PageControls page={waivers.page} totalPages={waivers.total_pages} total={waivers.total} label="Waiver" onChange={setWaiverPage}/><p className="panel-description">{waivers.note}</p></>}</section>}

        {tab==='lineup' && <section className="panel full-panel lineup-action-panel">
          <div className="panel-head"><div><span className="panel-kicker">ESPN ROSTER CONTROL</span><h2>Apply optimal lineup</h2></div><button className="button-action" disabled={lineupActionLoading} onClick={()=>void loadLineupPreview()}>{lineupActionLoading?'Checking ESPN…':'Preview lineup changes'} <ArrowRight size={15}/></button></div>
          <p className="panel-description">Nothing changes until you review and confirm. ESPN writes are disabled by default in your backend. Kickoffs, player locks and eligibility must all be verified.</p>
          {lineupActionError && <div className="detail-error">{lineupActionError}</div>}
          {lineupActionMessage && <div className="automation-success">{lineupActionMessage}</div>}
          {lineupPreview && <div className="apply-preview"><h3>Week {lineupPreview.week} · {lineupPreview.change_count} changes</h3>
            {lineupPreview.changes.length ? lineupPreview.changes.map(row=><div key={row.player_id} className="preview-move"><strong>{row.name}</strong><span>{row.from} → {row.to}</span></div>) : <p>Already optimal; no changes needed.</p>}
            {!lineupPreview.writes_enabled && <p className="detail-muted">ESPN write mode is disabled. Set ESPN_WRITES_ENABLED=1 on the server only after reviewing the safety notes.</p>}
            <div className="preview-actions"><button className="button-action" disabled={lineupActionLoading || !lineupPreview.writes_enabled} onClick={()=>void confirmLineupApply()}>Confirm and apply on ESPN</button><button className="refresh-button" onClick={()=>setLineupPreview(null)}>Cancel</button></div>
          </div>}
        </section>}

        {tab==='lineup' && <section className="panel full-panel"><div className="panel-head"><div><span className="panel-kicker">TRANSPARENT OPTIMIZER</span><h2>Optimal lineup, explained</h2></div><span className="count-pill">{points(lineup?.projected_total)} pts</span></div>
          <p className="panel-description">{lineup?.explanation?.method || 'Using the mean of available ESPN and FantasyPros league-scoring projections.'}</p>
          <p className="panel-description"><strong>Projection coverage:</strong> {lineup?.projection_coverage || 'Unknown'} starters have at least one actual provider projection. Missing an ECR ranking is not the same as missing a projection.</p>
          <div className="lineup-reasons">{Object.entries(lineup?.lineup || {}).map(([slot,p])=>{
            const why=lineup?.explanation?.slot_explanations?.[slot]
            return <div className="lineup-reason" key={slot}>
              {p ? <PlayerRow player={{...p,lineup_slot:slot}} showSlot onOpen={openPlayer}/> : <p>No eligible starter for {slot}</p>}
              {why && <div className="lineup-why"><p>{why.reason}</p><div className="lineup-source-pills">{Object.entries(why.projection_sources).map(([source,score])=><span key={source}>{source}: <strong>{points(score)}</strong></span>)}{Object.keys(why.projection_sources).length===0 && <span>No verified projection</span>}</div>
                {why.best_eligible_nonstarter && <small>Best available roster alternative: <strong>{why.best_eligible_nonstarter.name}</strong> · {points(why.best_eligible_nonstarter.projection)} projected</small>}
              </div>}
            </div>
          })}</div>
          <p className="panel-description">{lineup?.explanation?.comparison_note}</p>
          <p className="panel-description">{lineup?.explanation?.rankings_note}</p>
        </section>}

        {tab==='matchup' && <section className="panel full-panel league-details">
          <div className="panel-head"><div><span className="panel-kicker">ESPN LIVE LEAGUE DATA</span><h2>Week {strategy?.week || league?.week} matchup</h2></div><span className="count-pill">ESPN live matchup</span></div>
          {matchupError && <p className="panel-description">{matchupError}</p>}
          {!strategy && !matchupError && <p className="panel-description">Loading ESPN matchup…</p>}
          {strategy?.status!=='available' && strategy && <p className="panel-description">{strategy.note || `Matchup ${strategy.status}`}</p>}
          {strategy?.status==='available' && <>
            <div className="matchup-score-grid">
              <div className="matchup-team"><span className="panel-kicker">YOUR TEAM</span><h3>{strategy.my_team?.name}</h3><strong>{points(strategy.my_team?.projected_final)}<small> projected</small></strong><p>{points(strategy.my_team?.points_scored)} points scored</p></div>
              <div className="matchup-vs"><span>VS</span><strong>{strategy.projected_margin == null ? '—' : `${strategy.projected_margin > 0 ? '+' : ''}${points(strategy.projected_margin)}`}</strong><small>projection margin</small></div>
              <div className="matchup-team"><span className="panel-kicker">OPPONENT</span><h3>{strategy.opponent?.name}</h3><strong>{points(strategy.opponent?.projected_final)}<small> projected</small></strong><p>{points(strategy.opponent?.points_scored)} points scored</p></div>
            </div>
            <div className="win-probability-panel">
              <div><span className="panel-kicker">ESPN FANTASY WIN PROBABILITY</span>
                <strong>{strategy.espn_win_probability_pct != null ? `${points(strategy.espn_win_probability_pct)}%` : 'Unavailable'}</strong>
                <p>{strategy.win_probability_note || 'Live probability is not present in the ESPN response.'}</p>
              </div>
              {strategy.espn_win_probability_pct != null && <div className="probability-chart" aria-label={`ESPN estimates a ${strategy.espn_win_probability_pct}% chance for your team`}>
                <div style={{width:`${Math.max(0,Math.min(100,strategy.espn_win_probability_pct))}%`}} />
                <span>You {points(strategy.espn_win_probability_pct)}%</span><span>Opponent {points(strategy.espn_opponent_win_probability_pct)}%</span>
              </div>}
            </div>
            <div className="victory-target"><span className="panel-kicker">WHAT DO I NEED TO WIN?</span><h3>{points(strategy.extra_points_above_my_projection_to_take_lead)} points above projection</h3><p>To finish ahead if your opponent scores their currently projected total of {points(strategy.opponent?.projected_final)}. This is a conditional target, not a predicted winning score.</p></div>
            <div className="position-matchup">
              <div className="panel-head"><div><span className="panel-kicker">POSITION-BY-POSITION</span><h2>Starter matchups</h2></div><span className="count-pill">✓ Higher projected points</span></div>
              <div className="position-header"><span>YOU · {strategy.my_team?.name}</span><span>POSITION</span><span>{strategy.opponent?.name} · OPPONENT</span></div>
              {strategy.head_to_head?.length ? strategy.head_to_head.map((pair,index)=><div className="position-versus" key={`${pair.slot}-${index}`}>
                <StarterCell player={pair.my_player} winner={pair.projected_advantage==='me'} open={openPlayer}/>
                <div className="slot-label"><strong>{pair.slot}</strong><small>{pair.projection_margin == null ? '—' : pair.projection_margin===0 ? 'Tied' : `${pair.projection_margin>0?'+':''}${points(pair.projection_margin)} pts`}</small></div>
                <StarterCell player={pair.opponent_player} winner={pair.projected_advantage==='opponent'} open={openPlayer}/>
              </div>) : <p className="panel-description">Starting lineups unavailable in the ESPN matchup response.</p>}
              <div className="live-explainer"><span className="game-pill playing"><span className="live-dot"/>IN PLAY</span> Live NFL game · points scored update as ESPN publishes them.
                <span className="game-pill final">FINAL</span> Completed game. <span className="game-pill scheduled">UPCOMING</span> Not yet underway.
                {strategy.live_status!=='available' && <strong> NFL game-status feed unavailable.</strong>}
                {strategy.live_as_of && <small>Last checked: {new Date(strategy.live_as_of).toLocaleTimeString()}</small>}
              </div>
            </div>
            <div className="panel-head"><div><span className="panel-kicker">POTENTIAL IMPROVEMENTS</span><h2>Bench-to-starter upgrades</h2></div></div>
            {strategy.possible_lineup_upgrades?.length ? strategy.possible_lineup_upgrades.map((move,i)=><div className="upgrade-row" key={i}><span>Start <strong>{move.start}</strong> instead of <strong>{move.sit}</strong> ({move.slot})</span><strong>+{points(move.projected_gain)} proj. pts</strong></div>) : <p className="panel-description">No positive single-player projection swaps detected. Waivers may still be worth exploring.</p>}
            <p className="panel-description">Lineup swaps are alternatives, not additive. Confirm kickoff/lock status on ESPN before acting.</p>
            <button className="button-action padded-button" onClick={()=>void send(undefined,'What do I need to do to win this week, using my opponent projections and lineup?')}>Ask Claude for a game plan <ArrowRight size={14}/></button>
          </>}
          <div className="recap-panel">
            <div className="recap-heading"><div><span className="panel-kicker">POSTGAME · CLAUDE</span><h2>Weekly recap</h2></div>
              <label>Week <select value={recapWeek ?? league?.week ?? 1} onChange={e=>{setRecapWeek(Number(e.target.value));setRecap(null);setRecapStatus(null);setRecapError('')}}>
                {Array.from({length:league?.week || 1},(_,i)=>i+1).map(w=><option key={w} value={w}>{w}</option>)}
              </select></label>
            </div>
            <p className="panel-description">A recap unlocks once the final NFL game of the selected week is finished and ESPN Fantasy has reported both teams' scores. Checking the status is free.</p>
            <p className="panel-description">{recapStatus?.note || (recapError ? '' : 'Checking ESPN game results…')}</p>
            {recapStatus?.status==='ready' && !recap && <button className="button-action" onClick={()=>void generateRecap()} disabled={generatingRecap}>{generatingRecap?'Generating…':recapStatus.cached?'View saved recap (free)':'Generate recap with Claude (1 call)'} <Sparkles size={14}/></button>}
            {recapError && <p className="detail-error">{recapError}</p>}
            {recap && <div className="recap-result"><RichText text={recap.analysis}/><p className="detail-muted">Week {recap.week} · {recap.cached?'Saved locally · no new tokens':`${(recap.input_tokens+recap.output_tokens).toLocaleString()} tokens · 1 Claude call`}</p></div>}
          </div>
        </section>}

        {tab==='league' && <section className="panel full-panel league-details">
          <div className="panel-head"><div><span className="panel-kicker">YOUR ESPN LEAGUE</span><h2>League explorer</h2></div></div>
          <div className="league-controls">{(['standings','matchups','faab','schedule','activity'] as const).map(t=><button key={t} className={leagueSubTab===t?'selected':''} onClick={()=>setLeagueSubTab(t)}>{t==='faab'?'FAAB':t.charAt(0).toUpperCase()+t.slice(1)}</button>)}</div>
          {leagueSubTab==='standings' && (standings ? <div className="league-table-wrap"><table className="league-table"><thead><tr><th>Team</th><th>W–L–T</th><th>PF</th><th>PA</th></tr></thead><tbody>{standings.teams.map(t=><tr key={t.team_id} onClick={()=>{if(t.team_id!==league?.my_team_id)setSelectedTeam(t.team_id)}}><td><strong>{t.rank}. {t.name}</strong></td><td>{t.wins}-{t.losses}-{t.ties}</td><td>{points(t.points_for)}</td><td>{points(t.points_against)}</td></tr>)}</tbody></table></div>:<p className="panel-description">{leagueErrors.standings || 'Loading standings…'}</p>)}
          {leagueSubTab==='matchups' && <>
            <div className="matchup-week-filter"><label htmlFor="league-week">ESPN matchup week</label><select id="league-week" value={leagueMatchupsWeek ?? league?.week ?? 1} onChange={e=>setLeagueMatchupsWeek(Number(e.target.value))}>{Array.from({length:18},(_,i)=><option value={i+1} key={i}>{`Week ${i+1}`}</option>)}</select></div>
            {leagueMatchups?.note && <p className="panel-description">{leagueMatchups.note}</p>}
            {!leagueMatchups && <p className="panel-description">Loading week matchups…</p>}
            {leagueMatchups?.matchups.map((m,i)=><div className="league-matchup-row" key={i}>
              <button disabled={m.home?.team_id===league?.my_team_id} onClick={()=>{if(m.home?.team_id && m.home.team_id!==league?.my_team_id) setSelectedTeam(m.home.team_id)}}><strong>{m.home?.name || 'BYE'}</strong><span>{points(m.home?.projected_final)} projected · {points(m.home?.points_scored)} scored</span></button>
              <span>VS</span>
              <button disabled={m.away?.team_id===league?.my_team_id} onClick={()=>{if(m.away?.team_id && m.away.team_id!==league?.my_team_id) setSelectedTeam(m.away.team_id)}}><strong>{m.away?.name || 'BYE'}</strong><span>{points(m.away?.projected_final)} projected · {points(m.away?.points_scored)} scored</span></button>
            </div>)}
          </>}
          {leagueSubTab==='faab' && (faab ? faab.enabled ? <><p className="panel-description">Initial budget: ${points(faab.initial_budget)} · Minimum bid: ${faab.minimum_bid ?? '—'} · Remaining budgets are estimates.</p><div className="league-table-wrap"><table className="league-table"><thead><tr><th>Team</th><th>Spent</th><th>Est. left</th><th>Priority</th></tr></thead><tbody>{faab.teams.map(t=><tr key={t.team_id}><td>{t.name}</td><td>${points(t.spent)}</td><td>${points(t.remaining)}</td><td>{t.waiver_priority ?? '—'}</td></tr>)}</tbody></table></div><p className="panel-description">{faab.note}</p></>:<p className="panel-description">{faab.note}</p>:<p className="panel-description">{leagueErrors.faab || 'Loading FAAB…'}</p>)}
          {leagueSubTab==='schedule' && (schedule ? <>
            <div className="schedule-top"><p className="panel-description">One matchup per pair, grouped by week. Scores appear when ESPN provides them.</p>
              <label>Show week <select value={scheduleWeek} onChange={e=>setScheduleWeek(e.target.value==='all'?'all':e.target.value==='current'?'current':Number(e.target.value))}>
                <option value="current">Current week ({league?.week || '—'})</option><option value="all">All weeks</option>
                {(schedule.weeks || [...new Set(schedule.schedule.map(r=>r.week))]).map(w=><option key={w} value={w}>Week {w}</option>)}
              </select></label>
            </div>
            {(scheduleWeek==='all'?(schedule.weeks || [...new Set(schedule.schedule.map(r=>r.week))]):[scheduleWeek==='current'?(league?.week || 1):scheduleWeek]).map(w=><div className="schedule-week" key={w}>
              <h3>Week {w}{w === league?.week ? <small> CURRENT</small>:null}</h3>
              <div className="schedule-matchups">{schedule.schedule.filter(r=>r.week===w).map(r=><div className="schedule-game" key={`${r.week}-${r.team_id}-${r.opponent_id}`}>
                <div className="schedule-versus"><strong>{r.team}</strong><span>{r.bye?'BYE':'vs'}</span><strong>{r.bye?'No opponent':r.opponent}</strong></div>
                <div className="schedule-result">{r.score != null && r.opponent_score != null && w < (league?.week || 1) ? `${points(r.score)} – ${points(r.opponent_score)}` : r.result && w < (league?.week || 1) ? r.result : w===league?.week?'This week':'Scheduled'}</div>
              </div>)}{!schedule.schedule.some(r=>r.week===w)&&<p className="panel-description">No ESPN matchups for this week.</p>}</div>
            </div>)}
          </>:<p className="panel-description">{leagueErrors.schedule || 'Loading schedule…'}</p>)}
          {leagueSubTab==='activity' && (transactions ? transactions.transactions.length ? <div className="transactions-list">{transactions.transactions.map((event,i)=><div key={i} className="transaction-row"><small>{event.timestamp_ms ? new Date(event.timestamp_ms).toLocaleString(): 'Date unknown'}</small>{event.actions.map((a,j)=><div key={j}>{a.team}: {a.type} — <strong>{a.player}</strong>{a.bid && a.bid > 0 ? ` ($${a.bid})`:''}</div>)}</div>)}</div>:<p className="panel-description">No recent completed transactions returned.</p>:<p className="panel-description">{leagueErrors.activity || 'Loading league activity…'}</p>)}
          <div className="team-inspector"><div className="panel-head"><div><span className="panel-kicker">OPPONENT INTELLIGENCE</span><h2>Team roster inspector</h2></div><select value={selectedTeam ?? ''} onChange={e=>setSelectedTeam(e.target.value?Number(e.target.value):null)}><option value="">Select team…</option>{standings?.teams.filter(t=>t.team_id !== league?.my_team_id).map(t=><option key={t.team_id} value={t.team_id}>{t.name}</option>)}</select></div>
            {selectedTeam!==null && !opponentRoster && <p className="panel-description">Loading roster…</p>}
            {opponentRoster && <div className="opponent-roster-board">
              <p className="panel-description"><strong>{opponentRoster.name}</strong> · Week {opponentRoster.week} · ESPN lineup slots, with week-specific projections when available. Click any player for stats and analysis.</p>
              <RosterColumns title={`${opponentRoster.name} starters`} onOpen={openPlayer} players={opponentRoster.roster.map(p=>({
                espn_id:p.espn_id,name:p.name,position:p.position,team:p.nfl_team,
                lineup_slot:p.slot,opponent:p.opponent,injury_status:p.injury_status,
                espn_projection:p.projected_points,
              }))}/>
            </div>}
          </div>
          <div className="pending-note">Pending waiver claims: {claims?.status==='not_supported' ? claims.reason : 'Not verified by ESPN. Only completed transactions are shown.'}</div>
        </section>}

        {tab==='feed' && <section className="panel full-panel league-details">
          <div className="panel-head"><div><span className="panel-kicker">FANTASY FOOTBALL COVERAGE</span><h2>Fantasy News & My Team</h2></div>
            <button className="refresh-button" onClick={()=>{setNews(null);setNewsPage(1);setTeamNewsPage(1);setNewsError('');setRosterNews(null);setRosterNewsError('');setExpandedNewsPlayers({});setRosterNewsRefresh(n=>n+1)}}><RefreshCw size={15}/> Refresh coverage</button></div>
          <div className="league-controls">
            <button className={feedTab==='fantasy'?'selected':''} onClick={()=>setFeedTab('fantasy')}>Fantasy articles</button>
            <button className={feedTab==='team'?'selected':''} onClick={()=>setFeedTab('team')}>My Team News</button>
          </div>
          {feedTab==='fantasy' && <>
            <div className="league-controls">{['ALL',...(news?.sources||['ESPN','RotoWire','FantasyPros Articles'])].map(t=><button key={t} onClick={()=>{setFeedSource(t);setNewsPage(1)}} className={feedSource===t?'selected':''}>{t==='ALL'?'All sources':t}</button>)}</div>
            {newsError && <p className="panel-description">{newsError}</p>}
            {!news && !newsError && <p className="panel-description">Fetching fantasy football articles…</p>}
            {news?.stale && <p className="panel-description">Showing cached articles because the sources could not be refreshed.</p>}
            {news?.unavailable_sources?.length ? <p className="panel-description">Source unavailable: {news.unavailable_sources.join(', ')}</p>:null}
            {news && !news.articles.length && <p className="panel-description">No verified fantasy articles found in the available feeds.</p>}
            <div className="news-grid">{news?.articles.map(article=><a key={article.url} href={article.url} target="_blank" rel="noopener noreferrer" className="news-card">
              <div className="news-meta"><span>{article.source}</span><time>{article.published_at ? new Date(article.published_at).toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}) : 'Date unavailable'}</time></div>
              <h3>{article.title}</h3>{article.summary && <p>{article.summary}</p>}<small>Read full article ↗</small>
            </a>)}</div>
            {news && <PageControls page={news.page} totalPages={news.total_pages} total={news.total} label="News" onChange={setNewsPage}/>}
            <p className="panel-description">Non-fantasy NFL business and entertainment news is filtered out. Publisher links only; no paid Claude or FantasyPros API usage.</p>
          </>}
          {feedTab==='team' && <>
            <p className="panel-description">News for each player actually on your ESPN roster. Injury updates take priority when relevant. Roster-player coverage is fetched only when you open this tab and cached.</p>
            {rosterNewsError && <p className="panel-description">{rosterNewsError}</p>}
            {!rosterNews && !rosterNewsError && <p className="panel-description">Checking your roster players and available ESPN stories…</p>}
            {rosterNews && <p className="panel-description">Ranked fantasy coverage found for {rosterNews.matched_players} of {rosterNews.total_players} players. The two highest-ranked articles appear first, based on freshness, player relevance and injury context.{rosterNews.refreshed_at && <> Updated {new Date(rosterNews.refreshed_at).toLocaleString()}.</>}</p>}
            <div className="roster-news-list">{rosterNews?.players.slice((teamNewsPage-1)*10, teamNewsPage*10).map(p=><div className="roster-news-player" key={p.name}>
              <div className="roster-news-heading"><strong>{p.name}</strong><span>{p.position || 'Player'}</span>{p.injury_status && p.injury_status!=='ACTIVE' && <span className="injury">{p.injury_status}</span>}
                {p.espn_id != null && <button className="subtle-link" onClick={()=>openPlayer(p.espn_id!,p.name)}>Stats & profile ↗</button>}</div>
              {p.articles.length ? (expandedNewsPlayers[p.name] ? p.articles : p.articles.slice(0,2)).map(a=><a key={a.url} className="news-card" href={a.url} rel="noopener noreferrer" target="_blank"><div className="news-meta"><span>{a.source}</span><time>{a.published_at?new Date(a.published_at).toLocaleDateString(undefined,{month:'short',day:'numeric',year:'numeric',hour:'numeric',minute:'2-digit'}):'Date unavailable'}</time></div><h3>{a.title}</h3>{a.summary && <p>{a.summary}</p>}<small>Read article ↗</small></a>)
                : <p className="detail-muted">No verified fantasy article returned for this player. <a href={p.coverage_url} target="_blank" rel="noopener noreferrer">Search current {p.name} coverage ↗</a></p>}
              {p.articles.length>2 && <button type="button" className="news-expand-button" aria-expanded={!!expandedNewsPlayers[p.name]} onClick={()=>setExpandedNewsPlayers(prev=>({...prev,[p.name]:!prev[p.name]}))}>{expandedNewsPlayers[p.name] ? 'Show top 2 articles' : `View all ${p.articles.length} ranked articles`} <ChevronDown size={15}/></button>}
              {p.articles.length>0 && p.coverage_url && <a className="news-more-link" href={p.coverage_url} target="_blank" rel="noopener noreferrer">Search additional live coverage ↗</a>}
            </div>)}</div>
            {rosterNews && <PageControls page={teamNewsPage} totalPages={Math.max(1,Math.ceil(rosterNews.players.length/10))} total={rosterNews.players.length} label="My Team News" onChange={setTeamNewsPage}/>}
          </>}
        </section>}

        <section className="chat-panel" aria-label="Fantasy Coach chat"><div className="chat-heading"><div className="coach-avatar"><Bot size={23}/></div><div><div className="coach-label"><h2>Ask Fantasy Coach</h2><span className="available"><span/> Ready to analyze</span></div><p>Personalized strategy, powered by your league data.</p></div><span className="chat-model">CLAUDE · v7.21</span></div>
          <div className="chat-body">{messages.length===0 ? <div className="chat-welcome"><div className="chat-welcome-icon"><MessageCircle size={21}/></div><h3>What are we figuring out today?</h3><p>Compare players, explore waivers, or ask for the best move for your roster.</p><div className="suggestions">{SUGGESTIONS.map(s=><button onClick={()=>void send(undefined,s)} key={s} disabled={sending}>{s}<ArrowRight size={13}/></button>)}</div></div> : <div className="messages">{messages.map(message=><div className={`message ${message.role}`} key={message.id}><div className="message-avatar">{message.role === 'assistant' ? <Bot size={17}/> : <span>YOU</span>}</div><div className="message-main"><div className="message-author">{message.role === 'assistant' ? 'Fantasy Coach' : 'You'}</div><RichText text={message.text}/>{message.usage && <span className="usage"><Check size={12}/>{moneyTokens(message.usage)}</span>}</div></div>)}{sending && <div className="typing"><span/><span/><span/> Analyzing league data…</div>}<div ref={chatBottom}/></div>}</div>
          {chatError && <div className="chat-error"><CircleAlert size={15}/>{chatError}</div>}
          <form className="composer" onSubmit={e=>void send(e)}><textarea id="coach-input" rows={2} maxLength={2000} placeholder="Ask about your lineup, trades, waivers, or player value…" value={input} onChange={e=>setInput(e.target.value)} onKeyDown={e=>{if(e.key==='Enter' && !e.shiftKey){e.preventDefault();void send()}}} disabled={sending}/><button aria-label="Send message" type="submit" disabled={sending || !input.trim()}>{sending ? <Clock3 size={19}/> : <Send size={19}/>}</button></form><div className="composer-foot"><span><ShieldCheck size={12}/> ESPN-derived scoring · No data guesswork</span><span>ENTER TO SEND <ChevronDown size={12}/> SHIFT+ENTER FOR NEW LINE</span></div>
        </section>
        <footer>FANTASY COACH · LOCAL DEVELOPMENT <span>Built to support better decisions, not guarantee outcomes.</span></footer>
      </div>
    </main>
    {tab==='settings' && <section className="panel full-panel"><div className="panel-head"><div><span className="panel-kicker">EMAIL SUBSCRIPTION · CONFIRMATION REQUIRED</span><h2>Fantasy Coach weekly updates</h2></div><Mail size={24}/></div>
      <form className="email-settings" onSubmit={e=>void submitEmail(e)}>
        <label className="email-field">Email address<input type="email" required value={email} onChange={e=>setEmail(e.target.value)} placeholder="you@example.com" autoComplete="email" /></label>
        {([
          ['thursday_lineup','Thursday morning optimal lineup and projections'],
          ['monday_recap','Weekly Claude recap after every NFL game is final'],
          ['injury_news','Relevant news links for injured players on my team'],
          ['bye_alerts','Weekly bye-week alerts'],
          ['auto_swap','Automatically replace OUT / IR / suspended / bye starters on ESPN (advanced)'],
        ] as const).map(([key,label])=><label className="email-option" key={key}><input type="checkbox" checked={emailPrefs[key]} onChange={e=>setEmailPrefs(prev=>({...prev,[key]:e.target.checked}))}/><span>{label}</span></label>)}
        <p className="detail-muted">Email delivery requires server SMTP settings and a scheduled job. You must click the confirmation link before any messages are sent. Automatic ESPN lineup swaps also require separate server-level opt-ins and refuse unverifiable or locked players. Claude recaps may incur token charges.</p>
        <button type="submit" disabled={emailSending} className="button-action">{emailSending?'Sending…':'Subscribe & email me a confirmation'} <Mail size={16}/></button>
        {emailError && <div className="detail-error">{emailError}</div>}
        {emailMessage && <div className="automation-success">{emailMessage}</div>}
      </form>
    </section>}

    {selectedPlayer && <PlayerDrawer selected={selectedPlayer} onClose={()=>setSelectedPlayer(null)} week={league?.week}/>}
  </div>
}
