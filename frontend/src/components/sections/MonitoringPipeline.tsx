import { FadeUp } from '../motion/FadeUp'
import { ArrowRight, Eye, Sparkles, Filter, MailCheck } from 'lucide-react'

const PIPELINE_STEPS = [
  {
    step: '01',
    phase: 'SCAN',
    icon: <Eye className="h-5 w-5 text-accent" />,
    title: 'SCHEDULED MONITORING',
    desc: 'The engine checks the source at the configured interval using separately authorized operator credentials. Students never supply VIERP passwords.',
    meta: 'OPERATOR-CONFIGURED SOURCE CHECKS',
  },
  {
    step: '02',
    phase: 'DIFF',
    icon: <Sparkles className="h-5 w-5 text-accent" />,
    title: 'CHANGE DETECTION',
    desc: 'Observed schedules are compared with stored source records. Material changes are recorded using stable source identities.',
    meta: 'STABLE SOURCE IDS · PERSISTENT STATE',
  },
  {
    step: '03',
    phase: 'MATCH',
    icon: <Filter className="h-5 w-5 text-accent" />,
    title: 'PROGRAM FILTERING',
    desc: 'Source programmes are checked against reviewed enrollment and your opportunity preferences. A programme match alone does not establish full employer eligibility.',
    meta: 'REVIEWED COHORT AND PROGRAMME',
  },
  {
    step: '04',
    phase: 'DISPATCH',
    icon: <MailCheck className="h-5 w-5 text-accent" />,
    title: 'ACTIONABLE INBOX ALERT',
    desc: 'When recipient checks pass and sending is available, opportunity updates can be emailed to your verified destination. Timing and delivery are not guaranteed.',
    meta: 'CONSENT REQUIRED · DELIVERY CONTROLS',
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
              <span className="text-muted">STAY INFORMED.</span>
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
