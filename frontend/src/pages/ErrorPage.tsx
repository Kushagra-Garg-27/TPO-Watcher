import { motion } from 'framer-motion'
import { AlertCircle, ArrowLeft } from 'lucide-react'

interface ErrorPageProps {
  message: string
  navigate: (path: string) => void
}

export function ErrorPage({ message, navigate }: ErrorPageProps) {
  return (
    <div className="min-h-screen bg-slate-50 flex items-center justify-center px-4">
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4 }}
        className="max-w-sm w-full card p-8 text-center"
      >
        <AlertCircle className="h-12 w-12 text-slate-300 mx-auto mb-4" />
        <h1 className="text-lg font-semibold text-slate-900">Page not found</h1>
        <p className="mt-2 text-sm text-slate-500">{message}</p>
        <button
          type="button"
          onClick={() => navigate('/')}
          className="mt-6 btn-secondary w-full text-sm"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to home
        </button>
      </motion.div>
    </div>
  )
}
