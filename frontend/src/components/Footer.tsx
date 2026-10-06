import { ArrowUpRight, ShieldCheck } from 'lucide-react'

interface FooterProps {
  navigate: (path: string) => void
}

export function Footer({ navigate }: FooterProps) {
  return (
    <footer className="border-t border-line bg-surface-1 pt-16 pb-12 text-foreground">
      <div className="section-container">
        {/* Top: Massive Signature & Mission */}
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 pb-12 border-b border-line">
          <div className="lg:col-span-6">
            <h2 className="font-display text-5xl sm:text-6xl md:text-7xl uppercase text-foreground leading-[0.88] tracking-tight">
              TPO WATCHER
            </h2>
            <p className="mt-3 text-sm text-muted font-light max-w-md font-sans">
              Autonomous placement intelligence and verified notification delivery for the VIT Pune Class of 2028.
            </p>
          </div>

          <div className="lg:col-span-6 flex flex-col sm:flex-row justify-start lg:justify-end gap-10 sm:gap-16 text-xs font-mono">
            <div>
              <span className="text-[10px] tracking-[0.2em] text-accent uppercase block mb-3 font-semibold">
                SYSTEM SECTIONS
              </span>
              <ul className="space-y-2 text-muted">
                <li>
                  <a href="#overview" className="hover:text-foreground transition-colors flex items-center gap-1">
                    OVERVIEW <ArrowUpRight className="h-3 w-3" />
                  </a>
                </li>
                <li>
                  <a href="#signup" className="hover:text-foreground transition-colors flex items-center gap-1">
                    SUBSCRIBE <ArrowUpRight className="h-3 w-3" />
                  </a>
                </li>
                <li>
                  <a href="#how-it-works" className="hover:text-foreground transition-colors flex items-center gap-1">
                    PIPELINE <ArrowUpRight className="h-3 w-3" />
                  </a>
                </li>
                <li>
                  <a href="#detections" className="hover:text-foreground transition-colors flex items-center gap-1">
                    ARCHIVE <ArrowUpRight className="h-3 w-3" />
                  </a>
                </li>
              </ul>
            </div>

            <div>
              <span className="text-[10px] tracking-[0.2em] text-accent uppercase block mb-3 font-semibold">
                SECURITY & PRIVACY
              </span>
              <ul className="space-y-2 text-muted">
                <li className="flex items-center gap-1 text-emerald-400">
                  <ShieldCheck className="h-3.5 w-3.5" />
                  NO VIERP PASSWORDS
                </li>
                <li>100% AIR-GAPPED AUTH</li>
                <li>256-BIT TOKEN ENTROPY</li>
                <li>
                  <button
                    type="button"
                    onClick={() => navigate('/preferences')}
                    className="hover:text-foreground transition-colors underline underline-offset-2"
                  >
                    MANAGE PREFERENCES →
                  </button>
                </li>
              </ul>
            </div>
          </div>
        </div>

        {/* Bottom Bar: Copyright & Compliance */}
        <div className="pt-8 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 text-[11px] font-mono text-muted">
          <div className="flex items-center gap-2">
            <span className="h-1.5 w-1.5 rounded-full bg-accent animate-pulse-beacon" />
            <span>VIT PUNE · B.TECH 2028 SPECIFICATION · PRODUCTION DEPLOYMENT</span>
          </div>

          <div className="flex items-center gap-4">
            <span>HTTPONLY SECURE SESSION</span>
            <span>·</span>
            <span>ZERO EXTERNAL TRACKING</span>
          </div>
        </div>
      </div>
    </footer>
  )
}
