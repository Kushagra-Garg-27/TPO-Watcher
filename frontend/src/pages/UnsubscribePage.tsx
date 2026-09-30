import { useState } from 'react'
import { motion } from 'framer-motion'
import { CheckCircle2, AlertCircle, Loader2, Bell } from 'lucide-react'

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
      return { state: 'error' as UnsubscribeState, message: params.get('message') || 'Invalid or expired unsubscribe link.' }
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
        setMessage(body.detail || 'Unsubscribe failed. The link may have expired.')
      }
    } catch {
      setState('error')
      setMessage('Network error. Please try again.')
    }
  }

  return (
    <div className="min-h-screen bg-slate-50 flex items-center justify-center px-4">
      <div className="max-w-md w-full">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
          className="card p-8 text-center"
        >
          {state === 'confirming' && (
            <>
              <div className="flex h-14 w-14 items-center justify-center rounded-full bg-warning-50 border border-warning-100 mx-auto mb-4">
                <Bell className="h-6 w-6 text-warning-500" />
              </div>
              <h1 className="text-lg font-semibold text-slate-900">Unsubscribe from TPO alerts?</h1>
              <p className="mt-2 text-sm text-slate-600">
                You'll stop receiving email notifications for matching TPO opportunities.
                You can re-subscribe at any time.
              </p>
              <div className="mt-6 flex flex-col gap-2">
                <button
                  type="button"
                  onClick={handleConfirm}
                  className="btn-destructive w-full text-sm"
                >
                  Yes, unsubscribe me
                </button>
                <button
                  type="button"
                  onClick={() => navigate('/')}
                  className="btn-secondary w-full text-sm"
                >
                  No, keep my alerts
                </button>
              </div>
            </>
          )}

          {state === 'loading' && (
            <>
              <Loader2 className="h-10 w-10 text-brand-500 animate-spin mx-auto mb-4" />
              <h1 className="text-base font-medium text-slate-700">Processing...</h1>
            </>
          )}

          {state === 'success' && (
            <>
              <motion.div
                initial={{ scale: 0 }}
                animate={{ scale: 1 }}
                transition={{ type: 'spring', stiffness: 400, damping: 20 }}
                className="flex h-16 w-16 items-center justify-center rounded-full bg-slate-100 mx-auto mb-4"
              >
                <CheckCircle2 className="h-8 w-8 text-slate-500" />
              </motion.div>
              <h1 className="text-lg font-semibold text-slate-900">Unsubscribed</h1>
              <p className="mt-2 text-sm text-slate-600">
                You've been unsubscribed from TPO opportunity alerts.
                You won't receive further notifications.
              </p>
              <p className="mt-4 text-xs text-slate-400">
                Changed your mind? You can sign up again on the home page.
              </p>
              <button
                type="button"
                onClick={() => navigate('/')}
                className="mt-6 btn-secondary w-full text-sm"
              >
                Back to home
              </button>
            </>
          )}

          {state === 'error' && (
            <>
              <AlertCircle className="h-12 w-12 text-danger-400 mx-auto mb-4" />
              <h1 className="text-lg font-semibold text-slate-900">Unsubscribe failed</h1>
              <p className="mt-2 text-sm text-slate-600">{message}</p>
              <button
                type="button"
                onClick={() => navigate('/')}
                className="mt-6 btn-secondary w-full text-sm"
              >
                Back to home
              </button>
            </>
          )}
        </motion.div>
      </div>
    </div>
  )
}
