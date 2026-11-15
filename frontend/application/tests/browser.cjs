// Real cookie-only product pages, synthetic HTTPS/provider/data, no production inputs.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),readline=require('node:readline')
const {spawn,execFileSync}=require('node:child_process'),{chromium}=require('playwright')
const {createScenarioRouter}=require('../../workspace-admin/tests/scenario-router.cjs')
async function within(p){let timer;try{return await Promise.race([p,new Promise((_,reject)=>timer=setTimeout(()=>reject(new Error('synthetic barrier timeout')),15000))])}finally{clearTimeout(timer)}}
async function main(){
 const temp=fs.mkdtempSync(path.join(os.tmpdir(),'ms-collab-application-cert-')),children=[];let browser
 try{
  execFileSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-days','1','-keyout',path.join(temp,'key.pem'),'-out',path.join(temp,'cert.pem'),'-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'],{stdio:'ignore'})
  const fixture=async closed=>{const child=spawn('python3',['-m','packages.application_ui.tests.browser_server',path.join(temp,'cert.pem'),path.join(temp,'key.pem'),...(closed?['closed']:[])],{env:{...process.env,MS_COLLAB_SYNTHETIC_APPLICATION_UI:'1'},stdio:['ignore','pipe','pipe']});children.push(child);child.stderr.resume();const lines=readline.createInterface({input:child.stdout});return within(new Promise((resolve,reject)=>{lines.once('line',line=>{try{resolve(JSON.parse(line))}catch(e){reject(e)}});child.once('exit',()=>reject(new Error('fixture stopped')))}))}
  const main=await fixture(false),closed=await fixture(true),folder=path.join(process.env.RUNNER_TEMP||os.tmpdir(),'ms-collab-application-ui');fs.mkdirSync(folder,{recursive:true})
  browser=await chromium.launch({headless:true})
  // Certificate exception is confined to this disposable context, never OS trust.
  const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1440,height:1050}}),page=await context.newPage(),errors=[],external=[],writes=[],logins=[],dialogs=[]
  page.on('pageerror',e=>{errors.push(e.message);console.error('Synthetic page error:',e.message)});page.on('dialog',async d=>{dialogs.push(d.message());await d.dismiss()})
  page.on('request',request=>{const url=new URL(request.url()),headers=request.headers();assert.ok(!headers.authorization);if(![main.origin,closed.origin,new URL(main.provider).origin].includes(url.origin))external.push(url.origin);if(request.method()!=='GET')writes.push(url.pathname);if(url.pathname==='/auth/start')logins.push(1)})
  await context.addInitScript(()=>{const original=window.fetch;window.fetch=(input,options={})=>{const {signal,...rest}=options;return original.call(window,input,rest)}})
  await page.goto(closed.origin+'/');await page.waitForFunction(()=>document.querySelector('#app-login-note').textContent.includes('no reviewed bound-code'))
  assert.equal(await page.locator('#app-sign-in').isDisabled(),true);assert.equal(await page.locator('input[type="password"]').count(),0);assert.equal(await page.locator('#app-data').isVisible(),false)
  const closedStart=await page.evaluate(async()=>{const r=await fetch('/auth/start',{method:'POST',headers:{'Content-Type':'application/json','X-MS-Collab-Login':'1'},body:'{}'});return r.status});assert.equal(closedStart,503)
  await page.screenshot({path:path.join(folder,'closed-login.png'),fullPage:true})
  await page.goto(main.origin+'/');await page.waitForFunction(()=>!document.querySelector('#app-sign-in').disabled)
  await page.locator('#app-sign-in').click();await page.waitForFunction(()=>document.querySelector('#app-principal')?.textContent==='admin'&&!document.querySelector('#app-data').hidden)
  assert.match(await page.locator('#app-workspaces').textContent(),/<img src=x onerror=alert\(1\)>/);assert.equal(await page.locator('#app-workspaces img').count(),0);assert.ok(!(await page.locator('#app-workspaces').textContent()).includes('commons'))
  const cookies=await context.cookies(main.origin),session=cookies.find(c=>c.name==='__Host-ms-collab-session'),csrf=cookies.find(c=>c.name==='__Host-ms-collab-csrf');assert.ok(session.secure&&session.httpOnly&&csrf.secure&&!csrf.httpOnly);assert.ok(!(await page.evaluate(()=>document.cookie)).includes('__Host-ms-collab-session'))
  await page.screenshot({path:path.join(folder,'overview.png'),fullPage:true})
  const loginCount=logins.length;await page.locator('nav a[href="/manage/"]').click();await page.waitForFunction(()=>document.querySelector('#members').children.length===3)
  assert.equal(logins.length,loginCount);assert.equal(await page.locator('input[type="password"]').count(),0)
  await page.getByRole('button',{name:'Edit reader',exact:true}).click();await page.locator('#role').selectOption('developer');await page.locator('#save').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('Membership saved'))
  assert.match(await page.locator('#members').textContent(),/reader · developer · active/)
  const before=writes.length;await page.locator('#create-open').click();await page.locator('#space-name').fill('Discarded workspace');await page.locator('#initial-admin').fill('admin');await page.locator('#create-cancel').click();assert.equal(writes.length,before)
  await page.locator('#invite-open').click();await page.locator('#invite-principal').fill('outsider');await page.locator('#invite-role').selectOption('developer');await page.locator('#invite-save').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('Invitation recorded'))
  assert.match(await page.locator('#invitations').textContent(),/outsider · developer · pending/)
  await page.screenshot({path:path.join(folder,'manage.png'),fullPage:true})
  await page.locator('nav a[href="/responsibility/"]').click();await page.waitForFunction(()=>document.querySelector('#app-principal').textContent==='admin'&&!document.querySelector('#session').hidden)
  const scope=async workspace=>{await page.locator('#workspace').fill(workspace);await page.locator('#connection').fill('source');await page.locator('#owner').fill('sample');await page.locator('#repo').fill('project');await page.locator('#scope-form button').click()}
  const revision=async n=>{try{await page.waitForFunction(n=>document.querySelector('#version').textContent.startsWith(`Record revision ${n} ·`),n)}catch(error){throw new Error(`Revision ${n}: ${await page.locator('#status').textContent()}`,{cause:error})}}
  await scope('alpha');await revision(1);assert.match(await page.locator('#candidates').textContent(),/Assignment unknown/)
  await page.locator('#edit').click();await page.locator('#add-field').click();const card=page.locator('#editor-fields fieldset');await card.locator('[data-entry="id"]').fill('ui');await card.locator('[data-entry="name"]').fill('Interface');await card.locator('[data-entry="paths"]').fill('ui');await card.locator('[data-entry="owners"]').fill('admin')
  await page.locator('#save').click();await page.evaluate(()=>{const f=document.querySelector('#confirm-form');f.requestSubmit();f.requestSubmit()});await revision(2)
  assert.equal(writes.filter(x=>x.endsWith('/responsibility/source/sample/project/config')).length,1)
  const update=async(name,expected)=>page.evaluate(async({name,expected})=>{const path='/api/workspaces/alpha/responsibility/source/sample/project/config',config=(await(await fetch(path)).json()).data;config.fields[0].name=name;const csrf=document.cookie.split('; ').find(x=>x.startsWith('__Host-ms-collab-csrf=')).split('=')[1];const r=await fetch(path,{method:'PUT',headers:{'Content-Type':'application/json','If-Match':`"${expected}"`,'X-CSRF-Token':csrf},body:JSON.stringify({fields:config.fields})});return r.status},{name,expected})
  await page.locator('#edit').click();await page.locator('[data-entry="name"]').fill('Stale local edit');await page.locator('#save').click();assert.equal(await update('Concurrent mapping',2),200);await page.locator('#confirm-submit').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('revision changed'))
  assert.equal(await page.locator('#panel').isVisible(),false);assert.equal(await page.locator('#editor-fields fieldset').count(),0);await page.screenshot({path:path.join(folder,'conflict.png'),fullPage:true})
  await page.locator('#reload').click();await revision(3);await page.screenshot({path:path.join(folder,'responsibility.png'),fullPage:true});await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(folder,'mobile.png'),fullPage:true});await page.setViewportSize({width:1440,height:1050})
  // Ineffective AbortSignal must not let an old scope issue follow-up reads or render.
  const router=createScenarioRouter({origin:main.origin,onExternal:()=>external.push('unexpected routed origin')});await context.route('**/api/**',route=>router.handle(route))
  const routePath='/api/workspaces/alpha/responsibility/source/sample/project/config';let seen,release,finished;const started=new Promise(r=>seen=r),barrier=new Promise(r=>release=r),done=new Promise(r=>finished=r)
  let oldCandidates=0;const listener=request=>{if(request.url().includes('/alpha/responsibility/')&&request.url().includes('/candidates?'))oldCandidates++};page.on('request',listener)
  router.set(routePath,async route=>{const response=await route.fetch();seen();await barrier;await route.fulfill({response});finished()})
  await page.locator('#reload').click();await within(started);await scope('beta');await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('unavailable'));const oldCount=oldCandidates
  release();await within(done);await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));assert.equal(oldCandidates,oldCount);assert.equal(await page.locator('#panel').isVisible(),false)
  page.off('request',listener);router.remove(routePath);await scope('alpha');await revision(3)
  // An accepted write with a late response cannot restore a page after pagehide/recheck.
  let putSeen,putRelease,putFinished;const putStarted=new Promise(r=>putSeen=r),putBarrier=new Promise(r=>putRelease=r),putDone=new Promise(r=>putFinished=r)
  router.set(routePath,async route=>{if(route.request().method()!=='PUT')return route.continue();const response=await route.fetch();putSeen();await putBarrier;await route.fulfill({response});putFinished()})
  await page.locator('#edit').click();await page.locator('[data-entry="name"]').fill('Saved while page was hidden');await page.locator('#save').click();await page.locator('#confirm-submit').click();await within(putStarted)
  await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide')));assert.equal(await page.locator('#app-principal').textContent(),'Not verified');assert.equal(await page.locator('#editor-fields fieldset').count(),0);assert.equal(await page.locator('#workspace').inputValue(),'')
  await page.locator('#app-recheck').click();await page.waitForFunction(()=>document.querySelector('#app-principal').textContent==='admin'&&!document.querySelector('#session').hidden);await scope('alpha');await revision(4)
  putRelease();await within(putDone);await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));await revision(4);assert.match(await page.locator('#configuration').textContent(),/Saved while page was hidden/);router.remove(routePath)
  await router.drain();await context.unrouteAll({behavior:'wait'})
  const noCSRF=await page.evaluate(async()=>{const r=await fetch('/api/workspaces/alpha/responsibility/source/sample/project/config',{method:'PUT',headers:{'Content-Type':'application/json','If-Match':'"4"'},body:'{"fields":[]}'});return r.status});assert.equal(noCSRF,401)
  // Current membership revocation applies to the same cookie across both modules.
  const revoked=await page.evaluate(async()=>{const r=await fetch('/synthetic/revoke-membership',{method:'POST'});return r.status});assert.equal(revoked,200)
  await page.locator('#reload').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('unavailable'))
  assert.equal(await page.locator('#configuration li').count(),0);assert.equal(await page.locator('#panel').isVisible(),false)
  await page.locator('nav a[href="/manage/"]').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('No accessible workspaces'));assert.equal(await page.locator('#members li').count(),0)
  // A stale server session clears all page state; no silent re-login or role fallback.
  const revokedSession=await page.evaluate(async()=>{const r=await fetch('/synthetic/revoke-session',{method:'POST'});return r.status});assert.equal(revokedSession,200)
  await page.locator('#reload').click();await page.waitForFunction(()=>document.querySelector('#app-principal').textContent==='Not verified');assert.equal(await page.locator('#session').isVisible(),false)
  await page.locator('#app-sign-in').click();await page.waitForFunction(()=>document.querySelector('#app-status').textContent.includes('rejected'));await page.locator('#app-sign-in').click();await page.waitForFunction(()=>document.querySelector('#app-principal')?.textContent==='admin')
  // Explicit logout clears UI immediately and revokes the server cookie once.
  const count=writes.filter(x=>x==='/auth/logout').length;await page.locator('#app-sign-out').click();await page.waitForFunction(()=>document.querySelector('#app-status').textContent.includes('server session is revoked'));assert.equal(writes.filter(x=>x==='/auth/logout').length,count+1)
  assert.equal((await context.cookies(main.origin)).some(c=>c.name==='__Host-ms-collab-session'),false)
  await page.goto('about:blank');await page.goBack();await page.waitForFunction(()=>document.querySelector('#app-principal')?.textContent==='Not verified');assert.equal(await page.locator('#app-data').isVisible(),false)
  assert.deepEqual(await page.evaluate(()=>[localStorage.length,sessionStorage.length]),[0,0]);assert.deepEqual(errors,[]);assert.deepEqual(dialogs,[]);assert.deepEqual(external,[])
  console.log('Unified UI acceptance passed: real cookie-only login, management, invitations, responsibility CAS, CSRF, workspace/session revocation, closed provider and six responsive screenshots. Synthetic HTTPS only.')
 }finally{await browser?.close();for(const child of children){if(child.exitCode===null){child.kill();await within(new Promise(resolve=>child.once('exit',resolve)))}}fs.rmSync(temp,{recursive:true,force:true})}
}
main().catch(error=>{console.error(error.stack);if(error.cause)console.error(error.cause.stack);process.exitCode=1})
