import { useEffect, useState } from 'react'
import { Navbar } from './components/Navbar'
import { SignupPage } from './pages/SignupPage'
import { PreferencesPage } from './pages/PreferencesPage'
import { VerifyPage } from './pages/VerifyPage'
import { UnsubscribePage } from './pages/UnsubscribePage'
import { ErrorPage } from './pages/ErrorPage'

type Route =
  | { page: 'home' }
  | { page: 'preferences' }
  | { page: 'verify'; token: string }
  | { page: 'unsubscribe'; token: string }
  | { page: 'error'; message: string }

function getRoute(): Route {
  const path = window.location.pathname
  const search = new URLSearchParams(window.location.search)
  const token = search.get('token') || ''

  if (path === '/api/v1/auth/verify' || path === '/verify') return { page: 'verify', token }
  if (path === '/api/v1/unsubscribe' || path === '/unsubscribe') return { page: 'unsubscribe', token }
  if (path === '/api/v1/preferences/request') return { page: 'verify', token } // magic link exchange, handled server-side but show UI if JS loads
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
        {route.page === 'verify' && <VerifyPage token={route.token} navigate={navigate} />}
        {route.page === 'unsubscribe' && <UnsubscribePage token={route.token} navigate={navigate} />}
        {route.page === 'error' && <ErrorPage message={route.message} navigate={navigate} />}
      </main>
    </div>
  )
}
