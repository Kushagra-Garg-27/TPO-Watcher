import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Calendar, Award, Sparkles, AlertCircle, Building2, Clock, History } from 'lucide-react'
import { api, type VerifiedOpportunity, type LegacyOpportunity } from '../../lib/api'
import { parseDeadline } from '../../lib/deadline'


function formatPackage(pkg: string | null | undefined, unit: string | null): string | null {
  if (!pkg) return null
  const trimmed = pkg.trim()
  if (!trimmed || trimmed === '0' || trimmed === '0.0' || trimmed.toLowerCase() === 'null') {
    return null
  }
  return unit ? `${trimmed} (${unit})` : `${trimmed} (unit unverified)`
}

function parsePrograms(programsStr: string | null | undefined): string[] {
  if (!programsStr) return []
  const unique = new Set<string>()
  programsStr
    .split(',')
    .map((p) => p.trim())
    .filter((p) => p && !p.toLowerCase().includes('null'))
    .forEach((p) => {
      const clean = p
        .replace(/\s+/g, ' ')
        .trim()
      if (clean && clean.toLowerCase() !== 'null' && !unique.has(clean)) {
        unique.add(clean)
      }
    })
  return Array.from(unique)
}

export function LiveOpportunities() {
  const [verifiedOpportunities, setVerifiedOpportunities] = useState<VerifiedOpportunity[]>([])
  const [historicalOpportunities, setHistoricalOpportunities] = useState<LegacyOpportunity[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeId, setActiveId] = useState<string | null>(null)
  const [totalVerified, setTotalVerified] = useState(0)
  const [sourceStatus, setSourceStatus] = useState<string>('UNVERIFIED')
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    let isMounted = true

    async function fetchOpportunities() {
      const [listingRes, legacyRes] = await Promise.all([
        api.getOpportunityListing(),
        api.getOpportunities(),
      ])
      if (!isMounted) return

      if (listingRes.ok && listingRes.data) {
        setVerifiedOpportunities(listingRes.data.items.slice(0, 5))
        setTotalVerified(listingRes.data.total)
        setSourceStatus(listingRes.data.status)
      } else {
        setVerifiedOpportunities([])
        setTotalVerified(0)
        setSourceStatus('UNVERIFIED')
      }

      if (legacyRes.ok && legacyRes.data) {
        setHistoricalOpportunities(legacyRes.data.slice(0, 5))
      } else {
        setHistoricalOpportunities([])
      }

      // Case D: Error when neither call succeeds
      if (!listingRes.ok && !legacyRes.ok) {
        setError(listingRes.error || legacyRes.error || 'Failed to load opportunities')
      } else {
        setError(null)
      }

      setLoading(false)
    }

    fetchOpportunities()
    const refresh = window.setInterval(fetchOpportunities, 30000)
    const tick = window.setInterval(() => setNow(Date.now()), 1000)

    return () => {
      isMounted = false
      window.clearInterval(refresh)
      window.clearInterval(tick)
    }
  }, [])

  // Case A: Verified current listings (status VERIFIED and valid time window)
  const currentVerified = verifiedOpportunities.filter(
    (opp) => sourceStatus === 'VERIFIED' && opp.observed_ms <= now && opp.valid_until_ms > now
  )

  const isVerifiedCurrent = currentVerified.length > 0
  const isHistoricalFallback = !isVerifiedCurrent && historicalOpportunities.length > 0
  const isEmpty = !loading && !error && !isVerifiedCurrent && !isHistoricalFallback
  const isApiError = !loading && error && !isVerifiedCurrent && !isHistoricalFallback

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.94, y: 30 }}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      transition={{ duration: 0.8, delay: 0.35, ease: [0.16, 1, 0.3, 1] }}
      className="card bg-surface-1 border border-line-strong p-4 sm:p-5 relative overflow-hidden shadow-[0_20px_50px_rgba(0,0,0,0.9)]"
    >
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line pb-3.5 mb-3.5">
        <div className="flex items-center gap-2">
          {isVerifiedCurrent ? (
            <span className="h-2 w-2 rounded-full bg-accent animate-pulse-beacon" />
          ) : isHistoricalFallback ? (
            <span className="h-2 w-2 rounded-full bg-amber-500/90" />
          ) : (
            <span className="h-2 w-2 rounded-full bg-line-strong" />
          )}
          <span className="text-[11px] font-mono tracking-widest uppercase font-semibold text-accent">
            {isHistoricalFallback ? 'RECENTLY DETECTED OPPORTUNITIES' : 'TPO OPPORTUNITIES'}
          </span>
        </div>

        {!loading && !error && (
          <span className="text-[11px] font-mono text-muted tracking-wider">
            {isVerifiedCurrent ? (
              `${currentVerified.length} SHOWN / ${totalVerified} AT LAST CHECK`
            ) : isHistoricalFallback ? (
              `${historicalOpportunities.length} RECORDED DETECTION${historicalOpportunities.length !== 1 ? 'S' : ''}`
            ) : null}
          </span>
        )}
      </div>

      {/* Case B Context Banner: Explicit Historical Fallback Notification */}
      {isHistoricalFallback && !loading && (
        <div className="mb-3.5 px-3 py-2 rounded-md bg-surface-2/70 border border-line/60 text-xs font-mono text-muted flex flex-col sm:flex-row sm:items-center justify-between gap-1.5">
          <div className="flex items-center gap-1.5 text-foreground/80">
            <Clock className="h-3.5 w-3.5 text-amber-500/80 shrink-0" />
            <span className="text-[11px] leading-tight">
              Previously detected by TPO-WATCHER — current availability not verified
            </span>
          </div>
          <span className="text-[10px] tracking-wider uppercase text-muted/70 shrink-0">
            DATABASE RECORDS
          </span>
        </div>
      )}

      {/* Loading Skeleton */}
      {loading && (
        <div className="space-y-2 py-1">
          {[1, 2, 3, 4, 5].map((idx) => (
            <div
              key={idx}
              className="h-12 rounded-lg bg-surface-2/40 border border-line/60 animate-pulse flex items-center justify-between px-3.5"
            >
              <div className="h-3 w-28 bg-surface-3/70 rounded" />
              <div className="h-3 w-14 bg-surface-3/70 rounded" />
            </div>
          ))}
        </div>
      )}

      {/* Case D: API Unavailable Error State */}
      {isApiError && (
        <div className="py-10 px-4 text-center flex flex-col items-center justify-center">
          <AlertCircle className="h-7 w-7 text-accent/80 mb-2.5" />
          <p className="font-display text-lg uppercase text-foreground/90 tracking-wide">
            LIVE DATA UNAVAILABLE
          </p>
          <p className="text-xs font-mono text-muted mt-1 uppercase tracking-wider">
            UNABLE TO REACH TPO SERVICE — RETRYING AUTOMATICALLY
          </p>
        </div>
      )}

      {/* Case C: Genuine Empty State */}
      {isEmpty && (
        <div className="py-10 px-4 text-center flex flex-col items-center justify-center">
          <Building2 className="h-7 w-7 text-muted/60 mb-2.5" />
          <p className="font-display text-lg uppercase text-foreground/90 tracking-wide">
            NO RECORDED OPPORTUNITIES
          </p>
          <p className="text-xs font-mono text-muted mt-1 uppercase tracking-wider">
            LATEST PORTAL CHECK HAS NO MATCHING ACTIVE LISTINGS OR HISTORICAL DETECTIONS
          </p>
        </div>
      )}

      {/* Case A: Verified Current Listings Stack */}
      {isVerifiedCurrent && !loading && (
        <div className="space-y-2" onMouseLeave={() => setActiveId(null)}>
          {currentVerified.map((opp) => {
            const isExpanded = activeId === opp.id
            const isReceded = activeId !== null && !isExpanded
            const pkg = formatPackage(opp.max_package, opp.compensation_unit)
            const programs = parsePrograms(opp.eligible_programs)
            const hasStart = Boolean(opp.registration_start || opp.registration_start_time || opp.registration_start_alternate)
            const hasEnd = Boolean(opp.registration_end || opp.registration_end_time || opp.registration_end_alternate)

            return (
              <div
                key={opp.id}
                role="button"
                tabIndex={0}
                aria-label={`${opp.company}, verified listing ID ${opp.id}`}
                aria-expanded={isExpanded}
                onMouseEnter={() => setActiveId(opp.id)}
                onClick={() => setActiveId(isExpanded ? null : opp.id)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault()
                    setActiveId(isExpanded ? null : opp.id)
                  }
                }}
                className={`relative rounded-lg border transition-all duration-300 ease-out cursor-pointer select-none ${
                  isExpanded
                    ? 'bg-surface-2 border-accent/40 shadow-[0_8px_24px_rgba(255,59,29,0.12)] scale-[1.01] z-10 p-3.5'
                    : isReceded
                    ? 'bg-surface-1/40 border-line/30 opacity-45 scale-[0.99] py-2 px-3'
                    : 'bg-surface-2/60 border-line hover:border-line-strong hover:bg-surface-2 py-2.5 px-3'
                }`}
              >
                {isExpanded && (
                  <div className="absolute -inset-px bg-gradient-to-r from-accent/15 via-accent/5 to-transparent rounded-lg pointer-events-none -z-10" />
                )}

                <div className="flex flex-col items-start gap-2 sm:flex-row sm:justify-between sm:gap-3">
                  <div className="w-full min-w-0 sm:w-auto sm:flex-1">
                    <h4
                      className={`font-display uppercase tracking-wide transition-all duration-200 ${
                        isExpanded ? 'break-words' : 'truncate'
                      } ${
                        isExpanded
                          ? 'text-2xl text-foreground'
                          : isReceded
                          ? 'text-base text-foreground/80'
                          : 'text-xl text-foreground'
                      }`}
                    >
                      {opp.company}
                    </h4>
                    {opp.placement_type && !isReceded && (
                      <span className="text-xs font-mono text-muted truncate block mt-0.5">
                        {opp.placement_type}
                      </span>
                    )}
                  </div>

                  {pkg && (
                    <div className="flex max-w-full min-w-0 items-center gap-1.5 sm:max-w-[45%]">
                      <Award
                        className={`text-accent shrink-0 ${
                          isReceded ? 'h-3 w-3 opacity-60' : 'h-3.5 w-3.5'
                        }`}
                      />
                      <span
                        className={`min-w-0 break-words font-display text-accent tabular-nums ${
                          isReceded ? 'text-sm opacity-80' : 'text-lg sm:text-xl'
                        }`}
                      >
                        {pkg}
                      </span>
                    </div>
                  )}
                </div>

                <AnimatePresence>
                  {isExpanded && (
                    <motion.div
                      initial={{ opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: 'auto' }}
                      exit={{ opacity: 0, height: 0 }}
                      transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
                      className="overflow-hidden pt-2.5 mt-2.5 border-t border-line/60 space-y-2.5"
                    >
                      <div className="text-xs font-mono break-words space-y-1">
                        <p><span className="text-muted">SOURCE ID: </span>{opp.id}</p>
                        <p><span className="text-muted">FIRST SEEN (RECORDED): </span>{opp.first_seen_at || 'Not reported'}</p>
                      </div>

                      {(hasStart || hasEnd || opp.company_type) && (
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs font-mono break-words">
                          {hasStart && (
                            <div className="min-w-0 bg-surface-3/60 p-2 rounded border border-line/40">
                              <span className="text-[11px] text-muted uppercase tracking-wider block font-medium">
                                SOURCE REGISTRATION START
                              </span>
                              <div className="flex items-center gap-1.5 mt-0.5 text-foreground font-semibold">
                                <Calendar className="h-3 w-3 shrink-0 text-muted" />
                                <span>{opp.registration_start || 'date not reported'} {opp.registration_start_time || 'time not reported'}</span>
                              </div>
                              <span>{opp.deadline_timezone || 'Timezone unverified'}</span>
                              {opp.registration_start_alternate && <span className="block">Alternate: {opp.registration_start_alternate}</span>}
                            </div>
                          )}
                          {hasEnd && (
                            <div className="min-w-0 bg-surface-3/60 p-2 rounded border border-line/40">
                              <span className="text-[11px] text-muted uppercase tracking-wider block font-medium">
                                SOURCE REGISTRATION END
                              </span>
                              <div className="flex items-center gap-1.5 mt-0.5 text-foreground font-semibold">
                                <Calendar className="h-3 w-3 shrink-0 text-muted" />
                                <span>{opp.registration_end || 'date not reported'} {opp.registration_end_time || 'time not reported'}</span>
                              </div>
                              <span>{opp.deadline_timezone || 'Timezone unverified'}</span>
                              {opp.registration_end_alternate && <span className="block">Alternate: {opp.registration_end_alternate}</span>}
                            </div>
                          )}

                          {opp.company_type && (
                            <div className="min-w-0 bg-surface-3/60 p-2 rounded border border-line/40">
                              <span className="text-[11px] text-muted uppercase tracking-wider block font-medium">
                                CATEGORY
                              </span>
                              <span className="text-foreground font-semibold mt-0.5 block truncate">
                                {opp.company_type}
                              </span>
                            </div>
                          )}
                        </div>
                      )}

                      {programs.length > 0 && (
                        <div>
                          <span className="text-[11px] font-mono text-muted uppercase tracking-wider block mb-1">
                            SOURCE PROGRAMMES
                          </span>
                          <div className="flex flex-wrap gap-1">
                            {programs.map((prog) => (
                              <span
                                key={prog}
                                className="max-w-full break-words px-1.5 py-0.5 bg-surface-3 text-[11px] font-mono text-foreground/80 rounded border border-line/60"
                              >
                                {prog}
                              </span>
                            ))}
                          </div>
                          <p className="text-xs text-muted mt-1">Programme match does not confirm full employer eligibility.</p>
                        </div>
                      )}

                      <div className="p-2 bg-surface-3/40 rounded border border-line/40 flex flex-wrap gap-2 items-center justify-between text-xs font-mono">
                        <span className="flex items-center gap-1.5 text-muted">
                          <Sparkles className="h-3 w-3 text-accent" />
                          <span>LISTING OBSERVED</span>
                        </span>
                        <span className="text-emerald-400 font-semibold flex items-center gap-1">
                          {new Date(opp.observed_ms).toLocaleString()}
                        </span>
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            )
          })}
        </div>
      )}

      {/* Case B: Recently Detected Opportunities Stack (Historical Fallback) */}
      {isHistoricalFallback && !loading && (
        <div className="space-y-2" onMouseLeave={() => setActiveId(null)}>
          {historicalOpportunities.map((opp) => {
            const isExpanded = activeId === opp.id
            const isReceded = activeId !== null && !isExpanded
            const pkg = formatPackage(opp.max_package, null)
            const programs = parsePrograms(opp.eligible_programs)
            const deadlineInfo = parseDeadline(opp.registration_end, now)

            return (
              <div
                key={opp.id}
                role="button"
                tabIndex={0}
                aria-label={`${opp.company}, historical record ID ${opp.id}`}
                aria-expanded={isExpanded}
                onMouseEnter={() => setActiveId(opp.id)}
                onClick={() => setActiveId(isExpanded ? null : opp.id)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault()
                    setActiveId(isExpanded ? null : opp.id)
                  }
                }}
                className={`relative rounded-lg border transition-all duration-300 ease-out cursor-pointer select-none ${
                  isExpanded
                    ? 'bg-surface-2 border-line-strong shadow-[0_8px_24px_rgba(0,0,0,0.5)] scale-[1.01] z-10 p-3.5'
                    : isReceded
                    ? 'bg-surface-1/40 border-line/30 opacity-45 scale-[0.99] py-2 px-3'
                    : 'bg-surface-2/60 border-line hover:border-line-strong hover:bg-surface-2 py-2.5 px-3'
                }`}
              >
                {/* Primary Row: Company Name & Package */}
                <div className="flex flex-col items-start gap-2 sm:flex-row sm:justify-between sm:gap-3">
                  <div className="w-full min-w-0 sm:w-auto sm:flex-1">
                    <h4
                      className={`font-display uppercase tracking-wide transition-all duration-200 ${
                        isExpanded ? 'break-words' : 'truncate'
                      } ${
                        isExpanded
                          ? 'text-2xl text-foreground'
                          : isReceded
                          ? 'text-base text-foreground/80'
                          : 'text-xl text-foreground'
                      }`}
                    >
                      {opp.company}
                    </h4>
                    {opp.placement_type && !isReceded && (
                      <span className="text-xs font-mono text-muted truncate block mt-0.5">
                        {opp.placement_type}
                      </span>
                    )}
                  </div>

                  {pkg && (
                    <div className="flex max-w-full min-w-0 items-center gap-1.5 sm:max-w-[45%]">
                      <Award
                        className={`text-accent shrink-0 ${
                          isReceded ? 'h-3 w-3 opacity-60' : 'h-3.5 w-3.5'
                        }`}
                      />
                      <span
                        className={`min-w-0 break-words font-display text-accent tabular-nums ${
                          isReceded ? 'text-sm opacity-80' : 'text-lg sm:text-xl'
                        }`}
                      >
                        {pkg}
                      </span>
                    </div>
                  )}
                </div>

                {/* Expanded Details Section */}
                <AnimatePresence>
                  {isExpanded && (
                    <motion.div
                      initial={{ opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: 'auto' }}
                      exit={{ opacity: 0, height: 0 }}
                      transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
                      className="overflow-hidden pt-2.5 mt-2.5 border-t border-line/60 space-y-2.5"
                    >
                      <div className="text-xs font-mono break-words space-y-1">
                        <p><span className="text-muted">SOURCE ID: </span>{opp.id}</p>
                        <p><span className="text-muted">FIRST SEEN (RECORDED): </span>{opp.first_seen_at || 'Not reported'}</p>
                      </div>

                      {/* Registration and Category Metadata */}
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs font-mono break-words">
                        <div className="min-w-0 bg-surface-3/60 p-2 rounded border border-line/40">
                          <span className="text-[11px] text-muted uppercase tracking-wider block font-medium">
                            RECORDED REGISTRATION END
                          </span>
                          <div className="flex items-center gap-1.5 mt-0.5 text-foreground font-semibold">
                            <Calendar className="h-3 w-3 shrink-0 text-muted" />
                            <span className={deadlineInfo.isPassed ? 'text-muted' : 'text-foreground'}>
                              {deadlineInfo.text}
                            </span>
                          </div>
                          <span className="text-[10px] text-muted block mt-0.5 leading-tight">
                            Portal availability not verified; registration window may be closed or modified.
                          </span>
                        </div>

                        {opp.company_type && (
                          <div className="min-w-0 bg-surface-3/60 p-2 rounded border border-line/40">
                            <span className="text-[11px] text-muted uppercase tracking-wider block font-medium">
                              CATEGORY
                            </span>
                            <span className="text-foreground font-semibold mt-0.5 block truncate">
                              {opp.company_type}
                            </span>
                          </div>
                        )}
                      </div>

                      {/* Eligible Programs */}
                      {programs.length > 0 && (
                        <div>
                          <span className="text-[11px] font-mono text-muted uppercase tracking-wider block mb-1">
                            SOURCE PROGRAMMES
                          </span>
                          <div className="flex flex-wrap gap-1">
                            {programs.map((prog) => (
                              <span
                                key={prog}
                                className="max-w-full break-words px-1.5 py-0.5 bg-surface-3 text-[11px] font-mono text-foreground/80 rounded border border-line/60"
                              >
                                {prog}
                              </span>
                            ))}
                          </div>
                          <p className="text-xs text-muted mt-1 leading-normal">
                            Programme match indicates branch was listed; employer eligibility criteria and availability must be verified on portal.
                          </p>
                        </div>
                      )}

                      {/* Evidence Integrity Status Strip */}
                      <div className="p-2 bg-surface-3/40 rounded border border-line/40 flex flex-wrap gap-2 items-center justify-between text-xs font-mono">
                        <span className="flex items-center gap-1.5 text-muted">
                          <History className="h-3 w-3 text-muted" />
                          <span>STATUS: HISTORICAL RECORD</span>
                        </span>
                        <span className="text-muted/80 text-[11px]">
                          CURRENT PORTAL AVAILABILITY UNCONFIRMED
                        </span>
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            )
          })}
        </div>
      )}

      {/* Subtle watermark scan */}
      <div className="absolute right-2 bottom-1 text-[52px] font-display text-[rgba(243,241,234,0.02)] select-none pointer-events-none">
        {isVerifiedCurrent ? 'LIVE' : 'TPO'}
      </div>
    </motion.div>
  )
}
