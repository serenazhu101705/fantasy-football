export interface Player {
  espn_id?: number | null
  name: string
  position?: string
  team?: string
  lineup_slot?: string
  opponent?: string
  espn_projection?: number | null
  fantasypros_projection?: number | null
  weekly_score_advisory?: number | null
  lineup_projection?: number | null
  lineup_projection_sources?: string[]
  ros_score_advisory?: number | null
  injury_status?: string | null
  on_bye_week?: boolean
  can_play_this_week?: boolean
}

export interface LeagueData {
  name: string | null
  week: number
  scoring: { scoring?: string, reception_points?: number, team_count?: number }
  roster_size: number
  version: string
  my_team_id: number
}

export interface WaiverBrowserData {
  requested_position: string
  candidate_pool: Player[]
  page: number
  page_size: number
  total: number
  total_pages: number
  scope: string
  loaded_pool_size: number
  note: string
}

export interface WaiverData {
  requested_position: string
  candidate_pool: Player[]
  transaction_model_result: string
  matching_candidates_loaded: number
  moves: Array<{ category: string, add: Player, drop?: Player, reason?: string }>
}

export interface LineupData {
  week: number
  lineup: Record<string, Player | null>
  projected_total: number | null
  selection_method?: string
  projection_complete?: boolean
  projection_coverage?: string
  explanation?: LineupExplanation
}

export interface LineupExplanation {
  method:string
  comparison_note:string
  rankings_note:string
  projected_total:number|null
  projected_coverage?:string
  slot_explanations: Record<string, {
    player:string
    projection:number|null
    projection_sources: Record<string,number>
    weekly_ecr_available:boolean
    best_eligible_nonstarter: {name:string,projection:number|null,sources:Record<string,number>}|null
    projection_advantage:number|null
    reason:string
  }>
}

export interface CoachReply {
  answer: string
  api_calls: number
  input_tokens: number
  output_tokens: number
  prefetched: boolean
  intent: string
}

export class ApiError extends Error {
  constructor(readonly status: number, message: string) { super(message) }
}

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, { ...options, headers: { 'Accept': 'application/json', ...(options.headers || {}) } })
  } catch {
    throw new ApiError(0, 'Cannot reach Fantasy Coach API. Start the Python backend on port 8000.')
  }
  const body = await response.json().catch(() => ({})) as Record<string, unknown>
  if (!response.ok) {
    const detail = body.detail
    throw new ApiError(response.status, typeof detail === 'string' ? detail : `Request failed (${response.status})`)
  }
  return body as T
}

export function post<T>(path: string, data?: object, session?: string): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(session ? { 'X-Fantasy-Session': session } : {}) },
    body: JSON.stringify(data || {}),
  })
}

// Live league intelligence (all read-only ESPN endpoints; no Claude cost).
export interface MatchupPlayer {
  espn_id?: number | null
  name: string
  position?: string | null
  nfl_team?: string | null
  slot?: string | null
  starter: boolean
  projected_points: number | null
  actual_points: number | null
  game_status?: 'LIVE' | 'FINAL' | 'SCHEDULED' | 'BYE' | 'UNKNOWN'
  game_status_detail?: string | null
  game_kickoff?: string | null
  injury_status?: string | null
  opponent?: string | null
}
export interface MatchupSide {
  team_id: number
  name: string
  points_scored: number | null
  projected_final: number | null
  roster: MatchupPlayer[]
}
export interface MatchupStrategy {
  week: number
  status: string
  my_team?: MatchupSide
  opponent?: MatchupSide | null
  espn_win_probability_pct?: number | null
  espn_opponent_win_probability_pct?: number | null
  win_probability_status?: string
  win_probability_source?: string
  win_probability_note?: string
  projected_margin?: number | null
  projected_favorite?: string
  extra_points_above_my_projection_to_take_lead?: number | null
  target_to_exceed_opponent_projection?: number | null
  head_to_head?: HeadToHeadSlot[]
  live_status?: 'available' | 'unavailable'
  live_as_of?: string
  live_source?: string
  live_note?: string
  possible_lineup_upgrades?: Array<{start:string,sit:string,slot:string,projected_gain:number,needs_lock_check:boolean}>
  notes?: string[]
  note?: string
}
export interface StandingsData {
  teams: Array<{team_id:number,name:string,wins:number,losses:number,ties:number,rank:number,points_for:number,points_against:number}>
}
export interface FaabData {
  enabled:boolean
  initial_budget:number | null
  minimum_bid?:number | null
  note:string
  teams:Array<{team_id:number,name:string,spent:number | null,remaining:number | null,waiver_priority?:number | null}>
}
export interface ScheduleData {
  weeks?:number[]
  schedule:Array<{week:number,team_id:number,team:string,opponent_id:number | null,opponent:string | null,score:number | null,opponent_score:number | null,result:string | null,bye:boolean}>
}
export interface TransactionData {
  status:string
  transactions:Array<{timestamp_ms:number,actions:Array<{team_id:number | null,team:string,type:string,player:string,bid:number | null}>}>
}
export interface TeamRosterData {
  team_id:number
  name:string
  week:number
  projection_status:string
  roster:Array<{espn_id:number,name:string,position:string,nfl_team:string,slot:string,injury_status:string | null, projected_points:number | null, actual_points:number | null, opponent?:string | null, weekly_slot:string | null}>
}
export interface ClaimsData {status:string,claims:unknown,reason:string}

