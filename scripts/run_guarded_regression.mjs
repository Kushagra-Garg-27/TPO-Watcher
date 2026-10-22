// Orchestration only: every Python collection/test runs through the unchanged
// pre-import isolation bootstrap, in a fresh interpreter per test module.
import fs from 'node:fs'
import path from 'node:path'
import crypto from 'node:crypto'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const [python, destination] = process.argv.slice(2)
if (!python || !destination) throw new Error('Usage: node scripts/run_guarded_regression.mjs PYTHON NEW_EVIDENCE_DIRECTORY')
const output = path.resolve(destination)
fs.mkdirSync(output, { recursive: false }) // Never overwrite earlier evidence.
const write = (name, data) => fs.writeFileSync(path.join(output, name), JSON.stringify(data, null, 2))
const hash = value => crypto.createHash('sha256').update(value).digest('hex')
function sourceManifest() {
  const result = {}
  for (const area of ['app', 'tests']) {
    for (const entry of fs.readdirSync(path.join(root, area), { recursive: true })) {
      if (entry.endsWith('.py')) {
        const key = `${area}/${entry.replaceAll('\\', '/')}`
        result[key] = hash(fs.readFileSync(path.join(root, key)))
      }
    }
  }
  return result
}
const before = sourceManifest()
write('source-before.json', before)
function guarded(args, label) {
  const command = ['-I', '-B', 'tests/isolation_bootstrap.py', ...args]
  const run = spawnSync(python, command, { cwd: root, encoding: 'utf8', maxBuffer: 32 * 1024 * 1024,
    timeout: 20 * 60 * 1000, windowsHide: true })
  const text = (run.stdout || '') + (run.stderr || '')
  fs.writeFileSync(path.join(output, `${label}.log`), text)
  return { status: run.status, text, command: [python, ...command], error: run.error?.message }
}
const bootstrap = guarded(['--self-test'], 'bootstrap')
if (bootstrap.status !== 0) throw new Error('Bootstrap self-test failed')
const collection = guarded(['--pytest', '--collect-only', '-q', '-p', 'no:warnings'], 'collection')
if (collection.status !== 0) throw new Error('Guarded collection failed')
const nodes = collection.text.split(/\r?\n/).filter(line => /^tests\/.*\.py::/.test(line))
if (!nodes.length || new Set(nodes).size !== nodes.length) throw new Error('Empty/duplicate collection')
const groups = new Map()
for (const node of nodes) {
  const module = node.split('::')[0]
  if (!groups.has(module)) groups.set(module, [])
  groups.get(module).push(node)
}
const matrix = [...groups].map(([module, nodes], index) => ({ shard: index + 1, module, nodes, count: nodes.length }))
write('matrix.json', { count: nodes.length, shards: matrix, intentional_repeats: [] })
const outcomes = []
for (const shard of matrix) {
  const label = `shard-${String(shard.shard).padStart(3, '0')}`
  console.log(`${label}: ${shard.module} (${shard.count} nodes)`)
  const run = guarded(['--pytest', '-v', '--tb=short', '-p', 'no:warnings', '--color=no', shard.module], label)
  const actual = []
  let pending = null
  for (const line of run.text.split(/\r?\n/)) {
    const complete = line.match(/^(tests\/.+?)\s+(PASSED|FAILED|SKIPPED|ERROR|XFAIL|XPASS)(?:\s|$)/)
    if (complete) { actual.push({ node: complete[1], outcome: complete[2] }); pending = null; continue }
    if (/^tests\/.*\.py::/.test(line)) { pending = line.trim(); continue }
    const separate = line.match(/^(PASSED|FAILED|SKIPPED|ERROR|XFAIL|XPASS)(?:\s|$)/)
    if (separate && pending) { actual.push({ node: pending, outcome: separate[1] }); pending = null }
  }
  const actualNodes = actual.map(row => row.node)
  const missing = shard.nodes.filter(node => !actualNodes.includes(node))
  const unexpected = actualNodes.filter(node => !shard.nodes.includes(node))
  const duplicate = actualNodes.filter((node, i) => actualNodes.indexOf(node) !== i)
  const result = { ...shard, exit_code: run.status, error: run.error, command: run.command, actual, missing, unexpected, duplicate }
  outcomes.push(result)
  write('results.json', outcomes)
  console.log(`${label}: exit ${run.status}; ${actual.length}/${shard.count} recorded; ${missing.length} missing`)
}
const after = sourceManifest()
write('source-after.json', after)
const changed = [...new Set([...Object.keys(before), ...Object.keys(after)])].filter(key => before[key] !== after[key])
const failures = outcomes.flatMap(shard => shard.actual.filter(row => ['FAILED', 'ERROR', 'XPASS'].includes(row.outcome)))
const gaps = outcomes.filter(shard => shard.missing.length || shard.unexpected.length || shard.duplicate.length ||
  shard.exit_code === null || (shard.exit_code !== 0 && !shard.actual.some(row => ['FAILED', 'ERROR'].includes(row.outcome))))
const historical = JSON.parse(fs.readFileSync(path.join(root, 'tests/historical-c1-failures.json'), 'utf8'))
const activeFailures = failures.filter(row => !historical.nodes.includes(row.node))
const counts = outcomes.flatMap(shard => shard.actual).reduce((all, row) => ({ ...all, [row.outcome]: (all[row.outcome] || 0) + 1 }), {})
const summary = { collected: nodes.length, shards: matrix.length, counts, failures,
  historical_failures: failures.filter(row => historical.nodes.includes(row.node)), active_failures: activeFailures,
  coverage_gaps: gaps.map(row => row.shard), changed_during_run: changed,
  active_contract_passed: activeFailures.length === 0 && gaps.length === 0 && changed.length === 0,
  full_suite_passed: failures.length === 0 && gaps.length === 0 && changed.length === 0 }
write('summary.json', summary)
console.log(JSON.stringify(summary, null, 2))
// Historical diagnostics are still executed and remain visible as failures;
// exit 0 certifies only the explicitly selected active contract and coverage.
process.exitCode = summary.active_contract_passed ? 0 : 1
