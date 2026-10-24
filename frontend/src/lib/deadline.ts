export interface DeadlineInfo {
  text: string
  isPassed: boolean
}

/**
 * Render a source deadline verbatim unless the value itself carries an
 * explicit zone-aware timestamp.
 *
 * Date-only and zone-less values never gain an invented time-of-day, and the
 * viewer's locale never redefines the source instant: passage is reported
 * only when the source string proves it (explicit UTC offset or Z).
 */
export function parseDeadline(
  deadlineStr: string | null | undefined,
  currentMs: number = Date.now()
): DeadlineInfo {
  if (!deadlineStr || !deadlineStr.trim() || deadlineStr.toLowerCase() === 'null') {
    return { text: 'Not reported in source', isPassed: false }
  }
  const trimmed = deadlineStr.trim()
  const zoneAware = /\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})\s*$/i.test(trimmed)
  if (!zoneAware) {
    return { text: trimmed, isPassed: false }
  }
  const parsed = Date.parse(trimmed)
  if (Number.isNaN(parsed)) {
    return { text: trimmed, isPassed: false }
  }
  const isPassed = parsed < currentMs
  return { text: isPassed ? `${trimmed} (Passed)` : trimmed, isPassed }
}