export interface LeagueMatchupsData {
  week:number
  status?:string
  note?:string
  matchups:Array<{home:MatchupSide|null,away:MatchupSide|null,playoff:boolean}>
}

export interface RosterNewsData {
  status:string
  matched_players:number
  total_players:number
  note:string
  players:Array<{
    name:string
    position?:string
    injury_status?:string|null
    espn_id?:number|null
    status:'available'|'not_found'
    coverage_url?:string
    articles:NewsData['articles']
  }>
}

export interface NewsBrowseData extends NewsData {
  page:number
  page_size:number
  total:number
  total_pages:number
  selected_source:string
}

export interface NewsData {
  status:'available' | 'unavailable'
  articles:Array<{ title:string, summary:string, url:string, source:string, published_at:string | null }>
  sources:string[]
  unavailable_sources:string[]
  note:string
  cached?:boolean
  stale?:boolean
}

export interface HeadToHeadSlot {
  slot: string
  position_slot: string
  my_player: MatchupPlayer | null
  opponent_player: MatchupPlayer | null
  projection_margin: number | null
  projected_advantage: 'me' | 'opponent' | 'tie' | 'unknown'
}
export interface StatLine {
  key: string
  label: string
  value: number
}
export interface PlayerProfile {
  espn_id: number
  name: string
  nfl_team: string | null
  position: string | null
  injury_status: string | null
  week: number
  season_fantasy_points: number | null
  season_fantasy_ppg: number | null
  season_games_in_loaded_stats: number
  stat_coverage_weeks?:number
  season_stats_as_of_week?:number|null
  season_stats_source?:string
  season_stats_complete?:boolean
  available_weeks_stat_totals?:StatLine[]
  bio?: {height?:string,weight?:string,age?:number,college?:string,years_pro?:number,jersey?:string,source?:string}
  current_week: {projected_points:number|null,fantasy_points:number|null,stats:StatLine[],projected_stats:StatLine[]}
  last_completed_game: {week:number|null,game_date?:string|null,opponent?:string|null,fantasy_points:number|null,stats:StatLine[]} | null
  recent_games: Array<{week:number|null,game_date?:string|null,opponent?:string|null,fantasy_points:number|null,stats:StatLine[]}>
  source:string
  note?:string
}
export interface PlayerAnalysis {
  analysis:string
  cached:boolean
  source:string
  input_tokens:number
  output_tokens:number
  note?:string
}

export interface WeeklyRecapStatus {
  season:number
  week:number
  status:'ready'|'game_status_unavailable'|'schedule_incomplete'|'games_in_progress'|'matchup_unavailable'|'fantasy_scores_pending'
  nfl_games_seen?:number
  cached:boolean
  note:string
}
export interface WeeklyRecap {
  season:number
  week:number
  analysis:string
  source:string
  generated_at:string
  cached:boolean
  input_tokens:number
  output_tokens:number
}
