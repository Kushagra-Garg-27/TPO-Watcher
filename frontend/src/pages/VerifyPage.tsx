import { useState } from 'react'
import { motion } from 'framer-motion'
import { CheckCircle2, AlertCircle, Loader2, Mail } from 'lucide-react'

interface VerifyPageProps {
  token: string
  navigate: (path: string) => void
}

type VerifyState = 'verifying' | 'success' | 'error'

export function VerifyPage({ token, navigate }: VerifyPageProps) {
  const [initial] = useState(() => {
    const params = new URLSearchParams(window.location.search)
    const status = params.get('status')
    const msg = params.get('message') || params.get('detail')

    if (!token && !status) {
      return {
        state: 'error' as VerifyState,
        message: 'Invalid verification link.',
        detail: 'The token is missing. Please use the link from your email.',
      }
    }

    if (status === 'error' || window.location.pathname.includes('error')) {
      return {
        state: 'error' as VerifyState,
        message: 'Verification failed',
        detail: msg || 'The link may have expired or already been used. Please sign up again.',
      }
    }

    if (status === 'success' || window.location.pathname.includes('success')) {
      return {
        state: 'success' as VerifyState,
        message: msg || "Email verified! You'll now receive TPO opportunity alerts.",
        detail: '',
      }
    }

    return {
      state: 'success' as VerifyState,
      message: "Your email has been verified. You'll receive alerts for matching TPO opportunities.",
      detail: '',
    }
  })

  const [state] = useState<VerifyState>(initial.state)
  const [message] = useState(initial.message)
  const [detail] = useState(initial.detail)

  return (
    <div className="min-h-screen bg-slate-50 flex items-center justify-center px-4">
      <div className="max-w-md w-full">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
          className="card p-8 text-center"
        >
          {state === 'verifying' && (
            <>
              <Loader2 className="h-12 w-12 text-brand-500 animate-spin mx-auto mb-4" />
              <h1 className="text-lg font-semibold text-slate-900">Verifying your email...</h1>
              <p className="mt-2 text-sm text-slate-500">Just a moment.</p>
            </>
          )}

          {state === 'success' && (
            <>
              <motion.div
                initial={{ scale: 0 }}
                animate={{ scale: 1 }}
                transition={{ type: 'spring', stiffness: 400, damping: 20 }}
                className="flex h-16 w-16 items-center justify-center rounded-full bg-success-100 mx-auto mb-4"
              >
                <CheckCircle2 className="h-8 w-8 text-success-600" />
              </motion.div>
              <h1 className="text-lg font-semibold text-slate-900">Email verified</h1>
              <p className="mt-2 text-sm text-slate-600 max-w-sm mx-auto">{message}</p>
              <div className="mt-6 flex flex-col gap-2">
                <button
                  type="button"
                  onClick={() => navigate('/preferences')}
                  className="btn-primary w-full text-sm"
                >
                  <Mail className="h-4 w-4" />
                  Manage preferences
                </button>
                <button
                  type="button"
                  onClick={() => navigate('/')}
                  className="btn-secondary w-full text-sm"
                >
                  Back to home
                </button>
              </div>
            </>
          )}

          {state === 'error' && (
            <>
              <motion.div
                initial={{ scale: 0 }}
                animate={{ scale: 1 }}
                transition={{ type: 'spring', stiffness: 400, damping: 20 }}
                className="flex h-16 w-16 items-center justify-center rounded-full bg-danger-50 mx-auto mb-4"
              >
                <AlertCircle className="h-8 w-8 text-danger-500" />
              </motion.div>
              <h1 className="text-lg font-semibold text-slate-900">{message}</h1>
              <p className="mt-2 text-sm text-slate-600 max-w-sm mx-auto">{detail}</p>
              <button
                type="button"
                onClick={() => navigate('/')}
                className="mt-6 btn-secondary w-full text-sm"
              >
                Back to sign up
              </button>
            </>
          )}
        </motion.div>
      </div>
    </div>
  )
}
