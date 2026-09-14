import { useEffect } from 'react'
import { BrowserRouter, Routes, Route, useLocation, useNavigate } from 'react-router-dom'
import { useLaunchParams, useSignal } from '@telegram-apps/sdk-react'
import { backButton, themeParams } from '@telegram-apps/sdk'
import NavBar from './components/NavBar'
import Home from './pages/Home'
import Collection from './pages/Collection'
import Pack from './pages/Pack'
import Battle from './pages/Battle'
import Daily from './pages/Daily'
import Store from './pages/Store'
import Market from './pages/Market'
import Trade from './pages/Trade'
import Craft from './pages/Craft'
import { setReferrerHost } from './api/client'

function Inner() {
  const location = useLocation()
  const navigate = useNavigate()
  const bgColor  = useSignal(themeParams.backgroundColor)
  const txtColor = useSignal(themeParams.textColor)

  // Handle BackButton — show on all non-root pages
  useEffect(() => {
    const isRoot = location.pathname === '/'
    try {
      if (backButton.isSupported()) {
        isRoot ? backButton.hide() : backButton.show()
        const off = backButton.onClick(() => navigate(-1))
        return () => off()
      }
    } catch {
      // Outside Telegram — ignore
    }
  }, [location.pathname, navigate])

  // Handle deep link startParam — e.g. battle_X9K2QR, host_<token>
  const lp = useLaunchParams()
  useEffect(() => {
    const sp: string = (lp as any).startParam || (lp as any).tgWebAppStartParam || ''
    if (sp.startsWith('battle_')) {
      navigate(`/battle?id=${sp.replace('battle_', '')}`)
    }
    if (sp.startsWith('host_')) {
      const token = sp.slice(5).trim()
      if (token) {
        setReferrerHost(token).catch(() => {})
      }
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div style={{
      minHeight: 'var(--tg-viewport-height, 100vh)',
      backgroundColor: bgColor || '#0D0B2E',
      color: txtColor || '#ffffff',
      maxWidth: 480,
      margin: '0 auto',
    }}>
      <Routes>
        <Route path="/"           element={<Home />} />
        <Route path="/collection" element={<Collection />} />
        <Route path="/packs"      element={<Pack />} />
        <Route path="/store"      element={<Store />} />
        <Route path="/market"     element={<Market />} />
        <Route path="/trade"      element={<Trade />} />
        <Route path="/battle"     element={<Battle />} />
        <Route path="/daily"      element={<Daily />} />
        <Route path="/craft"      element={<Craft />} />
      </Routes>
      <NavBar />
    </div>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <Inner />
    </BrowserRouter>
  )
}
