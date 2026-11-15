// Real browser and HTTP, synthetic verifier/provider, separate refresh/review roles.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os')
const {spawn}=require('node:child_process'),{chromium}=require('playwright')
const within=async promise=>{let t;try{return await Promise.race([promise,new Promise((_,reject)=>t=setTimeout(()=>reject(new Error('barrier timeout')),15000))])}finally{clearTimeout(t)}}
async function main(){
 const child=spawn('python3',['-m','packages.artifact_admin.tests.browser_server'],{cwd:path.resolve(__dirname,'../../..'),stdio:['ignore','pipe','pipe']});child.stderr.resume()
 let browser
 try{
  const origin=await within(new Promise((resolve,reject)=>{let text='';child.stdout.on('data',c=>{text+=c;const m=text.match(/http:\/\/127\.0\.0\.1:\d+/);if(m)resolve(m[0])});child.once('exit',()=>reject(new Error('fixture stopped')))}))
  browser=await chromium.launch({headless:true})
  const context=await browser.newContext({viewport:{width:1440,height:1100}}),external=[],errors=[],dialogs=[],downloads=[]
  // Ignore AbortSignal for review reads to exercise generation fencing itself.
  await context.addInitScript(()=>{
   const original=window.fetch
   window.fetch=(input,options={})=>{if(String(input).endsWith('/review')){const {signal,...rest}=options;return original.call(window,input,rest)}return original.call(window,input,options)}
   if(crypto.subtle){const digest=crypto.subtle.digest.bind(crypto.subtle)
    crypto.subtle.digest=async(...args)=>{const result=await digest(...args);window.__digestCount=(window.__digestCount||0)+1;return result}}
  })
  await context.route('**/*',route=>{if(new URL(route.request().url()).origin===origin)return route.continue();external.push('unexpected origin');return route.abort()})
  const page=await context.newPage()
  page.on('pageerror',e=>errors.push(e.message));page.on('dialog',async d=>{dialogs.push(d.message());await d.dismiss()});page.on('download',()=>downloads.push(1))
  page.on('request',r=>assert.ok(!r.url().includes('synthetic-')))
  const base=origin+'/api/workspaces/alpha/artifact-admin/source/bucket'
  const auth=who=>({Authorization:'Bearer synthetic-'+who})
  const connect=async(who)=>{await page.locator('#credential').fill('synthetic-'+who);await page.locator('#connect button').click();assert.equal(await page.locator('#credential').inputValue(),'')}
  const scope=async(w='alpha')=>{await page.locator('#workspace').fill(w);await page.locator('#connection').fill('source');await page.locator('#bucket').fill('bucket');await page.locator('#scope-form button').click()}
  const revision=async n=>page.waitForFunction(n=>document.querySelector('#revision').textContent.startsWith(`Index revision ${n} ·`),n)
  const review=async()=>{await page.locator('#review').click();await page.waitForFunction(()=>!document.querySelector('#preview').hidden)}
  const reader=async()=>{const r=await context.request.get(origin+'/api/workspaces/alpha/artifacts/source/bucket/quarters',{headers:auth('reader')});return {status:r.status(),body:await r.json()}}
  const publish=async()=>{await page.locator('#acknowledge').check();await page.locator('#approve').click();await page.locator('#confirm-submit').click()}
  await page.goto(origin+'/artifact-admin/');await connect('admin');await scope();await revision(1)
  assert.equal(await page.locator('#review').isVisible(),false);assert.equal(await page.locator('#preview').isVisible(),false)
  await page.locator('#refresh').click();await page.locator('#cancel').click();assert.equal(await page.locator('#confirm-detail').textContent(),'');await revision(1)
  await page.locator('#refresh').click();await page.keyboard.press('Escape');assert.equal(await page.locator('#confirm-detail').textContent(),'');await revision(1)
  let submitted=0;page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/refresh'))submitted++})
  await page.locator('#refresh').click();assert.match(await page.locator('#confirm-detail').textContent(),/no new metadata is automatically shared/)
  await page.evaluate(()=>{const form=document.querySelector('#confirm-form');form.requestSubmit();form.requestSubmit()});await revision(2);assert.equal(submitted,1)
  assert.equal((await reader()).status,404);assert.ok(!(await page.locator('body').textContent()).includes('package-'))
  await page.locator('#disconnect').click();await connect('approver');await scope();await revision(2)
  assert.equal(await page.locator('#refresh').isVisible(),false);await review()
  assert.equal(await page.locator('#rows li').count(),13);assert.equal(await page.locator('#readers li').count(),2)
  assert.equal(await page.locator('#approve').isDisabled(),true);assert.equal(await page.locator('#rows svg').count(),0)
  assert.match(await page.locator('#rows').textContent(),/<svg onload=alert\(1\)>.txt/)
  // A refresh by another current admin invalidates the whole preview before approval.
  const race=await context.request.post(base+'/refresh',{headers:{...auth('admin'),'If-Match':'"2"'},data:{}});assert.equal(race.status(),200)
  await publish();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('version, candidate or reader set changed'))
  assert.equal(await page.locator('#preview').isVisible(),false);assert.equal(await page.locator('#rows li').count(),0);assert.equal((await reader()).status,404)
  const folder=path.join(process.env.RUNNER_TEMP||os.tmpdir(),'ms-collab-artifact-admin');fs.mkdirSync(folder,{recursive:true})
  await page.screenshot({path:path.join(folder,'conflict.png'),fullPage:true})
  await page.locator('#reload').click();await revision(3);await review();assert.equal(await page.locator('#rows li').count(),14)
  await page.screenshot({path:path.join(folder,'desktop.png'),fullPage:true})
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(folder,'mobile.png'),fullPage:true})
  await publish();await revision(4);assert.equal(await page.locator('#preview').isVisible(),false)
  assert.equal((await reader()).body.data.catalog_revision,4)
  // Refresh after approval is still private and never changes the published revision.
  const pending=await context.request.post(base+'/refresh',{headers:{...auth('admin'),'If-Match':'"4"'},data:{}});assert.equal(pending.status(),200)
  assert.equal((await reader()).body.data.catalog_revision,4)
  const readQuery=await context.request.post(origin+'/api/workspaces/alpha/artifacts/source/bucket/query',{headers:auth('reader'),data:{quarter:'2026Q1',definition:{files_only:true}}})
  const shared=await readQuery.json();assert.equal(shared.data.total,14);assert.ok(!JSON.stringify(shared).includes('new-candidate-3'))
  await page.locator('#reload').click();await revision(5);await review();assert.equal(await page.locator('#rows li').count(),15)
  await page.locator('#bucket').fill('other');assert.equal(await page.locator('#rows li').count(),0);assert.equal(await page.locator('#preview').isVisible(),false)
  await scope('commons');await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('unavailable to your identity'));assert.equal(await page.locator('#rows li').count(),0)
  await page.locator('#disconnect').click();await connect('reader');await scope();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('unavailable to your identity'));assert.equal(await page.locator('#panel').isVisible(),false)
  // Lost refresh response: server succeeds once, page clears state and never retries.
  await page.locator('#disconnect').click();await connect('admin');await scope();await revision(5)
  let writes=0
  await page.route('**/artifact-admin/source/bucket/refresh',async route=>{writes++;await route.fetch();await route.abort()})
  await page.locator('#refresh').click();await page.locator('#confirm-submit').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('accepted action may have completed'))
  assert.equal(writes,1);assert.equal(await page.locator('#panel').isVisible(),false)
  await page.unroute('**/artifact-admin/source/bucket/refresh');await page.locator('#reload').click();await revision(6);assert.equal(writes,1)
  await page.route('**/artifact-admin/source/bucket/status',route=>route.fulfill({status:401,contentType:'application/json',body:'{"error":"authentication_required"}'}))
  await page.locator('#reload').click();await page.waitForFunction(()=>!document.querySelector('#connect').hidden)
  assert.equal(await page.locator('#workspace').inputValue(),'');assert.equal(await page.locator('#rows li').count(),0)
  await page.unroute('**/artifact-admin/source/bucket/status')
  await connect('approver');await scope();await revision(6);await review()
  async function delayedReview(interrupt){
   let seen,release,done
   const started=new Promise(r=>seen=r),gate=new Promise(r=>release=r),finished=new Promise(r=>done=r)
   const previous=await page.evaluate(()=>window.__digestCount||0)
   await page.route('**/artifact-admin/source/bucket/review',async route=>{const response=await route.fetch();seen();await gate;await route.fulfill({response});done()})
   await page.locator('#review').click();await within(started);await interrupt();release();await within(finished)
   await page.waitForFunction(n=>(window.__digestCount||0)>n,previous)
   assert.equal(await page.locator('#rows li').count(),0);assert.equal(await page.locator('#preview').isVisible(),false)
   await page.unroute('**/artifact-admin/source/bucket/review')
  }
  await delayedReview(async()=>{await scope('commons');await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('unavailable to your identity'))})
  await scope();await revision(6)
  await delayedReview(async()=>{await page.locator('#disconnect').click()})
  assert.equal(await page.locator('#connect').isVisible(),true)
  await connect('approver');await scope();await revision(6);await review()
  await page.goto('about:blank');await page.goBack();await page.waitForFunction(()=>!document.querySelector('#connect').hidden)
  assert.equal(await page.locator('#workspace').inputValue(),'');assert.equal(await page.locator('#rows li').count(),0)
  await connect('approver');await scope();await revision(6);await review()
  await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide')));assert.equal(await page.locator('#rows li').count(),0)
  assert.deepEqual(await page.evaluate(()=>[localStorage.length,sessionStorage.length]),[0,0])
  assert.deepEqual(external,[]);assert.deepEqual(errors,[]);assert.deepEqual(dialogs,[]);assert.deepEqual(downloads,[])
  console.log('Artifact management acceptance passed; complete approval, conflicts, isolation and uncertain-result handling verified.')
 }finally{await browser?.close();child.kill();await new Promise(resolve=>{if(child.exitCode!==null)resolve();else child.once('exit',resolve)})}
}
main().catch(error=>{console.error(error.message);process.exitCode=1})
