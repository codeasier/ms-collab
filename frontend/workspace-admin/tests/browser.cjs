// Acceptance uses only the test-harness verifier and temporary synthetic store.
const assert=require('node:assert/strict'), fs=require('node:fs'), path=require('node:path'), os=require('node:os')
const {spawn}=require('node:child_process'), {chromium}=require('playwright')
const within=async(promise)=>{let t;try{return await Promise.race([promise,new Promise((_,reject)=>t=setTimeout(()=>reject(new Error('barrier timeout')),10000))])}finally{clearTimeout(t)}}
async function main(){
 const child=spawn('python3',['-m','packages.workspace_ui.tests.browser_server'],{cwd:path.resolve(__dirname,'../../..'),stdio:['ignore','pipe','pipe']});child.stderr.resume()
 let browser
 try{
  const origin=await within(new Promise((resolve,reject)=>{let s='';child.stdout.on('data',c=>{s+=c;const m=s.match(/http:\/\/127\.0\.0\.1:\d+/);if(m)resolve(m[0])});child.once('exit',()=>reject(new Error('server stopped')))}))
  browser=await chromium.launch({headless:true});const context=await browser.newContext({viewport:{width:1440,height:1000}})
  // Disable transport cancellation: generation guards must stand on their own.
  await context.addInitScript(()=>{
    AbortController.prototype.abort=function(){}
    const original=window.fetch.bind(window)
    window.acceptanceRequests=[];window.acceptanceCompleted=[]
    window.fetch=async(...args)=>{window.acceptanceRequests.push(String(args[0]));const response=await original(...args);window.acceptanceCompleted.push(String(args[0]));return response}
  })
  const external=[], errors=[], mutations=[], writes=[]
  await context.route('**/*',route=>{if(new URL(route.request().url()).origin===origin)return route.continue();external.push(route.request().url());return route.abort()})
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()==='PATCH')mutations.push(r.url());if(['PATCH','POST'].includes(r.method()))writes.push({method:r.method(),url:r.url(),key:r.headers()['idempotency-key']});assert.ok(!r.url().includes('synthetic-admin'))})
  await page.goto(origin+'/manage/');assert.equal(await page.locator('#session').isVisible(),false)
  await page.locator('#credential').fill('synthetic-admin');await page.locator('#connect button').click();await page.locator('#panel:not([hidden])').waitFor()
  assert.equal(await page.locator('#credential').inputValue(),'');assert.equal(await page.locator('#workspace option').count(),2)
  assert.match(await page.locator('#members').textContent(),/reader · viewer · active/)
  await page.getByRole('button',{name:'Edit reader',exact:true}).click();await page.locator('#member-state').selectOption('revoked');await page.locator('#cancel').click();assert.equal(mutations.length,0)
  await page.getByRole('button',{name:'Edit retiree',exact:true}).click();await page.locator('#member-state').selectOption('revoked');await page.locator('#save').click();await page.waitForFunction(()=>document.querySelector('#members').textContent.includes('retiree · viewer · revoked'))
  assert.equal(await page.getByRole('button',{name:'Edit retiree',exact:true}).isDisabled(),true)
  await page.getByRole('button',{name:'Edit admin',exact:true}).click();await page.locator('#role').selectOption('viewer');await page.locator('#save').click();await page.waitForFunction(()=>document.querySelector('#edit-status').textContent.includes('last administrator'));await page.locator('#cancel').click()
  // Change the same row through the real API after opening its edit revision.
  await page.getByRole('button',{name:'Edit reader',exact:true}).click()
  const current=await (await context.request.get(origin+'/api/workspaces/alpha/members',{headers:{Authorization:'Bearer synthetic-admin'}})).json()
  const member=current.data.find(m=>m.principal_id==='reader')
  const conflict=await context.request.patch(origin+'/api/workspaces/alpha/members/reader',{headers:{Authorization:'Bearer synthetic-admin','If-Match':`"${member.revision}"`},data:{role:'developer',state:'active'}});assert.equal(conflict.status(),200)
  await page.locator('#save').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('changed elsewhere'))
  assert.match(await page.locator('#members').textContent(),/reader · developer/);assert.equal(await page.locator('#editor').isVisible(),false)
  // Wait for the old members read before selecting another scope.
  let seen,release,done;const started=new Promise(r=>seen=r),gate=new Promise(r=>release=r),handled=new Promise(r=>done=r)
  await page.route('**/api/workspaces/alpha/members',async route=>{seen();await gate;await route.continue().catch(()=>{});done()})
  await page.locator('#reload').click();await within(started);await page.locator('#workspace').selectOption('commons');release();await within(handled)
  await page.waitForFunction(()=>document.querySelector('#title').textContent==='Shared collaboration');assert.ok(!(await page.locator('#members').textContent()).includes('reader'))
  await page.unroute('**/api/workspaces/alpha/members')
  // Reject a mixed-workspace payload, clear stale content and allow reload.
  await page.route('**/api/workspaces/commons/members',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({data:[{workspace_id:'alpha',principal_id:'reader',role:'viewer',state:'active',revision:1}]})}))
  await page.locator('#reload').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('valid response'));assert.equal(await page.locator('#panel').isVisible(),false)
  await page.unroute('**/api/workspaces/commons/members');await page.locator('#reload').click();await page.locator('#panel:not([hidden])').waitFor()
  const folder=path.join(process.env.RUNNER_TEMP||os.tmpdir(),'ms-collab-admin');fs.mkdirSync(folder,{recursive:true})
  await page.screenshot({path:path.join(folder,'desktop.png'),fullPage:true})
  await page.setViewportSize({width:390,height:844});await page.locator('#workspace').selectOption('alpha');await page.waitForFunction(()=>document.querySelector('#title').textContent==='Product Alpha')
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(folder,'mobile.png'),fullPage:true})
  // Lifecycle cleanup clears even the closed editor's private display state.
  await page.getByRole('button',{name:'Edit admin',exact:true}).click()
  await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide')))
  assert.deepEqual(await page.evaluate(()=>({editing:document.querySelector('#editing').textContent,role:document.querySelector('#role').value,state:document.querySelector('#member-state').value,members:document.querySelector('#members').textContent})),{editing:'',role:'',state:'',members:''})
  assert.equal(await page.locator('#editor').isVisible(),false)
  await page.locator('#credential').fill('synthetic-admin');await page.locator('#connect button').click();await page.locator('#panel:not([hidden])').waitFor()
  await page.route('**/api/workspaces/alpha/members',route=>route.fulfill({status:401,contentType:'application/json',body:'{"error":"authentication_required"}'}))
  await page.locator('#reload').click();await page.waitForFunction(()=>!document.querySelector('#connect').hidden);assert.equal(await page.locator('#members').textContent(),'')
  await page.unroute('**/api/workspaces/alpha/members');await page.locator('#credential').fill('synthetic-reader');await page.locator('#connect button').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('administrator permission'))
  assert.equal(await page.locator('#panel').isVisible(),false);await page.locator('#disconnect').click()
  // Platform operator control is separate from workspace membership.
  await page.setViewportSize({width:1440,height:1000})
  await page.locator('#credential').fill('synthetic-operator');await page.locator('#connect button').click();await page.locator('#operator-panel:not([hidden])').waitFor()
  assert.equal(await page.locator('#panel').isVisible(),false)
  const operatorHeaders={Authorization:'Bearer synthetic-operator'}
  assert.equal((await context.request.get(origin+'/api/workspaces/alpha/members',{headers:operatorHeaders})).status(),404)
  const fillCreate=async(name,kind='product')=>{await page.locator('#create-open').click();await page.locator('#space-name').fill(name);await page.locator('#space-kind').selectOption(kind);await page.locator('#initial-admin').fill('admin')}
  await fillCreate('Cancelled Team');const beforeCancel=writes.length;await page.locator('#create-cancel').click();assert.equal(writes.length,beforeCancel)
  // A committed creation with a lost response is retried with the same key.
  let lost=false
  await page.route('**/api/control/workspaces',async route=>{if(route.request().method()==='POST'&&!lost){lost=true;await route.fetch();return route.abort('failed')}return route.continue()})
  await fillCreate('Retry Team');await page.locator('#create-save').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('Result uncertain'))
  await page.unroute('**/api/control/workspaces')
  await fillCreate('Retry Team');await page.locator('#create-save').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.startsWith('Workspace created:'))
  const createWrites=writes.filter(r=>r.method==='POST'&&new URL(r.url).pathname==='/api/control/workspaces')
  assert.equal(createWrites.length,2);assert.equal(createWrites[0].key,createWrites[1].key)
  let controls=(await (await context.request.get(origin+'/api/control/workspaces',{headers:operatorHeaders})).json()).data
  assert.equal(controls.filter(w=>w.name==='Retry Team').length,1);const team=controls.find(w=>w.name==='Retry Team')
  await fillCreate('Shared Team','shared');await page.locator('#create-save').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('Workspace created: Shared Team'))
  assert.equal(await page.locator('#workspace option').count(),0)
  // Cancel archive is non-mutating; stale lifecycle revision must refresh.
  await page.getByRole('button',{name:'Archive Retry Team',exact:true}).click();const beforeArchive=writes.length;await page.locator('#action-cancel').click();assert.equal(writes.length,beforeArchive)
  await page.getByRole('button',{name:'Suspend Product Alpha',exact:true}).click()
  const suspended=await context.request.patch(origin+'/api/control/workspaces/alpha',{headers:{...operatorHeaders,'If-Match':'"1"'},data:{state:'suspended'}});assert.equal(suspended.status(),200)
  await page.locator('#action-save').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('changed elsewhere'))
  assert.equal((await context.request.get(origin+'/api/workspaces/alpha/members',{headers:{Authorization:'Bearer synthetic-admin'}})).status(),409)
  await page.getByRole('button',{name:'Reactivate Product Alpha',exact:true}).click();await page.locator('#action-save').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('lifecycle updated'))
  await page.screenshot({path:path.join(folder,'desktop.png'),fullPage:true})
  await page.locator('#disconnect').click();await page.locator('#credential').fill('synthetic-admin');await page.locator('#connect button').click();await page.locator('#panel:not([hidden])').waitFor()
  assert.equal(await page.locator('#operator-panel').isVisible(),false)
  await page.locator('#workspace').selectOption(team.id);await page.waitForFunction(()=>document.querySelector('#title').textContent==='Retry Team');await page.locator('#install-form:not([hidden])').waitFor()
  await page.locator('#module-key').selectOption('artifacts');await page.locator('#install').click();const beforeInstall=writes.length;await page.locator('#action-cancel').click();assert.equal(writes.length,beforeInstall)
  await page.locator('#install').click();await page.locator('#action-save').click();await page.waitForFunction(()=>document.querySelector('#modules').textContent.includes('artifacts · disabled'))
  for(const [button,state] of [['Enable artifacts','enabled'],['Begin disabling artifacts','disabling'],['Finish disabling artifacts','disabled']]){
    await page.getByRole('button',{name:button,exact:true}).click();await page.locator('#action-save').click();await page.waitForFunction(state=>document.querySelector('#modules').textContent.includes('artifacts · '+state),state)
  }
  // Module update races use its own revision, not a workspace revision.
  await page.getByRole('button',{name:'Enable artifacts',exact:true}).click()
  assert.equal((await context.request.patch(origin+`/api/workspaces/${team.id}/modules/artifacts`,{headers:{Authorization:'Bearer synthetic-admin','If-Match':'"4"'},data:{state:'enabled'}})).status(),200)
  await page.locator('#action-save').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('changed elsewhere'))
  await page.waitForFunction(()=>document.querySelector('#modules').textContent.includes('artifacts · enabled'))
  // A different workspace in a module response must never be rendered.
  const modulesPattern=`**/api/workspaces/${team.id}/modules`
  await page.route(modulesPattern,async route=>{const response=await route.fetch();const value=await response.json();value.data[0].workspace_id='alpha';await route.fulfill({response,json:value})})
  await page.locator('#reload').click();await page.waitForFunction(()=>document.querySelector('#module-status').textContent.includes('valid module state'));assert.equal(await page.locator('#modules').textContent(),'')
  await page.unroute(modulesPattern);await page.locator('#reload').click();await page.waitForFunction(()=>document.querySelector('#modules').textContent.includes('artifacts · enabled'))
  // A late first-stage module read must not issue a second-stage catalog read
  // for the old workspace after selection changes, even when abort does nothing.
  let moduleSeen,moduleRelease,moduleDone
  const moduleStarted=new Promise(r=>moduleSeen=r),moduleGate=new Promise(r=>moduleRelease=r),moduleHandled=new Promise(r=>moduleDone=r)
  const oldModulePath=`/api/workspaces/${team.id}/modules`,oldCatalogPath=`/api/workspaces/${team.id}/module-catalog`
  const catalogBefore=await page.evaluate(path=>window.acceptanceRequests.filter(p=>p===path).length,oldCatalogPath)
  const completedBefore=await page.evaluate(path=>window.acceptanceCompleted.filter(p=>p===path).length,oldModulePath)
  await page.route(modulesPattern,async route=>{moduleSeen();await moduleGate;await route.continue();moduleDone()})
  await page.locator('#reload').click();await within(moduleStarted);await page.locator('#workspace').selectOption('alpha');moduleRelease();await within(moduleHandled)
  await page.waitForFunction(()=>document.querySelector('#title').textContent==='Product Alpha')
  await page.waitForFunction(({path,count})=>window.acceptanceCompleted.filter(p=>p===path).length>count,{path:oldModulePath,count:completedBefore})
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))))
  assert.equal(await page.evaluate(path=>window.acceptanceRequests.filter(p=>p===path).length,oldCatalogPath),catalogBefore)
  await page.unroute(modulesPattern);await page.locator('#workspace').selectOption(team.id);await page.waitForFunction(()=>document.querySelector('#modules').textContent.includes('artifacts · enabled'))
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(folder,'mobile.png'),fullPage:true})
  await page.locator('#disconnect').click();await page.locator('#credential').fill('synthetic-operator');await page.locator('#connect button').click();await page.locator('#operator-panel:not([hidden])').waitFor()
  await page.getByRole('button',{name:'Archive Retry Team',exact:true}).click();assert.match(await page.locator('#action-description').textContent(),/irreversible/);await page.locator('#action-save').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('lifecycle updated'))
  assert.equal(await page.getByRole('button',{name:'Reactivate Retry Team',exact:true}).count(),0)
  await page.locator('#disconnect').click();await page.locator('#credential').fill('synthetic-admin');await page.locator('#connect button').click();await page.locator('#panel:not([hidden])').waitFor();await page.locator('#workspace').selectOption(team.id)
  await page.waitForFunction(()=>document.querySelector('#module-status').textContent.includes('read-only'))
  assert.equal(await page.locator('#install-form').isVisible(),false);assert.equal(await page.locator('#modules button').count(),0)
  await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide')))
  assert.equal(await page.locator('#control-spaces').textContent(),'');assert.equal(await page.locator('#modules').textContent(),'');assert.equal(await page.locator('#initial-admin').inputValue(),'')
  assert.deepEqual(await page.evaluate(()=>({local:localStorage.length,session:sessionStorage.length})),{local:0,session:0})
  assert.deepEqual(external,[]);assert.deepEqual(errors,[]);console.log('Workspace administration browser acceptance passed; two synthetic screenshots written.')
 }finally{await browser?.close();child.kill();await new Promise(resolve=>{if(child.exitCode!==null)resolve();else child.once('exit',resolve)})}
}
main().catch(error=>{console.error(error.message);process.exitCode=1})
