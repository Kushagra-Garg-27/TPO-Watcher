import { useState } from 'react'
import { motion } from 'framer-motion'
import { AlertCircle, Loader2, ShieldCheck, ArrowRight } from 'lucide-react'
import { api } from '../lib/api'

interface PreferencesConfirmPageProps {
  token: string
  navigate: (path: string) => void
}

type ConfirmState = 'confirming' | 'loading' | 'error'

export function PreferencesConfirmPage({ token, navigate }: PreferencesConfirmPageProps) {
  const [state, setState] = useState<ConfirmState>(() => {
    if (!token) return 'error'
    return 'confirming'
  })
  const [errorMessage, setErrorMessage] = useState(() => {
    if (!token) return 'The preference access token is missing. Please use the link directly from your email.'
    return ''
  })

  async function handleConfirm() {
    if (!token) {
      setState('error')
      setErrorMessage('The preference access token is missing. Please use the link directly from your email.')
      return
    }

    setState('loading')
    const res = await api.preferencesConfirm(token)
    if (res.ok) {
      navigate('/preferences')
    } else {
      setState('error')
      setErrorMessage(res.error || 'Invalid or expired preference link. Please request a new link.')
    }
  }

  return (
    <div className="min-h-screen bg-background text-foreground flex items-center justify-center px-4 relative overflow-hidden">
      <div className="absolute inset-0 bg-dark-grid opacity-30 pointer-events-none" />

      <div className="max-w-md w-full relative z-10">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
          className="card bg-surface-1 border border-line-strong p-8 sm:p-10 text-center rounded-xl shadow-[0_20px_50px_rgba(0,0,0,0.85)]"
        >
          {state === 'confirming' && (
            <>
              <div className="w-16 h-16 rounded-full bg-accent/10 border border-accent/30 flex items-center justify-center mx-auto mb-5 text-accent">
                <ShieldCheck className="h-8 w-8" />
              </div>
              <span className="text-[10px] font-mono tracking-[0.2em] text-accent uppercase block mb-1">
                SECURE ACCESS
              </span>
              <h1 className="font-display text-2xl uppercase text-foreground tracking-wide">
                MANAGE PREFERENCES
              </h1>
              <p className="mt-3 text-sm text-muted font-light max-w-sm mx-auto font-sans leading-relaxed">
                Click below to authenticate your session and update your notification preferences.
              </p>
              <div className="mt-8 flex flex-col gap-3">
                <button
                  type="button"
                  onClick={handleConfirm}
                  className="btn-primary w-full text-xs tracking-widest justify-center py-3.5"
                >
                  Continue to preferences
                  <ArrowRight className="h-3.5 w-3.5 ml-1" />
                </button>
                <button
                  type="button"
                  onClick={() => navigate('/')}
                  className="btn-secondary w-full text-xs tracking-widest justify-center py-3.5"
                >
                  RETURN TO HOME
                </button>
              </div>
            </>
          )}

          {state === 'loading' && (
            <>
              <Loader2 className="h-12 w-12 text-accent animate-spin mx-auto mb-5" />
              <h1 className="font-display text-2xl uppercase tracking-wide text-foreground">
                AUTHENTICATING...
              </h1>
              <p className="mt-2 text-xs font-mono text-muted tracking-wider">
                EXCHANGING SINGLE-USE TOKEN
              </p>
            </>
          )}

          {state === 'error' && (
            <>
              <div className="w-16 h-16 rounded-full bg-danger-500/10 border border-danger-500/30 flex items-center justify-center mx-auto mb-5 text-danger-500">
                <AlertCircle className="h-8 w-8" />
              </div>
              <span className="text-[10px] font-mono tracking-[0.2em] text-danger-500 uppercase block mb-1">
                ACCESS FAILED
              </span>
              <h1 className="font-display text-2xl uppercase text-foreground tracking-wide">
                LINK INVALID OR EXPIRED
              </h1>
              <p className="mt-3 text-sm text-muted font-light max-w-sm mx-auto font-sans leading-relaxed">
                {errorMessage}
              </p>
              <div className="mt-8 flex flex-col gap-3">
                <button
                  type="button"
                  onClick={() => navigate('/preferences')}
                  className="btn-primary w-full text-xs tracking-widest justify-center py-3.5"
                >
                  REQUEST A NEW LINK
                </button>
                <button
                  type="button"
                  onClick={() => navigate('/')}
                  className="btn-secondary w-full text-xs tracking-widest justify-center py-3.5"
                >
                  RETURN TO HOME
                </button>
              </div>
            </>
          )}
        </motion.div>
      </div>
    </div>
  )
}
