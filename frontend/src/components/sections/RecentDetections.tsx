import { useState } from 'react'
import { FadeUp } from '../motion/FadeUp'
import { Building2, Calendar, Award, CheckCircle2 } from 'lucide-react'

interface DetectionItem {
  number: string
  company: string
  type: string
  packageLPA: string | null
  deadline: string
  eligibleStreams: string[]
  firstSeen: string
  status: 'DISPATCHED' | 'ARCHIVED'
}

const VERIFIED_DETECTIONS: DetectionItem[] = [
  {
    number: '01',
    company: 'BMC SOFTWARE',
    type: 'Internship + Performance based PPO',
    packageLPA: '16.0 LPA',
    deadline: '17-AUG-2026',
    eligibleStreams: ['VIT_CE', 'VIT_IT', 'VIT_CSE_DS', 'VIT_AIDS', 'VIT_CSE_AIML'],
    firstSeen: 'AUG 2026',
    status: 'DISPATCHED',
  },
  {
    number: '02',
    company: 'SIEMENS',
    type: 'Internship + Performance based PPO',
    packageLPA: 'Competitive Stipend + PPO',
    deadline: '08-SEP-2026',
    eligibleStreams: ['VIT_CE', 'VIT_ENTC', 'VIT_ICE', 'VIT_MECH'],
    firstSeen: 'SEP 2026',
    status: 'DISPATCHED',
  },
  {
    number: '03',
    company: 'VISTEON',
    type: 'Internship + Performance based PPO',
    packageLPA: '7.0 LPA',
    deadline: '31-AUG-2026',
    eligibleStreams: ['VIT_ENTC', 'VIT_CE', 'VIT_IT', 'VIT_ICE'],
    firstSeen: 'AUG 2026',
    status: 'DISPATCHED',
  },
  {
    number: '04',
    company: 'MASTERCARD',
    type: 'Internship + Performance based PPO',
    packageLPA: 'Standard Tier 1 Package',
    deadline: '24-AUG-2026',
    eligibleStreams: ['VIT_CE', 'VIT_CSE_DS', 'VIT_IT', 'VIT_CSE_AI'],
    firstSeen: 'AUG 2026',
    status: 'DISPATCHED',
  },
]

export function RecentDetections() {
  const [activeTab, setActiveTab] = useState<'all' | 'high-package'>('all')

  const displayed =
    activeTab === 'high-package'
      ? VERIFIED_DETECTIONS.filter((d) => d.packageLPA && d.packageLPA.includes('LPA'))
      : VERIFIED_DETECTIONS

  return (
    <section id="detections" className="py-24 sm:py-32 relative border-t border-line">
      <div className="section-container">
        {/* Header */}
        <div className="flex flex-col md:flex-row md:items-end justify-between mb-16 gap-6">
          <FadeUp delay={0.1}>
            <span className="text-[11px] font-mono tracking-[0.25em] text-accent uppercase block mb-3">
              02 // INTELLIGENCE ARCHIVE
            </span>
            <h2 className="font-display text-4xl sm:text-6xl md:text-7xl uppercase text-foreground leading-[0.9] tracking-tight">
              VERIFIED OPPORTUNITY
              <br />
              <span className="text-muted">DETECTIONS</span>
            </h2>
          </FadeUp>

          <FadeUp delay={0.2}>
            <div className="flex items-center gap-2 p-1 bg-surface-1 border border-line rounded-lg w-fit">
              <button
                type="button"
                onClick={() => setActiveTab('all')}
                className={`px-3 py-1.5 text-xs font-mono tracking-wider uppercase rounded transition-colors ${
                  activeTab === 'all'
                    ? 'bg-surface-3 text-foreground font-semibold'
                    : 'text-muted hover:text-foreground'
                }`}
              >
                ALL RECENT ({VERIFIED_DETECTIONS.length})
              </button>
              <button
                type="button"
                onClick={() => setActiveTab('high-package')}
                className={`px-3 py-1.5 text-xs font-mono tracking-wider uppercase rounded transition-colors ${
                  activeTab === 'high-package'
                    ? 'bg-surface-3 text-foreground font-semibold'
                    : 'text-muted hover:text-foreground'
                }`}
              >
                CTC DISCLOSED
              </button>
            </div>
          </FadeUp>
        </div>

        {/* Editorial List View with Separators */}
        <div className="border-t border-line divide-y divide-line">
          {displayed.map((item, index) => (
            <FadeUp key={item.number} delay={0.1 + index * 0.08} duration={0.5}>
              <div className="group py-8 sm:py-10 transition-colors duration-200 hover:bg-surface-1/40 px-3 sm:px-6 rounded-lg -mx-3 sm:-mx-6">
                <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-center">
                  {/* Left: Number & Recruiter Name */}
                  <div className="lg:col-span-5 flex items-start gap-5">
                    <span className="font-display text-2xl sm:text-3xl text-muted/40 tabular-nums group-hover:text-accent transition-colors pt-1">
                      {item.number}
                    </span>
                    <div>
                      <div className="flex items-center gap-2 mb-1">
                        <span className="px-2 py-0.5 rounded bg-surface-2 border border-line text-[10px] font-mono tracking-widest text-muted uppercase">
                          {item.type}
                        </span>
                      </div>
                      <h3 className="font-display text-3xl sm:text-4xl text-foreground uppercase tracking-wide group-hover:translate-x-1 transition-transform duration-200">
                        {item.company}
                      </h3>
                    </div>
                  </div>

                  {/* Center: Package & Eligibility */}
                  <div className="lg:col-span-4 flex flex-col gap-2">
                    <div className="flex items-center gap-2">
                      <Award className="h-4 w-4 text-accent shrink-0" />
                      <span className="font-display text-xl sm:text-2xl text-foreground tabular-nums">
                        {item.packageLPA || 'STIPEND BASIS'}
                      </span>
                    </div>
                    <div className="flex flex-wrap gap-1">
                      {item.eligibleStreams.map((stream) => (
                        <span
                          key={stream}
                          className="px-1.5 py-0.5 bg-surface-2 text-[10px] font-mono text-muted rounded border border-line/60"
                        >
                          {stream}
                        </span>
                      ))}
                    </div>
                  </div>

                  {/* Right: Deadline & Dispatch Status */}
                  <div className="lg:col-span-3 flex lg:flex-col items-center lg:items-end justify-between gap-2 border-t lg:border-t-0 border-line/40 pt-4 lg:pt-0">
                    <div className="flex items-center gap-1.5 text-xs font-mono text-muted">
                      <Calendar className="h-3.5 w-3.5 text-muted" />
                      <span>DEADLINE: {item.deadline}</span>
                    </div>

                    <div className="flex items-center gap-1.5 text-xs font-mono text-emerald-400">
                      <CheckCircle2 className="h-3.5 w-3.5" />
                      <span className="tracking-wider uppercase">NOTIFIED</span>
                    </div>
                  </div>
                </div>
              </div>
            </FadeUp>
          ))}
        </div>

        {/* Footnote */}
        <div className="mt-12 text-xs font-mono text-muted flex items-center gap-3">
          <Building2 className="h-4 w-4 text-accent shrink-0" />
          <span>
            Records represent historical opportunities observed from the VIT TPO schedule repository. Values are verified and never simulated.
          </span>
        </div>
      </div>
    </section>
  )
}
