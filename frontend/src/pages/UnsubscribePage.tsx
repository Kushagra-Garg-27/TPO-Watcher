import { useState } from 'react'
import { motion } from 'framer-motion'
import { CheckCircle2, AlertCircle, Loader2, BellOff, ArrowRight } from 'lucide-react'

interface UnsubscribePageProps {
  token: string
  navigate: (path: string) => void
}

type UnsubscribeState = 'confirming' | 'loading' | 'success' | 'error'

export function UnsubscribePage({ token, navigate }: UnsubscribePageProps) {
  const [initial] = useState(() => {
    const isDirectApi = window.location.pathname.startsWith('/api/v1/unsubscribe')
    const params = new URLSearchParams(window.location.search)
    if (params.get('status') === 'error') {
      return {
        state: 'error' as UnsubscribeState,
        message: params.get('message') || 'Invalid or expired unsubscribe link.',
      }
    }
    if (isDirectApi || params.get('status') === 'success') {
      return { state: 'success' as UnsubscribeState, message: '' }
    }
    return { state: 'confirming' as UnsubscribeState, message: '' }
  })

  const [state, setState] = useState<UnsubscribeState>(initial.state)
  const [message, setMessage] = useState(initial.message)

  async function handleConfirm() {
    if (!token) {
      setState('error')
      setMessage('Invalid unsubscribe link. Token is missing.')
      return
    }

    setState('loading')

    try {
      const res = await fetch(`/api/v1/unsubscribe?token=${encodeURIComponent(token)}`, {
        method: 'GET',
      })

      if (res.ok) {
        setState('success')
      } else {
        const body = await res.json().catch(() => ({}))
        setState('error')
        setMessage(body.detail || 'Unsubscribe request failed. The token may have expired.')
      }
    } catch {
      setState('error')
      setMessage('Network error. Please check your connection and try again.')
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
                  YES, UNSUBSCRIBE ME
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
              <Loader2 className="h-10 w-10 text-accent animate-spin mx-auto mb-4" />
              <h2 className="font-display text-xl uppercase tracking-wide text-foreground">
                PROCESSING REQUEST...
              </h2>
            </>
          )}

          {state === 'success' && (
            <>
              <motion.div
                initial={{ scale: 0 }}
                animate={{ scale: 1 }}
                transition={{ type: 'spring', stiffness: 400, damping: 20 }}
                className="w-16 h-16 rounded-full bg-surface-2 border border-line flex items-center justify-center mx-auto mb-5 text-muted"
              >
                <CheckCircle2 className="h-8 w-8" />
              </motion.div>
              <span className="text-[10px] font-mono tracking-[0.2em] text-muted uppercase block mb-1">
                SUBSCRIPTION CANCELLED
              </span>
              <h1 className="font-display text-3xl uppercase text-foreground tracking-wide">
                UNSUBSCRIBED
              </h1>
              <p className="mt-3 text-sm text-muted font-light max-w-sm mx-auto font-sans leading-relaxed">
                Your email has been removed from the notification delivery pipeline. Any pending alerts have been revoked.
              </p>
              <button
                type="button"
                onClick={() => navigate('/')}
                className="mt-8 btn-secondary w-full text-xs tracking-widest justify-center py-3.5"
              >
                RETURN TO HOME
                <ArrowRight className="h-3.5 w-3.5" />
              </button>
            </>
          )}

          {state === 'error' && (
            <>
              <div className="w-16 h-16 rounded-full bg-danger-500/10 border border-danger-500/30 flex items-center justify-center mx-auto mb-5 text-danger-500">
                <AlertCircle className="h-8 w-8" />
              </div>
              <h1 className="font-display text-2xl uppercase text-foreground tracking-wide">
                UNSUBSCRIBE FAILED
              </h1>
              <p className="mt-3 text-sm text-muted font-light max-w-sm mx-auto font-sans">
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
