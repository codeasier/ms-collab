// Real cookie-only product pages, synthetic HTTPS/provider/data, no production inputs.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),readline=require('node:readline')
const {spawn,execFileSync}=require('node:child_process'),{chromium}=require('playwright')
const {createScenarioRouter}=require('../../workspace-admin/tests/scenario-router.cjs')
const {exerciseIdentitySelection}=require('../../workspace-admin/tests/identity-selection.cjs')
const {exerciseInvitationReview}=require('../../workspace-admin/tests/invitation-review.cjs')
const {exerciseModuleStatus}=require('./module-status.cjs')
const {exerciseAdminTransfer}=require('../../workspace-admin/tests/admin-transfer.cjs')
const {stopSyntheticFixtures}=require('./fixture-process.cjs')
async function within(p,label){let timer;try{return await Promise.race([p,new Promise((_,reject)=>timer=setTimeout(()=>reject(new Error(`Synthetic ${label} timed out`)),15000))])}finally{clearTimeout(timer)}}
// Check actual computed foreground/background after the browser has applied both
// stylesheets. Resolve transparent ancestors, reject invisible/disabled probes.
async function readable(page,selectors){
 const rows=await page.evaluate(selectors=>{
  const rgb=value=>{const values=value.match(/[\d.]+/g).map(Number);return {rgb:values.slice(0,3),alpha:values[3]??1}}
  const luminance=values=>values.map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4}).reduce((n,v,i)=>n+v*[.2126,.7152,.0722][i],0)
  return selectors.map(selector=>{const el=document.querySelector(selector);if(!el||!el.checkVisibility()||el.disabled)throw new Error('Invalid contrast probe '+selector)
   const style=getComputedStyle(el),fg=rgb(style.color);let node=el,bg
   while(node){const value=rgb(getComputedStyle(node).backgroundColor);if(value.alpha===1){bg=value.rgb;break}if(value.alpha!==0)throw new Error('Unsupported translucent contrast background');node=node.parentElement}
   if(!bg||fg.alpha!==1)throw new Error('Undefined opaque contrast pair')
   const values=[luminance(fg.rgb),luminance(bg)].sort((a,b)=>a-b);return [selector,(values[1]+.05)/(values[0]+.05)]})
 },selectors)
 for(const [selector,ratio]of rows)assert.ok(ratio>=4.5,`${selector} text contrast ${ratio.toFixed(2)} < 4.5`)
}
async function main(){
 const temp=fs.mkdtempSync(path.join(os.tmpdir(),'ms-collab-application-cert-')),children=[],diagnostics=[];let browser,failure
 try{
  execFileSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-days','1','-keyout',path.join(temp,'key.pem'),'-out',path.join(temp,'cert.pem'),'-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'],{stdio:'ignore'})
  const fixture=async(closed,mode='')=>{
   const child=spawn('python3',['-m','packages.application_ui.tests.browser_server',path.join(temp,'cert.pem'),path.join(temp,'key.pem'),...(closed?['closed']:mode?[mode]:[])],{env:{...process.env,MS_COLLAB_SYNTHETIC_APPLICATION_UI:'1'},stdio:['ignore','pipe','pipe']})
   children.push(child)
   const diagnostic={label:closed?'closed fixture':(mode||'main')+' fixture',stderr:''};diagnostics.push(diagnostic)
   child.stderr.setEncoding('utf8');child.stderr.on('data',chunk=>{diagnostic.stderr=(diagnostic.stderr+chunk).slice(-8192)})
   const lines=readline.createInterface({input:child.stdout})
   return within(new Promise((resolve,reject)=>{
    lines.once('line',line=>{try{resolve(JSON.parse(line))}catch(error){reject(error)}})
    child.once('error',reject);child.once('exit',()=>reject(new Error(`${diagnostic.label} stopped during startup`)))
   }),`${diagnostic.label} startup`)
  }
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
  const router=createScenarioRouter({origin:main.origin,onExternal:()=>external.push('unexpected routed origin')});await context.route('**/api/**',route=>router.handle(route));await context.route('**/app/bootstrap',route=>router.handle(route))
  let bootSeen,bootRelease,bootFinished;const bootStarted=new Promise(r=>bootSeen=r),bootBarrier=new Promise(r=>bootRelease=r),bootDone=new Promise(r=>bootFinished=r)
  router.set('/app/bootstrap',async route=>{const response=await route.fetch();bootSeen();await bootBarrier;await route.fulfill({response});bootFinished()})
  await page.goto(main.origin+'/');await within(bootStarted,'bootstrap request barrier');assert.equal(await page.locator('#app-recheck').isDisabled(),true);await page.evaluate(()=>document.querySelector('#app-recheck').dispatchEvent(new Event('click')))
  bootRelease();await within(bootDone,'bootstrap response release');router.remove('/app/bootstrap');await page.waitForFunction(()=>!document.querySelector('#app-sign-in').disabled)
  await page.locator('#app-sign-in').click();await page.waitForFunction(()=>document.querySelector('#app-principal')?.textContent==='admin'&&!document.querySelector('#app-data').hidden)
  assert.match(await page.locator('#app-workspaces').textContent(),/<img src=x onerror=alert\(1\)>/);assert.equal(await page.locator('#app-workspaces img').count(),0);assert.ok(!(await page.locator('#app-workspaces').textContent()).includes('commons'))
  const cookies=await context.cookies(main.origin),session=cookies.find(c=>c.name==='__Host-ms-collab-session'),csrf=cookies.find(c=>c.name==='__Host-ms-collab-csrf');assert.ok(session.secure&&session.httpOnly&&csrf.secure&&!csrf.httpOnly);assert.ok(!(await page.evaluate(()=>document.cookie)).includes('__Host-ms-collab-session'))
  await page.screenshot({path:path.join(folder,'overview.png'),fullPage:true})
  // Internal Sign out must interrupt a still-running page activation, not be dropped as busy.
  let loadSeen,loadRelease,loadFinished;const loadStarted=new Promise(r=>loadSeen=r),loadBarrier=new Promise(r=>loadRelease=r),loadDone=new Promise(r=>loadFinished=r)
  router.set('/api/workspaces',async route=>{const response=await route.fetch();loadSeen();await loadBarrier;await route.fulfill({response});loadFinished()})
  await page.locator('nav a[href="/manage/"]').click();await within(loadStarted,'management activation request barrier');await page.locator('#disconnect').click();await page.waitForFunction(()=>document.querySelector('#app-status').textContent.includes('server session is revoked'))
  loadRelease();await within(loadDone,'management activation response release');await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));assert.equal(await page.locator('#app-principal').textContent(),'Not verified');assert.equal(await page.locator('#session').isVisible(),false);router.remove('/api/workspaces')
  await page.locator('#app-sign-in').click();await page.waitForFunction(()=>document.querySelector('#app-principal')?.textContent==='admin'&&!document.querySelector('#app-data').hidden)
  const loginCount=logins.length;await page.locator('nav a[href="/manage/"]').click();await page.waitForFunction(()=>document.querySelector('#members').children.length===3)
  assert.equal(logins.length,loginCount);assert.equal(await page.locator('input[type="password"]').count(),0)
  assert.equal((await context.request.get(main.origin+'/api/workspaces/alpha/admin-transfers/capabilities')).status(),404)
  assert.equal(await page.locator('#transfer-open').isVisible(),false,'old no-handle mode stays unavailable')
  await page.getByRole('button',{name:'Edit reader',exact:true}).click();await readable(page,['#editing','#role','#member-state','#save','#cancel']);await page.locator('#role').selectOption('developer');await page.locator('#save').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('Membership saved'))
  assert.match(await page.locator('#members').textContent(),/reader · developer · active/)
  // Members/status are rendered before modules and invitations. Wait for the
  // current alpha invitation projection, not just its earlier member projection.
  const managementReady=()=>page.waitForFunction(()=>document.querySelector('#app-principal').textContent==='admin'&&!document.querySelector('#operator-panel').hidden&&document.querySelector('#workspace').value==='alpha'&&document.querySelector('#members').children.length===3&&!document.querySelector('#status').textContent.startsWith('Loading')&&!document.querySelector('#invitation-panel').hidden&&!document.querySelector('#invite-open').hidden&&/^\d+ recent invitations \(/.test(document.querySelector('#invitation-status').textContent))
  const reconnectManagement=async()=>{
   if((await page.locator('#app-status').textContent()).includes('server session is revoked')){
    await page.locator('#app-sign-in').click();await page.waitForFunction(()=>document.querySelector('#app-principal')?.textContent==='admin'&&!document.querySelector('#app-data').hidden);await page.locator('nav a[href="/manage/"]').click()
   }else if(await page.locator('#session').isHidden())await page.locator('#app-recheck').click()
   await managementReady()
  }
  await managementReady()
  await exerciseIdentitySelection({page,router,ready:managementReady,reconnect:reconnectManagement,invalidations:[
   ()=>page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide'))),
   ()=>page.evaluate(()=>document.querySelector('#workspace').dispatchEvent(new Event('change'))),
   ()=>page.evaluate(()=>document.querySelector('#app-recheck').dispatchEvent(new Event('click'))),
   async()=>{await page.evaluate(()=>document.querySelector('#app-sign-out').dispatchEvent(new Event('click')));await page.waitForFunction(()=>document.querySelector('#app-status').textContent.includes('server session is revoked'))}
  ]})
  await exerciseInvitationReview({page,router,ready:managementReady,reconnect:reconnectManagement,folder,readable,inspectState:()=>page.evaluate(async()=>({invitations:(await(await fetch('/api/workspaces/alpha/invitations')).json()).data,members:(await(await fetch('/api/workspaces/alpha/members')).json()).data})),invalidations:[
   ()=>page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide'))),
   ()=>page.evaluate(()=>document.querySelector('#app-recheck').dispatchEvent(new Event('click'))),
   async()=>{await page.evaluate(()=>document.querySelector('#app-sign-out').dispatchEvent(new Event('click')));await page.waitForFunction(()=>document.querySelector('#app-status').textContent.includes('server session is revoked'))},
   async()=>{await page.goto('about:blank');await page.goBack();await page.locator('#invite-dialog').waitFor({state:'attached'})}
  ]})
  const before=writes.length;await page.locator('#create-open').click();assert.equal(await page.locator('#create-save').isDisabled(),true)
  await readable(page,['#create-dialog h2','#space-name','#space-kind','#initial-admin','#create-cancel']);await page.locator('#space-name').fill('Discarded workspace');await page.locator('#initial-admin').fill('admin')
  await readable(page,['#initial-admin-review']);await page.locator('#initial-admin-review').click();await page.waitForFunction(()=>!document.querySelector('#create-save').disabled)
  await readable(page,['#create-save','#initial-admin-review','#initial-admin-selection','#create-status']);await page.screenshot({path:path.join(folder,'identity-selection.png'),fullPage:true})
  // Layout-only maximum-ID fixture. No create is submitted for this synthetic
  // identity; actual server selection and create are exercised above.
  const longPrincipal='principal-'+'x'.repeat(190),longSelectionPath='/api/control/initial-admin-selections/'+longPrincipal
  router.set(longSelectionPath,route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({data:{initial_admin:longPrincipal,precondition:'ias1-'+'a'.repeat(64)}})}))
  await page.setViewportSize({width:390,height:844});await page.locator('#initial-admin').fill(longPrincipal);assert.equal(await page.locator('#create-save').isDisabled(),true);await page.locator('#initial-admin-review').click();await page.waitForFunction(()=>!document.querySelector('#create-save').disabled)
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth&&document.querySelector('#create-dialog').scrollWidth<=document.querySelector('#create-dialog').clientWidth))
  assert.ok((await page.locator('#initial-admin-selection').textContent()).includes(longPrincipal));await readable(page,['#create-save','#initial-admin-review','#initial-admin-selection','#create-status'])
  // Native dialog scrolling must expose the actions, not merely leave them
  // checkVisibility()-true outside the viewport or clipped by the dialog.
  await page.locator('#create-save').scrollIntoViewIfNeeded();await page.locator('#create-status').scrollIntoViewIfNeeded()
  assert.deepEqual(page.viewportSize(),{width:390,height:844})
  assert.ok(await page.evaluate(()=>{
   const dialog=document.querySelector('#create-dialog'),clip=dialog.getBoundingClientRect()
   return dialog.scrollTop>0&&['#create-save','#create-cancel','#create-status'].every(selector=>{
    const node=document.querySelector(selector),box=node.getBoundingClientRect()
    return box.width>0&&box.height>0&&box.left>=0&&box.top>=0&&box.right<=innerWidth&&box.bottom<=innerHeight&&box.left>=clip.left&&box.top>=clip.top&&box.right<=clip.right&&box.bottom<=clip.bottom
   })
  }),'Create, Cancel and final status must be inside the mobile viewport and dialog after scrolling')
  for(const selector of ['#create-save','#create-cancel'])assert.equal(await page.locator(selector).isEnabled(),true)
  assert.equal(await page.locator('#initial-admin').inputValue(),longPrincipal)
  await page.screenshot({path:path.join(folder,'identity-selection-mobile.png'),fullPage:true})
  await within(router.drain(),'long identity selection route drain');router.remove(longSelectionPath)
  await page.locator('#create-cancel').click();assert.equal(await page.locator('#create-dialog').isVisible(),false);assert.equal(writes.length,before);await page.setViewportSize({width:1440,height:1050})
  await page.locator('#invite-open').click();await readable(page,['#invite-dialog h2','#invite-principal','#invite-role','#invite-cancel']);await page.locator('#invite-principal').fill('outsider');await page.locator('#invite-role').selectOption('developer');await page.locator('#invite-review').click();await page.waitForFunction(()=>!document.querySelector('#invite-save').disabled);await readable(page,['#invite-save']);await page.locator('#invite-save').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('Invitation recorded'))
  // Membership success notice precedes the separately awaited module/invitation reads.
  await page.waitForFunction(()=>document.querySelector('#invitations').textContent.includes('outsider · developer · pending'))
  assert.match(await page.locator('#invitations').textContent(),/outsider · developer · pending/)
  await page.waitForFunction(()=>!document.querySelector('#reload').disabled)
  await readable(page,['.notice','#status','#workspace','#reload','#panel h2','#state','#members li span','#members button','#operator-panel h2','#create-open','#module-status','#modules button','#invitation-status'])
  const beforeAction=writes.length;await page.locator('#control-spaces button').first().click();await readable(page,['#action-title','#action-description','#action-save','#action-cancel']);await page.locator('#action-cancel').click();assert.equal(writes.length,beforeAction)
  await page.screenshot({path:path.join(folder,'manage.png'),fullPage:true})
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(folder,'manage-mobile.png'),fullPage:true});await page.setViewportSize({width:1440,height:1050})
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
  const routePath='/api/workspaces/alpha/responsibility/source/sample/project/config';let seen,release,finished;const started=new Promise(r=>seen=r),barrier=new Promise(r=>release=r),done=new Promise(r=>finished=r)
  let oldCandidates=0;const listener=request=>{if(request.url().includes('/alpha/responsibility/')&&request.url().includes('/candidates?'))oldCandidates++};page.on('request',listener)
  router.set(routePath,async route=>{const response=await route.fetch();seen();await barrier;await route.fulfill({response});finished()})
  await page.locator('#reload').click();await within(started,'responsibility stale-scope request barrier');await scope('beta');await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('unavailable'));const oldCount=oldCandidates
  release();await within(done,'responsibility stale-scope response release');await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));assert.equal(oldCandidates,oldCount);assert.equal(await page.locator('#panel').isVisible(),false)
  page.off('request',listener);router.remove(routePath);await scope('alpha');await revision(3)
  // An accepted write with a late response cannot restore a page after pagehide/recheck.
  let putSeen,putRelease,putFinished;const putStarted=new Promise(r=>putSeen=r),putBarrier=new Promise(r=>putRelease=r),putDone=new Promise(r=>putFinished=r)
  router.set(routePath,async route=>{if(route.request().method()!=='PUT')return route.continue();const response=await route.fetch();putSeen();await putBarrier;await route.fulfill({response});putFinished()})
  await page.locator('#edit').click();await page.locator('[data-entry="name"]').fill('Saved while page was hidden');await page.locator('#save').click();await page.locator('#confirm-submit').click();await within(putStarted,'responsibility accepted-write request barrier')
  await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide')));assert.equal(await page.locator('#app-principal').textContent(),'Not verified');assert.equal(await page.locator('#editor-fields fieldset').count(),0);assert.equal(await page.locator('#workspace').inputValue(),'')
  await page.locator('#app-recheck').click();await page.waitForFunction(()=>document.querySelector('#app-principal').textContent==='admin'&&!document.querySelector('#session').hidden);await scope('alpha');await revision(4)
  putRelease();await within(putDone,'responsibility accepted-write response release');await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));await revision(4);assert.match(await page.locator('#configuration').textContent(),/Saved while page was hidden/);router.remove(routePath)
  await within(router.drain(),'scenario route drain');await within(context.unrouteAll({behavior:'wait'}),'scenario route removal')
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
  const statusFixture=await fixture(false,'module-status')
  await exerciseModuleStatus({browser,fixture:statusFixture,folder,readable})
  await exerciseAdminTransfer({browser,transport:'cookie',folder})
 }catch(error){failure=error}
 finally{
  const failures=failure?[failure]:[]
  // Cleanup errors must neither replace the original scenario failure nor skip
  // another child. A bounded/forced cleanup still fails the whole acceptance.
  try{if(browser)await within(browser.close(),'browser close')}catch(error){failures.push(error)}
  try{await stopSyntheticFixtures(children)}catch(error){failures.push(error)}
  try{fs.rmSync(temp,{recursive:true,force:true})}catch(error){failures.push(error)}
  if(failures.length)for(const diagnostic of diagnostics){
   if(diagnostic.stderr)console.error(`[${diagnostic.label} stderr tail]\n${diagnostic.stderr}`)
  }
  if(failures.length===1)throw failures[0]
  if(failures.length)throw new AggregateError(failures,'Unified UI scenario or cleanup failed')
 }
 console.log('Unified UI acceptance passed: real cookie-only login, management, invitations, responsibility CAS, CSRF, workspace/session revocation, closed provider, member module-status observations and thirteen original responsive screenshots with contrast checks, plus the awaited administrator-transfer journey and two new screenshots. Synthetic HTTPS only.')
}
function reportFailure(error){
 console.error(error?.stack||String(error))
 if(error instanceof AggregateError)for(const nested of error.errors)reportFailure(nested)
 if(error?.cause)reportFailure(error.cause)
}
main().catch(error=>{reportFailure(error);process.exitCode=1})
