// Awaited by both existing browser entrypoints. Every write below reaches the
// actual temporary TLS server. Delivery faults are deliberately AFTER commit.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),readline=require('node:readline')
const {spawn,execFileSync}=require('node:child_process')
const {createScenarioRouter}=require('./scenario-router.cjs')
const {stopSyntheticFixtures}=require('../../application/tests/fixture-process.cjs')
const ACTOR='actor-'+'🚀'.repeat(188)+'\0\x7f\\\ufeffXY'
const DISPLAY='actor-'+'🚀'.repeat(188)+'\\u{0}\\u{7F}\\\\\\u{FEFF}XY'
const ROOT=path.resolve(__dirname,'../../..')
const transferPath='/api/workspaces/alpha/admin-transfers'
const selection=target=>transferPath+'/selections/'+target
const within=async(p,label)=>{let timer;try{return await Promise.race([p,new Promise((_,reject)=>timer=setTimeout(()=>reject(Error(`Admin transfer ${label} timed out`)),15000))])}finally{clearTimeout(timer)}}
const settle=page=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))))
const state=(page,value)=>page.waitForFunction(value=>document.querySelector('#transfer-dialog')?.dataset.state===value,value)
const empty=async page=>{assert.equal(await page.locator('#transfer-dialog').isVisible(),false);for(const id of ['transfer-selection','transfer-reference'])assert.equal(await page.locator('#'+id).textContent(),'')}

// Sample the actual styled warning on its own composited dialog surface. A
// gradient, group opacity or non-opaque backing requires a new measurement,
// not an assumed white background. This probe neither scrolls nor clicks.
async function checkUnknownMobile(page,transport){
 const evidence=await page.locator('#transfer-status').evaluate(warning=>{
  const dialog=warning.closest('dialog'),layers=[]
  if(!dialog?.open||dialog.dataset.state!=='unknown'||!warning.getClientRects().length)throw Error('Unknown warning is not rendered.')
  for(let node=warning;node;node=node.parentElement){
   const style=getComputedStyle(node)
   if(style.visibility!=='visible'||style.backgroundImage!=='none'||Number(style.opacity)!==1||style.filter!=='none'||style.mixBlendMode!=='normal')throw Error('Unsupported warning paint; inspect the actual rendered colors.')
   layers.push(style.backgroundColor)
   if(node===dialog)break
  }
  const canvas=document.createElement('canvas');canvas.width=canvas.height=1
  const ctx=canvas.getContext('2d',{willReadFrequently:true})
  const paint=color=>{ctx.fillStyle=color;ctx.fillRect(0,0,1,1)}
  const pixel=()=>Array.from(ctx.getImageData(0,0,1,1).data)
  layers.reverse().forEach(paint)
  const background=pixel()
  if(background[3]!==255)throw Error('Warning backing is not opaque within the dialog.')
  paint(getComputedStyle(warning).color);const foreground=pixel()
  const luminance=rgba=>rgba.slice(0,3).reduce((sum,v,i)=>{const c=v/255;return sum+[.2126,.7152,.0722][i]*(c<=.04045?c/12.92:((c+.055)/1.055)**2.4)},0)
  const [dark,light]=[luminance(background),luminance(foreground)].sort((a,b)=>a-b)
  const box=dialog.getBoundingClientRect(),left=box.left+dialog.clientLeft,top=box.top+dialog.clientTop
  const clip={left:Math.max(0,left),top:Math.max(0,top),right:Math.min(innerWidth,left+dialog.clientWidth),bottom:Math.min(innerHeight,top+dialog.clientHeight)}
  const inside=r=>r.left>=clip.left&&r.right<=clip.right&&r.top>=clip.top&&r.bottom<=clip.bottom
  const buttons=['transfer-confirm','transfer-close'].map(id=>{
   const el=document.getElementById(id),r=el.getBoundingClientRect(),hit=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2)
   return {id,left:r.left,top:r.top,right:r.right,bottom:r.bottom,width:r.width,height:r.height,inside:inside(r),actionable:!el.disabled&&el.contains(hit)}
  })
  return {width:innerWidth,height:innerHeight,scrollTop:dialog.scrollTop,clip,warningInside:inside(warning.getBoundingClientRect()),foreground,background,contrast:(light+.05)/(dark+.05),buttons}
 })
 const detail=JSON.stringify(evidence)
 assert.deepEqual([evidence.width,evidence.height,evidence.scrollTop],[390,844,0],detail)
 assert.ok(evidence.warningInside&&evidence.contrast>=4.5,'Unknown warning must be fully visible with at least 4.5:1 contrast: '+detail)
 for(const button of evidence.buttons)assert.ok(button.inside&&button.actionable&&button.width>=44&&button.height>=44,'Retry and Close must be fully on screen and hit-testable at 44px or larger: '+detail)
 console.log(`Admin transfer ${transport} mobile visual measurements: ${detail}`)
}

