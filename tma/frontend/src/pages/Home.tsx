import { useEffect, useState } from 'react'
import { mountMainButton, setMainButtonParams, onMainButtonClick, unmountMainButton } from '@telegram-apps/sdk'
import { useNavigate } from 'react-router-dom'
import { getMe } from '../api/client'

export default function Home() {
  const [user, setUser] = useState<any>(null)
  const [error, setError] = useState('')
  const navigate = useNavigate()

  useEffect(() => {
    getMe()
      .then(r => setUser(r.data))
      .catch((e: any) => setError(e?.response?.data?.detail || e?.message || 'Failed to load profile'))
  }, [])

  useEffect(() => {
    if (!mountMainButton.isAvailable()) return
    mountMainButton()
    setMainButtonParams({ text: '🛒 Open Store', isEnabled: true, isVisible: true, backgroundColor: '#F4A800', textColor: '#000000' })
    const off = onMainButtonClick(() => navigate('/store'))
    return () => { off(); unmountMainButton() }
  }, [navigate])

  const LOGO = 'https://olive-generous-kangaroo-378.mypinata.cloud/ipfs/bafybeiehxk5zhdxidab4qtuxg6lblrasxcxb2bkj6a3ipyjue5f7pzo3qi'

  return (
    <div style={{ padding: '20px 16px', paddingBottom: 100 }}>
      <div style={{ textAlign: 'center', marginBottom: 28 }}>
        <img src={LOGO} alt="Music Legends" style={{ width: 72, height: 72, borderRadius: '50%', border: '2px solid #F4A800' }} />
        <h2 style={{ color: '#F4A800', margin: '10px 0 4px' }}>Music Legends</h2>
        {user && (
          <p style={{ color: '#8888aa', margin: 0, fontSize: 13 }}>
            Welcome{user.username ? `, ${user.username}` : ''}!{user.is_new ? ' 🎉 New player!' : ''}
          </p>
        )}
      </div>

      {error && (
        <div style={{ background: '#3a1a1a', border: '1px solid #E74C3C', borderRadius: 10, padding: '12px 16px', marginBottom: 16, color: '#E74C3C', fontSize: 13 }}>
          ❌ {error}
        </div>
      )}

      {user && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 20 }}>
          {[
            { label: '💰 Gold',    value: user.gold?.toLocaleString() },
            { label: '⭐ XP',      value: user.xp?.toLocaleString() },
            { label: '⚔️ Battles', value: user.total_battles },
            { label: '🏆 Wins',    value: user.wins },
          ].map(s => (
            <div key={s.label} style={{
              background: '#1a1740', borderRadius: 12, padding: '14px 12px',
              textAlign: 'center', border: '1px solid #2a2760',
            }}>
              <div style={{ fontSize: 22, fontWeight: 700 }}>{s.value ?? '—'}</div>
              <div style={{ color: '#8888aa', fontSize: 12, marginTop: 4 }}>{s.label}</div>
            </div>
          ))}
        </div>
      )}

      <h3 style={{ color: '#F4A800', margin: '6px 0 10px', fontSize: 16 }}>Game Menu</h3>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
        {[
          { label: '🎁 Daily', path: '/daily', desc: 'Claim rewards' },
          { label: '🛒 Store', path: '/store', desc: 'Buy packs with gold' },
          { label: '🏪 Market', path: '/market', desc: 'Buy and sell cards' },
          { label: '🤝 Trade', path: '/trade', desc: 'Player trades' },
          { label: '📦 My Packs', path: '/packs', desc: 'Open owned packs' },
          { label: '⚔️ Battle', path: '/battle', desc: 'Challenge players' },
          { label: '🧪 Craft', path: '/craft', desc: 'Fuse 4 duplicates into a rarer card' },
        ].map((item) => (
          <button
            key={item.path}
            onClick={() => navigate(item.path)}
            style={{
              background: '#1a1740',
              border: '1px solid #2a2760',
              borderRadius: 12,
              padding: '12px 10px',
              textAlign: 'left',
              color: '#fff',
              cursor: 'pointer',
            }}
          >
            <div style={{ fontWeight: 700, fontSize: 14 }}>{item.label}</div>
            <div style={{ color: '#8888aa', fontSize: 12, marginTop: 3 }}>{item.desc}</div>
          </button>
        ))}
      </div>

      {/* New player guidance */}
      {user?.is_new && (
        <div style={{ background: '#1a3a1a', border: '1px solid #2ECC71', borderRadius: 12, padding: '14px 16px', marginBottom: 16 }}>
          <div style={{ color: '#2ECC71', fontWeight: 700, marginBottom: 6 }}>🎉 Welcome to Music Legends!</div>
          <div style={{ color: '#aaa', fontSize: 13 }}>Start at Daily, then buy from Store and battle from the Battle tab.</div>
        </div>
      )}

      {/* Fallback button if MainButton not available */}
      {!mountMainButton.isAvailable() && (
        <button onClick={() => navigate('/store')} style={{
          width: '100%', padding: '14px 0',
          background: '#F4A800', color: '#000', border: 'none',
          borderRadius: 12, fontWeight: 700, fontSize: 15, cursor: 'pointer',
        }}>
          🛒 Open Store
        </button>
      )}
    </div>
  )
}
