import { FadeUp } from '../motion/FadeUp'
import { ArrowRight, Eye, Sparkles, Filter, MailCheck } from 'lucide-react'

const PIPELINE_STEPS = [
  {
    step: '01',
    phase: 'SCAN',
    icon: <Eye className="h-5 w-5 text-accent" />,
    title: 'SCHEDULED MONITORING',
    desc: 'The engine initiates autonomous browser sessions at 10:00, 17:00, and 00:00 IST, authenticating securely with server-side credentials.',
    meta: '3 DAILY SCAN WINDOWS · ASIA/KOLKATA',
  },
  {
    step: '02',
    phase: 'DIFF',
    icon: <Sparkles className="h-5 w-5 text-accent" />,
    title: 'CHANGE DETECTION',
    desc: 'Every observed company schedule is reconciled against known database state. New recruiters, registration dates, or package changes are immediately flagged.',
    meta: 'ZERO FALSE DUPLICATES · PERSISTENT STATE',
  },
  {
    step: '03',
    phase: 'MATCH',
    icon: <Filter className="h-5 w-5 text-accent" />,
    title: 'PROGRAM FILTERING',
    desc: 'The matching engine parses eligibility across all 12 canonical VIT B.Tech programs and reconciles your preference for Internships, PPOs, or Placements.',
    meta: '12 CANONICAL B.TECH DISCIPLINES',
  },
  {
    step: '04',
    phase: 'DISPATCH',
    icon: <MailCheck className="h-5 w-5 text-accent" />,
    title: 'ACTIONABLE INBOX ALERT',
    desc: 'A verified notification is dispatched to your email with recruiter name, package CTC, deadline, and eligibility criteria—giving you the earliest window to act.',
    meta: 'ZERO DELAY · DIRECT TO YOUR INBOX',
  },
]

export function MonitoringPipeline() {
  return (
    <section id="how-it-works" className="py-24 sm:py-32 relative border-t border-line overflow-hidden">
      <div className="section-container">
        {/* Section Header */}
        <div className="max-w-3xl mb-16 sm:mb-20">
          <FadeUp delay={0.1}>
            <div className="flex items-center gap-2 mb-3">
              <span className="text-[11px] font-mono tracking-[0.25em] text-accent uppercase">
                01 // THE WATCHER ENGINE
              </span>
            </div>
            <h2 className="font-display text-4xl sm:text-6xl md:text-7xl uppercase text-foreground leading-[0.9] tracking-tight">
              A SYSTEM IS WATCHING.
              <br />
              <span className="text-muted">YOU KNOW IMMEDIATELY.</span>
            </h2>
            <p className="mt-5 text-sm sm:text-base text-muted font-light leading-relaxed max-w-xl font-sans">
              Instead of manually checking VIERP dozens of times every week, TPO-Watcher continuously monitors the source, processes eligibility diffs, and alerts you before registration closes.
            </p>
          </FadeUp>
        </div>

        {/* 4-Step Editorial Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 relative">
          {PIPELINE_STEPS.map((item, index) => (
            <FadeUp key={item.step} delay={0.15 + index * 0.1} duration={0.6}>
              <div className="card bg-surface-1 border border-line p-6 sm:p-7 h-full flex flex-col justify-between hover:border-line-strong transition-all duration-300 group relative">
                {/* Header Step & Phase */}
                <div>
                  <div className="flex items-center justify-between border-b border-line pb-4 mb-6">
                    <span className="font-display text-3xl sm:text-4xl text-accent tabular-nums group-hover:scale-105 transition-transform duration-200">
                      {item.step}
                    </span>
                    <span className="px-2 py-0.5 rounded bg-surface-2 border border-line text-[10px] font-mono tracking-widest text-muted uppercase">
                      {item.phase}
                    </span>
                  </div>

                  <div className="mb-4">{item.icon}</div>

                  <h3 className="font-display text-xl sm:text-2xl text-foreground uppercase tracking-wide leading-tight">
                    {item.title}
                  </h3>

                  <p className="mt-3 text-xs sm:text-sm text-muted font-light leading-relaxed font-sans">
                    {item.desc}
                  </p>
                </div>

                {/* Footer Tag */}
                <div className="mt-8 pt-4 border-t border-line/60">
                  <span className="text-[10px] font-mono tracking-wider text-muted/80 block uppercase">
                    {item.meta}
                  </span>
                </div>
              </div>
            </FadeUp>
          ))}
        </div>

        {/* Interactive Story Callout */}
        <FadeUp delay={0.5} duration={0.6}>
          <div className="mt-12 p-6 sm:p-8 bg-surface-2 border border-line rounded-xl flex flex-col sm:flex-row items-start sm:items-center justify-between gap-6">
            <div className="flex items-center gap-4">
              <span className="w-10 h-10 rounded bg-accent/10 border border-accent/20 flex items-center justify-center text-accent text-lg">
                ✦
              </span>
              <div>
                <h4 className="font-display text-lg sm:text-xl text-foreground uppercase tracking-wide">
                  RESTRICTED ACCESS FOR CLASS OF 2028
                </h4>
                <p className="text-xs text-muted font-light mt-0.5 font-sans">
                  Tailored specifically to the 2028 graduating cohort and current VIT Pune internship/placement rules.
                </p>
              </div>
            </div>
            <a
              href="#signup"
              onClick={(e) => {
                e.preventDefault()
                document.querySelector('#signup')?.scrollIntoView({ behavior: 'smooth' })
              }}
              className="btn-secondary text-xs py-2.5 px-4 tracking-widest shrink-0"
            >
              CONFIGURE YOUR BRANCH
              <ArrowRight className="h-3.5 w-3.5" />
            </a>
          </div>
        </FadeUp>
      </div>
    </section>
  )
}
