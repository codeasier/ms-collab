const {stopSyntheticFixture}=require('./fixture-process.cjs')
// Synthetic local HTTPS, real session boundary, no production credentials/provider.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),readline=require('node:readline')
const {spawn,execFileSync}=require('node:child_process'),{chromium}=require('playwright')
const {createScenarioRouter}=require('../../workspace-admin/tests/scenario-router.cjs')
async function within(p){let timer;try{return await Promise.race([p,new Promise((_,reject)=>timer=setTimeout(()=>reject(new Error('synthetic barrier timeout')),15000))])}finally{clearTimeout(timer)}}
function deferred(){let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve}}
async function main(){
 const temp=fs.mkdtempSync(path.join(os.tmpdir(),'ms-collab-community-session-'));let browser,child
 try{
  execFileSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-days','1','-keyout',path.join(temp,'key.pem'),'-out',path.join(temp,'cert.pem'),'-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'],{stdio:'ignore'})
  child=spawn('python3',['-m','packages.application_ui.tests.community_browser_server',path.join(temp,'cert.pem'),path.join(temp,'key.pem')],{env:{...process.env,MS_COLLAB_SYNTHETIC_COMMUNITY_SESSION_UI:'1'},stdio:['ignore','pipe','pipe']});child.stderr.pipe(process.stderr)
  const lines=readline.createInterface({input:child.stdout}),fixture=await within(new Promise((resolve,reject)=>{lines.once('line',line=>{try{resolve(JSON.parse(line))}catch(e){reject(e)}});child.once('exit',()=>reject(new Error('synthetic fixture stopped')))}))
  const {origin,provider}=fixture,base='/api/workspaces/alpha/community/source/sample/project',folder=path.join(process.env.RUNNER_TEMP||os.tmpdir(),'ms-collab-community-session');fs.mkdirSync(folder,{recursive:true})
  browser=await chromium.launch({headless:true});const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1440,height:1050}}),page=await context.newPage(),errors=[],external=[],dialogs=[],writes=[]
  page.on('pageerror',error=>errors.push(error.message));page.on('dialog',async d=>{dialogs.push(d.message());await d.dismiss()})
  page.on('request',r=>{const url=new URL(r.url());assert.equal(r.headers().authorization,undefined);if(![origin,new URL(provider).origin].includes(url.origin))external.push(url.origin);if(r.method()==='POST'&&url.pathname.endsWith('/approve'))writes.push({path:url.pathname,headers:r.headers(),body:r.postDataJSON()})})
  // Defeat AbortSignal intentionally: generation guards, not cancellation, prove safety.
  await context.addInitScript(()=>{const fetcher=window.fetch;window.fetch=(input,options={})=>{const {signal,...rest}=options;return fetcher.call(window,input,rest)}})
  const router=createScenarioRouter({origin,onExternal:()=>external.push('routed external origin')});await context.route('**/api/**',route=>router.handle(route))
  const control=async action=>assert.equal(await page.evaluate(async action=>(await fetch('/synthetic-community/'+action,{method:'POST'})).status,action),200)
  const settled=()=>page.waitForFunction(()=>!document.querySelector('#panel').hidden&&!document.querySelector('#reload').disabled)
  const open=async(workspace='alpha')=>{await page.locator('#workspace').fill(workspace);await page.locator('#connection').fill('source');await page.locator('#repository').fill('sample/project');await page.locator('#scope-form button').click()}
  const ready=async(workspace='alpha')=>{await open(workspace);await settled()}
  const login=async subject=>{
   await control('subject/'+subject);await page.locator('#app-sign-in').click();await page.waitForFunction(subject=>document.querySelector('#app-principal')?.textContent===subject,subject)
   if(new URL(page.url()).pathname!=='/community/')await page.locator('nav a[href="/community/"]').click()
   await page.locator('#session:not([hidden])').waitFor()
  }
  const logout=async()=>{await page.locator('#app-sign-out').click();await page.waitForFunction(()=>document.querySelector('#app-status').textContent.includes('server session is revoked'))}
  const renderTick=()=>page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))))
  await page.goto(origin+'/community/');await page.waitForFunction(()=>!document.querySelector('#app-sign-in').disabled)
  assert.equal(await page.locator('input[type="password"],#credential,#connect').count(),0);assert.equal(await page.locator('#session').isVisible(),false)
  await login('reader');await ready();assert.match(await page.locator('#summary').textContent(),/2 matching/)
  assert.equal(await page.locator('#review-view').isVisible(),false);assert.equal(await page.locator('#refresh').isVisible(),false);assert.equal(await page.locator('#items img,#items b').count(),0);assert.match(await page.locator('#items').textContent(),/<img src=x/)
  const cookies=await context.cookies(origin),session=cookies.find(c=>c.name==='__Host-ms-collab-session'),csrf=cookies.find(c=>c.name==='__Host-ms-collab-csrf')
  assert.ok(session.secure&&session.httpOnly&&csrf.secure&&!csrf.httpOnly);assert.ok(!(await page.evaluate(()=>document.cookie)).includes('__Host-ms-collab-session'))
  await logout();await login('reviewer');await ready();assert.equal(await page.locator('#items-view').isVisible(),false);assert.equal(await page.locator('#refresh').isVisible(),false);assert.equal(await page.locator('#approval-panel').isVisible(),true);assert.match(await page.locator('#summary').textContent(),/101 loaded/)
  await logout();await login('admin');await ready();assert.equal(await page.locator('#refresh').isVisible(),false)
  await page.locator('#review-view').click();await settled();assert.match(await page.locator('#summary').textContent(),/101 loaded/);assert.match(await page.locator('#approval-summary').textContent(),/2 approved/)
  await page.screenshot({path:path.join(folder,'desktop-list.png'),fullPage:true})
  await page.getByRole('checkbox',{name:'Approve issue 100',exact:true}).check();await page.locator('#save').click();assert.match(await page.locator('#confirm-note').textContent(),/Add 1, remove 0; 3 total/)
  await page.screenshot({path:path.join(folder,'desktop-confirm.png'),fullPage:true});await page.locator('#cancel').click();assert.equal(writes.length,0)
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(folder,'mobile-list.png'),fullPage:true})
  await page.locator('#save').click();await page.screenshot({path:path.join(folder,'mobile-confirm.png'),fullPage:true});await page.keyboard.press('Escape');assert.equal(writes.length,0);assert.equal(await page.locator('#confirm-note').textContent(),'');await page.setViewportSize({width:1440,height:1050})
  // Filtering excludes both previously approved rows but does not discard them.
  await page.locator('#query').fill('Synthetic issue 100');await page.locator('#filters button').click();assert.match(await page.locator('#summary').textContent(),/1 matching/)
  await page.locator('#save').click();await page.evaluate(()=>{document.querySelector('#confirm-form').requestSubmit();document.querySelector('#confirm-form').requestSubmit()});await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('Approval set saved'));await settled()
  assert.equal(writes.length,1);assert.equal(writes[0].body.fingerprints.length,3);assert.match(writes[0].headers['if-match'],/^"\d+"$/);assert.ok(writes[0].headers['x-csrf-token'])
  const approved=await page.evaluate(async base=>{const data=(await(await fetch(base+'/items?limit=100')).json()).data;return data.items.map(x=>x.number)},base);assert.deepEqual(approved.sort((a,b)=>a-b),[1,100,101])
  await page.locator('#query').fill('');await page.locator('#filters button').click();await page.getByRole('checkbox',{name:'Approve issue 99',exact:true}).check();await page.locator('#save').click();await control('conflict');await page.locator('#confirm').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('revision changed'));await settled();assert.equal(writes.length,2)
  // Cookie alone is not sufficient to mutate; lack of CSRF rejects before action.
  const noCSRF=await page.evaluate(async base=>(await fetch(base+'/approve',{method:'POST',headers:{'Content-Type':'application/json','If-Match':'"1"'},body:'{"fingerprints":[]}'})).status,base);assert.equal(noCSRF,401)
  // Wrong second-page scope invalidates the entire approval set, including first page.
  router.set(base+'/review',async route=>{const r=await route.fetch();if(new URL(route.request().url()).searchParams.get('offset')==='100'){const body=await r.json();body.data.scope.workspace_id='beta';await route.fulfill({response:r,json:body})}else await route.fulfill({response:r})})
  await page.locator('#reload').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('valid, consistent'));assert.equal(await page.locator('#panel').isVisible(),false);assert.equal(await page.locator('#items').textContent(),'');router.remove(base+'/review');await ready();await page.locator('#review-view').click();await settled()
  router.set(base+'/capabilities',route=>route.fulfill({status:403,contentType:'application/json',body:'{"error":"forbidden"}'}));await page.locator('#reload').click();await page.waitForFunction(()=>document.querySelector('#panel').hidden&&document.querySelector('#status').textContent.includes('permissions'));assert.equal(await page.locator('#items').textContent(),'');router.remove(base+'/capabilities');await ready()
  await open('commons');await page.waitForFunction(()=>document.querySelector('#panel').hidden&&document.querySelector('#status').textContent.includes('unavailable'));assert.equal(await page.locator('#items').textContent(),'')
  await ready('empty');assert.match(await page.locator('#summary').textContent(),/No snapshot/)
  await ready();await page.locator('#review-view').click();await settled()
  // One routing owner and explicit barrier: stale read cannot repaint a newer scope.
  const seen=deferred(),release=deferred(),finished=deferred();router.set(base+'/review',async route=>{const r=await route.fetch();seen.resolve();await release.promise;await route.fulfill({response:r});finished.resolve()})
  await page.locator('#reload').click();await within(seen.promise);await ready('beta');assert.match(await page.locator('#scope-title').textContent(),/^beta/);release.resolve();await within(finished.promise);await renderTick();assert.match(await page.locator('#scope-title').textContent(),/^beta/);router.remove(base+'/review')
  // Actual navigation/history creates no extra login and no cached approvals/dialog.
  await page.locator('nav a[href="/"]').click();await page.waitForFunction(()=>!document.querySelector('#app-data').hidden);await page.goBack();await page.waitForFunction(()=>document.querySelector('#app-recheck')&&!document.querySelector('#app-recheck').disabled);if(await page.locator('#session').isHidden())await page.locator('#app-recheck').click();await page.locator('#session:not([hidden])').waitFor();assert.equal(await page.locator('#workspace').inputValue(),'');assert.equal(await page.locator('#items').textContent(),'');assert.equal(await page.locator('#confirmation').isVisible(),false)
  await ready();await page.locator('#review-view').click();await settled();await page.getByRole('checkbox',{name:'Approve issue 98',exact:true}).check();await page.locator('#save').click()
  const writeSeen=deferred(),writeRelease=deferred(),writeDone=deferred();router.set(base+'/approve',async route=>{const r=await route.fetch();assert.equal(r.status(),200);writeSeen.resolve();await writeRelease.promise;await route.fulfill({response:r});writeDone.resolve()})
  const before=writes.length;await page.locator('#confirm').click();await within(writeSeen.promise);await page.locator('#disconnect').click();await page.waitForFunction(()=>document.querySelector('#app-status').textContent.includes('server session is revoked'));writeRelease.resolve();await within(writeDone.promise);await renderTick()
  assert.equal(writes.length,before+1);assert.equal(await page.locator('#session').isVisible(),false);assert.equal(await page.locator('#items').textContent(),'');assert.equal(await page.locator('#workspace').inputValue(),'');router.remove(base+'/approve');await router.drain()
  await login('admin');await ready();const after=await page.evaluate(async base=>(await(await fetch(base+'/items?limit=100')).json()).data.items.map(x=>x.number),base);assert.ok(after.includes(98))
  await control('revoke-membership');await page.locator('#reload').click();await page.waitForFunction(()=>document.querySelector('#panel').hidden&&document.querySelector('#status').textContent.includes('unavailable'));assert.equal(await page.locator('#items').textContent(),'')
  await ready('beta');await control('revoke-session');await page.locator('#reload').click();await page.waitForFunction(()=>document.querySelector('#app-principal').textContent==='Not verified');assert.equal(await page.locator('#session').isVisible(),false);assert.equal(await page.locator('#items').textContent(),'')
  await router.drain();await context.unrouteAll({behavior:'wait'});await page.goto('about:blank');await page.goBack();await page.waitForFunction(()=>document.querySelector('#app-principal')?.textContent==='Not verified');assert.equal(await page.locator('#session').isVisible(),false)
  assert.deepEqual(await page.evaluate(()=>[localStorage.length,sessionStorage.length]),[0,0]);assert.deepEqual(errors,[]);assert.deepEqual(dialogs,[]);assert.deepEqual(external,[])
  console.log('Community session acceptance: real S256 cookies; reader/reviewer/admin; 101-row full approval preservation; CSRF/CAS; hostile text; late reads and accepted writes after logout; current membership/session revocation; history; four responsive screenshots. Synthetic HTTPS only.')
 }finally{await browser?.close();await stopSyntheticFixture(child)fs.rmSync(temp,{recursive:true,force:true})}
}
main().catch(error=>{console.error(error.stack);process.exitCode=1})
