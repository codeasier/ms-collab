// Real HTTP/browser acceptance, isolated test verifier and temporary SQLite only.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os')
const {spawn}=require('node:child_process'),{chromium}=require('playwright')
const within=async(p)=>{let t;try{return await Promise.race([p,new Promise((_,reject)=>t=setTimeout(()=>reject(new Error('barrier timeout')),10000))])}finally{clearTimeout(t)}}
async function main(){
 const child=spawn('python3',['-m','packages.workspace_ui.tests.browser_server'],{cwd:path.resolve(__dirname,'../../..'),stdio:['ignore','pipe','pipe']});child.stderr.resume()
 let browser
 try{
  const origin=await within(new Promise((resolve,reject)=>{let s='';child.stdout.on('data',c=>{s+=c;const m=s.match(/http:\/\/127\.0\.0\.1:\d+/);if(m)resolve(m[0])});child.once('exit',()=>reject(new Error('server stopped')))}))
  browser=await chromium.launch({headless:true});const context=await browser.newContext({viewport:{width:1440,height:1000}})
  await context.addInitScript(()=>{
    AbortController.prototype.abort=function(){}
    const original=window.fetch.bind(window);window.finishedInvitationWrites=[]
    window.fetch=async(...args)=>{const response=await original(...args);if(args[1]?.method==='POST')window.finishedInvitationWrites.push(String(args[0]));return response}
  })
  const errors=[],external=[],writes=[]
  await context.route('**/*',route=>{if(new URL(route.request().url()).origin===origin)return route.continue();external.push(route.request().url());return route.abort()})
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{assert.ok(!r.url().includes('synthetic-'));if(['POST','PATCH'].includes(r.method()))writes.push({path:new URL(r.url()).pathname,key:r.headers()['idempotency-key']})})
  const connect=async token=>{await page.locator('#credential').fill(token);await page.locator('#connect button').click();await page.locator('#inbox-panel:not([hidden])').waitFor()}
  const invite=async(target='newcomer',role='developer')=>{await page.locator('#invite-open').click();await page.locator('#invite-principal').fill(target);await page.locator('#invite-role').selectOption(role)}
  await page.goto(origin+'/manage/');await connect('synthetic-admin');await page.locator('#invite-open:not([hidden])').waitFor()
  await invite();const before=writes.length;await page.locator('#invite-cancel').click();assert.equal(writes.length,before)
  // Server accepted creation, client lost its response. Retry must keep its key.
  let lost=false
  await page.route('**/api/workspaces/alpha/invitations',async route=>{if(route.request().method()==='POST'&&!lost){lost=true;await route.fetch();return route.abort('failed')}return route.continue()})
  await invite();await page.locator('#invite-save').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('Result uncertain'))
  await page.unroute('**/api/workspaces/alpha/invitations');await page.locator('#invite-open:not([hidden])').waitFor()
  await invite();await page.locator('#invite-save').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('Invitation recorded'))
  const creation=writes.filter(r=>r.path==='/api/workspaces/alpha/invitations');assert.equal(creation.length,2);assert.equal(creation[0].key,creation[1].key)
  await page.waitForFunction(()=>document.querySelector('#invitations').textContent.includes('newcomer · developer · pending'))
  const admin={Authorization:'Bearer synthetic-admin'},newcomer={Authorization:'Bearer synthetic-newcomer'}
  assert.equal((await context.request.get(origin+'/api/workspaces/alpha/members',{headers:newcomer})).status(),404)
  const folder=path.join(process.env.RUNNER_TEMP||os.tmpdir(),'ms-collab-admin');fs.mkdirSync(folder,{recursive:true})
  await page.screenshot({path:path.join(folder,'invitations-desktop.png'),fullPage:true})
  await page.locator('#disconnect').click();await connect('synthetic-newcomer');await page.getByRole('button',{name:'Join Product Alpha',exact:true}).waitFor()
  assert.equal(await page.locator('#workspace option').count(),0)
  await page.getByRole('button',{name:'Join Product Alpha',exact:true}).click();const beforeJoin=writes.length;await page.locator('#action-cancel').click();assert.equal(writes.length,beforeJoin)
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth))
  await page.screenshot({path:path.join(folder,'invitations-mobile.png'),fullPage:true})
  await page.getByRole('button',{name:'Join Product Alpha',exact:true}).click();await page.locator('#action-save').click();await page.waitForFunction(()=>document.querySelector('#workspace option')?.value==='alpha')
  const members=(await(await context.request.get(origin+'/api/workspaces/alpha/members',{headers:admin})).json()).data
  assert.equal(members.find(m=>m.principal_id==='newcomer').role,'developer')
  assert.equal(await page.locator('#inbox button').count(),0)
  // Revoking membership does not permit replaying the old accepted invitation.
  const old=(await(await context.request.get(origin+'/api/workspaces/alpha/invitations',{headers:admin})).json()).data[0]
  assert.equal((await context.request.patch(origin+'/api/workspaces/alpha/members/newcomer',{headers:{...admin,'If-Match':'"1"'},data:{role:'viewer',state:'revoked'}})).status(),200)
  assert.equal((await context.request.patch(origin+'/api/invitations/'+old.id,{headers:{...newcomer,'If-Match':`"${old.revision}"`},data:{state:'accepted'}})).status(),409)
  await page.locator('#disconnect').click();await connect('synthetic-admin');await page.locator('#invite-open:not([hidden])').waitFor()
  await invite('newcomer','viewer');await page.locator('#invite-save').click();await page.waitForFunction(()=>document.querySelector('#invitations').textContent.includes('newcomer · viewer · pending'))
  // Exact revision conflict: admin revokes after the recipient opened Join.
  await page.locator('#disconnect').click();await connect('synthetic-newcomer');await page.getByRole('button',{name:'Join Product Alpha',exact:true}).click()
  let active=(await(await context.request.get(origin+'/api/workspaces/alpha/invitations',{headers:admin})).json()).data.find(i=>i.state==='pending')
  assert.equal((await context.request.patch(origin+'/api/workspaces/alpha/invitations/'+active.id,{headers:{...admin,'If-Match':`"${active.revision}"`},data:{state:'revoked'}})).status(),200)
  await page.locator('#action-save').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('changed elsewhere'))
  assert.equal(await page.locator('#inbox button').count(),0);assert.equal(await page.locator('#action-dialog').isVisible(),false)
  // Late inbox response cannot restore private content after disconnect, even
  // when AbortController does nothing.
  let seen,release,done;const started=new Promise(r=>seen=r),gate=new Promise(r=>release=r),handled=new Promise(r=>done=r)
  await page.route('**/api/invitations',async route=>{seen();await gate;await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({data:[{...old,state:'pending'}]})}).catch(()=>{});done()})
  await page.locator('#reload').click();await within(started);await page.locator('#disconnect').click();release();await within(handled)
  assert.equal(await page.locator('#inbox').textContent(),'');assert.equal(await page.locator('#session').isVisible(),false)
  await page.unroute('**/api/invitations');await connect('synthetic-admin');await page.locator('#invite-open:not([hidden])').waitFor()
  // A mismatched scope response is rejected rather than rendered.
  await page.route('**/api/workspaces/alpha/invitations',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({data:[{...old,workspace_id:'hidden',workspace_name:'must-not-display'}]})}))
  await page.locator('#reload').click();await page.waitForFunction(()=>document.querySelector('#invitation-status').textContent.includes('Unable to load valid'))
  assert.equal(await page.locator('#invitations').textContent(),'');await page.unroute('**/api/workspaces/alpha/invitations')
  await page.locator('#reload').click();await page.locator('#invite-open:not([hidden])').waitFor()
  // Pending old write cannot wedge a reconnect or unlock a newer write. The
  // real server may still accept the old request; disconnect is not rollback.
  let seenA,releaseA,doneA,seenB,releaseB,doneB
  const startedA=new Promise(r=>seenA=r),gateA=new Promise(r=>releaseA=r),handledA=new Promise(r=>doneA=r)
  const startedB=new Promise(r=>seenB=r),gateB=new Promise(r=>releaseB=r),handledB=new Promise(r=>doneB=r)
  await page.route('**/api/workspaces/alpha/invitations',async route=>{if(route.request().method()!=='POST')return route.continue();seenA();await gateA;const response=await route.fetch();await route.fulfill({response});doneA()})
  await invite();await page.locator('#invite-save').click();await within(startedA)
  await page.locator('#disconnect').click();await connect('synthetic-admin');await page.locator('#workspace').selectOption('commons');await page.waitForFunction(()=>document.querySelector('#title').textContent==='Shared collaboration'&&!document.querySelector('#invite-open').hidden)
  await page.route('**/api/workspaces/commons/invitations',async route=>{if(route.request().method()!=='POST')return route.continue();seenB();await gateB;const response=await route.fetch();await route.fulfill({response});doneB()})
  await invite();assert.equal(await page.locator('#invite-save').isDisabled(),false);await page.locator('#invite-save').click();await within(startedB)
  const completed=await page.evaluate(()=>window.finishedInvitationWrites.length);releaseA();await within(handledA)
  await page.waitForFunction(n=>window.finishedInvitationWrites.length>n,completed)
  assert.equal(await page.locator('#invite-save').isDisabled(),true)
  releaseB();await within(handledB);await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('Invitation recorded'))
  await page.unroute('**/api/workspaces/alpha/invitations');await page.unroute('**/api/workspaces/commons/invitations')
  await page.locator('#invite-open:not([hidden])').waitFor();await invite('newcomer')
  await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide')))
  assert.equal(await page.locator('#invite-dialog').isVisible(),false);assert.equal(await page.locator('#invite-principal').inputValue(),'');assert.equal(await page.locator('#invitations').textContent(),'');assert.equal(await page.locator('#inbox').textContent(),'')
  assert.deepEqual(await page.evaluate(()=>({local:localStorage.length,session:sessionStorage.length,cookie:document.cookie})),{local:0,session:0,cookie:''})
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);console.log('Invitation acceptance passed: real SQLite/HTTP, identity-bound join, cancel/replay/revoke/races/cleanup; synthetic only.')
 }finally{if(browser)await browser.close();child.kill();await new Promise(r=>child.exitCode!==null?r():child.once('exit',r))}
}
main().catch(error=>{console.error(error);process.exitCode=1})
