import { useEffect, useState } from 'react'

/** ESPN Fantasy player IDs are ESPN NFL athlete IDs for offensive players.
 * Defense/team entries may use different IDs, so treat failed images as missing.
 */
export function espnHeadshotUrl(id?: number | null): string | null {
  return typeof id === 'number' && Number.isSafeInteger(id) && id > 0
    ? `https://a.espncdn.com/i/headshots/nfl/players/full/${id}.png`
    : null
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  return (parts.length > 1 ? `${parts[0][0]}${parts[parts.length - 1][0]}` : (parts[0] || '?').slice(0, 2)).toUpperCase()
}

/** Displays verified-ID ESPN headshot URLs with a graceful missing-photo fallback. */
export function PlayerHeadshot({
  id, name, size = 'sm',
}: { id?: number | null, name: string, size?: 'sm' | 'lg' }) {
  const [failed, setFailed] = useState(false)
  useEffect(() => setFailed(false), [id])
  const src = espnHeadshotUrl(id)
  return <span className={`player-headshot player-headshot-${size}`} title={`${name} headshot`}>
    {src && !failed
      ? <img src={src} alt={`${name} headshot`} loading={size === 'lg' ? 'eager' : 'lazy'} decoding="async"
          onError={() => setFailed(true)} />
      : <span className="player-headshot-fallback" role="img" aria-label={`${name} photo unavailable`}>{initials(name)}</span>}
  </span>
}
