// Actual connections -> refresh -> private review -> complete approval -> reader flow.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),readline=require('node:readline')
const {spawn,execFileSync}=require('node:child_process'),{chromium}=require('playwright')
const {createScenarioRouter}=require('../../workspace-admin/tests/scenario-router.cjs'),{stopSyntheticFixture}=require('./fixture-process.cjs')
async function within(p){let timer;try{return await Promise.race([p,new Promise((_,reject)=>timer=setTimeout(()=>reject(Error('community refresh barrier timeout')),15000))])}finally{clearTimeout(timer)}}
async function main(){
 const temp=fs.mkdtempSync(path.join(os.tmpdir(),'community-refresh-'));let child,browser;const releases=[]
 try{
  execFileSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-days','1','-keyout',path.join(temp,'key.pem'),'-out',path.join(temp,'cert.pem'),'-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'],{stdio:'ignore'})
  child=spawn('python3',['-m','packages.application_ui.tests.connections_browser_server',path.join(temp,'cert.pem'),path.join(temp,'key.pem')],{env:{...process.env,MS_COLLAB_SYNTHETIC_CONNECTIONS_UI:'1',MS_COLLAB_SYNTHETIC_COMMUNITY_REFRESH_UI:'1'},stdio:['ignore','pipe','pipe']});child.stderr.resume()
  const lines=readline.createInterface({input:child.stdout}),info=await within(new Promise((resolve,reject)=>{lines.once('line',line=>{try{resolve(JSON.parse(line))}catch(error){reject(error)}});child.once('exit',()=>reject(Error('fixture stopped')))}))
  const output=path.join(process.env.RUNNER_TEMP||os.tmpdir(),'ms-collab-community-refresh');fs.mkdirSync(output,{recursive:true})
  browser=await chromium.launch({headless:true});const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1440,height:1050}}),page=await context.newPage(),errors=[],writes=[]
  page.on('pageerror',error=>errors.push(error.message));page.on('dialog',async dialog=>{errors.push(dialog.message());await dialog.dismiss()})
  page.on('request',request=>{const url=new URL(request.url());assert.equal(request.headers().authorization,undefined);assert.ok([info.origin,new URL(info.provider).origin].includes(url.origin));if(request.method()==='POST'&&/\/(refresh|approve)$/.test(url.pathname))writes.push({path:url.pathname,body:request.postDataJSON(),headers:request.headers()})})
  await context.addInitScript(()=>{const original=window.fetch;window.fetch=(input,options={})=>{const {signal,...rest}=options;return original.call(window,input,rest)}})
  const router=createScenarioRouter({origin:info.origin,onExternal:()=>errors.push('external route')});await context.route('**/api/**',route=>router.handle(route))
  const control=action=>page.evaluate(async action=>{const response=await fetch('/synthetic-community/'+action,{method:'POST'});if(!response.ok)throw Error('fixture control failed');return (await response.text())||''},action)
  const state=async()=>JSON.parse(await control('refresh-state'))
  const settled=()=>page.waitForFunction(()=>!document.querySelector('#panel').hidden&&!document.querySelector('#reload').disabled)
  const verified=actor=>page.waitForFunction(actor=>document.querySelector('#app-principal')?.textContent===actor&&!document.querySelector('#app-recheck').disabled,actor)
  const open=async()=>{for(const [id,value]of Object.entries({workspace:'alpha',connection:'source',repository:'sample/project'}))await page.locator('#'+id).fill(value);await page.locator('#scope-form button').click();await settled()}
  const board=async()=>{await page.locator('#app-community-link').click();await page.locator('#session:not([hidden])').waitFor();await open()}
  const login=async actor=>{await control('subject/'+actor);await page.locator('#app-sign-in').click();await verified(actor)}
  const logout=async()=>{await page.locator('#app-sign-out').click();await page.waitForFunction(()=>document.querySelector('#app-status').textContent.includes('server session is revoked'))}
  const refresh=async()=>{await page.locator('#refresh').click();await page.locator('#confirm').click()}
  const base='/api/workspaces/alpha/community/source/sample/project',endpoint=base+'/refresh'
  await page.goto(info.origin+'/connections/');await page.waitForFunction(()=>!document.querySelector('#app-sign-in').disabled);await login('admin');await page.locator('#app-connections-link').click();await verified('admin')
  await page.locator('#connection-workspace').selectOption('alpha');await page.getByRole('button',{name:'Load current connections'}).click();await page.locator('.connection-card').waitFor();assert.match(await page.locator('.connection-card').textContent(),/2 approvals/)
  // A real audience adjustment, not a no-op, discards prior snapshot and approval.
  await page.locator('input[data-principal=second][data-grant=reader_ids]').check();await page.getByRole('button',{name:'Review grant changes'}).click();await page.locator('#connection-save').click();await page.waitForFunction(()=>document.querySelector('#connection-status').textContent.includes('Grants replaced'))
  await board();assert.match(await page.locator('#summary').textContent(),/No snapshot/);assert.equal((await state()).calls,0)
  assert.equal(await page.evaluate(async endpoint=>(await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json','If-Match':'"2"'},body:'{}'})).status,endpoint),401);assert.equal((await state()).loads,0)
  await page.locator('#refresh').click();await page.screenshot({path:path.join(output,'confirmation.png'),fullPage:true});await page.locator('#cancel').click();assert.equal((await state()).calls,0)
  await page.locator('#refresh').click();await page.keyboard.press('Escape');assert.equal((await state()).calls,0)
  const beforeSubmit=writes.length
  await page.locator('#refresh').click();await page.evaluate(()=>{const form=document.querySelector('#confirm-form');form.requestSubmit();form.requestSubmit()});await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('Snapshot refreshed'));await settled()
  assert.equal((await state()).calls,3);assert.equal(writes.length,beforeSubmit+1);assert.deepEqual(writes.at(-1).body,{});assert.equal(writes.at(-1).headers['if-match'],'"2"');assert.ok(writes.at(-1).headers['x-csrf-token'])
  assert.match(await page.locator('#summary').textContent(),/0 matching items of 0 loaded/)
  await page.locator('#review-view').click();await settled();assert.match(await page.locator('#summary').textContent(),/101 loaded/);assert.match(await page.locator('#approval-summary').textContent(),/0 approved/)
  assert.equal(await page.locator('#items img,#items b').count(),0);await page.screenshot({path:path.join(output,'private-desktop.png'),fullPage:true});await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(output,'private-mobile.png'),fullPage:true});await page.setViewportSize({width:1440,height:1050})
  await logout();await login('reader');await board();assert.match(await page.locator('#summary').textContent(),/0 matching items of 0 loaded/);assert.equal(await page.locator('#review-view').isVisible(),false);assert.equal(await page.locator('#refresh').isVisible(),false)
  await logout();await login('admin');await board();await page.locator('#review-view').click();await settled()
  // Full-snapshot approval preserves a first-page selection while viewing a filter.
  await page.getByRole('checkbox',{name:'Approve issue 101',exact:true}).check();await page.locator('#query').fill('Refreshed synthetic issue 1');await page.locator('#filters button').click()
  for(let i=0;i<5&&await page.getByRole('checkbox',{name:'Approve issue 1',exact:true}).count()===0;i++)await page.locator('#next').click()
  await page.getByRole('checkbox',{name:'Approve issue 1',exact:true}).check();await page.locator('#save').click();assert.match(await page.locator('#confirm-note').textContent(),/2 total approved/);await page.locator('#confirm').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('Approval set saved'));await settled();assert.equal(writes.at(-1).body.fingerprints.length,2)
  await logout();await login('reader');await board();assert.match(await page.locator('#summary').textContent(),/2 matching items of 2 loaded/);assert.equal(await page.locator('#items img,#items b').count(),0);await page.screenshot({path:path.join(output,'reader-desktop.png'),fullPage:true});await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(output,'reader-mobile.png'),fullPage:true});await page.setViewportSize({width:1440,height:1050})
  await logout();await login('admin');await board();await control('refresh-change');await refresh();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('Snapshot refreshed'));await settled()
  await logout();await login('reader');await board();assert.match(await page.locator('#summary').textContent(),/1 matching items of 1 loaded/);assert.ok(!(await page.locator('#items').textContent()).includes('Changed private candidate'))
  await logout();await login('admin');await board();await page.locator('#review-view').click();await settled();assert.match(await page.locator('#items').textContent(),/Changed private candidate 101/);assert.equal(await page.getByRole('checkbox',{name:'Approve issue 101',exact:true}).isChecked(),false)
  // A response lost AFTER actual acceptance triggers only a readback, never retry.
  let lost=0;router.set(endpoint,async route=>{lost++;const response=await route.fetch();assert.equal(response.status(),200);await route.abort('failed')})
  const before=(await state()).calls;await refresh();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('Result uncertain'));await settled();assert.equal(lost,1);assert.equal((await state()).calls,before+3);router.remove(endpoint)
  // CAS rejects before loading a credential or making a provider call.
  await page.locator('#refresh').click();await control('conflict');const stale=(await state()).calls;await page.locator('#confirm').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('revision changed'));await settled();assert.equal((await state()).calls,stale)
  // Real concurrent logout while the synthetic GET owns the durable lease.
  await control('refresh-pause');releases.push(()=>control('refresh-release').catch(()=>{}));await refresh();await page.waitForFunction(async()=>JSON.parse(await(await fetch('/synthetic-community/refresh-state',{method:'POST'})).text()).active===1)
  const pending=(await state()).revision;await logout();await control('refresh-release');await page.waitForFunction(async()=>JSON.parse(await(await fetch('/synthetic-community/refresh-state',{method:'POST'})).text()).active===0);assert.equal((await state()).revision,pending);assert.equal(await page.locator('#panel').isVisible(),false)
  await login('admin');await board();await control('refresh-pause');await refresh();await page.waitForFunction(async()=>JSON.parse(await(await fetch('/synthetic-community/refresh-state',{method:'POST'})).text()).active===1);await control('revoke-membership');await control('refresh-release');await page.waitForFunction(async()=>JSON.parse(await(await fetch('/synthetic-community/refresh-state',{method:'POST'})).text()).active===0);await page.waitForFunction(()=>document.querySelector('#panel').hidden);assert.equal((await state()).revision,pending)
  await router.drain();await context.unrouteAll({behavior:'wait'});assert.deepEqual(errors,[]);assert.deepEqual(await page.evaluate(()=>[localStorage.length,sessionStorage.length]),[0,0]);assert.ok((await state()).leases.every(value=>value==='complete'||value==='failed'))
 }finally{for(const release of releases)await release();await browser?.close();await stopSyntheticFixture(child);fs.rmSync(temp,{recursive:true,force:true})}
 console.log('Community refresh PASS: real connections grant invalidation, cookie/CSRF, cancellation, once-only bounded scan, private full review, exact complete approval, reader board, changed-content isolation, uncertain result without retry, CAS, concurrent logout/revocation, and five responsive screenshots. Synthetic GitCode GETs only.')
}
main().catch(error=>{console.error(error.stack);process.exitCode=1})
