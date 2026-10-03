import { FadeUp } from '../motion/FadeUp'
import { Terminal, Server, Shield, Layers, RefreshCw, Lock } from 'lucide-react'

const TECH_PILLARS = [
  {
    icon: Terminal,
    tag: 'SCRAPING ENGINE',
    title: 'HEADLESS PLAYWRIGHT RUNNER',
    detail:
      'Robust Chromium automation handles dynamic VIERP authentication, navigation lifecycles, and structured DOM extraction on isolated execution loops.',
  },
  {
    icon: Server,
    tag: 'BACKEND CORE',
    title: 'FASTAPI & ASYNCIO ARCHITECTURE',
    detail:
      'High-throughput asynchronous Python 3.12 backend serving same-origin APIs, rate-limited input endpoints, and lightweight health probes.',
  },
  {
    icon: Layers,
    tag: 'PERSISTENCE',
    title: 'ATOMIC SQLITE DATASTORE',
    detail:
      'Transactional integrity with explicit schema migrations, lease-based delivery queues, and SHA-256 token hashing.',
  },
  {
    icon: Shield,
    tag: 'PRODUCTION INFRA',
    title: 'CADDY & DOCKER DEPLOYMENT',
    detail:
      'Automated HTTPS certificate management via Caddy reverse proxy, fronting containerized FastAPI processes bound strictly to localhost.',
  },
  {
    icon: Lock,
    tag: 'SECURITY BOUNDARY',
    title: 'COMPLETE CREDENTIAL SEPARATION',
    detail:
      'Internal VIERP credentials remain strictly server-side. Subscribers authenticate via single-use magic links without ever storing passwords.',
  },
  {
    icon: RefreshCw,
    tag: 'RELIABILITY',
    title: 'SCHEDULE RECONCILIATION',
    detail:
      'Deterministic polling cycles at 10:00, 17:00, and 00:00 IST verify portal state with graceful handling for campus portal downtime.',
  },
]

export function TechnicalStory() {
  return (
    <section id="technology" className="py-24 sm:py-32 relative border-t border-line bg-surface-1/30">
      <div className="section-container">
        {/* Header */}
        <div className="max-w-3xl mb-16 sm:mb-20">
          <FadeUp delay={0.1}>
            <span className="text-[11px] font-mono tracking-[0.25em] text-accent uppercase block mb-3">
              04 // TECHNICAL SPECIFICATION
            </span>
            <h2 className="font-display text-4xl sm:text-6xl md:text-7xl uppercase text-foreground leading-[0.9] tracking-tight">
              ENGINEERED FOR
              <br />
              <span className="text-muted">RELIABLE INTELLIGENCE.</span>
            </h2>
            <p className="mt-5 text-sm sm:text-base text-muted font-light leading-relaxed max-w-xl font-sans">
              Built on modern infrastructure standards. Designed for fault tolerance, deterministic diff execution, and hardened identity boundaries.
            </p>
          </FadeUp>
        </div>

        {/* 2x3 Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 sm:gap-8">
          {TECH_PILLARS.map((pillar, index) => {
            const Icon = pillar.icon
            return (
              <FadeUp key={pillar.title} delay={0.1 + index * 0.08} duration={0.5}>
                <div className="border border-line bg-surface-1 p-6 sm:p-7 rounded-xl h-full flex flex-col justify-between hover:border-line-strong transition-colors">
                  <div>
                    <div className="flex items-center justify-between mb-4">
                      <span className="text-[10px] font-mono tracking-widest text-accent uppercase font-semibold">
                        {pillar.tag}
                      </span>
                      <Icon className="h-4 w-4 text-muted" />
                    </div>

                    <h3 className="font-display text-xl sm:text-2xl text-foreground uppercase tracking-wide leading-tight">
                      {pillar.title}
                    </h3>

                    <p className="mt-3 text-xs sm:text-sm text-muted font-light leading-relaxed font-sans">
                      {pillar.detail}
                    </p>
                  </div>

                  <div className="mt-6 pt-4 border-t border-line/40 flex items-center justify-between text-[10px] font-mono text-muted">
                    <span>SPECIFIED & VERIFIED</span>
                    <span className="text-emerald-400">● ACTIVE STACK</span>
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
