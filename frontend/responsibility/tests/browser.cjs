// Real HTTP/browser acceptance. All identities, path observations and content synthetic.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os')
const {spawn}=require('node:child_process'),{chromium}=require('playwright')
async function within(promise){let timer;try{return await Promise.race([promise,new Promise((_,reject)=>timer=setTimeout(()=>reject(new Error('barrier timeout')),20000))])}finally{clearTimeout(timer)}}
async function main(){
 const child=spawn('python3',['-m','packages.responsibility_ui.tests.browser_server'],{cwd:path.resolve(__dirname,'../../..'),stdio:['ignore','pipe','pipe']});child.stderr.resume();let browser
 try{
  const origin=await within(new Promise((resolve,reject)=>{let output='';child.stdout.on('data',c=>{output+=c;const match=output.match(/http:\/\/127\.0\.0\.1:\d+/);if(match)resolve(match[0])});child.once('exit',()=>reject(new Error('fixture stopped')))}))
  browser=await chromium.launch({headless:true})
  const context=await browser.newContext({viewport:{width:1440,height:1100}}),external=[],errors=[],dialogs=[],downloads=[],writes=[]
  await context.addInitScript(()=>{const original=window.fetch;window.fetch=async(input,options={})=>{const {signal,...rest}=options;const response=await original.call(window,input,rest);window.__fetchDone=(window.__fetchDone||0)+1;return response}})
  await context.route('**/*',route=>{if(new URL(route.request().url()).origin===origin)return route.continue();external.push('unexpected origin');return route.abort()})
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));page.on('dialog',async d=>{dialogs.push(d.message());await d.dismiss()});page.on('download',()=>downloads.push(1))
  page.on('request',r=>{assert.ok(!r.url().includes('test-admin')&&!r.url().includes('test-reader'));if(r.method()==='PUT')writes.push(r.url())})
  const base=origin+'/api/workspaces/alpha/responsibility/source/sample/project'
  const auth={Authorization:'Bearer test-admin'}
  const current=async()=>{const r=await context.request.get(base+'/config',{headers:auth});assert.equal(r.status(),200);return (await r.json()).data}
  const connect=async(who='admin')=>{await page.locator('#credential').fill('test-'+who);await page.locator('#connect button').click();assert.equal(await page.locator('#credential').inputValue(),'')}
  const scope=async(workspace='alpha')=>{await page.locator('#workspace').fill(workspace);await page.locator('#connection').fill('source');await page.locator('#owner').fill('sample');await page.locator('#repo').fill('project');await page.locator('#scope-form button').click()}
  const revision=async n=>page.waitForFunction(n=>document.querySelector('#version').textContent.startsWith(`Record revision ${n} ·`),n)
  const change=async(name)=>{await page.locator('#edit').click();await page.locator('#editor-fields [data-entry="name"]').first().fill(name);await page.locator('#save').click()}
  const confirm=async()=>page.locator('#confirm-submit').click()
  const flush=async()=>page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))))
  await page.goto(origin+'/responsibility/');await connect();await scope();await revision(3)
  assert.equal(await page.locator('#candidates li').count(),20);assert.equal(await page.locator('#candidates img').count(),0)
  assert.match(await page.locator('#candidates').textContent(),/Assignment unknown/);assert.match(await page.locator('#candidates').textContent(),/<img src=x onerror=alert\(1\)>/)
  assert.match(await page.locator('#candidates').textContent(),/Unpinned path observation only/)
  assert.ok(!(await page.locator('body').textContent()).includes('Synthetic UI change 33'))
  await page.locator('#next').click();await page.waitForFunction(()=>document.querySelector('#page').textContent==='21–32 of 32')
  assert.equal(await page.locator('#candidates li').count(),12);assert.match(await page.locator('#candidates').textContent(),/Path evidence incomplete/)
  await page.locator('#view').selectOption('mine');await page.locator('#filters button').click();await page.waitForFunction(()=>document.querySelector('#page').textContent==='1–20 of 30')
  await page.locator('#view').selectOption('field');await page.locator('#field').selectOption('review');await page.locator('#filters button').click();await page.waitForFunction(()=>document.querySelector('#page').textContent==='1–20 of 30')
  await page.locator('#view').selectOption('unassigned');await page.locator('#filters button').click();await page.waitForFunction(()=>document.querySelector('#page').textContent==='1–1 of 1');assert.match(await page.locator('#candidates').textContent(),/Assignment unknown/)
  await page.locator('#reload').click();await revision(3)
  await change('Interface mapping');await page.locator('#cancel').click();assert.equal(await page.locator('#confirm-summary').textContent(),'');assert.equal(writes.length,0)
  await page.locator('#save').click();await page.keyboard.press('Escape');assert.equal(await page.locator('#confirm-scope').textContent(),'');assert.equal(writes.length,0)
  await page.locator('#save').click();await page.evaluate(()=>{const form=document.querySelector('#confirm-form');form.requestSubmit();form.requestSubmit()});await revision(4);assert.equal(writes.length,1)
  assert.equal((await current()).fields[0].name,'Interface mapping');assert.equal(await page.locator('#editor-fields fieldset').count(),0)
  // Another authorized editor wins the CAS; the stale page never overwrites or retries.
  await change('Stale local edit');const other=await current();other.fields[0].name='Concurrent configuration'
  const updated=await context.request.put(base+'/config',{headers:{...auth,'If-Match':'"4"'},data:{fields:other.fields}});assert.equal(updated.status(),200)
  await confirm();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('revision changed'))
  assert.equal(writes.length,2);assert.equal(await page.locator('#panel').isVisible(),false);assert.equal(await page.locator('#editor-fields fieldset').count(),0)
  assert.equal((await current()).fields[0].name,'Concurrent configuration')
  const folder=path.join(process.env.RUNNER_TEMP||os.tmpdir(),'ms-collab-responsibility');fs.mkdirSync(folder,{recursive:true})
  await page.screenshot({path:path.join(folder,'conflict.png'),fullPage:true})
  await page.locator('#reload').click();await revision(5)
  await page.locator('#view').selectOption('unassigned');await page.locator('#filters button').click();await page.waitForFunction(()=>document.querySelector('#page').textContent==='1–1 of 1')
  await page.screenshot({path:path.join(folder,'desktop.png'),fullPage:true})
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(folder,'mobile.png'),fullPage:true})
  await page.setViewportSize({width:1440,height:1100});await page.locator('#edit').click();await page.screenshot({path:path.join(folder,'editor.png'),fullPage:true})
  await page.locator('#cancel-edit').click();assert.equal(await page.locator('#editor-fields fieldset').count(),0)
  // Successful write with lost reply: clear uncertain local state; never retry.
  let lost=0;await page.route('**/responsibility/source/sample/project/config',async route=>{if(route.request().method()!=='PUT')return route.continue();lost++;await route.fetch();await route.abort()})
  await change('Saved despite lost response');await confirm();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('save may already have completed'))
  assert.equal(lost,1);assert.equal(await page.locator('#panel').isVisible(),false);await page.unroute('**/responsibility/source/sample/project/config')
  await page.locator('#reload').click();await revision(6);assert.equal((await current()).fields[0].name,'Saved despite lost response');assert.equal(lost,1)
  // Read-only identity: no edit affordance, and direct API mutation is rejected.
  await page.locator('#disconnect').click();await connect('reader');await scope();await revision(6);assert.equal(await page.locator('#edit').isVisible(),false)
  const denied=await context.request.put(base+'/config',{headers:{Authorization:'Bearer test-reader','If-Match':'"6"'},data:{fields:(await current()).fields}});assert.equal(denied.status(),404)
  // A late config response after a new scope must not trigger the old candidate GET.
  let seen,release,finished;const started=new Promise(r=>seen=r),barrier=new Promise(r=>release=r),done=new Promise(r=>finished=r);let oldCandidates=0
  const listener=r=>{if(r.url().includes('/alpha/responsibility/')&&r.url().includes('/candidates?'))oldCandidates++};page.on('request',listener)
  await page.route('**/api/workspaces/alpha/responsibility/source/sample/project/config',async route=>{const response=await route.fetch();seen();await barrier;await route.fulfill({response});finished()})
  await page.locator('#reload').click();await within(started);await scope('commons');await revision(1);const before=oldCandidates
  release();await within(done);await flush();assert.equal(oldCandidates,before);assert.match(await page.locator('#scope-title').textContent(),/^commons/);assert.ok(!(await page.locator('#panel').textContent()).includes('Synthetic UI change'))
  page.off('request',listener);await page.unroute('**/api/workspaces/alpha/responsibility/source/sample/project/config')
  // Pagehide disconnects pending writes. Old replies cannot disturb a new identity/scope.
  await page.locator('#disconnect').click();await connect();await scope();await revision(6)
  let putSeen,putRelease,putFinished;const putStarted=new Promise(r=>putSeen=r),putBarrier=new Promise(r=>putRelease=r),putDone=new Promise(r=>putFinished=r)
  await page.route('**/responsibility/source/sample/project/config',async route=>{if(route.request().method()!=='PUT')return route.continue();const response=await route.fetch();putSeen();await putBarrier;await route.fulfill({response});putFinished()})
  await change('Saved while disconnected');await confirm();await within(putStarted);await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide')))
  assert.equal(await page.locator('#editor-fields fieldset').count(),0);assert.equal(await page.locator('#confirm-summary').textContent(),'');assert.equal(await page.locator('#workspace').inputValue(),'')
  await connect('reader');await scope();await revision(7);putRelease();await within(putDone);await flush();await revision(7);assert.equal(await page.locator('#edit').isVisible(),false)
  await page.unroute('**/responsibility/source/sample/project/config')
  await page.route('**/responsibility/source/sample/project/capabilities',route=>route.fulfill({status:401,contentType:'application/json',body:'{"error":"authentication_required"}'}))
  await page.locator('#reload').click();await page.waitForFunction(()=>!document.querySelector('#connect').hidden);assert.equal(await page.locator('#workspace').inputValue(),'');assert.equal(await page.locator('#configuration li').count(),0)
  await page.unroute('**/responsibility/source/sample/project/capabilities');await connect('reader');await scope();await revision(7)
  await page.goto('about:blank');await page.goBack();await page.waitForFunction(()=>!document.querySelector('#connect').hidden);assert.equal(await page.locator('#candidates li').count(),0)
  assert.deepEqual(await page.evaluate(()=>[localStorage.length,sessionStorage.length]),[0,0]);assert.deepEqual(external,[]);assert.deepEqual(errors,[]);assert.deepEqual(dialogs,[]);assert.deepEqual(downloads,[])
  console.log('Responsibility acceptance passed: real authorized API, config CAS, uncertainty, scope/identity fencing, XSS and responsive screenshots.')
 }finally{await browser?.close();child.kill();await new Promise(resolve=>{if(child.exitCode!==null)resolve();else child.once('exit',resolve)})}
}
main().catch(error=>{console.error(error.message);process.exitCode=1})
