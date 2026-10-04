import { useEffect, useState } from 'react'
import { Navbar } from './components/Navbar'
import { SignupPage } from './pages/SignupPage'
import { PreferencesPage } from './pages/PreferencesPage'
import { PreferencesConfirmPage } from './pages/PreferencesConfirmPage'
import { VerifyPage } from './pages/VerifyPage'
import { UnsubscribePage } from './pages/UnsubscribePage'
import { ErrorPage } from './pages/ErrorPage'

type Route =
  | { page: 'home' }
  | { page: 'preferences' }
  | { page: 'preferences_confirm'; token: string }
  | { page: 'verify'; token: string }
  | { page: 'unsubscribe'; token: string }
  | { page: 'error'; message: string }

function extractAndStripToken(): string {
  let token = ''

  // 1. Read fragment token first (#token=...)
  const hash = window.location.hash
  if (hash) {
    const rawHash = hash.startsWith('#') ? hash.slice(1) : hash
    const hashParams = new URLSearchParams(rawHash)
    token = hashParams.get('token') || ''
  }

  // 2. Otherwise read query token for compatibility (?token=...)
  if (!token) {
    const searchParams = new URLSearchParams(window.location.search)
    token = searchParams.get('token') || ''
  }

  // 3 & 4. Immediately remove token from visible URL/browser history using history.replaceState
  if (token || window.location.hash.includes('token') || window.location.search.includes('token')) {
    const url = new URL(window.location.href)
    if (url.hash) {
      const rawHash = url.hash.startsWith('#') ? url.hash.slice(1) : url.hash
      const hashParams = new URLSearchParams(rawHash)
      hashParams.delete('token')
      const remainingHash = hashParams.toString()
      url.hash = remainingHash ? `#${remainingHash}` : ''
    }
    url.searchParams.delete('token')
    const cleanPath = url.pathname + (url.search ? url.search : '') + (url.hash ? url.hash : '')
    window.history.replaceState(null, '', cleanPath)
  }

  return token
}

function getRoute(): Route {
  const path = window.location.pathname
  const token = extractAndStripToken()

  if (path === '/api/v1/auth/verify' || path === '/verify') return { page: 'verify', token }
  if (path === '/api/v1/unsubscribe' || path === '/unsubscribe') return { page: 'unsubscribe', token }
  if (path === '/api/v1/preferences/request' || path === '/preferences/confirm') return { page: 'preferences_confirm', token }
  if (path === '/preferences') return { page: 'preferences' }
  if (path === '/signup' || path === '/') return { page: 'home' }
  return { page: 'error', message: 'Page not found.' }
}

export default function App() {
  const [route, setRoute] = useState<Route>(getRoute)

  useEffect(() => {
    const handler = () => setRoute(getRoute())
    window.addEventListener('popstate', handler)
    return () => window.removeEventListener('popstate', handler)
  }, [])

  function navigate(path: string) {
    window.history.pushState(null, '', path)
    setRoute(getRoute())
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const showNavbar = route.page === 'home' || route.page === 'preferences'

  return (
    <div className="min-h-screen bg-background text-foreground selection:bg-accent selection:text-white">
      <a href="#main-content" className="skip-link">Skip to main content</a>

      {showNavbar && <Navbar navigate={navigate} />}

      <main id="main-content">
        {route.page === 'home' && <SignupPage navigate={navigate} />}
        {route.page === 'preferences' && <PreferencesPage navigate={navigate} />}
        {route.page === 'preferences_confirm' && <PreferencesConfirmPage token={route.token} navigate={navigate} />}
        {route.page === 'verify' && <VerifyPage token={route.token} navigate={navigate} />}
        {route.page === 'unsubscribe' && <UnsubscribePage token={route.token} navigate={navigate} />}
        {route.page === 'error' && <ErrorPage message={route.message} navigate={navigate} />}
      </main>
    </div>
  )
}
