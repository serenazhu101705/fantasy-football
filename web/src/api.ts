export interface Player {
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
  name: string
  position?: string | null
  nfl_team?: string | null
  slot?: string | null
  starter: boolean
  projected_points: number | null
  actual_points: number | null
  injury_status?: string | null
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
  projected_margin?: number | null
  projected_favorite?: string
  extra_points_above_my_projection_to_take_lead?: number | null
  target_to_exceed_opponent_projection?: number | null
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
  schedule:Array<{week:number,team_id:number,team:string,opponent_id:number | null,opponent:string | null,score:number | null,result:string | null,bye:boolean}>
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
  roster:Array<{espn_id:number,name:string,position:string,nfl_team:string,slot:string,injury_status:string | null, projected_points:number | null, actual_points:number | null, weekly_slot:string | null}>
}
export interface ClaimsData {status:string,claims:unknown,reason:string}

export interface LeagueMatchupsData {
  week:number
  status?:string
  note?:string
  matchups:Array<{home:MatchupSide|null,away:MatchupSide|null,playoff:boolean}>
}
