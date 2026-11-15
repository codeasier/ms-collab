// Synthetic provider-to-UI acceptance; never supply real credentials or upstream data.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),{spawn}=require('node:child_process'),{chromium}=require('playwright')
const within=async promise=>{let timer;try{return await Promise.race([promise,new Promise((_,reject)=>timer=setTimeout(()=>reject(Error('barrier timeout')),15000))])}finally{clearTimeout(timer)}}
async function main(){
 const child=spawn('python3',['-m','packages.pipeline_ui.tests.browser_server'],{cwd:path.resolve(__dirname,'../../..'),stdio:['ignore','pipe','pipe']});child.stderr.resume();let browser
 try{
  const origin=await within(new Promise((resolve,reject)=>{let output='';child.stdout.on('data',c=>{output+=c;const match=output.match(/http:\/\/127\.0\.0\.1:\d+/);if(match)resolve(match[0])});child.once('exit',()=>reject(Error('synthetic server stopped')))}))
  browser=await chromium.launch({headless:true,...(process.env.MS_COLLAB_BROWSER_EXECUTABLE?{executablePath:process.env.MS_COLLAB_BROWSER_EXECUTABLE}:{})})
  const context=await browser.newContext({viewport:{width:1440,height:1000}}),external=[],errors=[],writes=[]
  await context.route('**/*',route=>{if(new URL(route.request().url()).origin===origin)return route.continue();external.push(route.request().url());return route.abort()})
  const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));page.on('request',request=>{assert.ok(!request.url().includes('synthetic-'));if(request.method()!=='GET')writes.push(request.method())})
  const ready=()=>page.locator('#panel:not([hidden])').waitFor()
  const connect=async()=>{await page.locator('#credential').fill('synthetic-reader');await page.locator('#connect button').click()}
  const scope=async(workspace='alpha')=>{await page.locator('#workspace').fill(workspace);await page.locator('#connection').fill('example-ci');await page.locator('#pipeline').fill('build');await page.locator('#scope-form button').click()}
  const open=async(workspace='alpha')=>{await scope(workspace);await ready()}
  await page.goto(origin+'/pipelines/');await connect();assert.equal(await page.locator('#credential').inputValue(),'');await open()
  assert.match(await page.locator('#summary').textContent(),/20 matching runs of 20 stored/);assert.match(await page.locator('#revision').textContent(),/snapshot version 2/)
  assert.match(await page.locator('#coverage').textContent(),/2 runs with valid samples/);assert.match(await page.locator('#durations').textContent(),/12/)
  assert.equal(await page.locator('#durations img,#durations b').count(),0);assert.match(await page.locator('#durations').textContent(),/<img src=x/)
  await page.locator('#next').click();assert.match(await page.locator('#summary').textContent(),/Page 2 of 2/);await page.locator('#previous').click()
  await page.locator('#state').selectOption('running');await page.locator('#filters button').click();assert.match(await page.locator('#summary').textContent(),/1 matching runs/);assert.match(await page.locator('#coverage').textContent(),/2 runs with valid samples/)
  await page.getByRole('button',{name:'View run-19',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#detail-body').textContent.includes('not available'));await page.keyboard.press('Escape');assert.equal(await page.locator('#detail').isVisible(),false)
  await page.locator('#state').selectOption('');await page.locator('#filters button').click();await page.getByRole('button',{name:'View run-20',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#detail-status').textContent.includes('failure'))
  assert.equal(await page.locator('#detail-body img,#detail-body b').count(),0);assert.match(await page.locator('#detail-body').textContent(),/<img src=x/)
  const folder=path.join(process.env.RUNNER_TEMP||os.tmpdir(),'ms-collab-pipeline-ui');fs.mkdirSync(folder,{recursive:true});await page.screenshot({path:path.join(folder,'detail.png'),fullPage:true});await page.locator('#detail-close').click()
  // Closing a delayed detail and selecting a different run fences its late response.
  let seen,release,done;const started=new Promise(r=>seen=r),gate=new Promise(r=>release=r),handled=new Promise(r=>done=r)
  await page.route('**/alpha/pipelines/example-ci/build/runs/run-20',async route=>{seen();await gate;await route.continue().catch(()=>{});done()})
  await page.getByRole('button',{name:'View run-20',exact:true}).click();await within(started);await page.locator('#detail-close').click();await page.getByRole('button',{name:'View run-18',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#detail-status').textContent.includes('failure'));release();await within(handled);assert.equal(await page.locator('#detail-title').textContent(),'run-18');await page.locator('#detail-close').click();await page.unroute('**/alpha/pipelines/example-ci/build/runs/run-20')
  // A detail read from a newer snapshot must not be combined with the old list.
  await page.route('**/alpha/pipelines/example-ci/build/runs/run-20',async route=>{const response=await route.fetch(),body=await response.json();body.data.source.revision++;await route.fulfill({response,json:body})})
  await page.getByRole('button',{name:'View run-20',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('could not be validated'));assert.equal(await page.locator('#panel').isVisible(),false);assert.equal(await page.locator('#detail').isVisible(),false);await page.unroute('**/alpha/pipelines/example-ci/build/runs/run-20');await open()
  // A late duration response cannot restore the previous workspace.
  let listSeen,listRelease,listDone;const listStarted=new Promise(r=>listSeen=r),listGate=new Promise(r=>listRelease=r),listHandled=new Promise(r=>listDone=r)
  await page.route('**/alpha/pipelines/example-ci/build/durations?*',async route=>{listSeen();await listGate;await route.continue().catch(()=>{});listDone()})
  await page.locator('#reload').click();await within(listStarted);await open('commons');listRelease();await within(listHandled);assert.match(await page.locator('#scope-title').textContent(),/^commons/);await page.unroute('**/alpha/pipelines/example-ci/build/durations?*')
  // Wrong scope fails closed; stored empty subset and unauthorized scope are distinct.
  await page.route('**/commons/pipelines/example-ci/build/durations?*',async route=>{const response=await route.fetch(),body=await response.json();body.data.scope.workspace_id='beta';await route.fulfill({response,json:body})})
  await page.locator('#reload').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('could not be validated'));assert.equal(await page.locator('#runs').textContent(),'');await page.unroute('**/commons/pipelines/example-ci/build/durations?*')
  await scope('beta');await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('unavailable to your current identity'));assert.equal(await page.locator('#panel').isVisible(),false)
  await open('empty');assert.match(await page.locator('#summary').textContent(),/0 matching runs/);assert.match(await page.locator('#durations').textContent(),/No valid/)
  await open('commons');await page.screenshot({path:path.join(folder,'desktop.png'),fullPage:true});await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.ok(await page.locator('button:visible').evaluateAll(buttons=>buttons.every(b=>b.scrollWidth<=b.clientWidth)));await page.screenshot({path:path.join(folder,'mobile.png'),fullPage:true})
  // Real Back/Forward lifecycle must not retain credentials or private records.
  await page.goto(origin+'/unavailable');await page.goBack();await page.locator('#connect:not([hidden])').waitFor();await page.goForward();await page.goBack();await page.locator('#connect:not([hidden])').waitFor();assert.equal(await page.locator('#runs').textContent(),'');assert.equal(await page.locator('#workspace').inputValue(),'');assert.equal(await page.locator('#credential').inputValue(),'')
  await connect();await open();await page.route('**/alpha/pipelines/example-ci/build/runs?*',route=>route.fulfill({status:401,contentType:'application/json',body:'{"error":"expired"}'}));await page.locator('#reload').click();await page.locator('#connect:not([hidden])').waitFor();assert.equal(await page.locator('#runs').textContent(),'');assert.equal(await page.locator('#detail-body').textContent(),'')
  assert.deepEqual(await page.evaluate(()=>({local:localStorage.length,session:sessionStorage.length})),{local:0,session:0});assert.deepEqual(external,[]);assert.deepEqual(errors,[]);assert.deepEqual(writes,[])
  console.log('Pipeline provider-to-UI synthetic acceptance passed; three screenshots written.')
 }finally{await browser?.close();child.kill();await new Promise(resolve=>{if(child.exitCode!==null)resolve();else child.once('exit',resolve)})}
}
main().catch(error=>{console.error(error.stack);process.exitCode=1})
