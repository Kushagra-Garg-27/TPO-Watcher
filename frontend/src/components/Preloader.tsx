import { useState, useEffect } from 'react'
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion'

interface PreloaderProps {
  onComplete: () => void
}

export function Preloader({ onComplete }: PreloaderProps) {
  const shouldReduceMotion = useReducedMotion()
  const [progress, setProgress] = useState(0)
  const [visible, setVisible] = useState(() => !shouldReduceMotion)

  useEffect(() => {
    if (shouldReduceMotion) {
      onComplete()
      return
    }

    const startTime = performance.now()
    const duration = 650 // snappier, premium perception

    const frame = (now: number) => {
      const elapsed = now - startTime
      const p = Math.min(Math.round((elapsed / duration) * 100), 100)
      setProgress(p)

      if (p < 100) {
        requestAnimationFrame(frame)
      } else {
        setTimeout(() => {
          setVisible(false)
          setTimeout(onComplete, 400)
        }, 150)
      }
    }

    const handle = requestAnimationFrame(frame)
    return () => cancelAnimationFrame(handle)
  }, [onComplete, shouldReduceMotion])

  if (!visible && shouldReduceMotion) return null

  return (
    <AnimatePresence>
      {visible && (
        <motion.div
          key="preloader"
          initial={{ opacity: 1 }}
          exit={{
            y: '-100%',
            transition: { duration: 0.6, ease: [0.16, 1, 0.3, 1] },
          }}
          className="fixed inset-0 z-50 flex flex-col justify-between p-6 sm:p-12 bg-background border-b border-line text-foreground"
        >
          {/* Header */}
          <div className="flex items-center justify-between text-xs tracking-[0.2em] font-mono text-muted uppercase">
            <span>TPO WATCHER // ENGINE BOOT</span>
            <span className="flex items-center gap-2">
              <span className="h-1.5 w-1.5 rounded-full bg-accent animate-pulse-beacon" />
              SYSTEM ACTIVE
            </span>
          </div>

          {/* Center Brand Statement */}
          <div className="my-auto">
            <h1 className="font-display text-5xl sm:text-7xl md:text-9xl uppercase tracking-tight text-foreground leading-[0.88]">
              PLACEMENT
              <br />
              <span className="text-muted">INTELLIGENCE</span>
            </h1>
            <p className="mt-4 text-xs sm:text-sm text-muted max-w-sm tracking-wide font-sans">
              Autonomous monitoring for VIT Pune Class of 2028.
            </p>
          </div>

          {/* Bottom Counter */}
          <div className="flex items-end justify-between border-t border-line pt-4">
            <span className="text-xs font-mono text-muted uppercase tracking-widest">
              SCHEDULED 10:00 · 17:00 · 00:00 IST
            </span>
            <span className="font-display text-4xl sm:text-6xl text-foreground tabular-nums">
              {progress.toString().padStart(2, '0')}%
            </span>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
