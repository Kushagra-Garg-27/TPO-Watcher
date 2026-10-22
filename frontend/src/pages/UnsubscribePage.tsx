import { useState } from 'react'
import { motion } from 'framer-motion'
import { CheckCircle2, AlertCircle, Loader2, BellOff, ArrowRight } from 'lucide-react'
import { api } from '../lib/api'

interface UnsubscribePageProps {
  token: string
  navigate: (path: string) => void
}

type UnsubscribeState = 'confirming' | 'loading' | 'success' | 'error'

export function UnsubscribePage({ token, navigate }: UnsubscribePageProps) {
  const [state, setState] = useState<UnsubscribeState>(() => {
    if (!token) return 'error'
    return 'confirming'
  })

  const [message, setMessage] = useState(() => {
    if (!token) {
      return 'The unsubscribe token is missing. Please use the link directly from your email.'
    }
    return ''
  })

  async function handleConfirm() {
    if (!token) {
      setState('error')
      setMessage('The unsubscribe token is missing. Please use the link directly from your email.')
      return
    }

    setState('loading')
    const res = await api.unsubscribeConfirm(token)
    if (res.ok) {
      setState('success')
    } else {
      setState('error')
      setMessage(res.error || 'Unsubscribe request failed. The token may have expired or already been used.')
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
              <div className="w-16 h-16 rounded-full bg-warning-500/10 border border-warning-500/30 flex items-center justify-center mx-auto mb-5 text-warning-500">
                <BellOff className="h-7 w-7" />
              </div>
              <span className="text-[10px] font-mono tracking-[0.2em] text-warning-500 uppercase block mb-1">
                CONFIRMATION REQUIRED
              </span>
              <h1 className="font-display text-2xl uppercase text-foreground tracking-wide">
                CANCEL TPO ALERTS?
              </h1>
              <p className="mt-3 text-sm text-muted font-light max-w-sm mx-auto font-sans leading-relaxed">
                You will stop receiving automated email alerts for newly detected VIT Pune placement and internship opportunities.
              </p>
              <div className="mt-8 flex flex-col gap-3">
                <button
                  type="button"
                  onClick={handleConfirm}
                  className="btn-destructive w-full text-xs tracking-widest justify-center py-3.5"
                >
                  Confirm unsubscribe
                  <ArrowRight className="h-3.5 w-3.5 ml-1" />
                </button>
                <button
                  type="button"
                  onClick={() => navigate('/')}
                  className="btn-secondary w-full text-xs tracking-widest justify-center py-3.5"
                >
                  KEEP MY SUBSCRIPTION ACTIVE
                </button>
              </div>
            </>
          )}

          {state === 'loading' && (
            <>
              <Loader2 className="h-12 w-12 text-accent animate-spin mx-auto mb-5" />
              <h1 className="font-display text-2xl uppercase tracking-wide text-foreground">
                UNSUBSCRIBING...
              </h1>
              <p className="mt-2 text-xs font-mono text-muted tracking-wider">
                PROCESSING REQUEST
              </p>
            </>
          )}

          {state === 'success' && (
            <>
              <div className="w-16 h-16 rounded-full bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center mx-auto mb-5 text-emerald-400">
                <CheckCircle2 className="h-8 w-8" />
              </div>
              <span className="text-[10px] font-mono tracking-[0.2em] text-accent uppercase block mb-1">
                UNSUBSCRIBED
              </span>
              <h1 className="font-display text-2xl uppercase text-foreground tracking-wide">
                SUBSCRIPTION CANCELLED
              </h1>
              <p className="mt-3 text-sm text-muted font-light max-w-sm mx-auto font-sans leading-relaxed">
                You have been unsubscribed from VIT TPO alerts. Future sends are suppressed. An email already being sent or accepted by the mail provider may still arrive.
              </p>
              <button
                type="button"
                onClick={() => navigate('/')}
                className="mt-8 btn-secondary w-full text-xs tracking-widest justify-center py-3.5"
              >
                RETURN TO HOME
              </button>
            </>
          )}

          {state === 'error' && (
            <>
              <div className="w-16 h-16 rounded-full bg-danger-500/10 border border-danger-500/30 flex items-center justify-center mx-auto mb-5 text-danger-500">
                <AlertCircle className="h-8 w-8" />
              </div>
              <span className="text-[10px] font-mono tracking-[0.2em] text-danger-500 uppercase block mb-1">
                UNSUBSCRIBE FAILED
              </span>
              <h1 className="font-display text-2xl uppercase text-foreground tracking-wide">
                LINK INVALID OR EXPIRED
              </h1>
              <p className="mt-3 text-sm text-muted font-light max-w-sm mx-auto font-sans leading-relaxed">
                {message}
              </p>
              <button
                type="button"
                onClick={() => navigate('/')}
                className="mt-8 btn-secondary w-full text-xs tracking-widest justify-center py-3.5"
              >
                RETURN TO HOME
              </button>
            </>
          )}
        </motion.div>
      </div>
    </div>
  )
}
