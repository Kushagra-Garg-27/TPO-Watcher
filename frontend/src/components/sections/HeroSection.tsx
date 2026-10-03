import { ArrowDown, Bell, CheckCircle2, ChevronRight } from 'lucide-react'
import { LetterReveal } from '../motion/LetterReveal'
import { FadeUp } from '../motion/FadeUp'
import { LiveOpportunities } from './LiveOpportunities'

interface HeroSectionProps {
  onSubscribeClick: () => void
}

export function HeroSection({ onSubscribeClick }: HeroSectionProps) {
  return (
    <section className="relative min-h-[100dvh] flex flex-col justify-between pt-28 pb-12 sm:pb-16 overflow-hidden">
      {/* ── LAYER 1: ATMOSPHERIC BACKGROUND ─────────────────────────── */}
      <div className="absolute inset-0 bg-dark-grid opacity-35 pointer-events-none" />

      {/* Subtle radial glows */}
      <div
        className="ambient-glow -top-32 left-1/2 -translate-x-1/2 w-[700px] h-[500px]"
        style={{ background: 'radial-gradient(ellipse at center, rgba(255, 59, 29, 0.12), transparent 70%)' }}
      />
      <div
        className="ambient-glow top-1/2 -right-40 w-[500px] h-[400px]"
        style={{ background: 'radial-gradient(ellipse at center, rgba(255, 106, 61, 0.06), transparent 70%)' }}
      />


      {/* ── LAYER 2 & 3: HERO COMPOSITION ─────────────────────────── */}
      <div className="section-container relative z-10 w-full my-auto py-8">
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 lg:gap-12 items-center">
          {/* Left Column (col-span-7): Massive Typography & Thesis */}
          <div className="lg:col-span-7 flex flex-col">

            {/* Giant Anton Title */}
            <h1 className="font-display uppercase text-7xl sm:text-8xl md:text-9xl lg:text-[7.5rem] xl:text-[8.5rem] tracking-tight text-foreground leading-[0.84] select-none">
              <span className="block">
                <LetterReveal delay={0.15}>TPO</LetterReveal>
              </span>
              <span className="block text-foreground">
                <LetterReveal delay={0.25}>WATCHER</LetterReveal>
              </span>
            </h1>

            {/* Value Proposition Thesis */}
            <FadeUp delay={0.4} duration={0.65} yOffset={16}>
              <div className="mt-6 sm:mt-8 max-w-xl">
                <p className="font-display text-2xl sm:text-3xl lg:text-4xl uppercase tracking-wide text-foreground/90 leading-tight">
                  NEVER MISS THE NEXT <span className="text-accent underline decoration-accent/40 decoration-2 underline-offset-4">OPPORTUNITY.</span>
                </p>
                <p className="mt-3 text-sm sm:text-base text-muted leading-relaxed font-sans font-light">
                  Continuous automated intelligence for VIT Pune placement schedules.
                  When matching internships, PPOs, or full-time opportunities appear on VIERP, you know immediately.
                </p>
              </div>
            </FadeUp>

            {/* System Status Metrics Strip */}
            <FadeUp delay={0.5} duration={0.6} yOffset={16}>
              <div className="mt-8 pt-6 border-t border-line flex flex-wrap items-center gap-6 sm:gap-10 text-xs font-mono">
                <div>
                  <span className="text-muted block text-[11px] tracking-widest uppercase">SCAN FREQUENCY</span>
                  <span className="text-foreground font-semibold mt-0.5 block">10:00 · 17:00 · 00:00 IST</span>
                </div>
                <div className="w-px h-6 bg-line hidden sm:block" />
                <div>
                  <span className="text-muted block text-[11px] tracking-widest uppercase">STUDENT CREDENTIALS</span>
                  <span className="text-emerald-400 font-semibold mt-0.5 flex items-center gap-1">
                    <CheckCircle2 className="h-3 w-3" /> NEVER REQUESTED
                  </span>
                </div>
                <div className="w-px h-6 bg-line hidden sm:block" />
                <div>
                  <span className="text-muted block text-[11px] tracking-widest uppercase">TARGET SCOPE</span>
                  <span className="text-foreground font-semibold mt-0.5 block">12 B.TECH BRANCHES</span>
                </div>
              </div>
            </FadeUp>

            {/* Action Bar */}
            <FadeUp delay={0.6} duration={0.6} yOffset={16}>
              <div className="mt-8 flex flex-wrap items-center gap-4">
                <button
                  type="button"
                  onClick={onSubscribeClick}
                  className="btn-primary"
                >
                  <Bell className="h-4 w-4" />
                  SUBSCRIBE FOR ALERTS
                </button>
                <a
                  href="#how-it-works"
                  onClick={(e) => {
                    e.preventDefault()
                    document.querySelector('#how-it-works')?.scrollIntoView({ behavior: 'smooth' })
                  }}
                  className="btn-secondary"
                >
                  HOW THE PIPELINE WORKS
                  <ChevronRight className="h-4 w-4 text-muted" />
                </a>
              </div>
            </FadeUp>
          </div>

          {/* Right Column (col-span-5): LIVE OPPORTUNITIES STACK */}
          <div className="lg:col-span-5 relative mt-6 lg:mt-0">
            {/* Ambient beacon backdrop for card */}
            <div className="absolute -inset-1 bg-gradient-to-r from-accent/20 to-accent-2/10 rounded-2xl blur-xl opacity-60 -z-10 pointer-events-none" />

            <LiveOpportunities />
          </div>
        </div>
      </div>

      {/* ── FOOTER SCROLL EXPLORE CUE ─────────────────────────────── */}
      <div className="section-container relative z-10 w-full pt-4">
        <div className="flex items-center justify-between border-t border-line pt-4 text-xs font-mono text-muted">
          <div className="flex items-center gap-2">
            <span className="h-1.5 w-1.5 rounded-full bg-accent" />
            <span>VIERP SCHEDULE RECONCILIATION ENGINE</span>
          </div>
          <a
            href="#overview"
            onClick={(e) => {
              e.preventDefault()
              document.querySelector('#overview')?.scrollIntoView({ behavior: 'smooth' })
            }}
            className="flex items-center gap-2 hover:text-foreground transition-colors group"
          >
            <span>EXPLORE SYSTEM</span>
            <ArrowDown className="h-3.5 w-3.5 group-hover:translate-y-0.5 transition-transform" />
          </a>
        </div>
      </div>
    </section>
  )
}
