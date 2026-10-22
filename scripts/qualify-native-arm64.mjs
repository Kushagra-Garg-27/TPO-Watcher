// Same entrypoint on a GitHub native runner or an approved disposable ARM64 host.
import fs from 'node:fs'
import path from 'node:path'
import crypto from 'node:crypto'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex')
const git = args => {
  const r = spawnSync('git', args, { cwd: root, encoding: 'utf8' })
  if (r.status !== 0) throw Error('Git inventory failed')
  return r.stdout
}
const files = [...new Set(git(['ls-files', '-co', '--exclude-standard', '-z']).split('\0').filter(Boolean))]
  .filter(p => !p.startsWith('native-evidence/') && fs.existsSync(path.join(root,p)) && fs.statSync(path.join(root,p)).isFile()).sort()
// Normalize checkout CRLF in text only; retain raw hashes, including binary assets.
const manifest = files.map(p => {
  const b = fs.readFileSync(path.join(root,p))
  const text=!b.includes(0) && Buffer.from(b.toString('utf8'),'utf8').equals(b)
  return { path:p, sha256:sha(b), canonical:text ? sha(b.toString('utf8').replaceAll('\r\n','\n')) : sha(b) }
})
const canonical = sha(JSON.stringify(manifest.map(({path,canonical})=>({path,sha256:canonical}))))
if (process.argv.includes('--source-identity')) {
  console.log(canonical)
  process.exit(0)
}
if (!/^[a-f0-9]{64}$/.test(process.env.EXPECTED_SOURCE_SHA256 || '') || canonical !== process.env.EXPECTED_SOURCE_SHA256)
  throw Error('Approved source identity mismatch')
if (process.arch !== 'arm64' || process.platform !== 'linux') throw Error('Native Linux ARM64 required; no emulation')
const identityPath='/sys/devices/virtual/dmi/id/board_asset_tag'
if(fs.existsSync(identityPath) && fs.readFileSync(identityPath,'utf8').trim()==='i-03328ee0e60347254')
  throw Error('Qualification on existing production instance is forbidden')
const out = path.join(root,'native-evidence')
fs.mkdirSync(out) // New evidence only, never overwrite.
const write = (n,v) => fs.writeFileSync(path.join(out,n),JSON.stringify(v,null,2))
write('source.json',{commit:git(['rev-parse','HEAD']).trim(),canonical,files:manifest})
const run = (label, cmd, args) => {
  const r = spawnSync(cmd,args,{cwd:root,encoding:'utf8',maxBuffer:32*1024*1024,timeout:45*60*1000})
  fs.writeFileSync(path.join(out,label+'.log'),(r.stdout||'')+(r.stderr||''))
  if(r.status!==0) throw Error(label+' failed; see retained evidence')
  return r.stdout.trim()
}
const daemon = run('daemon','docker',['info','--format','{{.Architecture}}'])
if (!['aarch64','arm64'].includes(daemon)) throw Error('ARM64 Docker daemon required')
run('host','uname',['-a'])
const tag='tpo-native:'+canonical.slice(0,12), verification=tag+'-verification'
run('runtime-build','docker',['buildx','build','--platform','linux/arm64','--target','runtime','--load',
  '--metadata-file',path.join(out,'build-metadata.json'),'-f','Dockerfile.release',
  '--build-arg','SOURCE_MANIFEST_SHA256='+canonical,'-t',tag,'.'])
const image=JSON.parse(run('image','docker',['image','inspect',tag]))[0]
if(image.Architecture!=='arm64' || image.Config.User!=='10001:10001') throw Error('Image architecture/user mismatch')
write('runtime-image.json',image)
const base=['run','--rm','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
  '--tmpfs','/tmp:rw,nosuid,size=512m,mode=1777','--shm-size','256m']
run('installed-lock','docker',[...base,'--entrypoint','python',tag,'-m','pip','check'])
run('packages','docker',[...base,'--entrypoint','python',tag,'-m','pip','list','--format=json'])
// Browser dependency smoke only: no application imports, source login, or network.
run('chromium','docker',[...base,'--entrypoint','python',tag,'-B','-c',
  "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True,args=['--no-sandbox']); page=b.new_page(); page.set_content('<title>fictional-native</title>'); assert page.title()=='fictional-native'; print(b.version); b.close(); p.stop()"])
run('verification-build','docker',['buildx','build','--platform','linux/arm64','--target','verification','--load',
  '-f','Dockerfile.release','--build-arg','SOURCE_MANIFEST_SHA256='+canonical,'-t',verification,'.'])
const vi=JSON.parse(run('verification-image','docker',['image','inspect',verification]))[0]
if(JSON.stringify(vi.RootFS.Layers.slice(0,image.RootFS.Layers.length))!==JSON.stringify(image.RootFS.Layers))
  throw Error('Verification image does not contain the exact runtime layers')
run('bootstrap','docker',[...base,verification,'--self-test'])
const modules=[
  'tests/integration/test_native_release_restart.py',
  'tests/integration/test_production_entrypoint_startup.py',
  'tests/integration/test_production_migration_recovery.py',
  'tests/integration/test_production_runtime.py',
  'tests/integration/test_notification_controller_host_crash.py',
  'tests/integration/test_relay_request_views.py',
]
for (const [i,module] of modules.entries())
  run('guarded-'+i,'docker',[...base,verification,'--pytest','-v','--tb=short',module])
const nativeTests=[
  'tests/integration/test_test_isolation.py::test_native_sendmsg_method_has_installed_guard',
  'tests/integration/test_test_isolation.py::test_linux_owned_descriptor_operations_and_safe_cleanup',
]
const native=run('native-linux','docker',[...base,verification,'--pytest','-v','--tb=short',...nativeTests])
if(/\bSKIPPED\b|\bskipped\b/.test(native) || !/2 passed/.test(native)) throw Error('Native cases must execute and pass')
run('save-runtime','docker',['save','-o',path.join(out,'runtime.docker.tar'),tag])
// Stream large archive hashes without buffering the image in Node's heap.
const archiveHash=await new Promise((resolve,reject)=>{const h=crypto.createHash('sha256');const s=fs.createReadStream(path.join(out,'runtime.docker.tar'));s.on('data',b=>h.update(b));s.on('end',()=>resolve(h.digest('hex')));s.on('error',reject)})
const metadata=JSON.parse(fs.readFileSync(path.join(out,'build-metadata.json'),'utf8'))
if(!/^sha256:[a-f0-9]{64}$/.test(metadata['containerimage.digest']||'')) throw Error('Build manifest digest absent')
write('acceptance.json',{passed:true,canonical,image_id:image.Id,build_manifest_digest:metadata['containerimage.digest'],
  registry_digest:null,archive_sha256:archiveHash,modules,nativeTests,
  scope:'Fictional guarded startup/health/restart/recovery plus native Chromium; no live source, SMTP or production deployment'})
console.log('Native acceptance evidence: '+out)
