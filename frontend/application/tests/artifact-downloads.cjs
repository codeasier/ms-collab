// Real HTTPS cookie and native-browser download acceptance using synthetic bytes.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),readline=require('node:readline'),crypto=require('node:crypto')
const {spawn,execFileSync}=require('node:child_process'),{chromium}=require('playwright'),{createScenarioRouter}=require('../../workspace-admin/tests/scenario-router.cjs')
const flush=page=>page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))))
async function within(p){let timer;try{return await Promise.race([p,new Promise((_,reject)=>timer=setTimeout(()=>reject(Error('synthetic barrier timeout')),15000))])}finally{clearTimeout(timer)}}
async function main(){
 const temp=fs.mkdtempSync(path.join(os.tmpdir(),'ms-collab-download-session-'));let child,browser,router;const releases=[]
 try{
  execFileSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-days','1','-keyout',path.join(temp,'key.pem'),'-out',path.join(temp,'cert.pem'),'-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'],{stdio:'ignore'})
  child=spawn('python3',['-m','packages.artifact_download.tests.session_browser_server',path.join(temp,'cert.pem'),path.join(temp,'key.pem')],{env:{...process.env,MS_COLLAB_SYNTHETIC_DOWNLOAD_SESSION:'1'},stdio:['ignore','pipe','pipe']});child.stderr.resume()
  const lines=readline.createInterface({input:child.stdout}),info=await within(new Promise((resolve,reject)=>{lines.once('line',l=>{try{resolve(JSON.parse(l))}catch(e){reject(e)}});child.once('exit',()=>reject(Error('fixture stopped')))}))
  const output=path.join(process.env.RUNNER_TEMP||os.tmpdir(),'ms-collab-download-session');fs.mkdirSync(output,{recursive:true})
  browser=await chromium.launch({headless:true});const context=await browser.newContext({ignoreHTTPSErrors:true,acceptDownloads:true,viewport:{width:1440,height:1100}}),page=await context.newPage(),downloads=[],errors=[],outside=[]
  page.on('pageerror',e=>{errors.push(e.message);console.error(e.message)});page.on('download',d=>downloads.push(d));page.on('request',r=>{assert.ok(!r.headers().authorization);const u=new URL(r.url());if(![info.origin,new URL(info.provider).origin].includes(u.origin))outside.push(u.origin)})
  await context.addInitScript(()=>{
   if(!globalThis.crypto?.subtle)return // about:blank has no secure-context digest
   const original=fetch;window.fetch=(input,options={})=>{const {signal,...rest}=options;return original(input,rest)}
   const digest=crypto.subtle.digest.bind(crypto.subtle);crypto.subtle.digest=async(...args)=>{const result=await digest(...args);if(window.__holdDigest){window.__digestStarted=true;await new Promise(r=>window.__releaseDigest=r)}return result}
   const create=URL.createObjectURL.bind(URL),revoke=URL.revokeObjectURL.bind(URL),urls=new Set();URL.createObjectURL=b=>{const u=create(b);urls.add(u);return u};URL.revokeObjectURL=u=>{urls.delete(u);return revoke(u)};window.__downloadURLCount=()=>urls.size
  })
  router=createScenarioRouter({origin:info.origin,onExternal:()=>outside.push('external')});await context.route('**/api/**',r=>router.handle(r))
  const ctrl=action=>page.evaluate(async action=>{const r=await fetch('/synthetic/download/'+action,{method:'POST'});return r.json()},action)
  const open=async()=>{for(const [id,value]of Object.entries({workspace:'alpha',connection:'source',bucket:'bucket'}))await page.locator('#'+id).fill(value);await page.locator('#scope-form button').click();await page.getByRole('button',{name:'builds/',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#results').textContent.includes('<svg'))}
  const check=async()=>{await page.locator('#results .row').filter({hasText:'<svg onload=alert(1)>'}).getByRole('button',{name:'Check download',exact:true}).click();await page.waitForFunction(()=>!document.querySelector('#download-confirm').disabled)}
  const login=async()=>{await page.locator('#app-sign-in').click();await page.waitForFunction(()=>document.querySelector('#app-principal')?.textContent==='admin');await page.locator('#app-artifacts-link').click();await page.waitForFunction(()=>!document.querySelector('#session').hidden)}
  await page.goto(info.origin+'/artifacts/');await page.waitForFunction(()=>!document.querySelector('#app-sign-in').disabled);await login();await open();assert.equal(await page.locator('input[type=password]').count(),0);assert.equal(await page.locator('svg').count(),0)
  await page.screenshot({path:path.join(output,'catalog.png'),fullPage:true});await check();assert.equal((await ctrl('state')).calls,0);assert.equal(await page.locator('#download-digest').textContent(),info.sha256)
  await page.screenshot({path:path.join(output,'confirmation.png'),fullPage:true});await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(output,'mobile.png'),fullPage:true});await page.setViewportSize({width:1440,height:1100})
  await page.locator('#download-cancel').click();assert.equal(await page.locator('#download-key').textContent(),'');assert.equal((await ctrl('state')).calls,0);assert.equal(downloads.length,0)
  // Same metadata/revision but changed trusted bytes must fail before provider/lease.
  await check();await ctrl('rotate-binding');await page.locator('#download-confirm').click();await page.waitForFunction(()=>document.querySelector('#download-status').textContent.includes('bytes or publication changed'))
  let state=await ctrl('state');assert.equal(state.loads,0);assert.equal(state.calls,0);assert.deepEqual(state.leases,[]);assert.equal(downloads.length,0);await ctrl('reset')
  await check();const fileEvent=page.waitForEvent('download');await page.locator('#download-confirm').click();const file=await within(fileEvent);await file.saveAs(path.join(temp,'verified.bin'))
  const bytes=fs.readFileSync(path.join(temp,'verified.bin'));assert.equal(bytes.length,info.size);assert.equal(crypto.createHash('sha256').update(bytes).digest('hex'),info.sha256);assert.equal(file.suggestedFilename(),'artifact-_svg_onload_alert_1__.bin')
  await page.waitForFunction(()=>document.querySelector('#download-status').textContent.includes('handed to your browser'));await page.waitForFunction(()=>window.__downloadURLCount()===0);await page.screenshot({path:path.join(output,'handoff.png'),fullPage:true})
  // Double submission creates only one transfer; a late response never launches two saves.
  const routePath='/api/workspaces/alpha/artifact-download/source/bucket/download'
  function held(){let started,release,finished;const seen=new Promise(r=>started=r),barrier=new Promise(r=>release=r),done=new Promise(r=>finished=r);releases.push(release);router.set(routePath,async route=>{const response=await route.fetch();started();await barrier;await route.fulfill({response});finished()});return {seen,release,done}}
  await check();let h=held(),before=downloads.length,calls=(await ctrl('state')).calls
  const repeatedEvent=page.waitForEvent('download');await page.evaluate(()=>{document.querySelector('#download-confirm').click();document.querySelector('#download-confirm').click()});await within(h.seen);h.release();await within(h.done);await within(repeatedEvent);await flush(page);assert.equal(downloads.length,before+1);assert.equal((await ctrl('state')).calls,calls+1);router.remove(routePath)
  await page.waitForFunction(()=>window.__downloadURLCount()===0)
  // Even a corrupt same-origin response cannot bypass the independent browser hash.
  router.set(routePath,async route=>{const response=await route.fetch(),data=await response.body();data[0]^=1;await route.fulfill({response,body:data})})
  await check();before=downloads.length;await page.locator('#download-confirm').click();await page.waitForFunction(()=>document.querySelector('#download-status').textContent.includes('checksum'));assert.equal(downloads.length,before);assert.equal(await page.evaluate(()=>window.__downloadURLCount()),0);router.remove(routePath)
  // Ignore abort: scope edit wins over an already accepted but delayed response.
  await check();h=held();await page.locator('#download-confirm').click();await within(h.seen);await page.locator('#workspace').fill('beta');h.release();await within(h.done);await flush(page);assert.equal(downloads.length,before);assert.equal(await page.locator('#download-panel').isVisible(),false);assert.equal(await page.locator('#download-key').textContent(),'');router.remove(routePath)
  await open();await check();await page.evaluate(()=>{window.__holdDigest=true;window.__digestStarted=false});await page.locator('#download-confirm').click();await page.waitForFunction(()=>window.__digestStarted)
  await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide')));await page.evaluate(()=>{window.__holdDigest=false;window.__releaseDigest()});await flush(page);assert.equal(downloads.length,before);assert.equal(await page.evaluate(()=>window.__downloadURLCount()),0);assert.equal(await page.locator('#download-key').textContent(),'')
  await page.locator('#app-recheck').click();await page.waitForFunction(()=>!document.querySelector('#session').hidden);await open();await check()
  // Genuine HTTP logout during provider acquisition stops output and cleans the lease.
  await ctrl('pause-acquire');await page.locator('#download-confirm').click();await page.waitForFunction(async()=>{const r=await fetch('/synthetic/download/state',{method:'POST'});return (await r.json()).active===1})
  await page.locator('#app-sign-out').click();await page.waitForFunction(()=>document.querySelector('#app-status').textContent.includes('server session is revoked'));await ctrl('release-acquire')
  await page.waitForFunction(async()=>{const r=await fetch('/synthetic/download/state',{method:'POST'});return (await r.json()).active===0});assert.equal(downloads.length,before);assert.equal(await page.locator('#download-panel').isVisible(),false)
  await login();await open();await check();calls=(await ctrl('state')).calls;await ctrl('revoke-member');await page.locator('#download-confirm').click();await page.waitForFunction(()=>document.querySelector('#download-status').textContent.includes('unavailable'));assert.equal((await ctrl('state')).calls,calls);assert.equal(downloads.length,before);assert.equal(await page.locator('#download-key').textContent(),'')
  await page.screenshot({path:path.join(output,'denied.png'),fullPage:true});await router.drain();await context.unrouteAll({behavior:'wait'})
  await page.goto('about:blank');await page.goBack();await page.waitForFunction(()=>document.querySelector('#app-principal')?.textContent==='admin');assert.equal(await page.locator('#catalog').isVisible(),false);assert.equal(await page.locator('#download-panel').isVisible(),false)
  assert.deepEqual(await page.evaluate(()=>[localStorage.length,sessionStorage.length,window.__downloadURLCount()]),[0,0,0]);assert.deepEqual(errors,[]);assert.deepEqual(outside,[])
  console.log('Synthetic HTTPS/native-file download acceptance passed: exact SHA/size, confirmation TOCTOU, no bearer, cancellation/late fetch/digest, scope and real logout, cleanup and responsive screenshots. No real cloud acceptance.')
 }finally{for(const release of releases)release();await browser?.close();if(child&&child.exitCode===null){child.kill();await within(new Promise(r=>child.once('exit',r)))}fs.rmSync(temp,{recursive:true,force:true})}
}
main().catch(e=>{console.error(e.stack);process.exitCode=1})