async function exerciseAdminTransfer({browser,transport,folder}){
 assert.ok(['bearer','cookie'].includes(transport));assert.equal(Array.from(ACTOR).length,200)
 const temp=fs.mkdtempSync(path.join(os.tmpdir(),'ms-collab-transfer-cert-'))
 const children=[],contexts=[],routers=[],releases=new Set(),diagnostics=[],lines=[]
 const errors=[],external=[],dialogs=[],branches=[]
 let failure
 const cleanup=[]
 async function fixture(){
  const module=transport==='cookie'?'packages.application_ui.tests.browser_server':'packages.workspace_ui.tests.browser_server'
  const child=spawn('python3',['-m',module,path.join(temp,'cert.pem'),path.join(temp,'key.pem'),'admin-transfer'],{
   cwd:ROOT,env:{...process.env,MS_COLLAB_SYNTHETIC_APPLICATION_UI:'1',MS_COLLAB_SYNTHETIC_ADMIN_TRANSFER:'1'},stdio:['ignore','pipe','pipe']})
  children.push(child)
  const diagnostic={label:`${transport} transfer fixture ${children.length}`,stderr:''};diagnostics.push(diagnostic)
  child.stderr.setEncoding('utf8');child.stderr.on('data',chunk=>{diagnostic.stderr=(diagnostic.stderr+chunk).slice(-8192)})
  const reader=readline.createInterface({input:child.stdout});lines.push(reader)
  return within(new Promise((resolve,reject)=>{
   reader.once('line',line=>{try{const result=JSON.parse(line);assert.match(result.origin,/^https:\/\/127\.0\.0\.1:\d+$/);resolve(result)}catch(error){reject(error)}})
   child.once('error',reject);child.once('exit',()=>reject(Error(diagnostic.label+' stopped during startup')))
  }),'fixture startup')
 }
 async function contextFor(fixture){
  // 127.0.0.1 cookies ignore ports. This context MUST NOT reuse the original
  // browser entrypoint's context, another fixture's context, or the reader's.
  const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1440,height:1050}});contexts.push(context)
  await context.addInitScript(()=>{
   const original=window.fetch.bind(window);window.__transferCompleted=[]
   window.fetch=async(input,options={})=>{const {signal,...rest}=options;const response=await original(input,rest);window.__transferCompleted.push(String(input));return response}
   window.__transferDocument=Array.from(crypto.getRandomValues(new Uint32Array(4)),value=>value.toString(16).padStart(8,'0')).join('')
   window.__transferPageShows=[];addEventListener('pageshow',event=>window.__transferPageShows.push({persisted:event.persisted}))
  })
  const calls=[],router=createScenarioRouter({origin:fixture.origin,onExternal:()=>external.push('unexpected routed origin')});routers.push({context,router})
  await context.route('**/api/**',route=>router.handle(route))
  function watch(page){
   page.on('pageerror',error=>errors.push(error.message));page.on('dialog',async dialog=>{dialogs.push(dialog.message());await dialog.dismiss()})
   page.on('request',request=>{
    const url=new URL(request.url()),headers=request.headers()
    if(![fixture.origin,...(fixture.provider?[new URL(fixture.provider).origin]:[])].includes(url.origin))external.push(url.origin)
    if(transport==='cookie')assert.ok(!headers.authorization,'Cookie journey must not forge a bearer actor')
    assert.ok(!url.search.includes('synthetic-transfer'))
    calls.push({path:url.pathname,method:request.method(),body:request.postData(),key:headers['idempotency-key'],revision:headers['if-match'],proof:headers['if-admin-transfer-match']})
   })
  }
  const page=await context.newPage();watch(page)
  return {context,page,router,calls,watch,fixture}
 }
 const ready=page=>page.waitForFunction(()=>!document.querySelector('#transfer-open').hidden&&document.querySelector('#workspace').value==='alpha'&&!document.querySelector('#module-panel').hidden&&!document.querySelector('#invitation-panel').hidden&&/^\d+ recent invitations \(/.test(document.querySelector('#invitation-status').textContent))
 async function login(journey,{reader=false,management=true}={}){
  const {page,fixture}=journey
  if(transport==='cookie'){
   if(reader){
    // A second genuine bound-code flow, at a fixed synthetic provider endpoint.
    // No cookie copy, principal header, product login option or recovery API.
    await page.route(fixture.provider+'?**',route=>{const url=new URL(route.request().url());url.pathname='/synthetic-provider-reader';return route.continue({url:url.toString()})})
   }
   await page.goto(fixture.origin+'/');await page.waitForFunction(()=>!document.querySelector('#app-sign-in').disabled)
   await page.locator('#app-sign-in').click()
   await page.waitForFunction(expected=>document.querySelector('#app-principal')?.textContent===expected&&!document.querySelector('#app-data').hidden,reader?'reader':DISPLAY)
   if(management){await page.locator('nav a[href="/manage/"]').click();await ready(page)}
  }else{
   await page.goto(fixture.origin+'/manage/')
   if(management){await page.locator('#credential').fill(reader?'synthetic-transfer-reader':'synthetic-transfer-actor');await page.locator('#connect button').click();await ready(page)}
  }
 }
 async function reconnect(journey){
  const {page}=journey
  if(transport==='cookie'){await page.locator('#app-recheck').click();await ready(page)}
  else {await page.locator('#credential').fill('synthetic-transfer-actor');await page.locator('#connect button').click();await ready(page)}
 }
 const writes=journey=>journey.calls.filter(call=>call.path===transferPath&&call.method==='POST')
 const adminReads=journey=>journey.calls.filter(call=>/^\/api\/workspaces\/alpha\/(members|modules|module-catalog|invitations)$/.test(call.path)&&call.method==='GET')
 const authReads=journey=>journey.calls.filter(call=>call.path==='/auth/session').length
 async function review(page,target='reader'){
  if(!await page.locator('#transfer-dialog').isVisible())await page.locator('#transfer-open').click()
  await page.locator('#transfer-target').selectOption(target);assert.equal(await page.locator('#transfer-confirm').isDisabled(),true)
  await page.locator('#transfer-review').click();await state(page,'reviewed')
  assert.equal(await page.locator('#transfer-confirm').isEnabled(),true)
  const copy=await page.locator('#transfer-selection').textContent()
  assert.ok(copy.includes(DISPLAY));assert.ok(copy.includes(`Replacement: ${target}`));assert.match(copy,/revision 1/);assert.match(copy,/you become developer and lose management access/i)
 }
 async function control(journey,name){
  return journey.page.evaluate(async name=>{const response=await fetch('/synthetic/admin-transfer/'+name,{method:'POST'});return {status:response.status,body:await response.json()}},name)
 }
 async function snapshot(journey){const value=await control(journey,'status');assert.equal(value.status,200);return value.body}
 function hold(journey,target,status=200){
  let seen,rejectStart,release,done,rejectDone
  const started=new Promise((resolve,reject)=>{seen=resolve;rejectStart=reject}),gate=new Promise(resolve=>release=resolve),finished=new Promise((resolve,reject)=>{done=resolve;rejectDone=reject})
  started.catch(()=>{});finished.catch(()=>{});releases.add(release)
  journey.router.set(target,async route=>{try{
   const response=await route.fetch();assert.equal(response.status(),200)
   // Do not gate WSGI processing: the actual API request has already completed.
   await response.body();seen();await gate
   if(status===200)await route.fulfill({response})
   else await route.fulfill({status,contentType:'application/json',body:'{"error":"authentication_required"}'})
   done()
  }catch(error){rejectStart(error);rejectDone(error);throw error}})
  return {started,finished,release(){release();releases.delete(release)}}
 }
 async function independentRoles(fixture){
  const reader=await contextFor(fixture);await login(reader,{reader:true,management:false})
  const response=await reader.context.request.get(fixture.origin+'/api/workspaces/alpha/members',transport==='bearer'?{headers:{Authorization:'Bearer synthetic-transfer-reader'}}:{})
  assert.equal(response.status(),200);const body=await response.json()
  const actor=body.data.find(row=>row.principal_id===ACTOR),replacement=body.data.find(row=>row.principal_id==='reader')
  assert.deepEqual([actor.role,actor.revision,replacement.role,replacement.revision],['developer',2,'admin',2])
  // This independently proves current roles only. The retained request identity
  // is proven later by exact historical replay, never by these observations.
 }
 try{
  execFileSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-days','1','-keyout',path.join(temp,'key.pem'),'-out',path.join(temp,'cert.pem'),'-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'],{stdio:'ignore'})
  fs.mkdirSync(folder,{recursive:true})
  const normal=await contextFor(await fixture());await login(normal)
  const {page,router}=normal
  assert.equal(writes(normal).length,0)
  assert.ok((await page.locator('#members').textContent()).includes(DISPLAY));assert.equal(await page.locator('#members img').count(),0)
  const nonRoute=await page.locator('#members li').filter({hasText:'actor-'}).locator('button').all()
  assert.ok(nonRoute.length>0);for(const button of nonRoute)assert.equal(await button.isDisabled(),true)
  const beforeUnsafe=normal.calls.length
  await nonRoute[0].evaluate(button=>button.dispatchEvent(new Event('click')))
  assert.equal(await page.locator('#editor').isVisible(),false)
  await page.locator('#transfer-open').click()
  await page.evaluate(actor=>{
   const select=document.querySelector('#transfer-target');select.value=actor
   select.dispatchEvent(new Event('input'));select.dispatchEvent(new Event('change'))
   document.querySelector('#transfer-review').dispatchEvent(new Event('click'))
   document.querySelector('#transfer-form').dispatchEvent(new Event('submit',{cancelable:true}))
  },ACTOR)
  await settle(page);assert.equal(normal.calls.length,beforeUnsafe,'programmatic non-route targeting must not construct a request')
  await page.locator('#transfer-target').selectOption('reader')
  // Same session epoch: old 200 and synthetic late 401 must neither clear the
  // real session nor release a newer review's busy ownership. Abort is ignored.
  for(const statusCode of [200,401]){
   await page.locator('#transfer-target').selectOption('reader')
   const old=hold(normal,selection('reader'),statusCode),beforeAuth=authReads(normal)
   const beforeCompleted=await page.evaluate(target=>window.__transferCompleted.filter(value=>value===target).length,selection('reader'))
   await page.locator('#transfer-review').click();await within(old.started,'old completed selection')
   await page.locator('#transfer-target').selectOption('second')
   const newer=hold(normal,selection('second'))
   await page.locator('#transfer-review').click();await within(newer.started,'new completed selection')
   old.release();await within(old.finished,'old selection delivery')
   await page.waitForFunction(({target,count})=>window.__transferCompleted.filter(value=>value===target).length>count,{target:selection('reader'),count:beforeCompleted});await settle(page)
   assert.equal(await page.locator('#transfer-dialog').getAttribute('data-state'),'reviewing')
   assert.equal(await page.locator('#transfer-selection').textContent(),'');assert.equal(await page.locator('#transfer-reference').textContent(),'')
   assert.equal(await page.locator('#transfer-review').isDisabled(),true);assert.equal(await page.locator('#transfer-confirm').isDisabled(),true)
   assert.equal(authReads(normal),beforeAuth,'stale reply must not recheck/change session epoch')
   if(transport==='cookie')assert.equal(await page.locator('#app-principal').textContent(),DISPLAY)
   else assert.equal(await page.locator('#session').isVisible(),true)
   newer.release();await within(newer.finished,'new selection delivery');await state(page,'reviewed')
   assert.match(await page.locator('#transfer-selection').textContent(),/Replacement: second/)
   router.remove(selection('reader'));router.remove(selection('second'))
  }
  assert.equal(writes(normal).length,0,'review and ignored late replies never submit')
  await page.locator('#transfer-close').click();await empty(page)
  // Actual Back evidence, not a fabricated BFCache claim.
  await review(page)
  const oldDocument=await page.evaluate(()=>window.__transferDocument)
  await page.goto('about:blank');await page.goBack();await page.locator('#transfer-dialog').waitFor({state:'attached'})
  const observed=await page.evaluate(()=>({document:window.__transferDocument,shows:window.__transferPageShows}))
  if(observed.document===oldDocument){assert.ok(observed.shows.some(event=>event.persisted));branches.push('actual Back: persisted document')}
  else {assert.notEqual(observed.document,oldDocument);assert.ok(observed.shows.some(event=>!event.persisted));branches.push('actual Back: fresh document')}
  await empty(page)
  if(transport==='cookie'){
   // A fresh document may inspect automatically; BFCache restoration is closed.
   if(observed.document===oldDocument)await reconnect(normal);else await ready(page)
  }else await reconnect(normal)
  await review(page)
  // Separately labeled handler coverage, not evidence that actual Back used it.
  await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true})))
  await empty(page);branches.push('synthetic pageshow.persisted handler cleared')
  await reconnect(normal)
  // A genuinely current 401 deliberately clears private state and the tuple.
  await page.locator('#transfer-open').click();await page.locator('#transfer-target').selectOption('reader')
  const current=hold(normal,selection('reader'),401)
  await page.locator('#transfer-review').click();await within(current.started,'current 401 completed source')
  current.release();await within(current.finished,'current 401 delivery')
  await page.waitForFunction(()=>document.querySelector('#session').hidden)
  await empty(page);router.remove(selection('reader'))
  if(transport==='cookie')assert.equal(await page.locator('#app-principal').textContent(),'Not verified')
  assert.match(await page.locator('#status').textContent(),/may already have completed/i)
  await reconnect(normal)
  await review(page)
  await page.screenshot({path:path.join(folder,'admin-transfer-review-desktop.png'),fullPage:true})
  const beforeNormal=await snapshot(normal),readsBefore=adminReads(normal).length
  await page.locator('#transfer-confirm').click();await page.waitForFunction(()=>document.querySelector('#transfer-outcome').textContent.includes('committed'))
  await empty(page);assert.equal(await page.locator('#members').textContent(),'');assert.equal(await page.locator('#modules').textContent(),'');assert.equal(await page.locator('#invitations').textContent(),'')
  await page.waitForFunction(()=>document.querySelector('#workspace').options.length===4&&document.querySelector('#workspace').value==='');await settle(page)
  assert.deepEqual(await page.locator('#workspace option').evaluateAll(options=>options.map(option=>option.value)),['','alpha','beta','commons'])
  assert.equal(adminReads(normal).length,readsBefore,'self-demotion refresh must not reread admin-only lists')
  assert.equal(writes(normal).length,1)
  const afterNormal=await snapshot(normal)
  assert.deepEqual([afterNormal.actor_role,afterNormal.replacement_role,afterNormal.receipts,afterNormal.transfer_audits],['developer','admin',1,2]);assert.equal(afterNormal.audits,beforeNormal.audits+2)
  await independentRoles(normal.fixture)
  // Another authorized scope is accessible only by deliberate selection.
  const commonsReads=Promise.all(['members','modules','module-catalog','invitations'].map(suffix=>page.waitForResponse(response=>new URL(response.url()).pathname==='/api/workspaces/commons/'+suffix&&response.request().method()==='GET')))
  await page.locator('#workspace').selectOption('commons')
  for(const response of await commonsReads)assert.equal(response.status(),200,'explicit commons selection reauthorizes each management read')
  await page.waitForFunction(()=>document.querySelector('#workspace').value==='commons'&&!document.querySelector('#panel').hidden&&!document.querySelector('#module-panel').hidden&&document.querySelector('#module-status').textContent.includes('installed modules')&&!document.querySelector('#invitation-panel').hidden&&/^\d+ recent invitations \(/.test(document.querySelector('#invitation-status').textContent))
  assert.ok((await page.locator('#members').textContent()).includes(DISPLAY));assert.ok(await page.locator('#modules li').count()>0)

  // Fresh server/context: a real successful commit whose 200 is not delivered.
  const lost=await contextFor(await fixture());await login(lost)
  // A second tab shares only the actual transport/session, never a local tuple.
  const observer=await lost.context.newPage();lost.watch(observer)
  await observer.goto(lost.fixture.origin+'/manage/')
  if(transport==='bearer'){await observer.locator('#credential').fill('synthetic-transfer-actor');await observer.locator('#connect button').click()}
  await ready(observer);await empty(observer)
  const initial=await snapshot(lost);let committedResponse
  lost.router.set(transferPath,async route=>{
   assert.equal(route.request().method(),'POST')
   const response=await route.fetch();assert.equal(response.status(),200);committedResponse=await response.json()
   await route.abort('failed') // real server completed; this is NOT rollback
  })
  await review(lost.page);await lost.page.locator('#transfer-confirm').click();await state(lost.page,'unknown')
  await within(lost.router.drain(),'dropped-response route drain');lost.router.remove(transferPath)
  const original={...writes(lost)[0]};assert.ok(original.key&&original.proof&&original.revision);assert.equal(original.body,'{"replacement_id":"reader","replacement_revision":"1"}')
  const unknownCopy=await lost.page.locator('#transfer-status').textContent();assert.match(unknownCopy,/Result unknown/);assert.match(unknownCopy,/Current roles alone do not identify this request/)
  const reference=await lost.page.locator('#transfer-reference').textContent();assert.ok(reference.includes(original.key));assert.ok(reference.includes(DISPLAY));assert.ok(!reference.includes('\0'))
  const committed=await snapshot(lost)
  assert.deepEqual([committed.actor_role,committed.replacement_role,committed.receipts,committed.transfer_audits],['developer','admin',1,2]);assert.equal(committed.audits,initial.audits+2)
  await independentRoles(lost.fixture)
  const observerDenial=observer.waitForResponse(response=>new URL(response.url()).pathname==='/api/workspaces/alpha/members'&&response.request().method()==='GET')
  await observer.locator('#reload').click();assert.equal((await observerDenial).status(),403)
  const deniedCopy=transport==='cookie'?'Your current permissions do not allow this change.':'You do not have the required administrator permission.'
  await observer.waitForFunction(copy=>document.querySelector('#panel').hidden&&document.querySelector('#status').textContent===copy,deniedCopy)
  await empty(observer);assert.equal(await observer.locator('#members').textContent(),'')
  assert.equal(await lost.page.locator('#transfer-reference').textContent(),reference,'a sibling tab must not acquire or clear the local retry tuple')
  const denied=lost.page.waitForResponse(response=>new URL(response.url()).pathname===transferPath&&response.request().method()==='POST')
  await lost.page.locator('#transfer-confirm').click();const deniedResponse=await denied;assert.equal(deniedResponse.status(),403)
  await state(lost.page,'unknown');assert.deepEqual(writes(lost)[1],original)
  assert.equal(await lost.page.locator('#transfer-reference').textContent(),reference)
  assert.equal(await lost.page.locator('#transfer-status').textContent(),unknownCopy)
  for(const id of ['members','modules','invitations'])assert.equal(await lost.page.locator('#'+id).textContent(),'')
  assert.equal(await lost.page.locator('#panel').isVisible(),false)
  assert.deepEqual(await snapshot(lost),committed,'current 403 creates no second receipt/audit pair')
  await lost.page.setViewportSize({width:390,height:844})
  assert.ok(await lost.page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth&&document.querySelector('#transfer-dialog').scrollWidth<=document.querySelector('#transfer-dialog').clientWidth))
  await lost.page.evaluate(()=>{document.querySelector('#transfer-dialog').scrollTop=0});await settle(lost.page)
  assert.equal(await lost.page.locator('#transfer-confirm').textContent(),'Retry original request')
  await checkUnknownMobile(lost.page,transport)
  await lost.page.screenshot({path:path.join(folder,'admin-transfer-unknown-mobile.png'),fullPage:true})
  // Permission setup in this TEST ONLY: fixed new-admin policy edit. Neither UI
  // nor product API recommends re-granting privileges to recover an unknown key.
  const promoted=await control(lost,'repromote');assert.equal(promoted.status,200)
  assert.deepEqual([promoted.body.actor_role,promoted.body.actor_revision,promoted.body.replacement_revision],['admin',3,2])
  assert.equal(promoted.body.receipts,1);assert.equal(promoted.body.transfer_audits,2);assert.equal(promoted.body.audits,committed.audits+1)
  const replay=lost.page.waitForResponse(response=>new URL(response.url()).pathname===transferPath&&response.request().method()==='POST')
  await lost.page.locator('#transfer-confirm').click();const replayResponse=await replay
  assert.equal(replayResponse.status(),200);assert.deepEqual(await replayResponse.json(),committedResponse)
  await lost.page.waitForFunction(()=>document.querySelector('#transfer-outcome').textContent.includes('committed'))
  assert.deepEqual(writes(lost)[2],original);assert.equal(writes(lost).length,3)
  assert.deepEqual(await snapshot(lost),promoted.body,'history replay must leave today\'s admin role/revision and all history unchanged')
  await empty(lost.page)
  for(const journey of [normal,lost])assert.deepEqual(await journey.page.evaluate(()=>[localStorage.length,sessionStorage.length]),[0,0])
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);assert.deepEqual(dialogs,[])
 }catch(error){failure=error}
 finally{
  // Release every browser-delivery gate before draining routes/contexts/children.
  // A forced or timed-out cleanup remains a failure and never prints PASS.
  for(const release of releases)release();releases.clear()
  for(const {context,router} of routers){
   try{await within(router.drain(),'route cleanup')}catch(error){cleanup.push(error)}
   try{await within(context.unrouteAll({behavior:'wait'}),'route removal')}catch(error){cleanup.push(error)}
  }
  for(const context of contexts)try{await within(context.close(),'context close')}catch(error){cleanup.push(error)}
  try{await stopSyntheticFixtures(children)}catch(error){cleanup.push(error)}
  for(const reader of lines)reader.close()
  try{fs.rmSync(temp,{recursive:true,force:true});assert.equal(fs.existsSync(temp),false)}catch(error){cleanup.push(error)}
 }
 const failures=[...(failure?[failure]:[]),...cleanup]
 if(failures.length){for(const diagnostic of diagnostics)if(diagnostic.stderr)console.error(`[${diagnostic.label} stderr tail]\n${diagnostic.stderr}`);if(failures.length===1)throw failures[0];throw new AggregateError(failures,'Admin transfer scenario or cleanup failed')}
 console.log(`Admin transfer ${transport} PASS after gate/context/child/temp cleanup: real TLS commit; dropped delivery → unknown → current 403 → synthetic legitimate re-promotion → exact historical replay; ${branches.join('; ')}. Two synthetic screenshots require exact-head visual review.`)
}
module.exports={exerciseAdminTransfer}
