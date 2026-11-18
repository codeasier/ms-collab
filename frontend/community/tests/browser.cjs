// Synthetic browser acceptance only. Never load real credentials or provider data.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os')
const {spawn}=require('node:child_process'),{chromium}=require('playwright')
const within=async promise=>{let t;try{return await Promise.race([promise,new Promise((_,reject)=>t=setTimeout(()=>reject(Error('barrier timeout')),10000))])}finally{clearTimeout(t)}}
async function main(){
 const child=spawn('python3',['-m','packages.community_ui.tests.browser_server'],{cwd:path.resolve(__dirname,'../../..'),stdio:['ignore','pipe','pipe']});child.stderr.resume();let browser
 try{
  const origin=await within(new Promise((resolve,reject)=>{let text='';child.stdout.on('data',c=>{text+=c;const m=text.match(/http:\/\/127\.0\.0\.1:\d+/);if(m)resolve(m[0])});child.once('exit',()=>reject(Error('server stopped')))}))
  browser=await chromium.launch({headless:true,...(process.env.MS_COLLAB_BROWSER_EXECUTABLE?{executablePath:process.env.MS_COLLAB_BROWSER_EXECUTABLE}:{})});const context=await browser.newContext({viewport:{width:1440,height:1000}}),external=[],errors=[],writes=[]
  await context.route('**/*',route=>{if(new URL(route.request().url()).origin===origin)return route.continue();external.push(route.request().url());return route.abort()})
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{assert.ok(!r.url().includes('synthetic-'));if(r.method()==='POST')writes.push({url:r.url(),body:r.postDataJSON()})})
  const base='/api/workspaces/alpha/community/source/sample/project'
  const headers={Authorization:'Bearer synthetic-admin'}
  const ready=()=>page.locator('#panel:not([hidden])').waitFor()
  const connect=async token=>{await page.locator('#credential').fill(token);await page.locator('#connect button').click()}
  const open=async(workspace='alpha')=>{await page.locator('#workspace').fill(workspace);await page.locator('#connection').fill('source');await page.locator('#repository').fill('sample/project');await page.locator('#scope-form button').click();await ready()}
  const settled=()=>page.waitForFunction(()=>!document.querySelector('#reload').disabled&&!document.querySelector('#panel').hidden)
  // Count all browser requests, not just the writes the original journey observes.
  let requestCount=0;page.on('request',()=>{requestCount++})
  const labelPanel=page.locator('#shared-labels'),labelInput=page.locator('#label-input'),options=page.locator('#label-options button')
  const expectNoLabelState=async()=>{
   assert.equal(await labelPanel.isVisible(),false);assert.equal(await options.count(),0)
   assert.equal(await labelInput.inputValue(),'');assert.equal(await page.locator('#label-status').textContent(),'')
  }
  const sharedReply=page.waitForResponse(r=>r.url()===origin+base+'/items?limit=100&offset=0'&&r.request().method()==='GET',{timeout:15000})
  await page.goto(origin+'/community/');await connect('synthetic-reader');await open()
  assert.equal(await page.locator('#credential').inputValue(),'');assert.equal(await page.locator('#review-view').isVisible(),false);assert.equal(await page.locator('#refresh').isVisible(),false)
  assert.match(await page.locator('#summary').textContent(),/2 matching items/);assert.equal(await page.locator('#items img,#items b').count(),0);assert.match(await page.locator('#items').textContent(),/<img src=x/)
  const response=await sharedReply;assert.equal(response.status(),200)
  const {data:shared}=await response.json(),label='<b>plain label</b>'
  assert.equal(shared.view,'items');assert.equal(shared.available,true);assert.equal(shared.total,2)
  assert.deepEqual(shared.scope,{workspace_id:'alpha',connection_id:'source',repository:'sample/project'})
  assert.deepEqual(shared.items.map(i=>i.number).sort((a,b)=>a-b),[1,101])
  assert.ok(shared.items.every(i=>i.kind==='issue'&&!Object.hasOwn(i,'approved')&&!Object.hasOwn(i,'fingerprint')&&i.labels.length===1&&i.labels[0]===label))
  const rendered=page.locator('#items .record'),beforeLabels=requestCount
  assert.equal(await labelPanel.isVisible(),true);assert.equal(await options.count(),1)
  assert.equal(await options.locator('.label-name').textContent(),'"'+label+'"')
  assert.match(await options.textContent(),/\b2 items\b/);assert.equal(await options.locator('b,img').count(),0)
  assert.equal(await options.getAttribute('aria-pressed'),'false');await options.click()
  assert.equal(await options.getAttribute('aria-pressed'),'true');assert.equal(await rendered.count(),2)
  // A title-only exact input and a case variant both miss; counts still describe the full base.
  for(const miss of ['Synthetic issue 1','<B>plain label</B>']){
   await labelInput.fill(miss);await page.locator('#label-apply').click()
   assert.equal(await rendered.count(),0);assert.match(await page.locator('#summary').textContent(),/0 matching items of 2 loaded/)
   assert.match(await options.textContent(),/\b2 items\b/);assert.equal(await options.getAttribute('aria-pressed'),'false')
  }
  await labelInput.fill(label);await labelInput.press('Enter');assert.equal(await rendered.count(),2);assert.equal(await options.getAttribute('aria-pressed'),'true')
  await page.locator('#label-clear').click();assert.equal(await options.getAttribute('aria-pressed'),'false');assert.equal(await labelInput.inputValue(),'')
  assert.equal(await rendered.count(),2);await page.waitForLoadState('networkidle');assert.equal(requestCount,beforeLabels);assert.equal(writes.length,0)
  await page.locator('#disconnect').click();await expectNoLabelState();await connect('synthetic-admin');await open()
  // Leave the shared view with a real selection; none of that state may survive into private review.
  await options.click();assert.equal(await options.getAttribute('aria-pressed'),'true')
  await page.locator('#review-view').click();await ready();await expectNoLabelState()
  assert.match(await page.locator('#summary').textContent(),/101 loaded/);assert.match(await page.locator('#approval-summary').textContent(),/2 approved/)
  await page.getByRole('checkbox',{name:'Approve issue 100',exact:true}).check();await page.locator('#save').click();assert.match(await page.locator('#confirm-note').textContent(),/Add 1, remove 0; 3 total/)
  await page.locator('#cancel').click();assert.equal(writes.length,0);await page.locator('#save').click();await page.keyboard.press('Escape');assert.equal(writes.length,0)
  // Filter hides both previously approved rows; saving must keep both anyway.
  await page.locator('#query').fill('Synthetic issue 100');await page.locator('#filters button').click();assert.match(await page.locator('#summary').textContent(),/1 matching/)
  await page.locator('#save').click();await page.locator('#confirm').dblclick();await settled();assert.equal(writes.length,1);assert.equal(writes[0].body.fingerprints.length,3)
  await page.locator('#query').fill('');await page.locator('#filters button').click()
  // External writer invalidates the displayed revision. No blind retry.
  await page.getByRole('checkbox',{name:'Approve issue 99',exact:true}).check();await page.locator('#save').click()
  const caps=(await (await context.request.get(origin+base+'/capabilities',{headers})).json()).data
  const conflict=await context.request.post(origin+base+'/approve',{headers:{...headers,'If-Match':`"${caps.revision}"`},data:{fingerprints:writes[0].body.fingerprints}});assert.equal(conflict.status(),200)
  await page.locator('#confirm').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('changed elsewhere'));assert.equal(await page.locator('#save').isDisabled(),true)
  // Refresh is explicit; changed titles and labels invalidate previous content approvals.
  await page.locator('#refresh').click();await page.locator('#cancel').click();assert.equal(writes.length,2)
  await page.locator('#refresh').click();await page.locator('#confirm').click();await settled();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('No new content'))
  await page.locator('#items-view').click();await ready();assert.match(await page.locator('#summary').textContent(),/0 matching/);assert.ok(!(await page.locator('#items').textContent()).includes('Changed synthetic'))
  await page.locator('#review-view').click();await ready();assert.match(await page.locator('#items').textContent(),/Changed synthetic issue/)
  // Delayed reads cannot repopulate a newer scope.
  let seen,release,done;const started=new Promise(r=>seen=r),gate=new Promise(r=>release=r),handled=new Promise(r=>done=r)
  await page.route('**/alpha/community/source/sample/project/review?*',async route=>{seen();await gate;await route.continue().catch(()=>{});done()})
  await page.locator('#reload').click();await within(started);await open('commons');release();await within(handled);assert.match(await page.locator('#scope-title').textContent(),/^commons/)
  await page.unroute('**/alpha/community/source/sample/project/review?*')
  // A wrong-scope response hides everything and never enables writes.
  await page.route('**/commons/community/source/sample/project/items?*',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({data:{scope:{workspace_id:'alpha'},revision:1,available:false,items:[]}})}))
  await page.locator('#reload').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('valid, consistent'));assert.equal(await page.locator('#panel').isVisible(),false)
  await page.unroute('**/commons/community/source/sample/project/items?*');await open('commons')
  const folder=path.join(process.env.RUNNER_TEMP||os.tmpdir(),'ms-collab-community');fs.mkdirSync(folder,{recursive:true})
  await page.locator('#review-view').click();await ready();await page.screenshot({path:path.join(folder,'desktop.png'),fullPage:true})
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.ok(await page.locator('#actions button:visible').evaluateAll(buttons=>buttons.every(button=>button.scrollWidth<=button.clientWidth)));await page.screenshot({path:path.join(folder,'mobile.png'),fullPage:true})
  await open('empty');assert.match(await page.locator('#summary').textContent(),/No snapshot/)
  // Page lifecycle clears credential, private content, scope, draft and dialog.
  await page.locator('#refresh').click();await page.goto(origin+'/unavailable');await page.goBack();await page.locator('#connect:not([hidden])').waitFor();await page.goForward();await page.goBack();await page.locator('#connect:not([hidden])').waitFor()
  assert.equal(await page.locator('#confirmation').isVisible(),false);assert.equal(await page.locator('#items').textContent(),'');assert.equal(await page.locator('#confirm-note').textContent(),'');assert.equal(await page.locator('#workspace').inputValue(),'')
  // Non-reader approver and refresher still get only their explicit capabilities.
  await connect('synthetic-reviewer');await open();assert.equal(await page.locator('#items-view').isVisible(),false);assert.equal(await page.locator('#refresh').isVisible(),false);assert.equal(await page.locator('#approval-panel').isVisible(),true)
  await expectNoLabelState()
  await page.locator('#disconnect').click();await connect('synthetic-refresh');await open();assert.equal(await page.locator('#items-view').isVisible(),false);assert.equal(await page.locator('#review-view').isVisible(),false);assert.equal(await page.locator('#refresh').isVisible(),true);assert.equal(await page.locator('#items').textContent(),'')
  await expectNoLabelState()
  await page.locator('#disconnect').click();await connect('synthetic-admin');await open()
  // An accepted mutation may finish after disconnect; its late reply must not restore data.
  let writeSeen,writeRelease,writeDone;const writeStarted=new Promise(r=>writeSeen=r),writeGate=new Promise(r=>writeRelease=r),writeHandled=new Promise(r=>writeDone=r)
  await page.route('**/alpha/community/source/sample/project/refresh',async route=>{const response=await route.fetch();writeSeen();await writeGate;await route.fulfill({response}).catch(()=>{});writeDone()})
  await page.locator('#refresh').click();await page.locator('#confirm').click();await within(writeStarted);await page.locator('#disconnect').click();writeRelease();await within(writeHandled)
  assert.equal(await page.locator('#panel').isVisible(),false);assert.equal(await page.locator('#items').textContent(),'');assert.match(await page.locator('#status').textContent(),/may have completed/)
  await page.unroute('**/alpha/community/source/sample/project/refresh');await connect('synthetic-admin');await open()
  await page.route('**/alpha/community/source/sample/project/capabilities',route=>route.fulfill({status:401,contentType:'application/json',body:'{"error":"expired"}'}))
  await page.locator('#reload').click();await page.waitForFunction(()=>!document.querySelector('#connect').hidden);assert.equal(await page.locator('#items').textContent(),'')
  assert.deepEqual(await page.evaluate(()=>({local:localStorage.length,session:sessionStorage.length})),{local:0,session:0});assert.deepEqual(external,[]);assert.deepEqual(errors,[])
  console.log('Authorized community browser acceptance passed; synthetic desktop/mobile screenshots written.')
 }finally{await browser?.close();child.kill();await new Promise(resolve=>{if(child.exitCode!==null)resolve();else child.once('exit',resolve)})}
}
main().then(()=>console.log('COMMUNITY_LABELS_STANDALONE_PASS: 2 real approved shared rows, exact label controls made no browser request; review and refresh-only contained no retained label state. Synthetic loopback only.')).catch(error=>{console.error(error.stack);process.exitCode=1})
