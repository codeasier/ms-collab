// Invoked and awaited by browser.cjs. Real isolated Cookie/HTTPS status reads;
// intercepted late errors test transport ownership, not backend lifecycle policy.
const assert=require('node:assert/strict'),path=require('node:path')
const {createScenarioRouter}=require('../../workspace-admin/tests/scenario-router.cjs')
const within=async(p,label)=>{let timer;try{return await Promise.race([p,new Promise((_,reject)=>timer=setTimeout(()=>reject(Error(`Module status ${label} timed out`)),15000))])}finally{clearTimeout(timer)}}
const settle=page=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))))
const statusPath=(workspace,key='pipelines')=>`/api/workspaces/${encodeURIComponent(workspace)}/modules/${encodeURIComponent(key)}`
async function exerciseModuleStatus({browser,fixture,folder,readable}){
 // Cookies are host-scoped, not port-scoped. Never use the original journey's
 // BrowserContext for this other 127.0.0.1 fixture. Only these two tabs share it.
 const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1440,height:1050}})
 const calls=[],errors=[],external=[],dialogs=[],releases=new Set(),rules=new Set()
 let failure
 const router=createScenarioRouter({origin:fixture.origin,onExternal:()=>external.push('unexpected routed origin')})
 const set=(key,handler)=>{rules.add(key);router.set(key,handler)},remove=key=>{rules.delete(key);router.remove(key)}
 const watch=page=>{
  page.on('pageerror',error=>errors.push(error.message));page.on('dialog',async dialog=>{dialogs.push(dialog.message());await dialog.dismiss()})
  page.on('request',request=>{const url=new URL(request.url()),headers=request.headers();assert.ok(!headers.authorization);if(![fixture.origin,new URL(fixture.provider).origin].includes(url.origin))external.push(url.origin);calls.push({path:url.pathname,method:request.method()})})
 }
 const statusCalls=()=>calls.filter(call=>/^\/api\/workspaces\/[^/]+\/modules\/[^/]+$/.test(call.path)&&call.method==='GET').length
 const apiCalls=()=>calls.filter(call=>call.path.startsWith('/api/'))
 const authChecks=()=>calls.filter(call=>call.path==='/auth/session').length
 const ready=page=>page.waitForFunction(()=>document.querySelector('#app-principal').textContent==='reader'&&!document.querySelector('#module-status').hidden)
 const empty=async page=>{assert.equal(await page.locator('#module-status-result').isVisible(),false);for(const key of ['workspace','key','state','revision'])assert.equal(await page.locator('#module-status-result-'+key).textContent(),'')}
 const fields=async(page,workspace,key='pipelines')=>{const before=apiCalls().length;await page.locator('#module-status-workspace').selectOption(workspace);await page.locator('#module-status-key').fill(key);await settle(page);assert.equal(apiCalls().length,before,'field edits must not request private data');await empty(page)}
 const result=async(page,workspace,key,state,revision)=>{
  await page.waitForFunction(expected=>!document.querySelector('#module-status-result').hidden&&Object.entries(expected).every(([key,value])=>document.querySelector('#module-status-result-'+key).textContent===String(value)),{workspace,key,state,revision})
  assert.equal(await page.locator('#module-status-check').isEnabled(),true)
 }
 const read=async(page,workspace,key,state,revision)=>{await fields(page,workspace,key);const before=apiCalls().length;await page.locator('#module-status-check').click();await result(page,workspace,key,state,revision);assert.deepEqual(apiCalls().slice(before),[{path:statusPath(workspace,key),method:'GET'}],'exact read has no list/catalog/business fallback')}
 const reject=async(page,workspace,key)=>{await fields(page,workspace,key);const before=apiCalls().length;await page.locator('#module-status-check').click();await page.waitForFunction(()=>!document.querySelector('#module-status-check').disabled&&document.querySelector('#module-status-message').textContent.length>0);await empty(page);assert.deepEqual(apiCalls().slice(before),[{path:statusPath(workspace,key),method:'GET'}],'rejection has no fallback');return page.locator('#module-status-message').textContent()}
 const api=(page,target)=>page.evaluate(async target=>{const response=await fetch(target,{cache:'no-store'});return {status:response.status,body:await response.json()}},target)
 const control=async(page,action)=>{const status=await page.evaluate(async action=>(await fetch('/synthetic/module-status/'+action,{method:'POST'})).status,action);assert.equal(status,200,action)}
 const recheck=async page=>{const before=statusCalls();await page.locator('#app-recheck').click();await ready(page);await empty(page);assert.equal(await page.locator('#module-status-workspace').inputValue(),'');assert.equal(await page.locator('#module-status-key').inputValue(),'');assert.equal(statusCalls(),before,'Recheck never repeats a module query')}
 const hold=(target,status=200)=>{
  let seen,startFailure,release,done,finishFailure
  const started=new Promise((resolve,reject)=>{seen=resolve;startFailure=reject}),gate=new Promise(resolve=>release=resolve),finished=new Promise((resolve,reject)=>{done=resolve;finishFailure=reject})
  // A prior failure can bypass the explicit wait; cleanup still drains the route.
  started.catch(()=>{});finished.catch(()=>{});releases.add(release)
  set(target,async route=>{try{
   const response=await route.fetch();assert.equal(response.status(),200)
   seen();await gate // Actual WSGI request has completed; only browser delivery waits.
   if(status===200)await route.fulfill({response})
   else await route.fulfill({status,contentType:'application/json',body:JSON.stringify({error:status===401?'authentication_required':'synthetic_conflict'})})
   done()
  }catch(error){startFailure(error);finishFailure(error);throw error}})
  return {started,finished,release(){release();releases.delete(release)}}
 }
 try{
  await context.addInitScript(()=>{
   // Ignore AbortSignal deliberately. Correctness must depend on request ownership.
   const original=window.fetch.bind(window);window.fetch=(input,options={})=>{const {signal,...rest}=options;return original(input,rest)}
   // Non-secret document marker: init scripts also run where secure-context-only
   // randomUUID is unavailable. getRandomValues does not require a secure context.
   window.__moduleStatusDocument=Array.from(crypto.getRandomValues(new Uint32Array(4)),value=>value.toString(16).padStart(8,'0')).join('');window.__moduleStatusPageShows=[]
   addEventListener('pageshow',event=>window.__moduleStatusPageShows.push({persisted:event.persisted}))
  })
  await context.route('**/api/**',route=>router.handle(route))
  const page=await context.newPage();watch(page)
  await page.goto(fixture.origin+'/');await page.waitForFunction(()=>!document.querySelector('#app-sign-in').disabled)
  await page.locator('#app-sign-in').click();await ready(page)
  assert.equal(statusCalls(),0);assert.equal(await page.locator('input[type="password"]').count(),0)
  assert.deepEqual(await page.locator('#module-status-workspace option').evaluateAll(options=>options.map(option=>option.value)),['','alpha','beta','commons'])
  const links=await page.locator('nav a').evaluateAll(nodes=>nodes.map(node=>({href:node.getAttribute('href'),hidden:node.hidden})))
  await page.locator('#module-status-key').fill('pipelines');assert.equal(await page.locator('#module-status-check').isDisabled(),true)
  await page.evaluate(()=>document.querySelector('#module-status-form').dispatchEvent(new Event('submit',{cancelable:true})));await settle(page);assert.equal(statusCalls(),0)
  await read(page,'alpha','pipelines','disabled',3)
  const alphaData=await api(page,'/api/workspaces/alpha/pipelines/example-ci/build/runs');assert.equal(alphaData.status,409);assert.deepEqual(alphaData.body,{error:'module_unavailable'})
  await read(page,'beta','pipelines','disabling',2)
  assert.equal((await api(page,'/api/workspaces/beta/pipelines/example-ci/build/runs')).status,409)
  for(const suffix of ['members','modules','module-catalog'])assert.equal((await api(page,'/api/workspaces/alpha/'+suffix)).status,403)
  await read(page,'commons','pipelines','enabled',1)
  assert.equal((await api(page,'/api/workspaces/commons/pipelines/example-ci/build/runs')).status,200)
  assert.deepEqual(await page.locator('nav a').evaluateAll(nodes=>nodes.map(node=>({href:node.getAttribute('href'),hidden:node.hidden}))),links,'stored enabled must not alter navigation')
  // Long-key screenshots use a real installed module in still-accessible commons.
  const long='m'.repeat(200)
  await read(page,'commons',long,'disabled',1)
  const disclaimer=await page.locator('#module-status-disclaimer').textContent();assert.match(disclaimer,/Stored lifecycle/i);assert.match(disclaimer,/permission/i);assert.match(disclaimer,/provider/i)
  await readable(page,['#module-status h2','#module-status-workspace','#module-status-key','#module-status-check','#module-status-clear','#module-status-result-key','#module-status-result-state','#module-status-disclaimer'])
  await page.screenshot({path:path.join(folder,'module-status-desktop.png'),fullPage:true})
  await page.setViewportSize({width:390,height:844});await page.locator('#module-status-result').scrollIntoViewIfNeeded()
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth&&document.querySelector('#module-status').scrollWidth<=document.querySelector('#module-status').clientWidth))
  await readable(page,['#module-status-result-key','#module-status-result-state','#module-status-disclaimer'])
  await page.screenshot({path:path.join(folder,'module-status-mobile.png'),fullPage:true});await page.setViewportSize({width:1440,height:1050})
  // Old and new deliveries overlap in the SAME session epoch. A stale 401 cannot
  // invalidate that session, unlike the already-covered old-session-epoch case.
  for(const status of [200,409,401]){
   await fields(page,'alpha');const old=hold(statusPath('alpha'),status),beforeEpoch=authChecks()
   await page.locator('#module-status-check').click();await within(old.started,'old status fetch')
   const count=statusCalls();await page.evaluate(()=>document.querySelector('#module-status-form').dispatchEvent(new Event('submit',{cancelable:true})));await settle(page);assert.equal(statusCalls(),count)
   await fields(page,'beta');const newer=hold(statusPath('beta'))
   await page.locator('#module-status-check').click();await within(newer.started,'new status fetch')
   old.release();await within(old.finished,'old status delivery');await settle(page)
   await empty(page);assert.equal(await page.locator('#app-principal').textContent(),'reader');assert.equal(await page.locator('#module-status-check').isDisabled(),true);assert.equal(await page.locator('#module-status-clear').isEnabled(),true);assert.equal(authChecks(),beforeEpoch)
   newer.release();await within(newer.finished,'new status delivery');await result(page,'beta','pipelines','disabling',2)
   remove(statusPath('alpha'));remove(statusPath('beta'))
  }
  // Clear abandons a request. Even returning to the exact tuple uses new ownership.
  await fields(page,'alpha');const abandoned=hold(statusPath('alpha'))
  await page.locator('#module-status-check').click();await within(abandoned.started,'abandoned fetch')
  await page.locator('#module-status-clear').click();await empty(page);assert.equal(await page.locator('#module-status-workspace').inputValue(),'');assert.equal(await page.locator('#module-status-key').inputValue(),'')
  await fields(page,'alpha');const replacement=hold(statusPath('alpha'))
  await page.locator('#module-status-check').click();await within(replacement.started,'replacement fetch')
  abandoned.release();await within(abandoned.finished,'abandoned delivery');await settle(page);await empty(page);assert.equal(await page.locator('#module-status-check').isDisabled(),true)
  replacement.release();await within(replacement.finished,'replacement delivery');await result(page,'alpha','pipelines','disabled',3);remove(statusPath('alpha'))
  // An older full-instance backend and malformed/private errors do not fall back
  // to admin data or leak response/parser details into the result/message.
  set(statusPath('alpha'),async route=>{const response=await route.fetch(),value=await response.json();value.data.capabilities=['read'];await route.fulfill({response,json:value})})
  await reject(page,'alpha','pipelines');assert.ok(!(await page.locator('#module-status').textContent()).includes('capabilities'));remove(statusPath('alpha'))
  set(statusPath('alpha'),route=>route.fulfill({status:200,contentType:'application/json',body:'{"private":"STATUS_TEST_DO_NOT_DISPLAY"'}))
  await reject(page,'alpha','pipelines');assert.ok(!(await page.locator('#module-status').textContent()).includes('STATUS_TEST_DO_NOT_DISPLAY'));remove(statusPath('alpha'))
  await read(page,'alpha','pipelines','disabled',3);await recheck(page)
  await read(page,'alpha','pipelines','disabled',3)
  await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide')));await empty(page);assert.equal(await page.locator('#module-status-key').inputValue(),'');assert.equal(await page.locator('#app-principal').textContent(),'Not verified');await recheck(page)
  // Real Back may reload a document or restore it. Record the observed branch;
  // the separately dispatched persisted event below is handler evidence only.
  await read(page,'alpha','pipelines','disabled',3)
  const documentId=await page.evaluate(()=>window.__moduleStatusDocument),beforeBack=statusCalls()
  await page.goto('about:blank');await page.goBack();await page.locator('#module-status-form').waitFor({state:'attached'})
  await page.waitForFunction(()=>window.__moduleStatusPageShows?.length>0)
  const returned=await page.evaluate(()=>({id:window.__moduleStatusDocument,persisted:window.__moduleStatusPageShows.at(-1).persisted}))
  if(returned.id===documentId){assert.equal(returned.persisted,true);assert.equal(await page.locator('#app-principal').textContent(),'Not verified');await empty(page);await recheck(page);console.log('Module status actual Back branch: persisted document.')}
  else{assert.equal(returned.persisted,false);await ready(page);console.log('Module status actual Back branch: fresh document; real BFCache not observed.')}
  await empty(page);assert.equal(await page.locator('#module-status-key').inputValue(),'');assert.equal(await page.locator('#module-status-workspace').inputValue(),'');assert.equal(statusCalls(),beforeBack)
  await read(page,'alpha','pipelines','disabled',3);const beforePersisted=statusCalls()
  await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true})));await empty(page);assert.equal(await page.locator('#app-principal').textContent(),'Not verified');assert.equal(statusCalls(),beforePersisted);await recheck(page)
  console.log('Module status synthetic persisted-pageshow: handler clear/recheck evidence only.')
  // Same-origin tabs share cookies, never their selected workspace or observations.
  const second=await context.newPage();watch(second);await second.goto(fixture.origin+'/');await ready(second)
  await read(page,'alpha','pipelines','disabled',3);await read(second,'beta','pipelines','disabling',2)
  const tabHeld=hold(statusPath('alpha'));await page.locator('#module-status-check').click();await within(tabHeld.started,'first tab fetch')
  await recheck(second);await read(second,'beta','pipelines','disabling',2)
  tabHeld.release();await within(tabHeld.finished,'first tab delivery');await result(page,'alpha','pipelines','disabled',3);remove(statusPath('alpha'))
  await result(second,'beta','pipelines','disabling',2)
  await second.locator('#module-status-clear').click();await empty(second);await result(page,'alpha','pipelines','disabled',3)
  // Fixed synthetic state changes occur only after the normal/two-tab observations.
  await control(page,'empty-capabilities-commons');await read(page,'commons','pipelines','enabled',1)
  assert.equal((await api(page,'/api/workspaces/commons/pipelines/example-ci/build/runs')).status,403)
  const unknown=await reject(page,'beta','not-installed');assert.ok(!/not installed/i.test(unknown))
  await fields(page,'alpha');await control(page,'suspend-alpha');await page.locator('#module-status-check').click();await page.waitForFunction(()=>!document.querySelector('#module-status-check').disabled&&document.querySelector('#module-status-message').textContent.length>0);await empty(page)
  assert.equal((await api(page,statusPath('alpha'))).status,409)
  await control(page,'archive-beta');await read(page,'beta','pipelines','disabling',2)
  await control(page,'revoke-beta-reader')
  // No Recheck here: the old beta option is retained until the explicit request.
  assert.equal(await page.locator('#module-status-workspace').inputValue(),'beta');await page.locator('#module-status-check').click()
  await page.waitForFunction(()=>!document.querySelector('#module-status-check').disabled&&document.querySelector('#module-status-message').textContent.length>0);await empty(page)
  assert.equal(await page.locator('#module-status-message').textContent(),unknown);assert.equal((await api(page,statusPath('beta'))).status,404)
  await recheck(page);assert.deepEqual(await page.locator('#module-status-workspace option').evaluateAll(options=>options.map(option=>option.value)),['','commons'])
  await recheck(second);await read(second,'commons','pipelines','enabled',1)
  await read(page,'commons',long,'disabled',1)
  assert.deepEqual(await page.evaluate(()=>[localStorage.length,sessionStorage.length]),[0,0]);assert.deepEqual(await second.evaluate(()=>[localStorage.length,sessionStorage.length]),[0,0])
  // Real logout last. B still has a valid local commons selection; the next GET
  // must observe the revoked/shared Cookie session, not a removed workspace input.
  const logouts=calls.filter(call=>call.path==='/auth/logout').length
  await page.locator('#app-sign-out').click();await page.waitForFunction(()=>document.querySelector('#app-status').textContent.includes('server session is revoked'));await empty(page)
  assert.equal(calls.filter(call=>call.path==='/auth/logout').length,logouts+1)
  assert.equal(await second.locator('#module-status-workspace').inputValue(),'commons');await second.locator('#module-status-check').click();await second.waitForFunction(()=>document.querySelector('#app-principal').textContent==='Not verified');await empty(second)
  assert.equal(await second.locator('#module-status-key').inputValue(),'')
  const writes=calls.filter(call=>call.method!=='GET');assert.ok(writes.every(call=>call.path.startsWith('/auth/')||call.path.startsWith('/synthetic/module-status/')),'status UI must not issue business writes')
  assert.deepEqual(errors,[]);assert.deepEqual(dialogs,[]);assert.deepEqual(external,[])
 }catch(error){failure=error}
 finally{
  for(const release of releases)release()
  const failures=failure?[failure]:[]
  try{await within(router.drain(),'route drain');for(const rule of rules)router.remove(rule);await within(context.unrouteAll({behavior:'wait'}),'route removal')}catch(error){failures.push(error)}
  try{await within(context.close(),'isolated context cleanup')}catch(error){failures.push(error)}
  if(failures.length===1)throw failures[0]
  if(failures.length)throw new AggregateError(failures,'Module status journey or cleanup failed')
 }
 // Main browser entry owns final child/temp cleanup and the single overall PASS.
 console.log('Module status journey complete: real Cookie/SQLite exact observations, same-epoch held responses, two-tab locality and isolated context cleanup; synthetic only.')
}
module.exports={exerciseModuleStatus}
