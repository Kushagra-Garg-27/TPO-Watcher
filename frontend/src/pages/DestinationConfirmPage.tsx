import { useState } from 'react'
import { api } from '../lib/api'

export function DestinationConfirmPage({ token, navigate }: { token: string; navigate: (path: string) => void }) {
  const [busy, setBusy] = useState(false)
  const [complete, setComplete] = useState(false)
  const [error, setError] = useState(token ? '' : 'The verification token is missing. Open the link from your email.')
  async function confirm() {
    setBusy(true); setError('')
    const result = await api.confirmDestination(token)
    if (result.ok) setComplete(true)
    else setError(result.error || 'This verification link is invalid, expired or already used.')
    setBusy(false)
  }
  return <div className="min-h-screen flex items-center justify-center px-4">
    <section className="card bg-surface-1 border border-line p-8 rounded-xl max-w-md w-full">
      <h1 className="font-display text-3xl uppercase">{complete ? 'Destination verified' : 'Verify notification email'}</h1>
      <p className="text-sm text-muted my-5">{complete ?
        'Ownership is confirmed. Email consent and approved enrollment evidence are still required for opportunity notifications.' :
        'Confirm that you own this address and want to use it as the notification destination for the requesting account.'}</p>
      {error && <p role="alert" className="alert-error mb-4">{error}</p>}
      {!complete && <button className="btn-primary w-full" type="button" disabled={!token || busy} onClick={confirm}>
        {busy ? 'Verifying…' : 'Confirm email ownership'}</button>}
      <button className="btn-secondary w-full mt-4" type="button" onClick={() => navigate('/preferences')}>Go to account settings</button>
    </section>
  </div>
}
