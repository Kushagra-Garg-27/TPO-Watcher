import { motion } from 'framer-motion'
import { AlertCircle, ArrowLeft } from 'lucide-react'

interface ErrorPageProps {
  message: string
  navigate: (path: string) => void
}

export function ErrorPage({ message, navigate }: ErrorPageProps) {
  return (
    <div className="min-h-screen bg-background text-foreground flex items-center justify-center px-4 relative overflow-hidden">
      <div className="absolute inset-0 bg-dark-grid opacity-30 pointer-events-none" />

      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4 }}
        className="max-w-md w-full card bg-surface-1 border border-line-strong p-8 sm:p-10 text-center rounded-xl shadow-[0_20px_50px_rgba(0,0,0,0.85)] relative z-10"
      >
        <div className="w-14 h-14 rounded-full bg-surface-2 border border-line flex items-center justify-center mx-auto mb-5 text-accent">
          <AlertCircle className="h-7 w-7" />
        </div>
        <span className="text-[10px] font-mono tracking-[0.2em] text-accent uppercase block mb-1">
          404 // NOT FOUND
        </span>
        <h1 className="font-display text-3xl uppercase text-foreground tracking-wide">
          ROUTE UNAVAILABLE
        </h1>
        <p className="mt-3 text-sm text-muted font-light font-sans max-w-sm mx-auto">
          {message || 'The requested resource does not exist in the TPO Watcher system.'}
        </p>
        <button
          type="button"
          onClick={() => navigate('/')}
          className="mt-8 btn-secondary w-full text-xs tracking-widest justify-center py-3.5"
        >
          <ArrowLeft className="h-4 w-4" />
          RETURN TO OVERVIEW
        </button>
      </motion.div>
    </div>
  )
}
