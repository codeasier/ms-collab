// Synthetic acquisition -> explicit approval -> actual browser/HTTP/index.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os')
const {spawn}=require('node:child_process'),{chromium}=require('playwright')
async function main(){
 const child=spawn('python3',['-m','packages.artifact_sync.tests.browser_server'],{cwd:path.resolve(__dirname,'../../..'),stdio:['ignore','pipe','pipe']});child.stderr.resume()
 let browser
 try{
  const origin=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('fixture timeout')),15000);let text='';child.stdout.on('data',chunk=>{text+=chunk;const m=text.match(/http:\/\/127\.0\.0\.1:\d+/);if(m){clearTimeout(timer);resolve(m[0])}});child.once('exit',()=>{clearTimeout(timer);reject(new Error('fixture stopped'))})})
  browser=await chromium.launch({headless:true})
  const context=await browser.newContext({viewport:{width:1440,height:1000}}),external=[],errors=[],dialogs=[],downloads=[]
  await context.route('**/*',route=>{if(new URL(route.request().url()).origin===origin)return route.continue();external.push('unexpected origin');return route.abort()})
  const page=await context.newPage()
  page.on('pageerror',e=>errors.push(e.message));page.on('dialog',async d=>{dialogs.push(d.message());await d.dismiss()});page.on('download',()=>downloads.push(1))
  page.on('request',r=>{assert.ok(!r.url().includes('synthetic-reader'));assert.ok(r.method()==='GET'||r.method()==='POST'&&new URL(r.url()).pathname.endsWith('/query'))})
  await page.goto(origin+'/artifacts/');await page.locator('#credential').fill('synthetic-reader');await page.locator('#connect button').click()
  assert.equal(await page.locator('#credential').inputValue(),'')
  const scope=async(w='alpha')=>{await page.locator('#workspace').fill(w);await page.locator('#connection').fill('source');await page.locator('#bucket').fill('bucket');await page.locator('#scope-form button').click()}
  await scope();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('loaded for alpha /'))
  await page.getByRole('button',{name:'builds/',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#page-label').textContent==='Page 1 · 2 matching items')
  assert.match(await page.locator('#results').textContent(),/approved.txt/)
  assert.match(await page.locator('#results').textContent(),/<svg onload=alert\(1\)>.txt/)
  assert.ok(!(await page.locator('#results').textContent()).includes('PENDING-PRIVATE'))
  assert.equal(await page.locator('#results svg').count(),0)
  assert.match(await page.locator('#evidence').textContent(),/Catalog revision 3/)
  const folder=path.join(process.env.RUNNER_TEMP||os.tmpdir(),'ms-collab-artifact-index');fs.mkdirSync(folder,{recursive:true})
  await page.screenshot({path:path.join(folder,'desktop.png'),fullPage:true})
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth))
  await page.screenshot({path:path.join(folder,'mobile.png'),fullPage:true})
  await scope('commons');await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('unavailable or no longer accessible'))
  assert.equal(await page.locator('#results li').count(),0)
  await page.locator('#disconnect').click();assert.equal(await page.locator('#catalog').isVisible(),false)
  assert.deepEqual(await page.evaluate(()=>[localStorage.length,sessionStorage.length]),[0,0])
  assert.deepEqual(external,[]);assert.deepEqual(errors,[]);assert.deepEqual(dialogs,[]);assert.deepEqual(downloads,[])
  console.log('Approved index browser acceptance passed; two synthetic screenshots written.')
 }finally{await browser?.close();child.kill();await new Promise(resolve=>{if(child.exitCode!==null)resolve();else child.once('exit',resolve)})}
}
main().catch(error=>{console.error(error.message);process.exitCode=1})
