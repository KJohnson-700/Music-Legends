import { useEffect, useState, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import { mountMainButton, setMainButtonParams, onMainButtonClick, unmountMainButton,
         hapticFeedbackNotificationOccurred } from '@telegram-apps/sdk'
import { getPacks, createChallenge, acceptBattle, cancelBattle, getBattle, getBattleUpdates, registerBattlePlayer, getBattleOpponents, searchBattleOpponents, getLineupCards, scoutBattle } from '../api/client'
import LineupBuilder, { type LineupCard } from '../components/LineupBuilder'
import RoundReveal from '../components/RoundReveal'

type Phase = 'select-pack' | 'challenge-sent' | 'accept' | 'resolving' | 'result'

const RARITY_COLORS: Record<string, string> = {
  common: '#95A5A6', rare: '#4488FF', epic: '#6B2EBE', legendary: '#F4A800', mythic: '#E74C3C',
}

export default function Battle() {
  const [searchParams] = useSearchParams()
  const battleIdParam = searchParams.get('id')
  const [phase, setPhase] = useState<Phase>(battleIdParam ? 'accept' : 'select-pack')
  const [packs, setPacks] = useState<any[]>([])
  const [selectedPackId, setSelectedPackId] = useState<string>('')
  const [selectionType, setSelectionType] = useState<'pack' | 'card'>('pack')
  const [partnerQuery, setPartnerQuery] = useState('')
  const [partnerResults, setPartnerResults] = useState<any[]>([])
  const [selectedPartner, setSelectedPartner] = useState<any>(null)
  const [incomingChallenges, setIncomingChallenges] = useState<any[]>([])
  const [outgoingChallenges, setOutgoingChallenges] = useState<any[]>([])
  const [registeringBattle, setRegisteringBattle] = useState(false)
  const [loadingOpponents, setLoadingOpponents] = useState(false)
  const [registerError, setRegisterError] = useState('')
  const [opponentError, setOpponentError] = useState('')
  const [battleId, setBattleId] = useState(battleIdParam || '')
  const [expiresAt, setExpiresAt] = useState<string | null>(null)
  const [countdown, setCountdown] = useState('')
  const [result, setResult] = useState<any>(null)
  const [loadingBattle, setLoadingBattle] = useState(!!battleIdParam)
  // Phase 4 — lineup builder state
  const [lineupCards, setLineupCards] = useState<LineupCard[]>([])
  const [lineup, setLineup] = useState<string[]>([])
  const [ability, setAbility] = useState<string>('')
  const [abilitySlot, setAbilitySlot] = useState<number | null>(null)
  const [scoutInfo, setScoutInfo] = useState<{ slot: number; family: string } | null>(null)
  const [useLineup, setUseLineup] = useState(true)
  const [meIsChallenger, setMeIsChallenger] = useState(true)
  const lineupReady = lineup.length === 3 && !((ability === 'amp' || ability === 'scout') && abilitySlot === null)
  const loadLineupCards = async () => {
    try {
      const r = await getLineupCards()
      const cards: LineupCard[] = r.data?.cards || []
      setLineupCards(cards)
      if (cards.length < 3) setUseLineup(false)
      else if (lineup.length === 0 && Array.isArray(r.data?.suggested)) setLineup(r.data.suggested.slice(0, 3))
    } catch { /* fall back to pack selection */ setUseLineup(false) }
  }
  const resetLineupAbility = () => { setAbility(''); setAbilitySlot(null); setScoutInfo(null) }
  const lineupBody = () => {
    const body: any = { lineup }
    if (scoutInfo) { body.ability = 'scout'; body.ability_slot = scoutInfo.slot }
    else if (ability) { body.ability = ability; if (abilitySlot !== null) body.ability_slot = abilitySlot }
    return body
  }
  const handleScout = async (slot: number) => {
    if (!battleId) return
    try {
      const r = await scoutBattle(battleId, slot)
      setScoutInfo({ slot: r.data.slot, family: r.data.family })
      setAbility('scout'); setAbilitySlot(r.data.slot)
      if (hapticFeedbackNotificationOccurred.isAvailable()) hapticFeedbackNotificationOccurred('success')
    } catch (e: any) {
      alert(e?.response?.data?.detail || 'Scout failed')
    }
  }
  const pollRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined)
  const incomingPollRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined)
  const registerPollRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined)
  const incomingCountRef = useRef<number>(0)

  const normalizedPartnerQuery = partnerQuery.trim().replace(/^@+/, '').toLowerCase()
  const autoResolvedPartner = resolveAutoPartner(selectedPartner, partnerResults, normalizedPartnerQuery)

  const loadOpponents = async (query = '') => {
    setLoadingOpponents(true)
    setOpponentError('')
    try {
      const normalized = query.trim()
      const r = normalized ? await searchBattleOpponents(normalized) : await getBattleOpponents()
      setPartnerResults(r.data?.players || [])
    } catch (e: any) {
      setPartnerResults([])
      setOpponentError(e?.response?.data?.detail || 'Failed to load opponents')
    } finally {
      setLoadingOpponents(false)
    }
  }

  const loadBattleUpdates = async () => {
    try {
      const r = await getBattleUpdates()
      const nextIncoming = r.data?.incoming || []
      if (nextIncoming.length > incomingCountRef.current && hapticFeedbackNotificationOccurred.isAvailable()) {
        hapticFeedbackNotificationOccurred('success')
      }
      incomingCountRef.current = nextIncoming.length
      setIncomingChallenges(nextIncoming)
      setOutgoingChallenges(r.data?.outgoing || [])
      const completed = r.data?.completed || []
      if (battleId && phase === 'challenge-sent') {
        const completedCurrent = completed.find((b: any) => String(b?.battle_id) === String(battleId) && b?.result)
        if (completedCurrent) {
          if (pollRef.current) clearInterval(pollRef.current)
          setResult(completedCurrent.result)
          setPhase('result')
        }
      }
    } catch {
      setIncomingChallenges([])
      setOutgoingChallenges([])
    }
  }

  useEffect(() => {
    getPacks()
      .then(r => setPacks(r.data.packs || []))
      .catch(() => setPacks([]))
    if (battleIdParam) {
      getBattle(battleIdParam)
        .then((r) => {
          if (r.data?.status === 'complete' && r.data?.result) {
            setResult(r.data.result)
            setPhase('result')
          } else {
            setExpiresAt(r.data?.expires_at || null)
            setPhase('accept')
          }
        })
        .catch(() => {
          setPhase('accept')
        })
        .finally(() => setLoadingBattle(false))
    } else {
      setLoadingBattle(false)
    }
    loadBattleUpdates().catch(() => undefined)
    loadOpponents().catch(() => undefined)
    loadLineupCards().catch(() => undefined)
    // Keep this user battle-registered while the screen is open.
    registerBattlePlayer()
      .then(() => setRegisterError(''))
      .catch((e: any) => setRegisterError(e?.response?.data?.detail || 'Battle registration failed'))
    registerPollRef.current = setInterval(() => {
      registerBattlePlayer()
        .then(() => setRegisterError(''))
        .catch((e: any) => setRegisterError(e?.response?.data?.detail || 'Battle registration failed'))
    }, 30000)
    incomingPollRef.current = setInterval(() => {
      loadBattleUpdates().catch(() => undefined)
      loadOpponents().catch(() => undefined)
    }, 3000)
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
      if (incomingPollRef.current) clearInterval(incomingPollRef.current)
      if (registerPollRef.current) clearInterval(registerPollRef.current)
    }
  }, [])

  useEffect(() => {
    loadOpponents(normalizedPartnerQuery).catch(() => undefined)
  }, [phase, normalizedPartnerQuery])

  useEffect(() => {
    if (!selectedPartner?.telegram_id) return
    const match = partnerResults.find((p: any) => String(p?.telegram_id) === String(selectedPartner.telegram_id))
    if (match) {
      setSelectedPartner(match)
    }
  }, [partnerResults, selectedPartner?.telegram_id])

  const filteredPartners = partnerResults

  const handleRegisterForBattle = async () => {
    setRegisteringBattle(true)
    setRegisterError('')
    try {
      await registerBattlePlayer()
      await loadOpponents()
      if (hapticFeedbackNotificationOccurred.isAvailable()) hapticFeedbackNotificationOccurred('success')
      alert('You are now registered for battle matchmaking.')
    } catch (e: any) {
      const message = e?.response?.data?.detail || 'Failed to register for battle'
      setRegisterError(message)
      alert(message)
    } finally {
      setRegisteringBattle(false)
    }
  }

  const handleChallenge = async () => {
    if (useLineup ? !lineupReady : !selectedPackId) return
    const partner = autoResolvedPartner
    if (!partner?.telegram_id) {
      alert('Select who to challenge first')
      return
    }
    const selected = packs.find((p: any) => String(p.pack_id) === String(selectedPackId))
    const label = useLineup ? 'your 3-card lineup' : `"${selected?.pack_name || selected?.name || selectedPackId}"`
    if (!window.confirm(`Challenge @${partner.username || partner.telegram_id} using ${label}?`)) return
    try {
      const body: any = { opponent_telegram_id: Number(partner.telegram_id), wager_tier: 'casual' }
      if (useLineup) Object.assign(body, lineupBody())
      else if (selectionType === 'card' || selectedPackId.startsWith('card:')) body.card_id = selectedPackId.replace('card:', '')
      else body.pack_id = selectedPackId
      setMeIsChallenger(true)
      const r = await createChallenge(body)
      setBattleId(r.data.battle_id)
      setExpiresAt(r.data?.expires_at || null)
      setPhase('challenge-sent')
      if (hapticFeedbackNotificationOccurred.isAvailable()) hapticFeedbackNotificationOccurred('success')
      loadBattleUpdates().catch(() => undefined)
      pollRef.current = setInterval(async () => {
        try {
          const poll = await getBattle(r.data.battle_id)
          if (poll.data.status === 'complete') {
            clearInterval(pollRef.current)
            setResult(poll.data.result)
            setPhase('result')
            if (hapticFeedbackNotificationOccurred.isAvailable()) {
              hapticFeedbackNotificationOccurred(poll.data.result?.winner === 1 ? 'success' : 'error')
            }
          } else if (poll.data.status === 'cancelled' || poll.data.status === 'expired') {
            clearInterval(pollRef.current)
            setPhase('select-pack')
            alert(`Challenge ${poll.data.status}.`)
          }
        } catch {
          // Keep polling; transient network errors should not break battle completion flow.
        }
      }, 3000)
    } catch (e: any) {
      alert(e.response?.data?.detail || 'Failed to create challenge')
    }
  }

  const handleAccept = async () => {
    if ((useLineup ? !lineupReady : !selectedPackId) || !battleId) return
    const selected = packs.find((p: any) => String(p.pack_id) === String(selectedPackId))
    const label = useLineup ? 'your 3-card lineup' : `"${selected?.pack_name || selected?.name || selectedPackId}"`
    if (!window.confirm(`Confirm accepting this battle using ${label}?`)) return
    try {
      setPhase('resolving')
      const body: any = {}
      if (useLineup) Object.assign(body, lineupBody())
      else if (selectionType === 'card' || selectedPackId.startsWith('card:')) body.card_id = selectedPackId.replace('card:', '')
      else body.pack_id = selectedPackId
      setMeIsChallenger(false)
      const r = await acceptBattle(battleId, body)
      setResult(r.data.result)
      setPhase('result')
      loadBattleUpdates().catch(() => undefined)
      if (hapticFeedbackNotificationOccurred.isAvailable()) {
        hapticFeedbackNotificationOccurred(r.data.result?.winner === 2 ? 'success' : 'error')
      }
    } catch (e: any) {
      alert(e.response?.data?.detail || 'Failed to accept battle')
    }
  }

  const handleCancelChallenge = async () => {
    if (!battleId) return
    if (!window.confirm('Cancel this battle challenge?')) return
    try {
      await cancelBattle(battleId)
      if (pollRef.current) clearInterval(pollRef.current)
      setBattleId('')
      setExpiresAt(null)
      setCountdown('')
      setPhase('select-pack')
      loadBattleUpdates().catch(() => undefined)
      if (hapticFeedbackNotificationOccurred.isAvailable()) hapticFeedbackNotificationOccurred('success')
    } catch (e: any) {
      alert(e?.response?.data?.detail || 'Failed to cancel challenge')
    }
  }

  useEffect(() => {
    if (!expiresAt || !['challenge-sent', 'accept'].includes(phase)) {
      setCountdown('')
      return
    }
    const tick = () => {
      const left = getCountdownLabel(expiresAt)
      setCountdown(left)
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [expiresAt, phase])

  useEffect(() => {
    if (!mountMainButton.isAvailable()) return
    mountMainButton()
    if (phase === 'select-pack') {
      const canCreate = (useLineup ? lineupReady : !!selectedPackId) && !!autoResolvedPartner?.telegram_id
      setMainButtonParams({
        text: canCreate ? '⚔️ Create Challenge' : (useLineup ? 'Pick Opponent + 3 Cards' : 'Pick Opponent + Card/Pack'),
        isEnabled: canCreate,
        isVisible: true,
        backgroundColor: '#E74C3C',
      })
      const off = onMainButtonClick(handleChallenge)
      return () => { off(); unmountMainButton() }
    }
    if (phase === 'accept') {
      const canAccept = useLineup ? lineupReady : !!selectedPackId
      setMainButtonParams({
        text: canAccept ? '⚔️ Accept Battle!' : (useLineup ? 'Pick Your 3 Cards' : 'Select Your Card or Pack'),
        isEnabled: canAccept,
        isVisible: true,
        backgroundColor: '#E74C3C',
      })
      const off = onMainButtonClick(handleAccept)
      return () => { off(); unmountMainButton() }
    }
    if (phase === 'challenge-sent') {
      setMainButtonParams({ text: '❌ Cancel Challenge', isEnabled: true, isVisible: true, backgroundColor: '#E74C3C' })
      const off = onMainButtonClick(handleCancelChallenge)
      return () => { off(); unmountMainButton() }
    }
    if (phase === 'resolving') {
      setMainButtonParams({ text: 'Resolving Battle...', isEnabled: false, isVisible: true, backgroundColor: '#6B2EBE' })
      return () => { unmountMainButton() }
    }
    if (phase === 'result') {
      // "Play Again" button on result screen
      setMainButtonParams({ text: 'Battle Again', isEnabled: true, isVisible: true, backgroundColor: '#E74C3C' })
      const off = onMainButtonClick(() => {
        setResult(null)
        setSelectedPackId('')
        setBattleId('')
        resetLineupAbility()
        setPhase('select-pack')
      })
      return () => { off(); unmountMainButton() }
    }
    unmountMainButton()
  }, [phase, selectedPackId, autoResolvedPartner?.telegram_id, battleId, useLineup, lineupReady, lineup, ability, abilitySlot, scoutInfo]) // eslint-disable-line react-hooks/exhaustive-deps

  if (phase === 'result' && result && result.format === 'lineup_bo3') {
    return <RoundReveal result={result} meIsPlayer1={meIsChallenger} rarityColors={RARITY_COLORS} />
  }

  if (phase === 'result' && result) {
    const c = result.challenger, o = result.opponent, winner = result.winner
    const winnerCard = winner === 1 ? c : winner === 2 ? o : null
    return (
      <div className={result.is_critical ? 'shake' : ''} style={{ padding: '20px 16px 100px', textAlign: 'center' }}>
        <h3 style={{ color: '#F4A800', fontSize: 22 }}>
          {winner === 1 ? '🏆 You Won!' : winner === 2 ? '😔 You Lost' : '🤝 Draw!'}
        </h3>
        {result.is_critical && <p style={{ color: '#FF4E9A', fontWeight: 700 }}>⚡ CRITICAL HIT!</p>}
        <div style={{ display: 'flex', gap: 12, justifyContent: 'center', margin: '20px 0' }}>
          {[c, o].map((player, i) => (
            <div key={i} style={{
              flex: 1, background: '#1a1740', borderRadius: 12, padding: 12,
              border: `2px solid ${RARITY_COLORS[player?.rarity || 'common']}`,
              opacity: (i === 0 ? winner !== 2 : winner !== 1) ? 1 : 0.5,
            }}>
              {player?.image_url && <img src={player.image_url} alt={player.name} style={{ width: '100%', borderRadius: 8, aspectRatio: '16/9', objectFit: 'cover' }} />}
              <div style={{ fontWeight: 700, marginTop: 6, fontSize: 12 }}>{player?.name}</div>
              <div style={{ color: RARITY_COLORS[player?.rarity || 'common'], fontSize: 18, fontWeight: 700 }}>{player?.power}</div>
              <div style={{ color: '#2ECC71', fontSize: 12 }}>+{player?.gold_reward} 💰</div>
            </div>
          ))}
        </div>
        {/* YouTube link for the winning card */}
        {winnerCard?.youtube_url && (
          <a
            href={winnerCard.youtube_url}
            target="_blank"
            rel="noreferrer"
            style={{
              display: 'inline-block', marginTop: 8, padding: '8px 18px',
              background: '#FF0000', color: '#fff', borderRadius: 8,
              fontSize: 13, fontWeight: 700, textDecoration: 'none',
            }}
          >
            ▶ Watch {winnerCard.name} on YouTube
          </a>
        )}
      </div>
    )
  }

  return (
    <div style={{ padding: '16px 16px 80px' }}>
      {loadingBattle && (
        <div style={{ textAlign: 'center', color: '#8888aa', padding: '12px 0 16px' }}>
          Loading battle status...
        </div>
      )}
      <h3 style={{ color: '#F4A800', marginBottom: 6 }}>
        {phase === 'accept' ? '⚔️ Battle Challenge!' : '⚔️ Battle'}
      </h3>
      {phase === 'resolving' && (
        <div style={{ textAlign: 'center', padding: '20px 0' }}>
          <div style={{ fontSize: 36 }}>⚡</div>
          <div style={{ color: '#F4A800', fontWeight: 700 }}>Battle in progress...</div>
          <div style={{ color: '#8888aa', fontSize: 12, marginTop: 6 }}>Calculating winner and rewards.</div>
        </div>
      )}
      {phase === 'accept' && <p style={{ color: '#F4A800', fontSize: 13, marginBottom: 12 }}>Someone challenged you! Pick your best card or pack.</p>}
      {phase === 'challenge-sent' && (
        <div style={{ textAlign: 'center', padding: 20 }}>
          <div style={{ fontSize: 40 }}>✅</div>
          <p style={{ color: '#2ECC71', marginTop: 8 }}>Challenge created!</p>
          <p style={{ color: '#8888aa', fontSize: 12 }}>
            Opponent can accept from the Battle screen inside the Mini App.
          </p>
          {countdown && (
            <div style={{ marginTop: 8, color: '#F4A800', fontSize: 12 }}>
              Expires in: {countdown}
            </div>
          )}
          <button
            onClick={handleCancelChallenge}
            style={{ marginTop: 10, background: '#E74C3C', color: '#fff', border: 'none', borderRadius: 8, padding: '8px 12px', fontWeight: 700 }}
          >
            Cancel Challenge
          </button>
        </div>
      )}
      {phase === 'select-pack' && (
        <div style={{ background: '#1a1740', border: '1px solid #2a2760', borderRadius: 10, padding: 12, marginBottom: 12 }}>
          <div style={{ color: '#F4A800', fontWeight: 700, marginBottom: 6 }}>Challenge a player</div>
          <button
            onClick={handleRegisterForBattle}
            disabled={registeringBattle}
            style={{
              width: '100%',
              background: registeringBattle ? '#4a2a4a' : '#2ECC71',
              color: '#0a0a0a',
              border: 'none',
              borderRadius: 8,
              padding: '10px 12px',
              fontWeight: 700,
              marginBottom: 8,
            }}
          >
            {registeringBattle ? 'Registering...' : 'Register For Battle'}
          </button>
          <input
            value={partnerQuery}
            onChange={(e) => setPartnerQuery(e.target.value)}
            placeholder="Filter registered opponents"
            style={{
              width: '100%',
              background: '#0f0d2a',
              border: '1px solid #2a2760',
              borderRadius: 8,
              color: '#fff',
              padding: '10px 12px',
              marginBottom: 8,
            }}
          />
          {loadingOpponents && (
            <div style={{ color: '#8888aa', fontSize: 12, marginBottom: 8 }}>Loading registered opponents...</div>
          )}
          {!!opponentError && (
            <div style={{ color: '#ff6b6b', fontSize: 12, marginBottom: 8 }}>{opponentError}</div>
          )}
          {!!registerError && (
            <div style={{ color: '#ff6b6b', fontSize: 12, marginBottom: 8 }}>{registerError}</div>
          )}
          {filteredPartners.length > 0 && (
            <div style={{ background: '#0f1030', border: '1px solid #2a2760', borderRadius: 8, marginBottom: 8, maxHeight: 140, overflowY: 'auto' }}>
              {filteredPartners.map((p: any) => (
                <button
                  key={String(p.telegram_id)}
                  onClick={() => setSelectedPartner(p)}
                  style={{
                    width: '100%', textAlign: 'left',
                    background: selectedPartner?.telegram_id === p.telegram_id ? '#2a1760' : 'transparent',
                    color: '#fff', border: 'none', padding: '8px 10px', cursor: 'pointer',
                  }}
                >
                  @{p.username} (ID: {p.telegram_id})
                </button>
              ))}
            </div>
          )}
          {!loadingOpponents && filteredPartners.length === 0 && (
            <div style={{ color: '#8888aa', fontSize: 12 }}>
              No registered opponents found yet. Ask players to tap "Register For Battle".
            </div>
          )}
          {selectedPartner && (
            <div style={{ color: '#2ECC71', fontSize: 12 }}>
              Challenging: <b>@{selectedPartner.username}</b>
            </div>
          )}
          {!selectedPartner && autoResolvedPartner && normalizedPartnerQuery && (
            <div style={{ color: '#2ECC71', fontSize: 12 }}>
              Auto-selected: <b>@{autoResolvedPartner.username}</b>
            </div>
          )}
        </div>
      )}
      {phase === 'select-pack' && incomingChallenges.length > 0 && (
        <div style={{ background: '#1a1740', border: '1px solid #2a2760', borderRadius: 10, padding: 12, marginBottom: 12 }}>
          <div style={{ color: '#F4A800', fontWeight: 700, marginBottom: 6 }}>Incoming Challenges</div>
          {incomingChallenges.slice(0, 8).map((ch: any) => (
            <div key={ch.battle_id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, padding: '8px 0', borderBottom: '1px solid #2a2760' }}>
              <div style={{ fontSize: 12 }}>
                <div style={{ color: '#fff' }}>@{ch.challenger_username || 'player'} challenged you</div>
                <div style={{ color: '#8888aa' }}>Tier: {(ch.wager_tier || 'casual').toUpperCase()} • #{String(ch.battle_id).slice(0, 6)}</div>
              </div>
              <button
                onClick={() => {
                  setBattleId(String(ch.battle_id))
                  setExpiresAt(ch.expires_at || null)
                  setPhase('accept')
                }}
                style={{ background: '#2ECC71', color: '#000', border: 'none', borderRadius: 8, padding: '8px 10px', fontWeight: 700 }}
              >
                Accept
              </button>
            </div>
          ))}
        </div>
      )}
      {phase === 'select-pack' && outgoingChallenges.length > 0 && (
        <div style={{ background: '#1a1740', border: '1px solid #2a2760', borderRadius: 10, padding: 12, marginBottom: 12 }}>
          <div style={{ color: '#F4A800', fontWeight: 700, marginBottom: 6 }}>Outgoing Challenges</div>
          {outgoingChallenges.slice(0, 8).map((ch: any) => (
            <div key={ch.battle_id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, padding: '8px 0', borderBottom: '1px solid #2a2760' }}>
              <div style={{ fontSize: 12 }}>
                <div style={{ color: '#fff' }}>Waiting for @{ch.opponent_username || 'player'}</div>
                <div style={{ color: '#8888aa' }}>Tier: {(ch.wager_tier || 'casual').toUpperCase()} • #{String(ch.battle_id).slice(0, 6)}</div>
              </div>
            </div>
          ))}
        </div>
      )}
      {(phase === 'select-pack' || phase === 'accept') && lineupCards.length >= 3 && (
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 6 }}>
          <button onClick={() => setUseLineup(v => !v)}
            style={{ background: 'transparent', color: '#8888aa', border: '1px solid #2a2760', borderRadius: 6, padding: '4px 8px', fontSize: 11 }}>
            {useLineup ? 'Use quick pack pick instead' : 'Build a lineup instead'}
          </button>
        </div>
      )}
      {(phase === 'select-pack' || phase === 'accept') && useLineup && (
        <LineupBuilder
          cards={lineupCards}
          lineup={lineup}
          onChange={setLineup}
          ability={ability}
          abilitySlot={abilitySlot}
          onAbilityChange={(a, slot) => { setAbility(a); setAbilitySlot(slot) }}
          abilityLocked={scoutInfo ? 'scout' : null}
          scoutInfo={scoutInfo}
          onScout={handleScout}
          canScout={phase === 'accept' && !!battleId}
          rarityColors={RARITY_COLORS}
        />
      )}
      {(phase === 'select-pack' || phase === 'accept') && useLineup && !mountMainButton.isAvailable() && (
        <button
          onClick={phase === 'accept' ? handleAccept : handleChallenge}
          disabled={phase === 'accept' ? !lineupReady : (!lineupReady || !autoResolvedPartner?.telegram_id)}
          style={{
            width: '100%', padding: '14px 0', marginBottom: 16,
            background: (phase === 'accept' ? lineupReady : lineupReady && !!autoResolvedPartner?.telegram_id) ? '#E74C3C' : '#4a2a4a',
            color: '#fff', border: 'none', borderRadius: 12, fontWeight: 700, fontSize: 15,
          }}
        >
          {phase === 'accept' ? (lineupReady ? '⚔️ Accept Battle!' : 'Pick Your 3 Cards') : (lineupReady && !!autoResolvedPartner?.telegram_id ? '⚔️ Create Challenge' : 'Pick Opponent + 3 Cards')}
        </button>
      )}
      {(phase === 'select-pack' || phase === 'accept') && !useLineup && (
        <>
          <p style={{ color: '#8888aa', fontSize: 13, marginBottom: 14 }}>
            Choose your battle entry (pack or single card):
          </p>
          {packs.map(pack => (
            <div key={pack.pack_id} onClick={() => {
              setSelectedPackId(pack.pack_id)
              setSelectionType((pack._type === 'card' || String(pack.pack_id).startsWith('card:')) ? 'card' : 'pack')
            }} style={{
              background: selectedPackId === pack.pack_id ? '#2a1760' : '#1a1740',
              border: `2px solid ${selectedPackId === pack.pack_id ? '#F4A800' : '#2a2760'}`,
              borderRadius: 10, padding: 12, marginBottom: 8, cursor: 'pointer',
            }}>
              <div style={{ fontWeight: 700 }}>{pack.pack_name || pack.name || pack.pack_id}</div>
              <div style={{ fontSize: 12, color: '#8888aa' }}>
                {(pack.pack_tier || pack.tier || 'community').toUpperCase()} • {(pack.cards || []).length} card{(pack.cards || []).length === 1 ? '' : 's'}
                {(pack._type === 'card' || String(pack.pack_id).startsWith('card:')) ? ' • Auto-build squad' : ''}
              </div>
            </div>
          ))}
          {packs.length === 0 && <p style={{ color: '#888', textAlign: 'center', marginTop: 40 }}>No packs or cards found yet. Open/claim cards first, then try battle again.</p>}
          {packs.length > 0 && !mountMainButton.isAvailable() && (
            <button
              onClick={phase === 'accept' ? handleAccept : handleChallenge}
              disabled={phase === 'accept' ? !selectedPackId : (!selectedPackId || !autoResolvedPartner?.telegram_id)}
              data-mode="pack"
              style={{
                width: '100%', padding: '14px 0', marginTop: 16,
                background: (phase === 'accept'
                  ? !!selectedPackId
                  : !!selectedPackId && !!autoResolvedPartner?.telegram_id) ? '#E74C3C' : '#4a2a4a',
                color: '#fff', border: 'none', borderRadius: 12,
                fontWeight: 700, fontSize: 15, cursor: selectedPackId ? 'pointer' : 'not-allowed',
              }}
            >
              {phase === 'accept'
                ? (selectedPackId ? '⚔️ Accept Battle!' : 'Select Your Card or Pack')
                : ((selectedPackId && !!autoResolvedPartner?.telegram_id) ? '⚔️ Create Challenge' : 'Pick Opponent + Card/Pack')}
            </button>
          )}
        </>
      )}
    </div>
  )
}

