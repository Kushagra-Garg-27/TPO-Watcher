import { test, afterEach } from 'node:test'
import assert from 'node:assert/strict'
import { api } from '../src/lib/api.ts'

const originalFetch = globalThis.fetch
afterEach(() => { globalThis.fetch = originalFetch })
const legacy = { id: 'fixture-42', company: 'Fictional employer', max_package: '0',
  min_package: null, placement_type: 'Internship', registration_end: null,
  eligible_programs: 'VIT Computer Engineering', company_type: null,
  is_active: 'true', first_seen_at: '2026-10-20' }

test('missing richer endpoint preserves genuine legacy records without inventing freshness or total', async () => {
  const calls = []
  globalThis.fetch = async (url) => {
    calls.push(url)
    return Response.json(url.endsWith('opportunity-listing') ? { detail: 'Not Found' } : [legacy],
      { status: url.endsWith('opportunity-listing') ? 404 : 200 })
  }
  const result = await api.getOpportunityFeed()
  assert.equal(result.ok, true)
  assert.deepEqual(result.data, { items: [legacy], shown: 1, total: null, status: 'STORED', observed_ms: null })
  assert.deepEqual(calls, ['/api/v1/opportunity-listing', '/api/v1/opportunities'])
})

test('source or custody outage does not silently fall back to legacy records', async () => {
  const calls = []
  globalThis.fetch = async (url) => { calls.push(url); return Response.json({ detail: 'Unavailable' }, { status: 503 }) }
  assert.equal((await api.getOpportunityFeed()).ok, false)
  assert.equal(calls.length, 1)
})

test('explicit empty verified response does not query legacy records', async () => {
  let calls = 0
  const data = { items: [], total: 0, shown: 0, status: 'VERIFIED', observed_ms: 100 }
  globalThis.fetch = async () => { calls++; return Response.json(data) }
  assert.deepEqual((await api.getOpportunityFeed()).data, data)
  assert.equal(calls, 1)
})

test('legacy duplicate identities and malformed rows fail closed', async () => {
  for (const value of [[legacy, legacy], [{ ...legacy, id: '' }], [{ ...legacy, is_active: 'false' }], { items: [legacy] }]) {
    globalThis.fetch = async (url) => Response.json(url.endsWith('opportunity-listing') ? {} : value,
      { status: url.endsWith('opportunity-listing') ? 404 : 200 })
    assert.equal((await api.getOpportunityFeed()).ok, false)
  }
})

test('distinct IDs with the same company name remain separate', async () => {
  globalThis.fetch = async (url) => Response.json(url.endsWith('opportunity-listing') ? {} : [legacy, { ...legacy, id: 'fixture-43' }],
    { status: url.endsWith('opportunity-listing') ? 404 : 200 })
  assert.equal((await api.getOpportunityFeed()).data.items.length, 2)
})
