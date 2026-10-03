import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Loader2, CheckCircle2, AlertCircle,
  ArrowLeft, Mail, ShieldCheck
} from 'lucide-react'
import { PreferenceCards } from '../components/PreferenceCards'
import { api, type PreferencesResponse } from '../lib/api'
import { cn, isValidEmail } from '../lib/utils'
import { getBranchLabel } from '../lib/branches'
import { Footer } from '../components/Footer'

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

  // Load preferences (will 401 if no valid session cookie)
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
      setReqError('VIT email address is required.')
      return
    }
    if (!isValidEmail(reqEmail)) {
      setReqError('Enter a valid VIT email address.')
      return
    }

    setReqLoading(true)
    await api.requestPreferenceLink(reqEmail.trim().toLowerCase())
    setReqLoading(false)

    // Uniform response to prevent email enumeration
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
      setTimeout(() => setSaveState('idle'), 3500)
    } else {
      setSaveState('error')
      setSaveMsg(result.error || 'Failed to save preferences. Please try again.')
    }
  }

  function prefChange(id: 'pref_internship' | 'pref_placement' | 'pref_ppo', val: boolean) {
    setPrefs((p) => ({ ...p, [id]: val }))
    if (saveState === 'saved') setSaveState('idle')
  }

  return (
    <div className="min-h-screen bg-background text-foreground flex flex-col justify-between pt-24">
      <div className="section-container py-12 flex-1 w-full max-w-2xl mx-auto">
        {/* Back navigation */}
        <button
          type="button"
          onClick={() => navigate('/')}
          className="btn-ghost mb-8 -ml-2 text-xs font-mono tracking-widest uppercase flex items-center gap-2"
        >
          <ArrowLeft className="h-4 w-4" />
          BACK TO HOME
        </button>

        <AnimatePresence mode="wait">
          {/* ── 01: LOADING ───────────────────────────────────────────── */}
          {pageState === 'loading' && (
            <motion.div
              key="loading"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="flex flex-col items-center justify-center py-24 text-center"
            >
              <Loader2 className="h-8 w-8 text-accent animate-spin mb-4" />
              <p className="text-xs font-mono uppercase tracking-widest text-muted">
                VERIFYING ACTIVE SESSION...
              </p>
            </motion.div>
          )}

          {/* ── 02: REQUEST MAGIC LINK ────────────────────────────────── */}
          {pageState === 'request-link' && (
            <motion.div
              key="request-link"
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.35 }}
            >
              <div className="mb-8">
                <span className="text-[11px] font-mono tracking-[0.25em] text-accent uppercase block mb-2 font-semibold">
                  IDENTITY VERIFICATION
                </span>
                <h1 className="font-display text-4xl sm:text-5xl uppercase text-foreground leading-tight tracking-tight">
                  MANAGE PREFERENCES
                </h1>
                <p className="mt-3 text-sm text-muted font-light leading-relaxed font-sans">
                  To protect your subscription without requiring passwords, enter your registered email. We’ll send a secure single-use 15-minute access link.
                </p>
              </div>

              <div className="card bg-surface-1 border border-line-strong p-6 sm:p-8 rounded-xl shadow-[0_20px_50px_rgba(0,0,0,0.8)]">
                <form onSubmit={handleRequestLink} noValidate className="space-y-5">
                  {reqError && (
                    <div className="alert-error" role="alert">
                      <AlertCircle className="h-4 w-4 shrink-0 text-danger-500" />
                      <span>{reqError}</span>
                    </div>
                  )}

                  <div>
                    <label
                      htmlFor="req-email"
                      className="block text-xs font-mono uppercase tracking-wider text-muted mb-2"
                    >
                      REGISTERED VIT EMAIL
                    </label>
                    <input
                      id="req-email"
                      type="email"
                      autoComplete="email"
                      inputMode="email"
                      value={reqEmail}
                      onChange={(e) => {
                        setReqEmail(e.target.value)
                        if (reqError) setReqError('')
                      }}
                      placeholder="firstname.lastname@vit.edu"
                      className={cn('input-base font-sans', reqError && 'error')}
                      disabled={reqLoading}
                    />
                  </div>

                  <button
                    type="submit"
                    disabled={reqLoading}
                    className="btn-primary w-full py-3.5 text-xs tracking-widest justify-center"
                  >
                    {reqLoading ? (
                      <>
                        <Loader2 className="h-4 w-4 animate-spin" />
                        SENDING ACCESS LINK...
                      </>
                    ) : (
                      <>
                        SEND 15-MINUTE ACCESS LINK
                        <Mail className="h-4 w-4" />
                      </>
                    )}
                  </button>

                  <div className="pt-2 text-center text-[11px] font-mono text-muted flex items-center justify-center gap-1.5">
                    <ShieldCheck className="h-3.5 w-3.5 text-emerald-400" />
                    <span>Passwordless 256-bit CSPRNG token security.</span>
                  </div>
                </form>
              </div>
            </motion.div>
          )}

          {/* ── 03: MAGIC LINK SENT CONFIRMATION ──────────────────────── */}
          {pageState === 'link-sent' && (
            <motion.div
              key="link-sent"
              initial={{ opacity: 0, scale: 0.97 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.35 }}
              className="card bg-surface-1 border border-line-strong p-8 sm:p-12 text-center rounded-xl"
            >
              <div className="w-16 h-16 rounded-full bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center mx-auto mb-5 text-emerald-400">
                <CheckCircle2 className="h-8 w-8" />
              </div>
              <h2 className="font-display text-3xl uppercase text-foreground tracking-wide">
                ACCESS LINK DISPATCHED
              </h2>
              <p className="mt-3 text-sm text-muted font-light max-w-md mx-auto font-sans leading-relaxed">
                If an active verified subscription exists for that address, a secure 15-minute access link has been dispatched to your inbox.
              </p>
              <div className="mt-6 p-4 bg-surface-2 rounded-lg border border-line text-xs font-mono text-muted text-left">
                Open the link in your email to authenticate this browser session and update your opportunity alerts.
              </div>
              <button
                type="button"
                onClick={() => setPageState('request-link')}
                className="mt-8 btn-secondary text-xs tracking-widest"
              >
                REQUEST FOR ANOTHER ADDRESS
              </button>
            </motion.div>
          )}

          {/* ── 04: AUTHENTICATED PREFERENCES EDITOR ──────────────────── */}
          {pageState === 'loaded' && preferences && (
            <motion.div
              key="loaded"
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.35 }}
            >
              <div className="mb-8">
                <span className="text-[11px] font-mono tracking-[0.25em] text-accent uppercase block mb-2 font-semibold">
                  AUTHENTICATED SESSION
                </span>
                <h1 className="font-display text-4xl sm:text-5xl uppercase text-foreground leading-tight tracking-tight">
                  SUBSCRIPTION SETTINGS
                </h1>
                <p className="mt-2 text-sm text-muted font-light font-sans">
                  Manage which placement notifications are routed to your inbox.
                </p>
              </div>

              {/* Profile Details Card */}
              <div className="card bg-surface-1 border border-line p-5 rounded-xl mb-6">
                <div className="flex items-center justify-between border-b border-line pb-3 mb-3">
                  <span className="text-[10px] font-mono tracking-widest text-muted uppercase">SUBSCRIBER IDENTITY</span>
                  <span className="text-[10px] font-mono text-emerald-400 flex items-center gap-1">
                    ● ACTIVE
                  </span>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-xs font-mono">
                  <div>
                    <span className="text-muted block text-[10px] uppercase">EMAIL</span>
                    <span className="text-foreground font-semibold truncate block mt-0.5 font-sans">
                      {preferences.email}
                    </span>
                  </div>
                  <div>
                    <span className="text-muted block text-[10px] uppercase">PROGRAMME</span>
                    <span className="text-foreground font-semibold block mt-0.5">
                      {getBranchLabel(preferences.branch_canonical)}
                    </span>
                  </div>
                </div>
              </div>

              {/* Preferences Form */}
              <form onSubmit={handleSave} className="space-y-6">
                <div className="card bg-surface-1 border border-line-strong p-6 sm:p-8 rounded-xl shadow-[0_20px_50px_rgba(0,0,0,0.8)]">
                  <div className="mb-4">
                    <span className="text-xs font-mono uppercase tracking-wider text-muted block mb-1">
                      OPPORTUNITY CHANNELS
                    </span>
                    <p className="text-xs text-muted font-light font-sans">
                      Toggle the categories of recruitment opportunities you wish to receive.
                    </p>
                  </div>

                  <PreferenceCards values={prefs} onChange={prefChange} />

                  {saveMsg && (
                    <div
                      className={cn(
                        'mt-5',
                        saveState === 'saved' ? 'alert-success' : 'alert-error'
                      )}
                    >
                      {saveState === 'saved' ? (
                        <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" />
                      ) : (
                        <AlertCircle className="h-4 w-4 shrink-0 text-danger-500" />
                      )}
                      <span>{saveMsg}</span>
                    </div>
                  )}

                  <div className="mt-8 flex flex-col sm:flex-row items-center gap-4">
                    <button
                      type="submit"
                      disabled={saveState === 'loading'}
                      className="btn-primary w-full sm:w-auto text-xs tracking-widest justify-center py-3.5 px-6"
                    >
                      {saveState === 'loading' ? (
                        <>
                          <Loader2 className="h-4 w-4 animate-spin" />
                          SAVING PREFERENCES...
                        </>
                      ) : (
                        'SAVE PREFERENCES'
                      )}
                    </button>

                    <button
                      type="button"
                      onClick={() => navigate('/')}
                      className="btn-secondary w-full sm:w-auto text-xs tracking-widest justify-center py-3.5 px-5"
                    >
                      CANCEL
                    </button>
                  </div>
                </div>
              </form>
            </motion.div>
          )}

          {/* ── 05: LOAD ERROR ────────────────────────────────────────── */}
          {pageState === 'error' && (
            <motion.div
              key="error"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="card bg-surface-1 border border-line p-8 text-center rounded-xl"
            >
              <AlertCircle className="h-10 w-10 text-danger-500 mx-auto mb-4" />
              <h2 className="font-display text-2xl uppercase text-foreground">SESSION ERROR</h2>
              <p className="mt-2 text-sm text-muted">{loadError}</p>
              <button
                type="button"
                onClick={() => setPageState('request-link')}
                className="mt-6 btn-secondary text-xs tracking-widest"
              >
                REQUEST NEW ACCESS LINK
              </button>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      <Footer navigate={navigate} />
    </div>
  )
}
