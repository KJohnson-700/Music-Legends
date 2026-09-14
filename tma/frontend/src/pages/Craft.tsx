/**
 * Crafting / duplicate fusion (Phase 4.5): pick four cards of one rarity, optionally
 * buy a Stars boost, roll for the next rarity up.
 */
import { useEffect, useRef, useState } from 'react'
import { openInvoice, hapticFeedbackNotificationOccurred } from '@telegram-apps/sdk'
import { createStarsInvoice, doCraft, getCraftOptions, getStarsOrder, previewCraft } from '../api/client'
import { FamilyBadge } from '../components/LineupBuilder'

const RARITY_COLORS: Record<string, string> = {
  common: '#95A5A6', rare: '#4488FF', epic: '#6B2EBE', legendary: '#F4A800', mythic: '#E74C3C',
}
type Pick = { card_id: string; name: string; rarity: string; quantity: number; genre_family?: string; power?: number; image_url?: string }

async function payWithStars(link: string): Promise<string> {
  if (openInvoice.isAvailable()) {
    try { return await openInvoice(link, 'url') } catch (e: any) { return e?.message === 'cancelled' ? 'cancelled' : 'failed' }
  }
  const tg = (window as any)?.Telegram?.WebApp
  if (tg?.openInvoice) return await new Promise<string>((resolve) => tg.openInvoice(link, (s: string) => resolve(s)))
  window.open(link, '_blank', 'noopener,noreferrer')
  return 'pending'
}

