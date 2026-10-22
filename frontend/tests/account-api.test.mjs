import { test, afterEach } from 'node:test'
import assert from 'node:assert/strict'
import { api } from '../src/lib/api.ts'
const originalFetch = globalThis.fetch
afterEach(() => { globalThis.fetch = originalFetch })
test('structured validation errors are renderable text, never React child objects', async () => {
  globalThis.fetch = async () => Response.json({ detail: [{ loc: ['body', 'email'], msg: 'Invalid email', type: 'value_error' }] }, {status: 422})
  const result = await api.requestDestination('invalid')
  assert.equal(result.ok, false)
  assert.equal(typeof result.error, 'string')
  assert.match(result.error, /Invalid email/)
})
test('account actions use explicit mutation methods and consent policy', async () => {
  const calls=[]
  globalThis.fetch=async (url, init)=>{ calls.push({url,...init}); return Response.json({status:'ok'}) }
  await api.setConsent(false); await api.logoutAll(); await api.revokeAccount()
  assert.deepEqual(calls.map(c=>[c.url,c.method]), [['/api/v1/account/consent','POST'],['/api/v1/account/logout-all','POST'],['/api/v1/account','DELETE']])
  assert.deepEqual(JSON.parse(calls[0].body),{consent:false,policy_version:'student-email-v1'})
  assert.ok(calls.every(c=>c.credentials==='same-origin'))
})
