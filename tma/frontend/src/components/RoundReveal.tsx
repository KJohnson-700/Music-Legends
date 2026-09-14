/**
 * Phase 4 result screen: reveal rounds one at a time from the persisted
 * per-round breakdown (base → momentum → genre → ability → crit).
 */
import { useEffect, useState } from 'react'
import { FamilyBadge } from './LineupBuilder'

type Side = {
  card_id: string; name: string; rarity: string; image_url?: string; youtube_url?: string
  family: string; base: number; final: number; momentum: boolean
  genre_effect: 'counter' | 'countered' | 'neutral'; amped: boolean; critical_hit: boolean
}
type Round = { round: number; winner: number; power_difference: number; player1: Side; player2: Side }

type Props = {
  result: any
  meIsPlayer1: boolean
  rarityColors: Record<string, string>
}

function Tags({ s }: { s: Side }) {
  const tags: { t: string; c: string }[] = []
  if (s.momentum) tags.push({ t: '🔥 +10%', c: '#FF7A00' })
  if (s.genre_effect === 'counter') tags.push({ t: '⬆ counter ×1.35', c: '#2ECC71' })
  if (s.genre_effect === 'countered') tags.push({ t: '⬇ countered ×0.85', c: '#ff6b6b' })
  if (s.amped) tags.push({ t: '⚡ amp +20%', c: '#F4A800' })
  if (s.critical_hit) tags.push({ t: '💥 CRIT ×1.5', c: '#FF4E9A' })
  if (!tags.length) return <div style={{ color: '#55557a', fontSize: 10 }}>no modifiers</div>
  return <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, justifyContent: 'center' }}>
    {tags.map(x => <span key={x.t} style={{ color: x.c, fontSize: 10, fontWeight: 700 }}>{x.t}</span>)}
  </div>
}

function SideCard({ s, won, colors }: { s: Side; won: boolean; colors: Record<string, string> }) {
  return (
    <div style={{
      flex: 1, background: '#0f0d2a', borderRadius: 10, padding: 8, textAlign: 'center',
      border: `2px solid ${won ? '#2ECC71' : (colors[s.rarity] || '#2a2760')}`, opacity: won ? 1 : 0.75,
    }}>
      {s.image_url && <img src={s.image_url} alt={s.name} style={{ width: '100%', borderRadius: 6, aspectRatio: '16/9', objectFit: 'cover' }} />}
      <div style={{ fontSize: 12, fontWeight: 700, marginTop: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.name}</div>
      <div style={{ margin: '2px 0' }}><FamilyBadge family={s.family} small /></div>
      <div style={{ fontSize: 16, fontWeight: 700, color: colors[s.rarity] || '#fff' }}>
        {s.base !== s.final ? <><span style={{ color: '#8888aa', fontSize: 12 }}>{s.base} → </span>{s.final}</> : s.final}
      </div>
      <Tags s={s} />
    </div>
  )
}

export default function RoundReveal({ result, meIsPlayer1, rarityColors }: Props) {
  const rounds: Round[] = result.rounds || []
  const [shown, setShown] = useState(0)
  useEffect(() => {
    setShown(0)
    let i = 0
    const id = setInterval(() => {
      i += 1
      setShown(i)
      if (i >= rounds.length + 1) clearInterval(id)
    }, 1100)
    return () => clearInterval(id)
  }, [result?.seed, rounds.length])

  const winner: number = result.winner
  const iWon = winner !== 0 && ((winner === 1) === meIsPlayer1)
  const done = shown > rounds.length
  const me = meIsPlayer1 ? result.challenger : result.opponent
  const them = meIsPlayer1 ? result.opponent : result.challenger
  const decided = result.decided_by === 'total_power' ? 'decided on total power' : result.decided_by === 'tie' ? 'dead even' : `${result.rounds_won?.[winner - 1] ?? ''} rounds to ${result.rounds_won?.[2 - winner] ?? ''}`

  return (
    <div style={{ padding: '16px 12px 100px' }}>
      <h3 style={{ color: '#F4A800', fontSize: 20, textAlign: 'center', marginBottom: 4 }}>
        {done ? (winner === 0 ? '🤝 Draw!' : iWon ? '🏆 You Won!' : '😔 You Lost') : `⚔️ Round ${Math.min(shown + 1, rounds.length)}`}
      </h3>
      {done && <div style={{ color: '#8888aa', fontSize: 12, textAlign: 'center', marginBottom: 8 }}>{decided}</div>}

      {rounds.slice(0, Math.max(1, shown)).map((r, i) => {
        const left = meIsPlayer1 ? r.player1 : r.player2
        const right = meIsPlayer1 ? r.player2 : r.player1
        const leftWon = r.winner !== 0 && ((r.winner === 1) === meIsPlayer1)
        const rightWon = r.winner !== 0 && !leftWon
        const revealed = i < shown
        return (
          <div key={r.round} style={{ marginBottom: 10, opacity: revealed ? 1 : 0.35, transition: 'opacity .4s' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', color: '#8888aa', fontSize: 11, margin: '0 2px 4px' }}>
              <span>ROUND {r.round}</span>
              <span style={{ color: r.winner === 0 ? '#F4A800' : leftWon ? '#2ECC71' : '#ff6b6b', fontWeight: 700 }}>
                {revealed ? (r.winner === 0 ? 'tie' : leftWon ? 'you take it' : 'they take it') : '…'}
              </span>
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'stretch' }}>
              <SideCard s={left} won={revealed && leftWon} colors={rarityColors} />
              <div style={{ alignSelf: 'center', color: '#F4A800', fontWeight: 700 }}>VS</div>
              <SideCard s={right} won={revealed && rightWon} colors={rarityColors} />
            </div>
          </div>
        )
      })}

      {result.swaps?.length > 0 && done && (
        <div style={{ color: '#9B59B6', fontSize: 12, textAlign: 'center', marginBottom: 6 }}>
          {result.swaps.map((s: any) => <div key={s.player}>🔁 {(s.player === 1) === meIsPlayer1 ? 'You' : 'They'} swapped slots 2 and 3 after round 1</div>)}
        </div>
      )}

      {done && (
        <div style={{ background: '#1a1740', border: '1px solid #2a2760', borderRadius: 10, padding: 10, marginTop: 6, textAlign: 'center' }}>
          <div style={{ color: '#2ECC71', fontWeight: 700 }}>+{me?.gold_reward} 💰 &nbsp; +{me?.xp_reward} XP</div>
          <div style={{ color: '#8888aa', fontSize: 11, marginTop: 2 }}>Opponent: +{them?.gold_reward} 💰 · wager {result.wager} ({result.wager_tier})</div>
          {result.reveals?.length > 0 && <div style={{ color: '#4488FF', fontSize: 11, marginTop: 4 }}>🔍 Scout was used this battle</div>}
        </div>
      )}
    </div>
  )
}
