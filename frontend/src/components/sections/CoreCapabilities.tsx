import { FadeUp } from '../motion/FadeUp'
import { ShieldCheck, Cpu, Sliders, Database, KeyRound, BellRing } from 'lucide-react'

interface Capability {
  id: string
  num: string
  icon: typeof ShieldCheck
  title: string
  summary: string
  spec: string
}

const CAPABILITIES: Capability[] = [
  {
    id: 'polling',
    num: '01',
    icon: Cpu,
    title: 'AUTONOMOUS POLLING ENGINE',
    summary:
      'Scheduled browser automation executes deterministic checks against VIERP at 10:00, 17:00, and 00:00 IST without requiring any student interaction.',
    spec: 'PLAYWRIGHT · HEADLESS · SYSTEM RUNNER',
  },
  {
    id: 'canonical',
    num: '02',
    icon: Sliders,
    title: '12 CANONICAL B.TECH PROGRAMMES',
    summary:
      'Exact catalog alignment with VIT Pune undergraduate programs—from Computer Engineering to AIDS, EnTC, Mechanical, and Civil.',
    spec: 'CANONICAL ENUM · ZERO OMISSION',
  },
  {
    id: 'privacy',
    num: '03',
    icon: ShieldCheck,
    title: 'ZERO STUDENT CREDENTIALS',
    summary:
      'Students never provide VIERP passwords or session tokens. The watcher runs its own internal authentication isolated from subscribers.',
    spec: 'AIR-GAPPED AUTH · ZERO CREDENTIAL LEAK',
  },
  {
    id: 'diff',
    num: '04',
    icon: Database,
    title: 'DIFF & DUPLICATE PREVENTION',
    summary:
      'Normalized opportunity fingerprinting prevents redundant notifications. Only new openings or genuine deadline changes trigger dispatch.',
    spec: 'SQLITE REPOSITORY · IDEMPOTENT LEASE',
  },
  {
    id: 'magic-link',
    num: '05',
    icon: KeyRound,
    title: '15-MINUTE MAGIC LINK SESSIONS',
    summary:
      'Secure passwordless preference management. Single-use 256-bit CSPRNG tokens exchange into encrypted HttpOnly session cookies.',
    spec: 'SECURE COOKIES · RATE LIMIT PROTECTED',
  },
  {
    id: 'fanout',
    num: '06',
    icon: BellRing,
    title: 'GRANULAR OPPORTUNITY FILTERING',
    summary:
      'Subscribe specifically to what matters for your career trajectory: pure internships, performance-based PPOs, full-time placements, or all three.',
    spec: 'INDEPENDENT TOGGLES · 1-CLICK UNSUBSCRIBE',
  },
]

export function CoreCapabilities() {
  return (
    <section id="capabilities" className="py-24 sm:py-32 relative border-t border-line">
      <div className="section-container">
        {/* Header */}
        <div className="max-w-3xl mb-16 sm:mb-20">
          <FadeUp delay={0.1}>
            <span className="text-[11px] font-mono tracking-[0.25em] text-accent uppercase block mb-3">
              03 // CORE CAPABILITIES
            </span>
            <h2 className="font-display text-4xl sm:text-6xl md:text-7xl uppercase text-foreground leading-[0.9] tracking-tight">
              PRECISION ARCHITECTURE.
              <br />
              <span className="text-muted">ZERO COMPROMISE.</span>
            </h2>
            <p className="mt-5 text-sm sm:text-base text-muted font-light leading-relaxed max-w-xl font-sans">
              Engineered with production-grade reliability, strict security isolation, and disciplined data pipelines.
            </p>
          </FadeUp>
        </div>

        {/* 6-Card Editorial Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {CAPABILITIES.map((cap, index) => {
            const Icon = cap.icon
            return (
              <FadeUp key={cap.id} delay={0.1 + index * 0.08} duration={0.55}>
                <div className="card bg-surface-1 border border-line p-6 sm:p-8 h-full flex flex-col justify-between hover:border-line-strong transition-all duration-300 group">
                  <div>
                    <div className="flex items-center justify-between border-b border-line pb-4 mb-6">
                      <span className="font-display text-2xl text-muted/50 tabular-nums group-hover:text-accent transition-colors">
                        {cap.num}
                      </span>
                      <Icon className="h-5 w-5 text-muted group-hover:text-accent transition-colors" />
                    </div>

                    <h3 className="font-display text-2xl text-foreground uppercase tracking-wide leading-tight">
                      {cap.title}
                    </h3>

                    <p className="mt-3 text-xs sm:text-sm text-muted font-light leading-relaxed font-sans">
                      {cap.summary}
                    </p>
                  </div>

                  <div className="mt-8 pt-4 border-t border-line/50">
                    <span className="text-[10px] font-mono tracking-widest text-muted/70 block uppercase">
                      {cap.spec}
                    </span>
                  </div>
                </div>
              </FadeUp>
            )
          })}
        </div>
      </div>
    </section>
  )
}
