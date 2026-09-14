import { useEffect, useState, useCallback } from 'react'
import { mountMainButton, setMainButtonParams, onMainButtonClick, unmountMainButton,
         hapticFeedbackSelectionChanged } from '@telegram-apps/sdk'
import { useNavigate } from 'react-router-dom'
import AnimatedCard from '../components/AnimatedCard'
import { getCards } from '../api/client'

export default function Collection() {
  const [cards, setCards] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<any>(null)
  const navigate = useNavigate()

  const [error, setError] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    setError('')
    getCards()
      .then(r => { setCards(r.data.cards); setLoading(false) })
      .catch(() => { setError('Failed to load cards.'); setLoading(false) })
  }, [])

  useEffect(() => { load() }, [load])

  // MainButton → "⚔️ Battle" when a card is selected
  useEffect(() => {
    if (!mountMainButton.isAvailable()) return
    mountMainButton()
    if (selected) {
      setMainButtonParams({ text: '⚔️ Battle with this card', isEnabled: true, isVisible: true, backgroundColor: '#E74C3C' })
      const off = onMainButtonClick(() => navigate('/battle'))
      return () => { off(); unmountMainButton() }
    } else {
      setMainButtonParams({ isVisible: false })
      return () => { unmountMainButton() }
    }
  }, [selected, navigate])

  if (error) return (
    <div style={{ padding: 16, paddingBottom: 80 }}>
      <h3 style={{ color: '#F4A800' }}>🃏 My Collection</h3>
      <p style={{ color: '#E74C3C' }}>{error}</p>
      <button onClick={load} style={{ background: '#6B2EBE', color: '#fff', border: 'none', borderRadius: 8, padding: '10px 20px', cursor: 'pointer' }}>Retry</button>
    </div>
  )

  if (loading) return (
    <div style={{ padding: 16, paddingBottom: 80 }}>
      <h3 style={{ color: '#F4A800' }}>🃏 My Collection</h3>
      {/* Skeleton loading */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
        {[...Array(6)].map((_, i) => (
          <div key={i} style={{ background: '#1a1740', borderRadius: 14, height: 200,
            animation: 'pulse 1.5s ease-in-out infinite', opacity: 0.6 }} />
        ))}
      </div>
    </div>
  )

  return (
    <div style={{ padding: '16px 16px 80px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
        <h3 style={{ color: '#F4A800', margin: 0 }}>🃏 My Collection ({cards.length})</h3>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <button onClick={() => navigate('/craft')} style={{ background: '#6B2EBE', border: 'none', color: '#fff', fontSize: 12, fontWeight: 700, borderRadius: 8, padding: '6px 10px', cursor: 'pointer' }}>🧪 Craft</button>
          <button onClick={load} style={{ background: 'none', border: 'none', color: '#8888aa', fontSize: 18, cursor: 'pointer' }}>↻</button>
        </div>
      </div>

      {cards.length === 0 && (
        <p style={{ textAlign: 'center', color: '#888', marginTop: 40 }}>
          No cards yet! Claim your daily or open a pack.
        </p>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
        {cards.map(card => (
          <AnimatedCard
            key={card.card_id}
            card={card}
            onClick={() => {
              if (hapticFeedbackSelectionChanged.isAvailable()) hapticFeedbackSelectionChanged()
              setSelected(card.card_id === selected ? null : card.card_id)
            }}
          />
        ))}
      </div>
    </div>
  )
}
