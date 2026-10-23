import { useState, useEffect } from 'react'
import { motion } from 'framer-motion'
import { Building2, Calendar, Award, CheckCircle2, AlertCircle, Loader2 } from 'lucide-react'
import { FadeUp } from '../motion/FadeUp'
import { api, type Opportunity } from '../../lib/api'

// Maps backend Opportunity fields to the editorial display format.
// Never hardcodes company names, IDs, or package values.
function formatPackageDisplay(maxPkg: string | null): string | null {
  if (!maxPkg) return null
  const trimmed = maxPkg.trim()
  if (!trimmed || trimmed === '0' || trimmed === '0.0' || trimmed.toLowerCase() === 'null') {
    return null
  }
  // F03: Do not assert LPA — source unit is unverified. Show value verbatim.
  return `${trimmed} (source value)`
}

function parseEligibleStreams(programsStr: string | null | undefined): string[] {
  if (!programsStr) return []
  const unique = new Set<string>()
  programsStr
    .split(',')
    .map((p) => p.trim())
    .filter((p) => p && !p.toLowerCase().includes('null'))
    .forEach((p) => {
      const clean = p.replace(/\s+/g, ' ').trim()
      if (clean && clean.toLowerCase() !== 'null' && !unique.has(clean)) {
        unique.add(clean)
      }
    })
  return Array.from(unique)
}

function formatFirstSeen(firstSeenAt: string | null): string {
  if (!firstSeenAt) return 'Unknown'
  try {
    const d = new Date(firstSeenAt)
    return d.toLocaleDateString('en-GB', { month: 'short', year: 'numeric' }).toUpperCase()
  } catch {
    return firstSeenAt.slice(0, 7).toUpperCase()
  }
}

