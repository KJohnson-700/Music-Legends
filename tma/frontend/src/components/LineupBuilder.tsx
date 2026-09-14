/**
 * Phase 4 lineup builder: pick 3 cards in order, one ability, respecting the
 * "max 2 per family" rule. Pure UI — validation is repeated server-side.
 */
export const FAMILY_STYLE: Record<string, { label: string; color: string; icon: string }> = {
  HIP_HOP:    { label: 'Hip-Hop',    color: '#FF4E9A', icon: '🎤' },
  POP:        { label: 'Pop',        color: '#F4A800', icon: '✨' },
  ROCK:       { label: 'Rock',       color: '#E74C3C', icon: '🎸' },
  ELECTRONIC: { label: 'Electronic', color: '#4488FF', icon: '🎛️' },
  SOUL:       { label: 'Soul',       color: '#9B59B6', icon: '🎷' },
  NEUTRAL:    { label: 'Neutral',    color: '#95A5A6', icon: '🎵' },
}

export const RING: Record<string, string> = {
  HIP_HOP: 'POP', POP: 'ROCK', ROCK: 'ELECTRONIC', ELECTRONIC: 'SOUL', SOUL: 'HIP_HOP',
}

export const ABILITIES: { key: string; name: string; hint: string; needsSlot: 'own' | 'opp' | null }[] = [
  { key: '',      name: 'None',  hint: 'No ability this battle.', needsSlot: null },
  { key: 'swap',  name: 'Swap',  hint: 'If you lose round 1, slots 2 and 3 switch places.', needsSlot: null },
  { key: 'amp',   name: 'Amp',   hint: '+20% power to one of your slots.', needsSlot: 'own' },
  { key: 'scout', name: 'Scout', hint: "Reveal the genre of one of the opponent's slots before you commit.", needsSlot: 'opp' },
]

export type LineupCard = {
  card_id: string; name: string; title?: string; rarity: string; power: number
  genre_family: string; momentum?: boolean; image_url?: string
}

export function FamilyBadge({ family, small }: { family: string; small?: boolean }) {
  const f = FAMILY_STYLE[family] || FAMILY_STYLE.NEUTRAL
  return (
    <span style={{
      display: 'inline-block', padding: small ? '1px 6px' : '2px 8px', borderRadius: 999,
      background: `${f.color}22`, color: f.color, border: `1px solid ${f.color}66`,
      fontSize: small ? 10 : 11, fontWeight: 700, whiteSpace: 'nowrap',
    }}>{f.icon} {f.label}</span>
  )
}

export function familyCounts(cards: LineupCard[]) {
  const counts: Record<string, number> = {}
  for (const c of cards) if (c.genre_family && c.genre_family !== 'NEUTRAL') counts[c.genre_family] = (counts[c.genre_family] || 0) + 1
  return counts
}

type Props = {
  cards: LineupCard[]
  lineup: string[]
  onChange: (ids: string[]) => void
  ability: string
  abilitySlot: number | null
  onAbilityChange: (ability: string, slot: number | null) => void
  abilityLocked?: string | null          // e.g. 'scout' once the reveal has been used
  scoutInfo?: { slot: number; family: string } | null
  onScout?: (slot: number) => void
  canScout?: boolean
  maxSameFamily?: number
  rarityColors: Record<string, string>
}

