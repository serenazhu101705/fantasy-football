export interface Player {
  name: string
  position?: string
  team?: string
  lineup_slot?: string
  opponent?: string
  espn_projection?: number | null
  fantasypros_projection?: number | null
  weekly_score_advisory?: number | null
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
