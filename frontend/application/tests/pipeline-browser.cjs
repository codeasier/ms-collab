const {stopSyntheticFixture}=require('./fixture-process.cjs')
// Cookie-only pipeline acceptance; synthetic HTTPS, stored records and provider only.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),readline=require('node:readline')
const {spawn,execFileSync}=require('node:child_process'),{chromium}=require('playwright')
const {createScenarioRouter}=require('../../workspace-admin/tests/scenario-router.cjs')
async function within(p){let timer;try{return await Promise.race([p,new Promise((_,reject)=>timer=setTimeout(()=>reject(Error('synthetic barrier timeout')),15000))])}finally{clearTimeout(timer)}}
const frame=page=>page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))))
async function main(){
 const temp=fs.mkdtempSync(path.join(os.tmpdir(),'ms-collab-pipeline-cookie-'));let server,browser
 try{
  execFileSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-days','1','-keyout',path.join(temp,'key.pem'),'-out',path.join(temp,'cert.pem'),'-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'],{stdio:'ignore'})
  server=spawn('python3',['-m','packages.application_ui.tests.browser_server',path.join(temp,'cert.pem'),path.join(temp,'key.pem'),'pipelines'],{env:{...process.env,MS_COLLAB_SYNTHETIC_APPLICATION_UI:'1'},stdio:['ignore','pipe','pipe']});server.stderr.resume()
  const lines=readline.createInterface({input:server.stdout}),fixture=await within(new Promise((resolve,reject)=>{lines.once('line',line=>{try{resolve(JSON.parse(line))}catch(e){reject(e)}});server.once('exit',()=>reject(Error('fixture stopped')))}))
  const origin=fixture.origin,folder=path.join(process.env.RUNNER_TEMP||os.tmpdir(),'ms-collab-pipeline-cookie');fs.mkdirSync(folder,{recursive:true})
  browser=await chromium.launch({headless:true});const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1440,height:1050}}),page=await context.newPage(),errors=[],dialogs=[],requests=[],external=[]
  await context.addInitScript(()=>{const actual=window.fetch;window.fetch=(input,options={})=>{const {signal,...rest}=options;return actual.call(window,input,rest)}})
  const router=createScenarioRouter({origin,onExternal:()=>external.push('unexpected origin')});await context.route('**/api/**',route=>router.handle(route))
  page.on('pageerror',error=>errors.push(error.message));page.on('dialog',async d=>{dialogs.push(d.message());await d.dismiss()})
  page.on('request',r=>{const u=new URL(r.url());assert.equal(r.headers().authorization,undefined);if(![origin,new URL(fixture.provider).origin].includes(u.origin))external.push(u.origin);requests.push({path:u.pathname,method:r.method()})})
  const ready=()=>page.locator('#panel:not([hidden])').waitFor()
  const scope=async(workspace='alpha')=>{await page.locator('#workspace').fill(workspace);await page.locator('#connection').fill('example-ci');await page.locator('#pipeline').fill('build');await page.locator('#scope-form button').click()}
  const open=async()=>{await scope();await ready()}
  const activated=()=>page.waitForFunction(()=>document.querySelector('#app-principal')?.textContent==='admin'&&!document.querySelector('#session').hidden)
  const login=async()=>{await page.locator('#app-sign-in').click();await page.waitForFunction(()=>document.querySelector('#app-principal')?.textContent==='admin');await page.locator('#app-pipelines-link').click();await activated()}
  await page.goto(origin+'/');await page.waitForFunction(()=>!document.querySelector('#app-sign-in').disabled);await login();assert.equal(await page.locator('input[type="password"],#credential,#connect').count(),0)
  await open();assert.match(await page.locator('#summary').textContent(),/20 matching runs of 20 stored/);assert.match(await page.locator('#coverage').textContent(),/19 runs with valid samples/)
  assert.match(await page.locator('#durations').textContent(),/<img src=x/);assert.equal(await page.locator('#durations img,#durations b').count(),0)
  await page.locator('#next').click();assert.match(await page.locator('#summary').textContent(),/Page 2 of 2/);await page.locator('#previous').click()
  await page.locator('#state').selectOption('running');await page.locator('#filters button').click();assert.match(await page.locator('#summary').textContent(),/1 matching runs/);assert.match(await page.locator('#coverage').textContent(),/19 runs with valid samples/)
  await page.getByRole('button',{name:'View run-19',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#detail-body').textContent.includes('not available'));await page.keyboard.press('Escape')
  await page.locator('#state').selectOption('');await page.locator('#filters button').click()
  await page.screenshot({path:path.join(folder,'desktop.png'),fullPage:true})
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.ok(await page.locator('button:visible').evaluateAll(nodes=>nodes.every(n=>n.scrollWidth<=n.clientWidth)));await page.screenshot({path:path.join(folder,'mobile.png'),fullPage:true});await page.setViewportSize({width:1440,height:1050})
  await page.getByRole('button',{name:'View run-20',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#detail-status').textContent.includes('failure'));assert.match(await page.locator('#detail-body').textContent(),/<img src=x/);assert.equal(await page.locator('#detail-body img,#detail-body b').count(),0);await page.screenshot({path:path.join(folder,'detail.png'),fullPage:true});await page.locator('#detail-close').click()
  // Editing scope clears old rows immediately, even without submit.
  await page.locator('#workspace').fill('beta');assert.equal(await page.locator('#panel').isVisible(),false);assert.equal(await page.locator('#runs li').count(),0);await scope('commons');await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('unavailable'));assert.equal(await page.locator('#panel').isVisible(),false);await open()
  // A delayed list may not cause an old-scope duration request after cancellation.
  const base='/api/workspaces/alpha/pipelines/example-ci/build';let seen,release,finished;const started=new Promise(r=>seen=r),barrier=new Promise(r=>release=r),done=new Promise(r=>finished=r)
  router.set(base+'/runs',async route=>{const response=await route.fetch();seen();await barrier;await route.fulfill({response});finished()})
  await page.locator('#reload').click();await within(started);await scope('beta');await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('unavailable'));const count=requests.filter(r=>r.path===base+'/durations').length
  release();await within(done);await frame(page);assert.equal(requests.filter(r=>r.path===base+'/durations').length,count);assert.equal(await page.locator('#panel').isVisible(),false);router.remove(base+'/runs');await open()
  // Late detail cannot reopen a dismissed dialog or replace a newer selection.
  let detailSeen,detailRelease,detailFinished;const detailStarted=new Promise(r=>detailSeen=r),detailBarrier=new Promise(r=>detailRelease=r),detailDone=new Promise(r=>detailFinished=r)
  router.set(base+'/runs/run-20',async route=>{const response=await route.fetch();detailSeen();await detailBarrier;await route.fulfill({response});detailFinished()})
  await page.getByRole('button',{name:'View run-20',exact:true}).click();await within(detailStarted);await page.keyboard.press('Escape');await page.getByRole('button',{name:'View run-18',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#detail-status').textContent.includes('failure'));detailRelease();await within(detailDone);await frame(page);assert.equal(await page.locator('#detail-title').textContent(),'run-18');await page.locator('#detail-close').click();router.remove(base+'/runs/run-20')
  // Same-scope revision mismatch must clear, never combine views.
  router.set(base+'/durations',async route=>{const response=await route.fetch(),body=await response.json();body.data.source.revision++;await route.fulfill({response,json:body})})
  await page.locator('#reload').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('could not be validated'));assert.equal(await page.locator('#panel').isVisible(),false);router.remove(base+'/durations');await open()
  // Pagehide+explicit recheck: old list cannot render or issue follow-up requests.
  let hideSeen,hideRelease,hideFinished;const hideStarted=new Promise(r=>hideSeen=r),hideBarrier=new Promise(r=>hideRelease=r),hideDone=new Promise(r=>hideFinished=r)
  router.set(base+'/runs',async route=>{const response=await route.fetch();hideSeen();await hideBarrier;await route.fulfill({response});hideFinished()})
  await page.locator('#reload').click();await within(hideStarted);await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide')));assert.equal(await page.locator('#workspace').inputValue(),'');await page.locator('#app-recheck').click();await activated();hideRelease();await within(hideDone);await frame(page);assert.equal(await page.locator('#panel').isVisible(),false);assert.equal(await page.locator('#runs li').count(),0);router.remove(base+'/runs');await open()
  // A pending detail plus explicit logout cannot resurrect session or private rows.
  let outSeen,outRelease,outFinished;const outStarted=new Promise(r=>outSeen=r),outBarrier=new Promise(r=>outRelease=r),outDone=new Promise(r=>outFinished=r)
  router.set(base+'/runs/run-20',async route=>{const response=await route.fetch();outSeen();await outBarrier;await route.fulfill({response});outFinished()})
  await page.getByRole('button',{name:'View run-20',exact:true}).click();await within(outStarted);await page.keyboard.press('Escape');await page.locator('#disconnect').click();await page.waitForFunction(()=>document.querySelector('#app-status').textContent.includes('server session is revoked'));outRelease();await within(outDone);await frame(page);assert.equal(await page.locator('#app-principal').textContent(),'Not verified');assert.equal(await page.locator('#detail').isVisible(),false);assert.equal(await page.locator('#runs li').count(),0);router.remove(base+'/runs/run-20')
  await router.drain();await context.unrouteAll({behavior:'wait'});await login();await open()
  // Real Back/Forward leaves scopes/data blank, regardless of BFCache vs reload.
  await page.goto(origin+'/manage/');await page.goBack();await page.locator('#app-recheck').waitFor();await frame(page);assert.equal(await page.locator('#workspace').inputValue(),'');assert.equal(await page.locator('#runs li').count(),0);await page.locator('#app-recheck').click();await activated();await open()
  assert.equal(await page.evaluate(async()=>{const r=await fetch('/synthetic/revoke-membership',{method:'POST'});return r.status}),200)
  await page.locator('#reload').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('unavailable'));assert.equal(await page.locator('#panel').isVisible(),false)
  assert.equal(await page.evaluate(async()=>{const r=await fetch('/synthetic/revoke-session',{method:'POST'});return r.status}),200)
  await scope();await page.waitForFunction(()=>document.querySelector('#app-principal').textContent==='Not verified');assert.equal(await page.locator('#session').isVisible(),false);assert.equal(await page.locator('#workspace').inputValue(),'')
  assert.deepEqual(requests.filter(r=>r.path.includes('/pipelines/')&&r.method!=='GET'),[]);assert.deepEqual(await page.evaluate(()=>[localStorage.length,sessionStorage.length]),[0,0]);assert.deepEqual(errors,[]);assert.deepEqual(dialogs,[]);assert.deepEqual(external,[])
 }finally{
  const failures=[]
  try{await browser?.close()}catch(error){failures.push(error)}
  try{await stopSyntheticFixture(server)}catch(error){failures.push(error)}
  try{fs.rmSync(temp,{recursive:true,force:true})}catch(error){failures.push(error)}
  if(failures.length)throw new AggregateError(failures,'Synthetic pipeline cleanup failed')
 }
  console.log('Cookie-only pipeline HTTPS acceptance passed: shared login, scope/versions, chained-read cancellation, detail races, lifecycle/logout, revocation, XSS and three screenshots. Synthetic only.')
}
main().catch(error=>{console.error(error.stack);process.exitCode=1})