export default function LineupBuilder(p: Props) {
  const maxSame = p.maxSameFamily ?? 2
  const byId: Record<string, LineupCard> = {}
  for (const c of p.cards) byId[c.card_id] = c
  const chosen = p.lineup.map(id => byId[id]).filter(Boolean)
  const counts = familyCounts(chosen)
  const overCap = Object.entries(counts).filter(([, n]) => n > maxSame)

  const toggle = (id: string) => {
    if (p.lineup.includes(id)) return p.onChange(p.lineup.filter(x => x !== id))
    if (p.lineup.length >= 3) return
    p.onChange([...p.lineup, id])
  }

  const ability = ABILITIES.find(a => a.key === (p.abilityLocked || p.ability)) || ABILITIES[0]

  return (
    <div style={{ background: '#1a1740', border: '1px solid #2a2760', borderRadius: 10, padding: 12, marginBottom: 12 }}>
      <div style={{ color: '#F4A800', fontWeight: 700, marginBottom: 4 }}>Your lineup (best of 3)</div>
      <div style={{ color: '#8888aa', fontSize: 12, marginBottom: 10 }}>
        Tap cards in the order they fight. Round 1 faces their slot 1. Max {maxSame} per genre.
      </div>

      {/* Slots */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 10 }}>
        {[0, 1, 2].map(i => {
          const c = chosen[i]
          const amped = (p.abilityLocked || p.ability) === 'amp' && p.abilitySlot === i
          return (
            <div key={i} onClick={() => c && toggle(c.card_id)} style={{
              flex: 1, minHeight: 64, borderRadius: 8, padding: 8, cursor: c ? 'pointer' : 'default',
              background: c ? '#0f0d2a' : '#12102e', border: `2px dashed ${c ? (p.rarityColors[c.rarity] || '#2a2760') : '#2a2760'}`,
              textAlign: 'center',
            }}>
              <div style={{ color: '#8888aa', fontSize: 10 }}>SLOT {i + 1}{amped ? ' ⚡AMP' : ''}</div>
              {c ? (
                <>
                  <div style={{ fontSize: 12, fontWeight: 700, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.name}</div>
                  <div style={{ color: p.rarityColors[c.rarity] || '#fff', fontWeight: 700 }}>{c.power}{c.momentum ? ' 🔥' : ''}</div>
                  <FamilyBadge family={c.genre_family} small />
                </>
              ) : <div style={{ color: '#55557a', fontSize: 12, marginTop: 8 }}>empty</div>}
            </div>
          )
        })}
      </div>
      {overCap.length > 0 && (
        <div style={{ color: '#ff6b6b', fontSize: 12, marginBottom: 8 }}>
          Too many {FAMILY_STYLE[overCap[0][0]]?.label || overCap[0][0]} cards — max {maxSame} per genre.
        </div>
      )}

      {/* Ability */}
      <div style={{ color: '#F4A800', fontWeight: 700, fontSize: 13, margin: '6px 0 4px' }}>Ability (one per battle)</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 6 }}>
        {ABILITIES.map(a => {
          const locked = !!p.abilityLocked
          const active = (p.abilityLocked || p.ability) === a.key
          return (
            <button key={a.key || 'none'} disabled={locked && !active}
              onClick={() => !locked && p.onAbilityChange(a.key, a.needsSlot ? (p.abilitySlot ?? 0) : null)}
              style={{
                background: active ? '#6B2EBE' : '#0f0d2a', color: '#fff', border: `1px solid ${active ? '#9B59B6' : '#2a2760'}`,
                borderRadius: 8, padding: '6px 10px', fontSize: 12, fontWeight: 700, opacity: locked && !active ? 0.4 : 1,
              }}>{a.name}</button>
          )
        })}
      </div>
      <div style={{ color: '#8888aa', fontSize: 11, marginBottom: 6 }}>{ability.hint}</div>
      {ability.needsSlot && (
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 6 }}>
          <span style={{ color: '#8888aa', fontSize: 12 }}>{ability.needsSlot === 'own' ? 'Your slot:' : "Opponent's slot:"}</span>
          {[0, 1, 2].map(i => (
            <button key={i} disabled={!!p.abilityLocked}
              onClick={() => p.onAbilityChange(ability.key, i)}
              style={{
                background: p.abilitySlot === i ? '#F4A800' : '#0f0d2a', color: p.abilitySlot === i ? '#000' : '#fff',
                border: '1px solid #2a2760', borderRadius: 6, padding: '4px 10px', fontSize: 12, fontWeight: 700,
              }}>{i + 1}</button>
          ))}
          {ability.key === 'scout' && p.canScout && !p.scoutInfo && p.abilitySlot !== null && (
            <button onClick={() => p.onScout && p.onScout(p.abilitySlot as number)}
              style={{ marginLeft: 'auto', background: '#4488FF', color: '#fff', border: 'none', borderRadius: 6, padding: '4px 10px', fontSize: 12, fontWeight: 700 }}>
              🔍 Reveal
            </button>
          )}
        </div>
      )}
      {p.scoutInfo && (
        <div style={{ color: '#4488FF', fontSize: 12, marginBottom: 6 }}>
          🔍 Their slot {p.scoutInfo.slot + 1} is <FamilyBadge family={p.scoutInfo.family} small />
          {RING[p.scoutInfo.family] && <> — beaten by {FAMILY_STYLE[Object.keys(RING).find(k => RING[k] === p.scoutInfo!.family) || 'NEUTRAL']?.label}</>}
        </div>
      )}

      {/* Card picker */}
      <div style={{ color: '#F4A800', fontWeight: 700, fontSize: 13, margin: '8px 0 4px' }}>Your cards</div>
      <div style={{ maxHeight: 220, overflowY: 'auto', border: '1px solid #2a2760', borderRadius: 8 }}>
        {p.cards.map(c => {
          const idx = p.lineup.indexOf(c.card_id)
          return (
            <div key={c.card_id} onClick={() => toggle(c.card_id)} style={{
              display: 'flex', alignItems: 'center', gap: 8, padding: '6px 8px', cursor: 'pointer',
              background: idx >= 0 ? '#2a1760' : 'transparent', borderBottom: '1px solid #1f1c48',
            }}>
              <div style={{
                width: 22, height: 22, borderRadius: 11, background: idx >= 0 ? '#F4A800' : '#0f0d2a',
                color: idx >= 0 ? '#000' : '#55557a', fontSize: 12, fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>{idx >= 0 ? idx + 1 : '+'}</div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 12, fontWeight: 700, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.name}</div>
                <div style={{ fontSize: 10, color: p.rarityColors[c.rarity] || '#8888aa' }}>{(c.rarity || 'common').toUpperCase()}</div>
              </div>
              <FamilyBadge family={c.genre_family} small />
              <div style={{ fontWeight: 700, minWidth: 34, textAlign: 'right' }}>{c.power}{c.momentum ? '🔥' : ''}</div>
            </div>
          )
        })}
        {p.cards.length === 0 && <div style={{ color: '#8888aa', fontSize: 12, padding: 10 }}>No cards yet. Open a pack first.</div>}
      </div>
      <div style={{ color: '#55557a', fontSize: 10, marginTop: 6 }}>
        Ring: Hip-Hop › Pop › Rock › Electronic › Soul › Hip-Hop. Counter = ×1.35, countered = ×0.85. 🔥 = trending this week (+10%).
      </div>
    </div>
  )
}
