// Opt-in real HTTPS cookie/session refresh -> private candidate -> approval -> catalog.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),readline=require('node:readline')
const {spawn,execFileSync}=require('node:child_process'),{chromium}=require('playwright'),{createScenarioRouter}=require('../../workspace-admin/tests/scenario-router.cjs')
async function within(p){let t;try{return await Promise.race([p,new Promise((_,reject)=>t=setTimeout(()=>reject(Error('synthetic barrier timeout')),15000))])}finally{clearTimeout(t)}}
const frames=page=>page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))))
async function main(){
 const temp=fs.mkdtempSync(path.join(os.tmpdir(),'ms-collab-refresh-cert-'));let child,browser;const releases=[]
 try{
  execFileSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-days','1','-keyout',path.join(temp,'key.pem'),'-out',path.join(temp,'cert.pem'),'-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'],{stdio:'ignore'})
  child=spawn('python3',['-m','packages.artifact_refresh.tests.browser_server',path.join(temp,'cert.pem'),path.join(temp,'key.pem')],{env:{...process.env,MS_COLLAB_SYNTHETIC_REFRESH_SESSION:'1'},stdio:['ignore','pipe','pipe']});child.stderr.resume()
  const lines=readline.createInterface({input:child.stdout}),info=await within(new Promise((resolve,reject)=>{lines.once('line',l=>{try{resolve(JSON.parse(l))}catch(e){reject(e)}});child.once('exit',()=>reject(Error('fixture stopped')))}))
  const output=path.join(process.env.RUNNER_TEMP||os.tmpdir(),'ms-collab-refresh-session');fs.mkdirSync(output,{recursive:true})
  browser=await chromium.launch({headless:true});const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1440,height:1100}}),page=await context.newPage(),errors=[],outside=[],writes=[]
  page.on('pageerror',e=>errors.push(e.message));page.on('dialog',async d=>{errors.push(d.message());await d.dismiss()})
  page.on('request',r=>{assert.ok(!r.headers().authorization);const u=new URL(r.url());if(![info.origin,new URL(info.provider).origin].includes(u.origin))outside.push(u.origin);if(r.method()==='POST'&&u.pathname.endsWith('/refresh'))writes.push(u.pathname)})
  await context.addInitScript(()=>{const f=window.fetch;window.fetch=(input,options={})=>{const {signal,...rest}=options;return f(input,rest)}})
  const router=createScenarioRouter({origin:info.origin,onExternal:()=>outside.push('external')});await context.route('**/api/**',r=>router.handle(r))
  const ctrl=action=>page.evaluate(async action=>{const r=await fetch('/synthetic/refresh/'+action,{method:'POST'});return r.json()},action)
  const scope=async(w='alpha')=>{for(const [id,v]of Object.entries({workspace:w,connection:'source',bucket:'bucket'}))await page.locator('#'+id).fill(v);await page.locator('#scope-form button').click()}
  const ready=()=>page.waitForFunction(()=>!document.querySelector('#panel').hidden&&!document.querySelector('#refresh').disabled)
  const login=async()=>{await page.locator('#app-sign-in').click();await page.waitForFunction(()=>document.querySelector('#app-principal')?.textContent==='admin');await page.locator('#app-artifact-admin-link').click();await page.waitForFunction(()=>!document.querySelector('#session').hidden)}
  const confirm=async()=>{await page.locator('#refresh').click();await page.locator('#confirm-submit').click()}
  const endpoint='/api/workspaces/alpha/artifact-admin/source/bucket/refresh'
  await page.goto(info.origin+'/artifact-admin/');await page.waitForFunction(()=>!document.querySelector('#app-sign-in').disabled);await login();await page.locator('#artifact-picker-load').click();await page.waitForFunction(()=>!document.querySelector('#artifact-picker-use').disabled);await page.locator('#artifact-picker-use').click();await ready()
  assert.equal(await page.locator('input[type=password]').count(),0);assert.equal((await ctrl('state')).calls,0)
  assert.equal(await page.evaluate(async endpoint=>(await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json','If-Match':'"1"'},body:'{}'})).status,endpoint),401)
  await page.locator('#refresh').click();await page.screenshot({path:path.join(output,'confirmation.png'),fullPage:true});await page.locator('#cancel').click();assert.equal((await ctrl('state')).calls,0)
  await page.locator('#refresh').click();await page.keyboard.press('Escape');assert.equal(await page.locator('#confirm-detail').textContent(),'')
  await page.locator('#refresh').click();await page.evaluate(()=>{const f=document.querySelector('#confirm-form');f.requestSubmit();f.requestSubmit()})
  await page.waitForFunction(()=>document.querySelector('#revision').textContent.startsWith('Index revision 2'))
  assert.equal((await ctrl('state')).calls,1);assert.equal((await ctrl('state')).published_rows,0)
  await page.locator('#review').click();await page.waitForFunction(()=>!document.querySelector('#preview').hidden);assert.match(await page.locator('#summary').textContent(),/3 metadata rows/);assert.equal(await page.locator('#rows svg').count(),0)
  await page.screenshot({path:path.join(output,'private-candidate.png'),fullPage:true});await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(output,'mobile.png'),fullPage:true});await page.setViewportSize({width:1440,height:1100})
  const quarters=await page.evaluate(async()=>{const r=await fetch('/api/workspaces/alpha/artifacts/source/bucket/quarters');return r.json()});assert.deepEqual(quarters.data.quarters,[])
  await page.locator('#acknowledge').check();await page.locator('#approve').click();await page.locator('#confirm-submit').click();await page.waitForFunction(()=>document.querySelector('#revision').textContent.startsWith('Index revision 3'))
  assert.equal((await ctrl('state')).published_rows,3);assert.equal((await ctrl('state')).pending,false)
  await page.locator('#app-artifacts-link').click();await page.waitForFunction(()=>!document.querySelector('#session').hidden);await scope();await page.getByRole('button',{name:'builds/',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#results').textContent.includes('synthetic-report'))
  assert.equal(await page.locator('#results svg').count(),0);await page.screenshot({path:path.join(output,'approved-catalog.png'),fullPage:true})
  await page.locator('#app-artifact-admin-link').click();await page.waitForFunction(()=>!document.querySelector('#session').hidden);await scope();await ready()
  // A successfully accepted write whose reply is late cannot repopulate another scope.
  let seen,release,finished;const started=new Promise(r=>seen=r),barrier=new Promise(r=>release=r),done=new Promise(r=>finished=r);releases.push(release)
  router.set(endpoint,async route=>{const response=await route.fetch();seen();await barrier;await route.fulfill({response});finished()})
  await confirm();await within(started);await scope('beta');await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('unavailable'));const count=writes.length;release();await within(done);await frames(page);assert.equal(writes.length,count);assert.equal(await page.locator('#panel').isVisible(),false);router.remove(endpoint)
  await scope();await ready();assert.equal((await ctrl('state')).revision,4);assert.equal((await ctrl('state')).published_rows,3)
  await page.locator('#refresh').click();await ctrl('bump');const oldCalls=(await ctrl('state')).calls;await page.locator('#confirm-submit').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('revision changed'));assert.equal((await ctrl('state')).calls,oldCalls);assert.equal(await page.locator('#preview').isVisible(),false)
  await page.screenshot({path:path.join(output,'conflict.png'),fullPage:true});await page.locator('#reload').click();await ready()
  // A synthetic deadline prevents publication and completes only failed cleanup.
  await ctrl('timeout');let result=page.waitForResponse(r=>new URL(r.url()).pathname===endpoint&&r.request().method()==='POST');await confirm();assert.equal((await within(result)).status(),504);await frames(page)
  let state=await ctrl('state');assert.equal(state.revision,5);assert.equal(state.active,0);assert.equal(state.leases.at(-1),'failed');assert.equal(state.published_rows,3)
  await page.locator('#reload').click();await ready()
  // True parallel HTTP logout while acquisition is blocked, not a mock response.
  await ctrl('pause');await confirm();await page.waitForFunction(async()=>{const r=await fetch('/synthetic/refresh/state',{method:'POST'});return (await r.json()).active===1})
  await page.locator('#app-sign-out').click();await page.waitForFunction(()=>document.querySelector('#app-status').textContent.includes('server session is revoked'));await ctrl('release')
  await page.waitForFunction(async()=>{const r=await fetch('/synthetic/refresh/state',{method:'POST'});return (await r.json()).active===0});assert.equal(await page.locator('#panel').isVisible(),false);assert.equal((await ctrl('state')).revision,5)
  await login();await scope();await ready();await ctrl('pause');await confirm();await page.waitForFunction(async()=>{const r=await fetch('/synthetic/refresh/state',{method:'POST'});return (await r.json()).active===1})
  await ctrl('revoke');await ctrl('release');await page.waitForFunction(async()=>{const r=await fetch('/synthetic/refresh/state',{method:'POST'});return (await r.json()).active===0});await page.waitForFunction(()=>document.querySelector('#panel').hidden);assert.equal((await ctrl('state')).revision,5);assert.equal(await page.locator('#rows li').count(),0)
  await router.drain();await context.unrouteAll({behavior:'wait'});assert.deepEqual(errors,[]);assert.deepEqual(outside,[]);assert.deepEqual(await page.evaluate(()=>[localStorage.length,sessionStorage.length]),[0,0])
  console.log('Synthetic HTTPS refresh acceptance passed: current cookie/CSRF, bounded acquisition, private candidate, full approval and catalog, double-submit/cancel, scope-stale response, CAS, timeout, genuine concurrent logout/revoke and cleanup. No real cloud or production acceptance.')
 }finally{for(const r of releases)r();await browser?.close();if(child&&child.exitCode===null){child.kill();await within(new Promise(r=>child.once('exit',r)))}fs.rmSync(temp,{recursive:true,force:true})}
}
main().catch(e=>{console.error(e.stack);process.exitCode=1})
