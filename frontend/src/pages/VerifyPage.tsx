import { useState } from 'react'
import { motion } from 'framer-motion'
import { CheckCircle2, AlertCircle, Loader2, ArrowRight, Mail } from 'lucide-react'
import { api } from '../lib/api'

interface VerifyPageProps {
  token: string
  navigate: (path: string) => void
}

type VerifyState = 'confirming' | 'loading' | 'success' | 'error'

export function VerifyPage({ token, navigate }: VerifyPageProps) {
  const [state, setState] = useState<VerifyState>(() => {
    if (!token) {
      // Check if URL search has legacy status
      const params = new URLSearchParams(window.location.search)
      const status = params.get('status')
      if (status === 'success') return 'success'
      return 'error'
    }
    return 'confirming'
  })

  const [message, setMessage] = useState(() => {
    if (!token) {
      const params = new URLSearchParams(window.location.search)
      const msg = params.get('message') || params.get('detail')
      return msg || 'The verification token is missing. Please use the link directly from your email.'
    }
    return ''
  })

  async function handleVerify() {
    if (!token) {
      setState('error')
      setMessage('The verification token is missing. Please use the link directly from your email.')
      return
    }

    setState('loading')
    const res = await api.verifyConfirm(token)
    if (res.ok) {
      setState('success')
      setMessage(res.data?.message || 'Your email has been verified. You will receive alerts for matching TPO opportunities.')
    } else {
      setState('error')
      setMessage(res.error || 'The link may have expired or already been used. Please sign up again.')
    }
  }

  return (
    <div className="min-h-screen bg-background text-foreground flex items-center justify-center px-4 relative overflow-hidden">
      {/* Background Grid */}
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
                <Mail className="h-8 w-8" />
              </div>
              <span className="text-[10px] font-mono tracking-[0.2em] text-accent uppercase block mb-1">
                EMAIL VERIFICATION
              </span>
              <h1 className="font-display text-2xl uppercase text-foreground tracking-wide">
                ACTIVATE TPO ALERTS
              </h1>
              <p className="mt-3 text-sm text-muted font-light max-w-sm mx-auto font-sans leading-relaxed">
                Click below to verify your email address and activate your placement and internship notifications.
              </p>
              <div className="mt-8 flex flex-col gap-3">
                <button
                  type="button"
                  onClick={handleVerify}
                  className="btn-primary w-full text-xs tracking-widest justify-center py-3.5"
                >
                  Verify my email
                  <ArrowRight className="h-3.5 w-3.5 ml-1" />
                </button>
                <button
                  type="button"
                  onClick={() => navigate('/')}
                  className="btn-secondary w-full text-xs tracking-widest justify-center py-3.5"
                >
                  CANCEL
                </button>
              </div>
            </>
          )}

          {state === 'loading' && (
            <>
              <Loader2 className="h-12 w-12 text-accent animate-spin mx-auto mb-5" />
              <h1 className="font-display text-2xl uppercase tracking-wide text-foreground">
                VERIFYING SUBSCRIPTION...
              </h1>
              <p className="mt-2 text-xs font-mono text-muted tracking-wider">
                CONFIRMING CRYPTOGRAPHIC TOKEN
              </p>
            </>
          )}

          {state === 'success' && (
            <>
              <motion.div
                initial={{ scale: 0 }}
                animate={{ scale: 1 }}
                transition={{ type: 'spring', stiffness: 400, damping: 20 }}
                className="w-16 h-16 rounded-full bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center mx-auto mb-5 text-emerald-400"
              >
                <CheckCircle2 className="h-8 w-8" />
              </motion.div>
              <span className="text-[10px] font-mono tracking-[0.2em] text-accent uppercase block mb-1">
                ACTIVATION SUCCESSFUL
              </span>
              <h1 className="font-display text-3xl uppercase text-foreground tracking-wide">
                EMAIL VERIFIED
              </h1>
              <p className="mt-3 text-sm text-muted font-light max-w-sm mx-auto font-sans leading-relaxed">
                {message || 'Your email has been verified. You will receive alerts for matching TPO opportunities.'}
              </p>
              <div className="mt-8 flex flex-col gap-3">
                <button
                  type="button"
                  onClick={() => navigate('/preferences')}
                  className="btn-primary w-full text-xs tracking-widest justify-center py-3.5"
                >
                  MANAGE OPPORTUNITY PREFERENCES
                  <ArrowRight className="h-3.5 w-3.5" />
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

          {state === 'error' && (
            <>
              <div className="w-16 h-16 rounded-full bg-danger-500/10 border border-danger-500/30 flex items-center justify-center mx-auto mb-5 text-danger-500">
                <AlertCircle className="h-8 w-8" />
              </div>
              <span className="text-[10px] font-mono tracking-[0.2em] text-danger-500 uppercase block mb-1">
                TOKEN VERIFICATION FAILED
              </span>
              <h1 className="font-display text-2xl uppercase text-foreground tracking-wide">
                VERIFICATION FAILED
              </h1>
              <p className="mt-3 text-sm text-muted font-light max-w-sm mx-auto font-sans">
                {message}
              </p>
              <button
                type="button"
                onClick={() => navigate('/')}
                className="mt-8 btn-secondary w-full text-xs tracking-widest justify-center py-3.5"
              >
                BACK TO SUBSCRIPTION
              </button>
            </>
          )}
        </motion.div>
      </div>
    </div>
  )
}