const resolvePartnerFromQuery = (results: any[], normalizedQuery: string) => {
  if (!normalizedQuery) return null
  const exactUser = results.find((p: any) => String(p?.username || '').toLowerCase() === normalizedQuery)
  if (exactUser) return exactUser
  const digitsOnly = normalizedQuery.replace(/\D/g, '')
  if (digitsOnly) {
    const exactId = results.find((p: any) => String(p?.telegram_id || '') === digitsOnly)
    if (exactId) return exactId
  }
  const startsWith = results.find((p: any) => String(p?.username || '').toLowerCase().startsWith(normalizedQuery))
  if (startsWith) return startsWith
  const contains = results.find((p: any) => String(p?.username || '').toLowerCase().includes(normalizedQuery))
  if (contains) return contains
  return null
}

const resolveAutoPartner = (selected: any, results: any[], normalizedQuery: string) => {
  if (selected) {
    if (!normalizedQuery) return selected
    const uname = String(selected?.username || '').toLowerCase()
    const tid = String(selected?.telegram_id || '')
    const digits = normalizedQuery.replace(/\D/g, '')
    if (uname.includes(normalizedQuery) || (!!digits && tid.includes(digits))) return selected
  }
  return resolvePartnerFromQuery(results, normalizedQuery)
}

const getCountdownLabel = (expiresAt: string) => {
  const end = new Date(expiresAt).getTime()
  if (!Number.isFinite(end)) return ''
  const diff = Math.max(0, Math.floor((end - Date.now()) / 1000))
  const h = Math.floor(diff / 3600)
  const m = Math.floor((diff % 3600) / 60)
  const s = diff % 60
  return `${h}h ${m}m ${s}s`
}
