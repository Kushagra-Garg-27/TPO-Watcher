import { useReducedMotion } from 'framer-motion'
import type { ReactNode } from 'react'

interface MarqueeProps {
  items?: string[]
  children?: ReactNode
  speedSeconds?: number
  gapRem?: number
  direction?: 'left' | 'right'
  pauseOnHover?: boolean
  className?: string
}

export function Marquee({
  items,
  children,
  speedSeconds = 24,
  gapRem = 3,
  direction = 'left',
  pauseOnHover = true,
  className = '',
}: MarqueeProps) {
  const shouldReduceMotion = useReducedMotion()

  const defaultContent = (
    <div
      className="inline-flex items-center shrink-0"
      style={{ columnGap: `${gapRem}rem` }}
    >
      {(items || ['MONITOR', 'DETECT', 'MATCH', 'FILTER', 'NOTIFY', 'ACT']).map(
        (word, i) => (
          <span
            key={i}
            className="inline-flex items-center gap-6 font-display text-4xl sm:text-6xl md:text-7xl lg:text-8xl tracking-wider text-[rgba(243,241,234,0.18)] hover:text-foreground transition-colors duration-300 select-none uppercase"
          >
            <span>{word}</span>
            <span className="text-accent text-2xl sm:text-4xl opacity-70">✦</span>
          </span>
        )
      )}
    </div>
  )

  if (shouldReduceMotion) {
    return (
      <div className={`overflow-x-auto py-6 border-y border-line ${className}`}>
        <div className="flex gap-12 px-6">
          {children || defaultContent}
        </div>
      </div>
    )
  }

  return (
    <div
      className={`relative w-full overflow-hidden border-y border-line py-6 sm:py-8 bg-surface-1 ${className}`}
      aria-hidden="true"
    >
      {/* Edge gradient fade masks */}
      <div className="pointer-events-none absolute inset-y-0 left-0 w-24 bg-gradient-to-r from-background to-transparent z-10" />
      <div className="pointer-events-none absolute inset-y-0 right-0 w-24 bg-gradient-to-l from-background to-transparent z-10" />

      <div
        className={`flex w-fit ${pauseOnHover ? 'hover:[animation-play-state:paused]' : ''}`}
        style={{
          animation: `marquee ${speedSeconds}s linear infinite ${direction === 'right' ? 'reverse' : 'normal'}`,
        }}
      >
        <div className="inline-flex shrink-0 pr-12">
          {children || defaultContent}
        </div>
        <div className="inline-flex shrink-0 pr-12">
          {children || defaultContent}
        </div>
      </div>
    </div>
  )
}
