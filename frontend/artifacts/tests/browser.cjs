// Isolated synthetic browser-to-WSGI acceptance; no live provider or download.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os')
const {spawn}=require('node:child_process'),{chromium}=require('playwright')
const within=async promise=>{let t;try{return await Promise.race([promise,new Promise((_,reject)=>t=setTimeout(()=>reject(new Error('barrier timeout')),12000))])}finally{clearTimeout(t)}}
async function main(){
 const child=spawn('python3',['-m','packages.artifacts_ui.tests.browser_server'],{cwd:path.resolve(__dirname,'../../..'),stdio:['ignore','pipe','pipe']});child.stderr.resume()
 let browser
 try{
  const origin=await within(new Promise((resolve,reject)=>{let s='';child.stdout.on('data',c=>{s+=c;const m=s.match(/http:\/\/127\.0\.0\.1:\d+/);if(m)resolve(m[0])});child.once('exit',()=>reject(new Error('server stopped')))}))
  browser=await chromium.launch({headless:true,...(process.env.MS_COLLAB_BROWSER_EXECUTABLE?{executablePath:process.env.MS_COLLAB_BROWSER_EXECUTABLE}:{})})
  const context=await browser.newContext({viewport:{width:1440,height:1000}}),external=[],errors=[],downloads=[],dialogs=[],requests=[]
  await context.route('**/*',route=>{if(new URL(route.request().url()).origin===origin)return route.continue();external.push(route.request().url());return route.abort()})
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));page.on('download',d=>downloads.push(d));page.on('dialog',async d=>{dialogs.push(d.message());await d.dismiss()})
  page.on('request',r=>{requests.push({method:r.method(),url:r.url()});assert.ok(!r.url().includes('synthetic-reader'))})
  const connect=async(token='synthetic-reader')=>{await page.locator('#credential').fill(token);await page.locator('#connect button').click();assert.equal(await page.locator('#credential').inputValue(),'')}
  const scope=async(w='alpha')=>{await page.locator('#workspace').fill(w);await page.locator('#connection').fill('example-storage');await page.locator('#bucket').fill('releases');await page.locator('#scope-form button').click()}
  const loaded=async w=>page.waitForFunction(w=>document.querySelector('#status').textContent.includes(`loaded for ${w} /`),w)
  await page.goto(origin+'/artifacts/');assert.equal(await page.locator('#catalog').isVisible(),false);await connect();await scope();await loaded('alpha')
  assert.equal(await page.locator('#quarter option').count(),2);assert.match(await page.locator('#results').textContent(),/builds/)
  await page.getByRole('button',{name:'builds/',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#page-label').textContent==='Page 1 · 13 matching items')
  assert.ok((await page.locator('#results').textContent()).includes('<svg onload=alert(1)>.txt'));assert.equal(await page.locator('#results svg').count(),0)
  await page.locator('#next').click();await page.waitForFunction(()=>document.querySelector('#page-label').textContent==='Page 2 · 13 matching items');assert.equal(await page.locator('#results li').count(),3)
  await page.locator('#previous').click();await page.waitForFunction(()=>document.querySelector('#page-label').textContent.startsWith('Page 1'))
  await page.locator('#keyword').fill('package-1');assert.equal(await page.locator('#results li').count(),0);await page.locator('#query-form button').click();await page.waitForFunction(()=>document.querySelector('#page-label').textContent==='Page 1 · 2 matching items')
  await page.locator('#keyword').fill('');await page.locator('#sort').selectOption('size');await page.locator('#order').selectOption('desc');await page.locator('#query-form button').click();await loaded('alpha');assert.match(await page.locator('#results li').first().textContent(),/package-11/)
  await page.locator('#up').click();await loaded('alpha');await page.locator('#quarter').selectOption('2025Q4');await loaded('alpha');assert.match(await page.locator('#results').textContent(),/history/);assert.ok(!(await page.locator('#results').textContent()).includes('notes.txt'))
  await page.locator('#quarter').selectOption('2026Q1');await loaded('alpha');await page.locator('#view-id').fill('packages');await page.locator('#view-form button').first().click();await page.waitForFunction(()=>document.querySelector('#status').textContent.startsWith('Named view loaded'))
  assert.equal(await page.locator('#apply').isDisabled(),true);assert.equal(await page.locator('#up').isDisabled(),true);assert.match(await page.locator('#evidence').textContent(),/View revision 1/)
  await page.locator('#view-id').fill('missing');await page.locator('#view-form button').first().click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('unavailable or no longer accessible'));assert.equal(await page.locator('#results li').count(),0)
  await page.locator('#browse').click();await loaded('alpha')
  // Stale response cannot repopulate a newly selected scope, even if abort is late.
  let seen,release,done;const started=new Promise(r=>seen=r),gate=new Promise(r=>release=r),handled=new Promise(r=>done=r)
  await page.route('**/api/workspaces/alpha/artifacts/example-storage/releases/query',async route=>{seen();await gate;await route.continue().catch(()=>{});done()})
  await page.locator('#reload').click();await within(started);await scope('commons');release();await within(handled);await loaded('commons')
  assert.match(await page.locator('#results').textContent(),/shared-guide/);assert.ok(!(await page.locator('#results').textContent()).includes('package-'))
  await page.unroute('**/api/workspaces/alpha/artifacts/example-storage/releases/query')
  // An authorized-looking envelope with another workspace is never rendered.
  await page.route('**/api/workspaces/commons/artifacts/example-storage/releases/query',async route=>{const response=await route.fetch();const value=await response.json();value.data.scope.workspace_id='alpha';await route.fulfill({response,json:value})})
  await page.locator('#reload').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('valid response'));assert.equal(await page.locator('#results li').count(),0)
  await page.unroute('**/api/workspaces/commons/artifacts/example-storage/releases/query');await scope('alpha');await loaded('alpha');await page.getByRole('button',{name:'builds/',exact:true}).click();await loaded('alpha')
  const folder=path.join(process.env.RUNNER_TEMP||os.tmpdir(),'ms-collab-artifact-ui');fs.mkdirSync(folder,{recursive:true});await page.screenshot({path:path.join(folder,'desktop.png'),fullPage:true})
  await page.setViewportSize({width:390,height:844});await scope('commons');await loaded('commons');assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(folder,'mobile.png'),fullPage:true})
  // Unknown/private space cannot fall back to a visible public catalog.
  await scope('hidden');await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('unavailable or no longer accessible'));assert.equal(await page.locator('#catalog').isVisible(),false)
  await scope('alpha');await loaded('alpha')
  await page.route('**/api/workspaces/alpha/artifacts/example-storage/releases/quarters',route=>route.fulfill({status:401,contentType:'application/json',body:'{"error":"authentication_required"}'}))
  await page.locator('#reload').click();await page.waitForFunction(()=>!document.querySelector('#connect').hidden);assert.equal(await page.locator('#results').textContent(),'');assert.equal(await page.locator('#workspace').inputValue(),'')
  await page.unroute('**/api/workspaces/alpha/artifacts/example-storage/releases/quarters');await connect();await scope();await loaded('alpha')
  await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide')));assert.equal(await page.locator('#results').textContent(),'');assert.equal(await page.locator('#session').isVisible(),false)
  assert.deepEqual(await page.evaluate(()=>({local:localStorage.length,session:sessionStorage.length})),{local:0,session:0})
  assert.ok(requests.every(r=>r.method==='GET'||r.method==='POST'&&new URL(r.url).pathname.endsWith('/query')))
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);assert.deepEqual(downloads,[]);assert.deepEqual(dialogs,[])
  console.log('Artifact browser acceptance passed; two synthetic screenshots written.')
 }finally{await browser?.close();child.kill();await new Promise(resolve=>{if(child.exitCode!==null)resolve();else child.once('exit',resolve)})}
}
main().catch(error=>{console.error(error.message);process.exitCode=1})
