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
  const external=[], errors=[], mutations=[]
  await context.route('**/*',route=>{if(new URL(route.request().url()).origin===origin)return route.continue();external.push(route.request().url());return route.abort()})
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()==='PATCH')mutations.push(r.url());assert.ok(!r.url().includes('synthetic-admin'))})
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
  assert.deepEqual(await page.evaluate(()=>({local:localStorage.length,session:sessionStorage.length})),{local:0,session:0})
  assert.deepEqual(external,[]);assert.deepEqual(errors,[]);console.log('Workspace administration browser acceptance passed; two synthetic screenshots written.')
 }finally{await browser?.close();child.kill();await new Promise(resolve=>{if(child.exitCode!==null)resolve();else child.once('exit',resolve)})}
}
main().catch(error=>{console.error(error.message);process.exitCode=1})
