/** Shared ESPN-style fantasy starting slot order for all roster views. */
export const STARTER_ORDER = ['QB', 'RB', 'WR', 'TE', 'FLEX', 'SUPERFLEX', 'D/ST', 'K'] as const

export function canonicalSlot(raw?: string | null): string {
  const slot = String(raw || '').trim().toUpperCase()
  if (['RB/WR/TE', 'WR/RB/TE', 'WR/TE/RB', 'FLEX', 'RB/WR', 'WR/TE'].includes(slot)) return 'FLEX'
  if (['OP', 'QB/RB/WR/TE', 'SUPERFLEX'].includes(slot)) return 'SUPERFLEX'
  if (slot === 'DST' || slot === 'DEF') return 'D/ST'
  if (['IR', 'IR+', 'INJURED_RESERVE'].includes(slot)) return 'IR'
  if (['BE', 'BENCH'].includes(slot)) return 'BE'
  return slot || 'BE'
}

export function orderRoster<T>(rows: T[], getSlot: (row: T) => string | null | undefined) {
  const placed = rows.map((row, originalIndex) => ({row, slot:canonicalSlot(getSlot(row)), originalIndex}))
  const priority = (slot:string) => {
    if (slot === 'BE') return 90
    if (slot === 'IR') return 100
    const rank = STARTER_ORDER.indexOf(slot as typeof STARTER_ORDER[number])
    return rank < 0 ? 80 : rank
  }
  placed.sort((a,b) => priority(a.slot) - priority(b.slot) || a.originalIndex - b.originalIndex)
  const counts: Record<string, number> = {}
  return placed.map(item => {
    counts[item.slot] = (counts[item.slot] || 0) + 1
    // Slot index labels mirror ESPN's QB, RB1/RB2, WR1/WR2, TE, FLEX, D/ST, K.
    const label = ['RB','WR'].includes(item.slot) ? `${item.slot}${counts[item.slot]}` : item.slot
    return {...item, label, group:item.slot==='BE'?'bench':item.slot==='IR'?'ir':'starter'}
  })
}
