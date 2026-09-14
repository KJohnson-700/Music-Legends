import { useEffect, useRef, useState } from 'react'
import { openInvoice, hapticFeedbackNotificationOccurred } from '@telegram-apps/sdk'
import {
  buyPack,
  createStarsInvoice,
  getEconomy,
  getPackStore,
  getStarsCatalog,
  getStarsOrder,
} from '../api/client'

const RARITY_COLORS: Record<string, string> = {
  common: '#95A5A6', rare: '#4488FF', epic: '#6B2EBE', legendary: '#F4A800', mythic: '#E74C3C',
}
const TIER_DESC: Record<string, string> = {
  community: '5 cards · mostly Common/Rare · +100 gold',
  gold: '5 cards · guaranteed Rare+ · +250 gold',
  platinum: '10 cards · Epic+ with Mythic chance · +500 gold',
}

type Catalog = { tiers: Record<string, { stars: number; label: string; description?: string; cards?: number }>; packs: Record<string, number> }

/** Open a Telegram Stars invoice and resolve to its status. Falls back to opening the link. */
async function payWithStars(link: string): Promise<string> {
  if (openInvoice.isAvailable()) {
    try { return await openInvoice(link, 'url') } catch (e: any) { return e?.message === 'cancelled' ? 'cancelled' : 'failed' }
  }
  const tg = (window as any)?.Telegram?.WebApp
  if (tg?.openInvoice) {
    return await new Promise<string>((resolve) => tg.openInvoice(link, (status: string) => resolve(status)))
  }
  window.open(link, '_blank', 'noopener,noreferrer')
  return 'pending'
}

