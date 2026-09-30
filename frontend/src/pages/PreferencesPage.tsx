import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Bell, Loader2, CheckCircle2, AlertCircle,
  ArrowLeft, Settings, GraduationCap
} from 'lucide-react'
import { PreferenceCards } from '../components/PreferenceCards'
import { api, type PreferencesResponse } from '../lib/api'
import { cn, isValidEmail } from '../lib/utils'
import { getBranchLabel } from '../lib/branches'

interface PreferencesPageProps {
  navigate: (path: string) => void
}

type PageState = 'request-link' | 'loading' | 'loaded' | 'error' | 'link-sent'

export function PreferencesPage({ navigate }: PreferencesPageProps) {
  const [pageState, setPageState] = useState<PageState>('loading')
  const [preferences, setPreferences] = useState<PreferencesResponse | null>(null)
  const [loadError, setLoadError] = useState('')

  // Request-link form state
  const [reqEmail, setReqEmail] = useState('')
  const [reqError, setReqError] = useState('')
  const [reqLoading, setReqLoading] = useState(false)

  // Save form state
  const [prefs, setPrefs] = useState({
    pref_internship: false,
    pref_placement: false,
    pref_ppo: false,
  })
  const [saveState, setSaveState] = useState<'idle' | 'loading' | 'saved' | 'error'>('idle')
  const [saveMsg, setSaveMsg] = useState('')

  // Try to load preferences (will 401 if no session cookie)
  useEffect(() => {
    async function loadPrefs() {
      const result = await api.getPreferences()
      if (result.ok && result.data) {
        setPreferences(result.data)
        setPrefs({
          pref_internship: result.data.pref_internship,
          pref_placement: result.data.pref_placement,
          pref_ppo: result.data.pref_ppo,
        })
        setPageState('loaded')
      } else if (result.status === 401 || !result.status) {
        // 401 = not authenticated; no status = network error (dev mode without backend)
        // Both cases: prompt for magic link
        setPageState('request-link')
      } else {
        setLoadError(result.error || 'Failed to load preferences.')
        setPageState('error')
      }
    }
    loadPrefs()
  }, [])

  async function handleRequestLink(e: React.FormEvent) {
    e.preventDefault()
    setReqError('')

    if (!reqEmail.trim()) {
      setReqError('Email address is required.')
      return
    }
    if (!isValidEmail(reqEmail)) {
      setReqError('Enter a valid email address.')
      return
    }

    setReqLoading(true)
    await api.requestPreferenceLink(reqEmail.trim().toLowerCase())
    setReqLoading(false)

    // Always show success (security: don't reveal if email exists)
    setPageState('link-sent')
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault()
    setSaveState('loading')
    setSaveMsg('')

    const result = await api.updatePreferences(prefs)
    if (result.ok) {
      setSaveState('saved')
      setSaveMsg('Preferences updated successfully.')
      setTimeout(() => setSaveState('idle'), 3000)
    } else {
      setSaveState('error')
      setSaveMsg(result.error || 'Failed to save. Please try again.')
    }
  }

  function prefChange(id: 'pref_internship' | 'pref_placement' | 'pref_ppo', val: boolean) {
    setPrefs(p => ({ ...p, [id]: val }))
    if (saveState === 'saved') setSaveState('idle')
  }

  return (
    <div className="min-h-screen bg-slate-50 pt-14">
      <div className="section-container py-12">
        <div className="max-w-lg mx-auto">
          {/* Back button */}
          <button
            type="button"
            onClick={() => navigate('/')}
            className="btn-ghost mb-6 -ml-2"
          >
            <ArrowLeft className="h-4 w-4" />
            Back to home
          </button>

          <AnimatePresence mode="wait">
            {/* ── Loading ─────────────────────────────── */}
            {pageState === 'loading' && (
              <motion.div
                key="loading"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="flex items-center justify-center py-20"
              >
                <Loader2 className="h-8 w-8 text-brand-500 animate-spin" />
              </motion.div>
            )}

            {/* ── Request link ────────────────────────── */}
            {pageState === 'request-link' && (
              <motion.div
                key="request-link"
                initial={{ opacity: 0, y: 16 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.4 }}
              >
                <div className="flex items-center gap-3 mb-6">
                  <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-brand-100">
                    <Settings className="h-5 w-5 text-brand-600" />
                  </span>
                  <div>
                    <h1 className="text-xl font-bold text-slate-900">Manage your preferences</h1>
                    <p className="text-sm text-slate-500">Secure access, no password required</p>
                  </div>
                </div>

                <div className="card p-6">
                  <p className="text-sm text-slate-600 leading-relaxed mb-5">
                    Enter your registered VIT email to receive a single-use, 15-minute secure access link.
                    Your TPO credentials are never requested.
                  </p>

                  <form onSubmit={handleRequestLink} noValidate className="space-y-4">
                    <div>
                      <label htmlFor="req-email" className="block text-sm font-medium text-slate-700 mb-1.5">
                        Registered email address
                      </label>
                      <input
                        id="req-email"
                        type="email"
                        autoComplete="email"
                        inputMode="email"
                        value={reqEmail}
                        onChange={e => {
                          setReqEmail(e.target.value)
                          if (reqError) setReqError('')
                        }}
                        placeholder="firstname.lastname@vit.edu"
                        className={cn('input-base', reqError && 'error')}
                        aria-invalid={!!reqError}
                        aria-describedby={reqError ? 'req-email-error' : undefined}
                        disabled={reqLoading}
                      />
                      {reqError && (
                        <motion.p
                          id="req-email-error"
                          initial={{ opacity: 0, y: -4 }}
                          animate={{ opacity: 1, y: 0 }}
                          className="mt-1.5 text-xs text-danger-600 flex items-center gap-1"
                          role="alert"
                        >
                          <AlertCircle className="h-3 w-3" />
                          {reqError}
                        </motion.p>
                      )}
                    </div>

                    <button
                      type="submit"
                      disabled={reqLoading}
                      className="btn-primary w-full text-sm"
                    >
                      {reqLoading ? (
                        <><Loader2 className="h-4 w-4 animate-spin" />Sending...</>
                      ) : (
                        'Send Secure Access Link'
                      )}
                    </button>
                  </form>

                  <p className="mt-4 text-center text-xs text-slate-400">
                    Not signed up yet?{' '}
                    <button
                      type="button"
                      onClick={() => navigate('/')}
                      className="text-brand-600 font-medium hover:text-brand-700 focus:outline-none"
                    >
                      Subscribe for alerts →
                    </button>
                  </p>
                </div>
              </motion.div>
            )}

            {/* ── Link sent ───────────────────────────── */}
            {pageState === 'link-sent' && (
              <motion.div
                key="link-sent"
                initial={{ opacity: 0, scale: 0.97 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.35 }}
                className="card p-8 text-center"
              >
                <motion.div
                  initial={{ scale: 0 }}
                  animate={{ scale: 1 }}
                  transition={{ type: 'spring', stiffness: 400, damping: 20, delay: 0.1 }}
                  className="flex h-16 w-16 items-center justify-center rounded-full bg-success-100 mx-auto mb-4"
                >
                  <CheckCircle2 className="h-8 w-8 text-success-600" />
                </motion.div>
                <h2 className="text-lg font-semibold text-slate-900">Check your inbox</h2>
                <p className="mt-2 text-sm text-slate-600 max-w-sm mx-auto">
                  If an account exists for that email, a 15-minute access link has been sent to your inbox.
                </p>
                <p className="mt-4 text-xs text-slate-400">
                  Check your spam folder if the email doesn't arrive within a few minutes.
                </p>
                <button
                  type="button"
                  onClick={() => {
                    setPageState('request-link')
                    setReqEmail('')
                    setReqError('')
                  }}
                  className="mt-6 btn-secondary text-sm"
                >
                  Use a different email
                </button>
              </motion.div>
            )}

            {/* ── Error ───────────────────────────────── */}
            {pageState === 'error' && (
              <motion.div
                key="error"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                className="card p-8 text-center"
              >
                <AlertCircle className="h-12 w-12 text-danger-400 mx-auto mb-4" />
                <h2 className="text-lg font-semibold text-slate-900">Something went wrong</h2>
                <p className="mt-2 text-sm text-slate-600">{loadError}</p>
                <button
                  type="button"
                  onClick={() => { setPageState('loading'); setLoadError(''); window.location.reload() }}
                  className="mt-6 btn-secondary text-sm"
                >
                  Try again
                </button>
              </motion.div>
            )}

            {/* ── Loaded: Edit preferences ─────────────── */}
            {pageState === 'loaded' && preferences && (
              <motion.div
                key="loaded"
                initial={{ opacity: 0, y: 16 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.4 }}
              >
                <div className="flex items-center gap-3 mb-6">
                  <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-brand-100">
                    <Bell className="h-5 w-5 text-brand-600" />
                  </span>
                  <div>
                    <h1 className="text-xl font-bold text-slate-900">Notification preferences</h1>
                    <p className="text-sm text-slate-500">{preferences.email}</p>
                  </div>
                </div>

                {/* User info card */}
                <div className="card p-4 mb-5">
                  <div className="flex flex-col sm:flex-row gap-3 sm:gap-6 text-sm">
                    <span className="flex items-center gap-2 text-slate-600">
                      <GraduationCap className="h-4 w-4 text-slate-400" />
                      <span>Class of <strong className="text-slate-800">{preferences.graduation_year}</strong></span>
                    </span>
                    <span className="flex items-center gap-2 text-slate-600">
                      <span className="h-4 w-4 text-slate-400 font-bold text-base">B</span>
                      <span className="truncate" title={getBranchLabel(preferences.branch_canonical)}>
                        {getBranchLabel(preferences.branch_canonical)}
                      </span>
                    </span>
                  </div>
                </div>

                <form onSubmit={handleSave} className="card p-6 space-y-5">
                  <div>
                    <p className="text-sm font-semibold text-slate-900 mb-3">Alert preferences</p>
                    <PreferenceCards values={prefs} onChange={prefChange} />
                  </div>

                  {/* Status messages */}
                  <AnimatePresence>
                    {saveState === 'saved' && (
                      <motion.div
                        initial={{ opacity: 0, y: -4 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0 }}
                        className="alert-success text-sm"
                        role="status"
                      >
                        <CheckCircle2 className="h-4 w-4 flex-shrink-0" />
                        {saveMsg}
                      </motion.div>
                    )}
                    {saveState === 'error' && (
                      <motion.div
                        initial={{ opacity: 0, y: -4 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0 }}
                        className="alert-error text-sm"
                        role="alert"
                      >
                        <AlertCircle className="h-4 w-4 flex-shrink-0" />
                        {saveMsg}
                      </motion.div>
                    )}
                  </AnimatePresence>

                  <button
                    type="submit"
                    disabled={saveState === 'loading'}
                    className="btn-primary w-full text-sm"
                  >
                    {saveState === 'loading' ? (
                      <><Loader2 className="h-4 w-4 animate-spin" />Saving...</>
                    ) : (
                      'Save Preferences'
                    )}
                  </button>
                </form>

                <p className="mt-4 text-center text-xs text-slate-400">
                  To unsubscribe entirely, use the unsubscribe link in any alert email you receive.
                </p>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </div>
  )
}