export function RecentDetections() {
  const [opportunities, setOpportunities] = useState<Opportunity[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let isMounted = true

    async function fetchOpportunities() {
      setLoading(true)
      setError(null)
      const res = await api.getOpportunities()
      if (!isMounted) return

      if (res.ok && res.data) {
        setOpportunities(res.data)
      } else {
        setError(res.error || 'Failed to load detections')
      }
      setLoading(false)
    }

    fetchOpportunities()
    return () => { isMounted = false }
  }, [])

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

          {!loading && !error && opportunities.length > 0 && (
            <FadeUp delay={0.2}>
              <span className="text-xs font-mono text-muted tracking-wider uppercase">
                {opportunities.length} ACTIVE RECORD{opportunities.length !== 1 ? 'S' : ''} FROM PORTAL
              </span>
            </FadeUp>
          )}
        </div>

        {/* Loading State */}
        {loading && (
          <div className="border-t border-line divide-y divide-line">
            {[1, 2, 3].map((idx) => (
              <div key={idx} className="py-8 sm:py-10 px-3 sm:px-6 animate-pulse">
                <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-center">
                  <div className="lg:col-span-5 flex items-start gap-5">
                    <div className="h-8 w-8 bg-surface-2 rounded" />
                    <div className="flex-1 space-y-2">
                      <div className="h-3 w-24 bg-surface-2 rounded" />
                      <div className="h-8 w-48 bg-surface-2 rounded" />
                    </div>
                  </div>
                  <div className="lg:col-span-4 space-y-2">
                    <div className="h-6 w-32 bg-surface-2 rounded" />
                    <div className="flex gap-1">
                      <div className="h-4 w-16 bg-surface-2 rounded" />
                      <div className="h-4 w-14 bg-surface-2 rounded" />
                    </div>
                  </div>
                </div>
              </div>
            ))}
            <div className="py-8 text-center flex items-center justify-center gap-2 text-muted text-sm font-mono">
              <Loader2 className="h-4 w-4 animate-spin" />
              <span>LOADING DETECTIONS FROM PORTAL DATABASE...</span>
            </div>
          </div>
        )}

        {/* Error State */}
        {!loading && error && (
          <div className="border-t border-line py-16 text-center flex flex-col items-center gap-3">
            <AlertCircle className="h-8 w-8 text-accent/60" />
            <p className="font-display text-xl uppercase text-foreground/80">PORTAL DATA UNAVAILABLE</p>
            <p className="text-xs font-mono text-muted uppercase tracking-wider">DATABASE QUERY FAILED — RETRYING ON NEXT LOAD</p>
          </div>
        )}

        {/* Empty State */}
        {!loading && !error && opportunities.length === 0 && (
          <div className="border-t border-line py-16 text-center flex flex-col items-center gap-3">
            <Building2 className="h-8 w-8 text-muted/40" />
            <p className="font-display text-xl uppercase text-foreground/80">NO ACTIVE RECORDS</p>
            <p className="text-xs font-mono text-muted uppercase tracking-wider">PORTAL IS BEING MONITORED — NEW DETECTIONS WILL APPEAR HERE</p>
          </div>
        )}

        {/* Editorial List View */}
        {!loading && !error && opportunities.length > 0 && (
          <div className="border-t border-line divide-y divide-line">
            {opportunities.map((opp, index) => {
              const pkg = formatPackageDisplay(opp.max_package)
              const streams = parseEligibleStreams(opp.eligible_programs)
              const firstSeen = formatFirstSeen(opp.first_seen_at)
              const displayNum = String(index + 1).padStart(2, '0')

              return (
                <FadeUp key={opp.id} delay={0.1 + index * 0.08} duration={0.5}>
                  <div className="group py-8 sm:py-10 transition-colors duration-200 hover:bg-surface-1/40 px-3 sm:px-6 rounded-lg -mx-3 sm:-mx-6">
                    <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-center">
                      {/* Left: Number & Company Name */}
                      <div className="lg:col-span-5 flex items-start gap-5">
                        <span className="font-display text-2xl sm:text-3xl text-muted/40 tabular-nums group-hover:text-accent transition-colors pt-1">
                          {displayNum}
                        </span>
                        <div>
                          <div className="flex items-center gap-2 mb-1">
                            {opp.placement_type && (
                              <span className="px-2 py-0.5 rounded bg-surface-2 border border-line text-[10px] font-mono tracking-widest text-muted uppercase">
                                {opp.placement_type}
                              </span>
                            )}
                          </div>
                          <h3 className="font-display text-3xl sm:text-4xl text-foreground uppercase tracking-wide group-hover:translate-x-1 transition-transform duration-200">
                            {opp.company}
                          </h3>
                        </div>
                      </div>

                      {/* Center: Package & Eligibility */}
                      <div className="lg:col-span-4 flex flex-col gap-2">
                        {pkg && (
                          <div className="flex items-center gap-2">
                            <Award className="h-4 w-4 text-accent shrink-0" />
                            <span className="font-display text-xl sm:text-2xl text-foreground tabular-nums">
                              {pkg}
                            </span>
                          </div>
                        )}
                        {streams.length > 0 && (
                          <div className="flex flex-wrap gap-1">
                            {streams.slice(0, 4).map((stream) => (
                              <span
                                key={stream}
                                className="px-1.5 py-0.5 bg-surface-2 text-[10px] font-mono text-muted rounded border border-line/60"
                              >
                                {stream}
                              </span>
                            ))}
                            {streams.length > 4 && (
                              <span className="px-1.5 py-0.5 bg-surface-2 text-[10px] font-mono text-muted rounded border border-line/60">
                                +{streams.length - 4}
                              </span>
                            )}
                          </div>
                        )}
                      </div>

                      {/* Right: Deadline & Dispatch Status */}
                      <div className="lg:col-span-3 flex lg:flex-col items-center lg:items-end justify-between gap-2 border-t lg:border-t-0 border-line/40 pt-4 lg:pt-0">
                        {opp.registration_end && (
                          <div className="flex items-center gap-1.5 text-xs font-mono text-muted">
                            <Calendar className="h-3.5 w-3.5 text-muted" />
                            <span>DEADLINE: {opp.registration_end}</span>
                          </div>
                        )}
                        <div className="text-xs font-mono text-muted">
                          FIRST SEEN: {firstSeen}
                        </div>
                        <div className="flex items-center gap-1.5 text-xs font-mono text-emerald-400">
                          <CheckCircle2 className="h-3.5 w-3.5" />
                          <span className="tracking-wider uppercase">NOTIFIED</span>
                        </div>
                      </div>
                    </div>
                  </div>
                </FadeUp>
              )
            })}
          </div>
        )}

        {/* Footnote */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.5 }}
          className="mt-12 text-xs font-mono text-muted flex items-center gap-3"
        >
          <Building2 className="h-4 w-4 text-accent shrink-0" />
          <span>
            Records are sourced live from the VIT TPO portal database. Compensation values are shown verbatim from source — unit (LPA / stipend) must be verified on the official portal.
          </span>
        </motion.div>
      </div>
    </section>
  )
}