export default function Store() {
  const [packs, setPacks] = useState<any[]>([])
  const [catalog, setCatalog] = useState<Catalog>({ tiers: {}, packs: {} })
  const [gold, setGold] = useState(0)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<string>('')
  const [error, setError] = useState('')
  const [delivered, setDelivered] = useState<any | null>(null)
  const pollRef = useRef<any>(null)

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const [storeRes, ecoRes, catRes] = await Promise.all([getPackStore(), getEconomy(), getStarsCatalog().catch(() => ({ data: null }))])
      setPacks(storeRes.data?.packs || [])
      setGold(ecoRes.data?.gold || 0)
      if (catRes?.data) setCatalog({ tiers: catRes.data.tiers || {}, packs: catRes.data.packs || {} })
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || 'Failed to load store')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load(); return () => { if (pollRef.current) clearInterval(pollRef.current) } }, [])

  const handleBuyGold = async (packId: string) => {
    setBusy(`gold:${packId}`)
    setError('')
    try {
      await buyPack(packId)
      await load()
      alert('Pack purchased! Open it in My Packs.')
    } catch (e: any) {
      alert(e?.response?.data?.detail || 'Purchase failed')
    } finally {
      setBusy('')
    }
  }

  const waitForFulfilment = (orderId: string) => new Promise<any>((resolve) => {
    let tries = 0
    pollRef.current = setInterval(async () => {
      tries += 1
      try {
        const r = await getStarsOrder(orderId)
        const st = r.data?.status
        if (st === 'fulfilled' || st === 'failed' || tries > 20) { clearInterval(pollRef.current); resolve(r.data) }
      } catch { if (tries > 20) { clearInterval(pollRef.current); resolve(null) } }
    }, 1500)
  })

  const handleStars = async (productType: 'tier_pack' | 'creator_pack', ref: string, label: string) => {
    const key = `${productType}:${ref}`
    setBusy(key)
    setError('')
    try {
      const res = await createStarsInvoice(productType, ref)
      const { invoice_link, order_id } = res.data
      const status = await payWithStars(invoice_link)
      if (status === 'paid' || status === 'pending') {
        const order = await waitForFulfilment(order_id)
        if (order?.status === 'fulfilled') {
          if (hapticFeedbackNotificationOccurred.isAvailable()) hapticFeedbackNotificationOccurred('success')
          setDelivered({ ...order, label })
          await load()
        } else if (order?.status === 'failed') {
          alert('Payment went through but delivery failed. You will be refunded if it cannot be fixed.')
        } else {
          alert('Payment is processing. Your cards will appear shortly — check My Packs / Cards.')
        }
      } else if (status === 'failed') {
        alert('Payment failed. Nothing was charged.')
      }
      // cancelled: silent
    } catch (e: any) {
      alert(e?.response?.data?.detail || `Could not start Stars checkout for ${label}`)
    } finally {
      setBusy('')
    }
  }

  if (loading) return <div style={{ padding: 16, paddingBottom: 90 }}>Loading store...</div>

  if (delivered) {
    const cards: any[] = delivered.cards || []
    return (
      <div style={{ padding: '20px 16px 100px', textAlign: 'center' }}>
        <h3 style={{ color: '#F4A800', fontSize: 22 }}>⭐ {delivered.label}</h3>
        <p style={{ color: '#2ECC71', fontWeight: 700 }}>Payment complete — {delivered.stars} Stars</p>
        {cards.length > 0 ? (
          <>
            <p style={{ color: '#8888aa', fontSize: 13 }}>{cards.length} cards added to your collection</p>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginTop: 12 }}>
              {cards.map((c: any, i: number) => (
                <div key={c.card_id || i} style={{ background: '#1a1740', border: `2px solid ${RARITY_COLORS[(c.rarity || 'common').toLowerCase()] || '#2a2760'}`, borderRadius: 10, padding: 8 }}>
                  {c.image_url && <img src={c.image_url} alt={c.name} style={{ width: '100%', borderRadius: 6, aspectRatio: '16/9', objectFit: 'cover' }} />}
                  <div style={{ fontWeight: 700, fontSize: 12, marginTop: 4 }}>{c.name}</div>
                  <div style={{ fontSize: 10, color: RARITY_COLORS[(c.rarity || 'common').toLowerCase()] }}>{(c.rarity || 'common').toUpperCase()}</div>
                </div>
              ))}
            </div>
          </>
        ) : (
          <p style={{ color: '#8888aa', fontSize: 13 }}>Your pack is waiting in <b>My Packs</b> — open it for the reveal.</p>
        )}
        <button onClick={() => setDelivered(null)} style={{ marginTop: 16, padding: '10px 18px', background: '#F4A800', color: '#000', border: 'none', borderRadius: 8, fontWeight: 700 }}>
          Back to Store
        </button>
      </div>
    )
  }

  const tierKeys = Object.keys(catalog.tiers)

  return (
    <div style={{ padding: '16px 16px 90px' }}>
      <h3 style={{ color: '#F4A800', marginBottom: 6 }}>🛒 Store</h3>
      <p style={{ color: '#8888aa', marginTop: 0, fontSize: 13 }}>
        Buy live packs with gold, or pay with Telegram Stars ⭐. Cards are delivered right after payment.
      </p>
      <div style={{ background: '#1a1740', border: '1px solid #2a2760', borderRadius: 10, padding: 10, marginBottom: 14 }}>
        💰 Your Gold: <b>{gold.toLocaleString()}</b>
      </div>

      {tierKeys.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontWeight: 700, marginBottom: 8, fontSize: 14 }}>Tier packs ⭐</div>
          {tierKeys.map((tier) => {
            const t = catalog.tiers[tier]
            const key = `tier_pack:${tier}`
            return (
              <div key={tier} style={{ display: 'flex', alignItems: 'center', gap: 10, background: '#1a1740', border: '1px solid #2a2760', borderRadius: 10, padding: 10, marginBottom: 8 }}>
                <div style={{ flex: 1 }}>
                  <div style={{ fontWeight: 700 }}>{t.label}</div>
                  <div style={{ color: '#8888aa', fontSize: 12 }}>{t.description || TIER_DESC[tier] || ''}</div>
                </div>
                <button type="button" onClick={() => handleStars('tier_pack', tier, t.label)} disabled={!!busy}
                  style={{ padding: '8px 12px', background: busy === key ? '#4a2a4a' : '#6B2EBE', color: '#fff', border: 'none', borderRadius: 8, fontWeight: 700, minWidth: 92, cursor: busy ? 'wait' : 'pointer' }}>
                  {busy === key ? '…' : `⭐ ${t.stars}`}
                </button>
              </div>
            )
          })}
        </div>
      )}

      {error && <div style={{ color: '#E74C3C', marginBottom: 10 }}>{error}</div>}
      {packs.length === 0 && <p style={{ color: '#8888aa' }}>No packs in store right now.</p>}
      {packs.length > 0 && <div style={{ fontWeight: 700, marginBottom: 8, fontSize: 14 }}>Creator packs</div>}

      {packs.map((p) => {
        const price = Number(p.price || 0) > 0 ? Number(p.price) : 500
        const stars = catalog.packs[p.pack_id]
        const gkey = `gold:${p.pack_id}`, skey = `creator_pack:${p.pack_id}`
        return (
          <div key={p.pack_id} style={{ background: '#1a1740', border: '1px solid #2a2760', borderRadius: 12, padding: 12, marginBottom: 10 }}>
            <div style={{ fontWeight: 700 }}>{p.name || p.pack_name || p.pack_id}</div>
            <div style={{ color: '#8888aa', fontSize: 12, marginTop: 4 }}>
              {(p.pack_tier || 'community').toUpperCase()} • {(p.card_count || p.cards?.length || 0)} cards
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
              <button onClick={() => handleBuyGold(p.pack_id)} disabled={!!busy}
                style={{ flex: 1, padding: '10px 0', background: busy === gkey ? '#4a2a4a' : '#F4A800', color: '#000', border: 'none', borderRadius: 8, fontWeight: 700, cursor: busy ? 'wait' : 'pointer' }}>
                {busy === gkey ? 'Purchasing...' : `${price.toLocaleString()} gold`}
              </button>
              {stars && (
                <button type="button" onClick={() => handleStars('creator_pack', p.pack_id, p.name || p.pack_id)} disabled={!!busy}
                  style={{ flex: 1, padding: '10px 0', background: busy === skey ? '#4a2a4a' : '#6B2EBE', color: '#fff', border: 'none', borderRadius: 8, fontWeight: 700, cursor: busy ? 'wait' : 'pointer' }}>
                  {busy === skey ? 'Opening…' : `⭐ ${stars}`}
                </button>
              )}
            </div>
          </div>
        )
      })}
      <p style={{ color: '#55557a', fontSize: 10, marginTop: 12 }}>
        Stars purchases are processed by Telegram. Community hosts earn a share of every pack bought by players they invited.
      </p>
    </div>
  )
}
