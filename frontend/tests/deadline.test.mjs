import test from 'node:test'
import assert from 'node:assert/strict'
import { parseDeadline } from '../src/lib/deadline.ts'

const NOW = Date.parse('2026-10-25T12:00:00Z')

test('missing or null-like values stay unknown', () => {
  assert.deepEqual(parseDeadline(null, NOW), { text: 'Not reported in source', isPassed: false })
  assert.deepEqual(parseDeadline('null', NOW), { text: 'Not reported in source', isPassed: false })
  assert.deepEqual(parseDeadline('   ', NOW), { text: 'Not reported in source', isPassed: false })
})

test('date-only values render verbatim with no invented passage', () => {
  // A passed calendar date without a time still cannot prove closure:
  // time-of-day and source timezone are unknown.
  assert.deepEqual(parseDeadline('14-Oct-2026', NOW), { text: '14-Oct-2026', isPassed: false })
  assert.deepEqual(parseDeadline('2026-10-24', NOW), { text: '2026-10-24', isPassed: false })
  assert.deepEqual(parseDeadline('31-Oct-2026', NOW), { text: '31-Oct-2026', isPassed: false })
})

test('zone-less datetimes render verbatim regardless of viewer timezone', () => {
  assert.deepEqual(
    parseDeadline('14-Oct-2026 10:00', NOW),
    { text: '14-Oct-2026 10:00', isPassed: false }
  )
})

test('explicit zone-aware timestamps authorize passage inference', () => {
  const past = parseDeadline('2026-10-24T10:00:00+05:30', NOW)
  assert.equal(past.isPassed, true)
  assert.ok(past.text.includes('(Passed)'))

  const future = parseDeadline('2026-10-31T23:59:59Z', NOW)
  assert.equal(future.isPassed, false)
  assert.equal(future.text, '2026-10-31T23:59:59Z')

  const boundary = parseDeadline('2026-10-25T12:00:00Z', NOW)
  assert.equal(boundary.isPassed, false)
})
