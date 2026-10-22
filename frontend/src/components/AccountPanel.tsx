import { useEffect, useState } from 'react'
import { api, type AccountStatus, type ApiResult } from '../lib/api'
import { BranchSelector } from './BranchSelector'

export function AccountPanel({ onSessionEnd }: { onSessionEnd: () => void }) {
  const [account, setAccount] = useState<AccountStatus | null>(null)
  const [branch, setBranch] = useState('')
  const [destination, setDestination] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [confirmRevoke, setConfirmRevoke] = useState(false)

  useEffect(() => {
    let alive = true
    api.getAccount().then(result => {
      if (!alive) return
      if (result.ok && result.data) {
        setAccount(result.data)
        setBranch(result.data.branch_canonical)
      } else setError(result.status === 404 ? 'Account review controls are not available on this deployment.' :
        'Account status could not be loaded. Refresh or sign in again.')
    })
    return () => { alive = false }
  }, [])

  async function action(run: () => Promise<ApiResult<unknown>>, success: string, endsSession = false) {
    setBusy(true); setError(''); setMessage('')
    const result = await run()
    if (!result.ok) {
      setError(result.status === 401 ? 'Your session expired. Sign in again to continue.' : result.error || 'Request could not be completed.')
      setBusy(false)
      return
    }
    if (endsSession) { onSessionEnd(); return }
    const refreshed = await api.getAccount()
    if (refreshed.status === 401) { onSessionEnd(); return }
    if (refreshed.ok && refreshed.data) setAccount(refreshed.data)
    else setError('Saved, but account status could not be refreshed. Reload before making another change.')
    setMessage(success); setBusy(false)
  }

  return <section className="card bg-surface-1 border border-line p-6 sm:p-8 rounded-xl mt-6" aria-labelledby="account-title">
    <h2 id="account-title" className="font-display text-2xl uppercase">Account &amp; notification consent</h2>
    {error && <p className="alert-error mt-4" role="alert">{error}</p>}
    {message && <p className="alert-success mt-4" role="status">{message}</p>}
    {!account && !error && <p className="text-muted mt-4" role="status">Loading account status…</p>}
    {account && <>
      <dl className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm my-5">
        <div><dt className="text-muted">Email ownership</dt><dd>{account.email_verified ? 'Verified' : 'Not verified'}</dd></div>
        <div><dt className="text-muted">Enrollment review</dt><dd>{account.review_state === 'APPROVED' ? 'Approved' : account.review_state === 'REJECTED' ? 'Not approved' : 'Awaiting operator review'}</dd></div>
        <div><dt className="text-muted">Account access</dt><dd>{account.account_state.toLowerCase()}</dd></div>
        <div><dt className="text-muted">Email consent</dt><dd>{account.consent_state === 'GRANTED' ? 'Granted' : account.consent_state === 'WITHDRAWN' ? 'Withdrawn' : 'Not given'}</dd></div>
      </dl>
      <p className="text-sm text-muted mb-5">
        Email ownership does not verify enrollment or branch eligibility. Notifications require approved cohort and programme evidence, your consent, and a verified destination. Employer criteria still apply.
      </p>
      <p className="text-sm mb-5" role="status">{account.delivery_eligible ?
        'Your account meets the saved recipient checks. Delivery also depends on source eligibility and operator sending approval.' :
        'Eligibility-dependent email is held until all recipient checks pass.'}</p>
      <fieldset disabled={busy} className="space-y-6">
        <div>
          <h3 className="font-display text-lg uppercase mb-2">Declared programme</h3>
          <BranchSelector value={branch} onChange={setBranch} />
          <p className="text-xs text-muted my-2">Changing programme returns your enrollment to review and holds eligibility-dependent notifications.</p>
          <button type="button" className="btn-secondary text-xs" disabled={!branch || branch === account.branch_canonical}
            onClick={() => action(() => api.updateProfile(branch), 'Programme updated. Enrollment review is required.')}>Update programme</button>
        </div>
        <div>
          <h3 className="font-display text-lg uppercase mb-2">Notification destination</h3>
          <p className="text-sm break-all">{account.notification_email || 'No destination selected'}</p>
          <p className="text-xs text-muted mt-1 mb-3">{account.destination_verified ? 'Ownership verified.' : 'Ownership verification required.'} Selecting another address holds delivery until its owner confirms the link.</p>
          <label className="block text-xs text-muted mb-2" htmlFor="notification-destination">New notification email</label>
          <input id="notification-destination" className="input-base w-full" type="email" autoComplete="email" value={destination}
            onChange={event => setDestination(event.target.value)} />
          <button type="button" className="btn-secondary text-xs mt-3" disabled={!destination.trim()}
            onClick={() => action(() => api.requestDestination(destination.trim()), 'Destination verification requested. Mail may be held while sending is unavailable.')}>Request ownership verification</button>
        </div>
        <div>
          <h3 className="font-display text-lg uppercase mb-2">Opportunity email consent</h3>
          <p className="text-sm text-muted mb-3">Allow TPO-Watcher to email source opportunity updates to your verified destination according to your preferences. You can withdraw at any time. Already submitted mail cannot be recalled.</p>
          <button type="button" className={account.consent_state === 'GRANTED' ? 'btn-secondary text-xs' : 'btn-primary text-xs'}
            onClick={() => action(() => api.setConsent(account.consent_state !== 'GRANTED'), 'Email consent recorded. Other recipient checks still apply.', account.consent_state === 'GRANTED')}>
            {account.consent_state === 'GRANTED' ? 'Withdraw email consent' : 'I consent to opportunity emails'}
          </button>
        </div>
        <div className="border-t border-line pt-5">
          <button type="button" className="btn-secondary text-xs" onClick={() => action(api.logoutAll, '', true)}>Sign out of all sessions</button>
          <p className="text-xs text-muted mt-4">Close your account to revoke access and email consent. Required delivery and consent records are retained for recovery and privacy review; closure does not claim immediate erasure.</p>
          {!confirmRevoke ? <button type="button" className="btn-ghost text-xs mt-2" onClick={() => setConfirmRevoke(true)}>Close account…</button> :
            <div className="flex flex-wrap gap-3 mt-3">
              <button type="button" className="btn-secondary text-xs" onClick={() => action(api.revokeAccount, '', true)}>Confirm account closure</button>
              <button type="button" className="btn-ghost text-xs" onClick={() => setConfirmRevoke(false)}>Keep account</button>
            </div>}
        </div>
      </fieldset>
    </>}
  </section>
}