export default function Craft() {
  const [opts, setOpts] = useState<any>(null)
  const [rarity, setRarity] = useState<string>('common')
  const [picked, setPicked] = useState<string[]>([])           // card ids, duplicates allowed
  const [preview, setPreview] = useState<any>(null)
  const [boostOrder, setBoostOrder] = useState<{ order_id: string; target: string } | null>(null)
  const [busy, setBusy] = useState('')
  const [result, setResult] = useState<any>(null)
  const [error, setError] = useState('')
  const pollRef = useRef<any>(null)

  const load = async () => {
    try { const r = await getCraftOptions(); setOpts(r.data) } catch (e: any) { setError(e?.response?.data?.detail || 'Failed to load crafting') }
  }
  useEffect(() => { load(); return () => { if (pollRef.current) clearInterval(pollRef.current) } }, [])

  useEffect(() => {
    setPreview(null)
    if (picked.length !== 4) return
    previewCraft(picked).then(r => setPreview(r.data)).catch((e: any) => setError(e?.response?.data?.detail || 'Preview failed'))
  }, [picked])

  const group: Pick[] = opts?.groups?.[rarity] || []
  const countOf = (id: string) => picked.filter(x => x === id).length
  const add = (c: Pick) => {
    if (picked.length >= 4 || countOf(c.card_id) >= c.quantity) return
    setPicked([...picked, c.card_id])
  }
  const removeAt = (i: number) => setPicked(picked.filter((_, j) => j !== i))
  const target = opts?.targets?.[preview?.target_rarity]
  const boostReady = boostOrder && boostOrder.target === preview?.target_rarity

  const buyBoost = async () => {
    if (!preview) return
    setBusy('boost'); setError('')
    try {
      const res = await createStarsInvoice('craft_boost' as any, preview.target_rarity)
      const status = await payWithStars(res.data.invoice_link)
      if (status === 'paid' || status === 'pending') {
        let tries = 0
        await new Promise<void>((resolve) => {
          pollRef.current = setInterval(async () => {
            tries += 1
            try {
              const o = await getStarsOrder(res.data.order_id)
              if (o.data?.status === 'fulfilled') { clearInterval(pollRef.current); setBoostOrder({ order_id: res.data.order_id, target: preview.target_rarity }); resolve() }
              else if (o.data?.status === 'failed' || tries > 20) { clearInterval(pollRef.current); resolve() }
            } catch { if (tries > 20) { clearInterval(pollRef.current); resolve() } }
          }, 1500)
        })
      }
    } catch (e: any) {
      setError(e?.response?.data?.detail || 'Could not buy boost')
    } finally { setBusy('') }
  }

  const craft = async () => {
    if (picked.length !== 4) return
    const boosted = !!boostReady
    if (!window.confirm(`Fuse these 4 ${preview?.input_rarity} cards for a ${preview?.target_rarity}? ${boosted ? preview.boosted_pct : preview?.base_pct}% chance. Cards are consumed either way (one comes back on a miss).`)) return
    setBusy('craft'); setError('')
    try {
      const r = await doCraft(picked, boosted ? boostOrder!.order_id : undefined)
      setResult(r.data)
      if (hapticFeedbackNotificationOccurred.isAvailable()) hapticFeedbackNotificationOccurred(r.data.success ? 'success' : 'error')
      setPicked([]); setPreview(null); if (boosted) setBoostOrder(null)
      await load()
    } catch (e: any) {
      setError(e?.response?.data?.detail || 'Craft failed')
    } finally { setBusy('') }
  }

  if (result) {
    const o = result.output || {}
    const col = RARITY_COLORS[(o.rarity || 'common').toLowerCase()] || '#fff'
    return (
      <div style={{ padding: '20px 16px 100px', textAlign: 'center' }}>
        <h3 style={{ color: result.success ? '#2ECC71' : '#F4A800', fontSize: 22 }}>{result.success ? '✨ Fusion succeeded!' : '💨 Fusion fizzled'}</h3>
        <p style={{ color: '#8888aa', fontSize: 13 }}>
          {result.success ? `A ${result.target_rarity} card joins your collection.` : `Your ${result.input_rarity} cards were consumed — one came back.`} ({result.success_pct}% roll{result.boosted ? ', boosted' : ''})
        </p>
        <div style={{ display: 'inline-block', background: '#1a1740', border: `3px solid ${col}`, borderRadius: 14, padding: 12, width: 220, marginTop: 8 }}>
          {o.image_url && <img src={o.image_url} alt={o.name} style={{ width: '100%', borderRadius: 8, aspectRatio: '16/9', objectFit: 'cover' }} />}
          <div style={{ fontWeight: 700, marginTop: 6 }}>{o.name}</div>
          <div style={{ color: col, fontSize: 12, fontWeight: 700 }}>{(o.rarity || '').toUpperCase()} · {o.power}</div>
          <div style={{ marginTop: 4 }}><FamilyBadge family={o.genre_family || 'NEUTRAL'} small /></div>
        </div>
        <div>
          <button onClick={() => setResult(null)} style={{ marginTop: 16, padding: '10px 18px', background: '#6B2EBE', color: '#fff', border: 'none', borderRadius: 8, fontWeight: 700 }}>Craft again</button>
        </div>
      </div>
    )
  }

  return (
    <div style={{ padding: '16px 16px 100px' }}>
      <h3 style={{ color: '#F4A800', marginBottom: 4 }}>🧪 Craft</h3>
      <p style={{ color: '#8888aa', fontSize: 13, marginTop: 0 }}>
        Fuse <b>4 cards of one rarity</b> for a chance at the next rarity. Same genre on all four guarantees that genre.
      </p>
      {error && <div style={{ color: '#ff6b6b', fontSize: 12, marginBottom: 8 }}>{error}</div>}

      {/* rarity tabs */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 10, flexWrap: 'wrap' }}>
        {(opts?.rarity_order || []).slice(0, -1).map((r: string) => {
          const n = (opts?.groups?.[r] || []).reduce((a: number, c: Pick) => a + c.quantity, 0)
          const t = opts?.targets?.[opts.rarity_order[opts.rarity_order.indexOf(r) + 1]]
          return (
            <button key={r} onClick={() => { setRarity(r); setPicked([]) }} style={{
              background: rarity === r ? RARITY_COLORS[r] : '#1a1740', color: rarity === r ? '#000' : '#fff',
              border: `1px solid ${RARITY_COLORS[r]}`, borderRadius: 8, padding: '6px 10px', fontSize: 12, fontWeight: 700 }}>
              {r.toUpperCase()} ({n}) {t ? `· ${t.base_pct}%` : ''}
            </button>
          )
        })}
      </div>

      {/* slots */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
        {[0, 1, 2, 3].map(i => {
          const id = picked[i]
          const c = group.find(x => x.card_id === id)
          return (
            <div key={i} onClick={() => id && removeAt(i)} style={{ flex: 1, minHeight: 58, borderRadius: 8, padding: 6, textAlign: 'center',
              background: c ? '#0f0d2a' : '#12102e', border: `2px dashed ${c ? RARITY_COLORS[c.rarity] : '#2a2760'}`, cursor: c ? 'pointer' : 'default' }}>
              {c ? <>
                <div style={{ fontSize: 11, fontWeight: 700, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.name}</div>
                <div style={{ marginTop: 2 }}><FamilyBadge family={c.genre_family || 'NEUTRAL'} small /></div>
              </> : <div style={{ color: '#55557a', fontSize: 11, marginTop: 14 }}>slot {i + 1}</div>}
            </div>
          )
        })}
      </div>

      {preview && (
        <div style={{ background: '#1a1740', border: '1px solid #2a2760', borderRadius: 10, padding: 10, marginBottom: 10 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <div style={{ fontWeight: 700 }}>→ <span style={{ color: RARITY_COLORS[preview.target_rarity] }}>{preview.target_rarity.toUpperCase()}</span></div>
              <div style={{ color: '#8888aa', fontSize: 12 }}>
                {preview.inherited_family ? <>Guaranteed <FamilyBadge family={preview.inherited_family} small /></> : 'Random genre (inputs are mixed)'} · {preview.cap_remaining} left this season
              </div>
            </div>
            <div style={{ textAlign: 'right' }}>
              <div style={{ fontSize: 22, fontWeight: 700, color: boostReady ? '#F4A800' : '#fff' }}>{boostReady ? preview.boosted_pct : preview.base_pct}%</div>
              <div style={{ color: '#8888aa', fontSize: 10 }}>success</div>
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
            {!boostReady ? (
              <button onClick={buyBoost} disabled={!!busy} style={{ flex: 1, padding: '10px 0', background: '#2a2760', color: '#fff', border: '1px solid #6B2EBE', borderRadius: 8, fontWeight: 700 }}>
                {busy === 'boost' ? '…' : `⭐ ${preview.boost_stars} boost (+${preview.boost_pct}%)`}
              </button>
            ) : <div style={{ flex: 1, color: '#F4A800', fontSize: 12, alignSelf: 'center' }}>⭐ Boost armed</div>}
            <button onClick={craft} disabled={!!busy || (target && target.remaining <= 0)} style={{ flex: 1, padding: '10px 0', background: '#6B2EBE', color: '#fff', border: 'none', borderRadius: 8, fontWeight: 700 }}>
              {busy === 'craft' ? 'Fusing…' : '🧪 Fuse'}
            </button>
          </div>
        </div>
      )}

      {/* picker */}
      <div style={{ maxHeight: 320, overflowY: 'auto', border: '1px solid #2a2760', borderRadius: 8 }}>
        {group.map(c => {
          const used = countOf(c.card_id)
          return (
            <div key={c.card_id} onClick={() => add(c)} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 8px', borderBottom: '1px solid #1f1c48', cursor: 'pointer', opacity: used >= c.quantity ? 0.45 : 1 }}>
              <div style={{ width: 26, textAlign: 'center', fontWeight: 700, color: used ? '#F4A800' : '#55557a', fontSize: 12 }}>{used ? `${used}/` : ''}{c.quantity}</div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 12, fontWeight: 700, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.name}</div>
                <div style={{ fontSize: 10, color: '#8888aa' }}>×{c.quantity} owned</div>
              </div>
              <FamilyBadge family={c.genre_family || 'NEUTRAL'} small />
              <div style={{ fontWeight: 700, minWidth: 30, textAlign: 'right' }}>{c.power}</div>
            </div>
          )
        })}
        {group.length === 0 && <div style={{ color: '#8888aa', fontSize: 12, padding: 10 }}>No {rarity} cards. Open packs to collect duplicates.</div>}
      </div>
      <p style={{ color: '#55557a', fontSize: 10, marginTop: 8 }}>
        Rates: rare {opts?.targets?.rare?.base_pct}% · epic {opts?.targets?.epic?.base_pct}% · legendary {opts?.targets?.legendary?.base_pct}% · mythic {opts?.targets?.mythic?.base_pct}%. A Stars boost adds {opts?.boost_pct}% (max 95%) and is refunded if the craft can't complete.
      </p>
    </div>
  )
}
