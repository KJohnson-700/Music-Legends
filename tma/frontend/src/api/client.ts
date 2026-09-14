import axios from 'axios'

// In production FastAPI serves both API and frontend on same origin.
// In dev, Vite proxy forwards /api → localhost:8001.
const api = axios.create({ baseURL: '' })

function getInitData(): string {
  const tg = (window as any)?.Telegram?.WebApp
  const fromTg = (tg?.initData || tg?.initDataUnsafe || '').trim()
  if (fromTg) return fromTg
  try {
    const params = new URLSearchParams(window.location.search)
    const fromSearch = params.get('tgWebAppData') || params.get('initData')
    if (fromSearch) return fromSearch
    const hash = (window.location.hash || '').replace(/^#/, '')
    if (hash) {
      const hashParams = new URLSearchParams(hash)
      const fromHash = hashParams.get('tgWebAppData') || hashParams.get('initData')
      if (fromHash) return fromHash
    }
  } catch {
    /* ignore */
  }
  return ''
}

api.interceptors.request.use(config => {
  const initDataRaw = getInitData()
  if (initDataRaw) {
    config.headers['Authorization'] = `tma ${initDataRaw}`
    config.headers['X-Telegram-Init-Data'] = initDataRaw
  } else if (import.meta.env.DEV) {
    const devInitData = import.meta.env.VITE_DEV_INIT_DATA || 'dev'
    config.headers['Authorization'] = `tma ${devInitData}`
    config.headers['X-Telegram-Init-Data'] = devInitData
  }
  return config
})

export default api

// Typed endpoint helpers
export const getMe           = ()                         => api.get('/api/me')
export const getCards        = ()                         => api.get('/api/cards')
export const getCard         = (id: string)               => api.get(`/api/cards/${id}`)
export const getPacks        = ()                         => api.get('/api/packs')
export const getPackStore    = ()                         => api.get('/api/packs/store')
export const openPack        = (id: string)               => api.post(`/api/packs/${id}/open`)
export const buyPack         = (id: string)               => api.post(`/api/packs/${id}/purchase`)
export const getEconomy      = ()                         => api.get('/api/economy')
export const claimDaily      = ()                         => api.post('/api/economy/daily')
export const getLeaderboard  = (metric = 'wins')          => api.get(`/api/leaderboard?metric=${metric}`)
export const createChallenge = (body: object)             => api.post('/api/battle/challenge', body)
export const acceptBattle    = (id: string, body: object) => api.post(`/api/battle/${id}/accept`, body)
export const cancelBattle    = (id: string)               => api.post(`/api/battle/${id}/cancel`)
export const getBattle       = (id: string)               => api.get(`/api/battle/${id}`)
export const getIncomingBattles = ()                      => api.get('/api/battle/incoming')
export const getBattleUpdates = ()                        => api.get('/api/battle/updates')
export const searchPlayers   = (q: string, limit = 10)    => api.get(`/api/players/search?q=${encodeURIComponent(q)}&limit=${limit}`)
export const registerBattlePlayer = ()                    => api.post('/api/battle/register')
export const getBattleOpponents   = ()                    => api.get('/api/battle/opponents')
export const searchBattleOpponents = (q: string)         => api.get(`/api/battle/opponents/search?q=${encodeURIComponent(q)}`)
export const getLineupCards   = ()                        => api.get('/api/battle/lineup/cards')
export const scoutBattle      = (id: string, slot: number) => api.post(`/api/battle/${id}/scout`, { slot })
export const generateLink    = ()                         => api.post('/api/link/generate')
export const getMarketplace  = ()                         => api.get('/api/marketplace')
export const sellCard        = (body: object)             => api.post('/api/marketplace/sell', body)
export const buyListing      = (id: number)               => api.post(`/api/marketplace/buy/${id}`)
export const getTrades       = ()                         => api.get('/api/trades')
export const createTrade     = (body: object)             => api.post('/api/trades', body)
export const acceptTrade     = (id: string)               => api.post(`/api/trades/${id}/accept`)
export const cancelTrade     = (id: string)               => api.post(`/api/trades/${id}/cancel`)
export const searchTradePartners = (query = '')           => api.get(`/api/trades/partners?query=${encodeURIComponent(query)}`)
export const getPartnerCards = (telegramId: number)       => api.get(`/api/trades/partners/${telegramId}/cards`)
export const getDust         = ()                         => api.get('/api/dust')
export const dustCards       = (card_ids: string[])       => api.post('/api/dust/dust_cards', { card_ids })

export const setReferrerHost = (host_token: string)       => api.post('/api/me/referrer', { host_token })
// Phase 5 — Telegram Stars checkout (Mini App)
export const getStarsCatalog = ()                         => api.get('/api/stars/catalog')
export const createStarsInvoice = (product_type: 'tier_pack' | 'creator_pack', ref: string) => api.post('/api/stars/invoice', { product_type, ref })
export const getStarsOrder   = (order_id: string)         => api.get(`/api/stars/orders/${order_id}`)
