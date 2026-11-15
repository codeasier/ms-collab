// Actual cookie/session HTTP UI. Only synthetic provider, scopes and metadata.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),readline=require('node:readline')
const {spawn,execFileSync}=require('node:child_process'),{chromium}=require('playwright')
const {createScenarioRouter}=require('../../workspace-admin/tests/scenario-router.cjs')
async function within(p){let timer;try{return await Promise.race([p,new Promise((_,reject)=>timer=setTimeout(()=>reject(new Error('synthetic barrier timeout')),15000))])}finally{clearTimeout(timer)}}
const frames=page=>page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))))
async function readable(page,selectors){
 const ratios=await page.evaluate(selectors=>{
  const rgb=v=>{const n=v.match(/[\d.]+/g).map(Number);return {rgb:n.slice(0,3),alpha:n[3]??1}},lum=a=>a.map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4}).reduce((n,v,i)=>n+v*[.2126,.7152,.0722][i],0)
  return selectors.map(selector=>{const e=document.querySelector(selector);if(!e||!e.checkVisibility()||e.disabled)throw Error('Invalid contrast probe '+selector);const fg=rgb(getComputedStyle(e).color);let node=e,bg;while(node){const v=rgb(getComputedStyle(node).backgroundColor);if(v.alpha===1){bg=v.rgb;break}if(v.alpha!==0)throw Error('Translucent background');node=node.parentElement}if(!bg||fg.alpha!==1)throw Error('Missing opaque contrast');const v=[lum(fg.rgb),lum(bg)].sort((a,b)=>a-b);return [selector,(v[1]+.05)/(v[0]+.05)]})
 },selectors)
 for(const [selector,ratio] of ratios)assert.ok(ratio>=4.5,`${selector}: contrast ${ratio}<4.5`)
}
async function main(){
 const temp=fs.mkdtempSync(path.join(os.tmpdir(),'ms-collab-artifact-session-cert-'));let child,browser,router
 try{
  execFileSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-days','1','-keyout',path.join(temp,'key.pem'),'-out',path.join(temp,'cert.pem'),'-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'],{stdio:'ignore'})
  child=spawn('python3',['-m','packages.application_ui.tests.artifact_browser_server',path.join(temp,'cert.pem'),path.join(temp,'key.pem')],{env:{...process.env,MS_COLLAB_SYNTHETIC_ARTIFACT_SESSION:'1'},stdio:['ignore','pipe','pipe']});child.stderr.resume()
  const lines=readline.createInterface({input:child.stdout}),info=await within(new Promise((resolve,reject)=>{lines.once('line',l=>{try{resolve(JSON.parse(l))}catch(e){reject(e)}});child.once('exit',()=>reject(new Error('fixture stopped')))}))
  const folder=path.join(process.env.RUNNER_TEMP||os.tmpdir(),'ms-collab-artifact-session');fs.mkdirSync(folder,{recursive:true})
  browser=await chromium.launch({headless:true});const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1440,height:1100}}),page=await context.newPage(),errors=[],external=[],writes=[],logins=[],dialogs=[]
  page.on('pageerror',e=>{errors.push(e.message);console.error('Synthetic page error',e.message)});page.on('dialog',async d=>{dialogs.push(d.message());await d.dismiss()})
  page.on('request',r=>{assert.ok(!r.headers().authorization);const url=new URL(r.url());if(![info.origin,new URL(info.provider).origin].includes(url.origin))external.push(url.origin);if(r.method()!=='GET')writes.push(url.pathname);if(url.pathname==='/auth/start')logins.push(1)})
  await context.addInitScript(()=>{const original=window.fetch;window.fetch=(input,options={})=>{const {signal,...rest}=options;return original.call(window,input,rest)}})
  router=createScenarioRouter({origin:info.origin,onExternal:()=>external.push('routed external')});await context.route('**/api/**',r=>router.handle(r))
  await page.goto(info.origin+'/artifacts/');await page.waitForFunction(()=>!document.querySelector('#app-sign-in').disabled)
  assert.equal(await page.locator('input[type=password]').count(),0);assert.equal(await page.locator('#session').isVisible(),false)
  await page.locator('#app-sign-in').click();await page.waitForFunction(()=>document.querySelector('#app-principal')?.textContent==='admin')
  await page.locator('#app-artifacts-link').click();await page.waitForFunction(()=>!document.querySelector('#session').hidden)
  const scope=async(w='alpha')=>{for(const [id,value]of Object.entries({workspace:w,connection:'source',bucket:'bucket'}))await page.locator('#'+id).fill(value);await page.locator('#scope-form button').click()}
  await scope();await page.waitForFunction(()=>document.querySelector('#results').textContent.includes('builds'))
  await page.getByRole('button',{name:'builds/',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#results').textContent.includes('<svg'))
  assert.equal(await page.locator('#results svg').count(),0);assert.ok(!(await page.locator('#results').textContent()).includes('private-candidate'))
  await page.locator('#view-id').fill('files');await page.locator('#view-form button').first().click();await page.waitForFunction(()=>document.querySelector('#path-label').textContent.includes('Named view: files'))
  await readable(page,['h1','.notice','#status','#scope-title','#quarter','#results .entry-name','#evidence','#app-principal'])
  await page.screenshot({path:path.join(folder,'catalog-desktop.png'),fullPage:true});await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(folder,'catalog-mobile.png'),fullPage:true});await page.setViewportSize({width:1440,height:1100})
  const csrfDenied=await page.evaluate(async()=>{const r=await fetch('/api/workspaces/alpha/artifacts/source/bucket/query',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({quarter:'2026Q1',definition:{}})});return r.status});assert.equal(csrfDenied,401)
  // Ineffective abort cannot revive an old catalog or launch its follow-up query.
  let seen,release,finished;const started=new Promise(r=>seen=r),barrier=new Promise(r=>release=r),done=new Promise(r=>finished=r),quarters='/api/workspaces/alpha/artifacts/source/bucket/quarters'
  router.set(quarters,async route=>{const response=await route.fetch();seen();await barrier;await route.fulfill({response});finished()})
  await page.locator('#reload').click();await within(started);await scope('beta');await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('unavailable'))
  const beforeLate=writes.length;release();await within(done);await frames(page);assert.equal(writes.length,beforeLate);assert.equal(await page.locator('#catalog').isVisible(),false);router.remove(quarters)
  const firstLogin=logins.length;await page.locator('#app-artifact-admin-link').click();await page.waitForFunction(()=>!document.querySelector('#session').hidden);assert.equal(logins.length,firstLogin);assert.equal(await page.locator('input[type=password]').count(),0)
  await scope();await page.waitForFunction(()=>!document.querySelector('#panel').hidden);assert.equal(await page.locator('#refresh').isVisible(),false)
  await page.locator('#review').click();await page.waitForFunction(()=>!document.querySelector('#preview').hidden);assert.match(await page.locator('#summary').textContent(),/3 metadata rows/);assert.match(await page.locator('#rows').textContent(),/private-candidate/);assert.equal(await page.locator('#rows svg').count(),0)
  await readable(page,['h1','.notice','#status','#scope-title','#review','#summary','#readers li','#rows strong','#digest'])
  await page.screenshot({path:path.join(folder,'approval-desktop.png'),fullPage:true});await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(folder,'approval-mobile.png'),fullPage:true});await page.setViewportSize({width:1440,height:1100})
  await page.locator('#acknowledge').check();const beforeCancel=writes.length;await page.locator('#approve').click();await readable(page,['#confirm-title','#confirm-detail','#confirm-submit','#cancel']);await page.locator('#cancel').click();assert.equal(writes.length,beforeCancel)
  await page.locator('#approve').click();await page.keyboard.press('Escape');assert.equal(await page.locator('#confirmation').isVisible(),false);assert.equal(await page.locator('#confirm-detail').textContent(),'');assert.equal(writes.length,beforeCancel)
  // A concurrent candidate revision invalidates even an otherwise matching digest.
  await page.locator('#approve').click();const stage=()=>page.evaluate(async()=>{const r=await fetch('/synthetic/artifact/stage',{method:'POST'});return r.json()});assert.equal((await stage()).revision,3)
  await page.locator('#confirm-submit').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('revision changed'));assert.equal(await page.locator('#preview').isVisible(),false);assert.equal(await page.locator('#rows li').count(),0)
  await page.screenshot({path:path.join(folder,'conflict.png'),fullPage:true})
  await page.locator('#reload').click();await page.waitForFunction(()=>document.querySelector('#revision').textContent.startsWith('Index revision 3'))
  await page.locator('#review').click();await page.waitForFunction(()=>!document.querySelector('#preview').hidden);await page.locator('#acknowledge').check();await page.locator('#approve').click()
  const approvals=writes.filter(p=>p.endsWith('/approve')).length;await page.evaluate(()=>{const f=document.querySelector('#confirm-form');f.requestSubmit();f.requestSubmit()});await page.waitForFunction(()=>document.querySelector('#revision').textContent.startsWith('Index revision 4'))
  assert.equal(writes.filter(p=>p.endsWith('/approve')).length,approvals+1)
  // Full approval is now visible through the SAME cookie on the catalog page.
  await page.locator('#app-artifacts-link').click();await page.waitForFunction(()=>!document.querySelector('#session').hidden);await scope();await page.getByRole('button',{name:'builds/',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#results').textContent.includes('private-candidate'))
  await page.locator('#app-artifact-admin-link').click();await page.waitForFunction(()=>!document.querySelector('#session').hidden);await scope();await stage();await page.locator('#reload').click();await page.waitForFunction(()=>document.querySelector('#revision').textContent.startsWith('Index revision 5'))
  await page.locator('#review').click();await page.waitForFunction(()=>!document.querySelector('#preview').hidden);await page.locator('#acknowledge').check();await page.locator('#approve').click()
  // Server accepts approval; pagehide+recheck cannot be undone by its late reply.
  let saveSeen,saveRelease,saveFinished;const saveStarted=new Promise(r=>saveSeen=r),saveBarrier=new Promise(r=>saveRelease=r),saveDone=new Promise(r=>saveFinished=r),approve='/api/workspaces/alpha/artifact-admin/source/bucket/approve'
  router.set(approve,async route=>{const response=await route.fetch();saveSeen();await saveBarrier;await route.fulfill({response});saveFinished()})
  await page.locator('#confirm-submit').click();await within(saveStarted);await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide')));assert.equal(await page.locator('#rows li').count(),0);assert.equal(await page.locator('#workspace').inputValue(),'');assert.equal(await page.locator('#app-principal').textContent(),'Not verified')
  await page.locator('#app-recheck').click();await page.waitForFunction(()=>document.querySelector('#app-principal').textContent==='admin'&&!document.querySelector('#session').hidden);await scope();await page.waitForFunction(()=>document.querySelector('#revision').textContent.startsWith('Index revision 6'))
  saveRelease();await within(saveDone);await frames(page);assert.match(await page.locator('#revision').textContent(),/^Index revision 6/);router.remove(approve)
  await stage();await page.locator('#reload').click();await page.waitForFunction(()=>document.querySelector('#revision').textContent.startsWith('Index revision 7'))
  // A delayed private review cannot survive real explicit logout, even without abort.
  let reviewSeen,reviewRelease,reviewFinished;const reviewStarted=new Promise(r=>reviewSeen=r),reviewBarrier=new Promise(r=>reviewRelease=r),reviewDone=new Promise(r=>reviewFinished=r),reviewPath='/api/workspaces/alpha/artifact-admin/source/bucket/review'
  router.set(reviewPath,async route=>{const response=await route.fetch();reviewSeen();await reviewBarrier;await route.fulfill({response});reviewFinished()})
  await page.locator('#review').click();await within(reviewStarted);await page.locator('#app-sign-out').click();await page.waitForFunction(()=>document.querySelector('#app-status').textContent.includes('server session is revoked'))
  reviewRelease();await within(reviewDone);await frames(page);router.remove(reviewPath);assert.equal(await page.locator('#rows li').count(),0);assert.equal(await page.locator('#session').isVisible(),false);assert.equal(await page.locator('#app-principal').textContent(),'Not verified')
  await page.locator('#app-sign-in').click();await page.waitForFunction(()=>document.querySelector('#app-principal')?.textContent==='admin');await page.locator('#app-artifact-admin-link').click();await page.waitForFunction(()=>!document.querySelector('#session').hidden);await scope();await page.waitForFunction(()=>document.querySelector('#revision').textContent.startsWith('Index revision 7'))
  // Current workspace revoke clears private preview without pretending to log out.
  await page.locator('#review').click();await page.waitForFunction(()=>!document.querySelector('#preview').hidden)
  await page.evaluate(()=>fetch('/synthetic/artifact/revoke-membership',{method:'POST'}));await page.locator('#reload').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('unavailable'));assert.equal(await page.locator('#rows li').count(),0);assert.equal(await page.locator('#panel').isVisible(),false)
  await page.locator('#app-artifacts-link').click();await page.waitForFunction(()=>!document.querySelector('#session').hidden);await scope();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('unavailable'));assert.equal(await page.locator('#results').textContent(),'')
  await page.evaluate(()=>fetch('/synthetic/artifact/revoke-session',{method:'POST'}));await scope();await page.waitForFunction(()=>document.querySelector('#app-principal').textContent==='Not verified');assert.equal(await page.locator('#session').isVisible(),false)
  await router.drain();await context.unrouteAll({behavior:'wait'})
  await page.goto('about:blank');await page.goBack();await page.waitForFunction(()=>document.querySelector('#app-principal')?.textContent==='Not verified');assert.equal(await page.locator('#catalog').isVisible(),false)
  assert.deepEqual(await page.evaluate(()=>[localStorage.length,sessionStorage.length]),[0,0]);assert.deepEqual(errors,[]);assert.deepEqual(external,[]);assert.deepEqual(dialogs,[])
  console.log('Artifact session acceptance passed: real cookie-only catalog and whole-candidate approval, CSRF, CAS, cancellation/late replies, workspace/session revocation, safe text and five responsive screenshots. No real OBS or download UI claim.')
 }finally{await browser?.close();if(child&&child.exitCode===null){child.kill();await within(new Promise(r=>child.once('exit',r)))}fs.rmSync(temp,{recursive:true,force:true})}
}
main().catch(error=>{console.error(error.stack);process.exitCode=1})
