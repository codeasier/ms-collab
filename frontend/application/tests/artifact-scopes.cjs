// Actual HTTPS/Cookie/API/SQLite discovery; no remote provider or production data.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),readline=require('node:readline')
const {spawn,execFileSync}=require('node:child_process'),{chromium}=require('playwright')
const {createScenarioRouter}=require('../../workspace-admin/tests/scenario-router.cjs')
const {stopSyntheticFixture}=require('./fixture-process.cjs')
async function within(p){let timer;try{return await Promise.race([p,new Promise((_,reject)=>timer=setTimeout(()=>reject(Error('synthetic barrier timeout')),15000))])}finally{clearTimeout(timer)}}
const frames=p=>p.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))))
async function readable(page,selectors){
 const ratios=await page.evaluate(selectors=>{const rgb=v=>{const n=v.match(/[\d.]+/g).map(Number);return {rgb:n.slice(0,3),alpha:n[3]??1}},lum=a=>a.map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4}).reduce((n,v,i)=>n+v*[.2126,.7152,.0722][i],0);return selectors.map(selector=>{const e=document.querySelector(selector);if(!e?.checkVisibility()||e.disabled)throw Error('Invalid contrast probe '+selector);const fg=rgb(getComputedStyle(e).color);let n=e,bg;while(n){const b=rgb(getComputedStyle(n).backgroundColor);if(b.alpha===1){bg=b.rgb;break}if(b.alpha!==0)throw Error('Translucent background');n=n.parentElement}if(!bg||fg.alpha!==1)throw Error('Missing opaque contrast');const v=[lum(fg.rgb),lum(bg)].sort((a,b)=>a-b);return [selector,(v[1]+.05)/(v[0]+.05)]})},selectors)
 for(const [selector,ratio]of ratios)assert.ok(ratio>=4.5,`${selector}: ${ratio}<4.5`)
}
async function main(){
 const temp=fs.mkdtempSync(path.join(os.tmpdir(),'ms-collab-artifact-scopes-'));let child,browser,router;const releases=[]
 try{
  execFileSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-days','1','-keyout',path.join(temp,'key.pem'),'-out',path.join(temp,'cert.pem'),'-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'],{stdio:'ignore'})
  child=spawn('python3',['-m','packages.application_ui.tests.artifact_browser_server',path.join(temp,'cert.pem'),path.join(temp,'key.pem')],{env:{...process.env,MS_COLLAB_SYNTHETIC_ARTIFACT_SESSION:'1',MS_COLLAB_SYNTHETIC_ARTIFACT_DISCOVERY:'1'},stdio:['ignore','pipe','pipe']});child.stderr.resume()
  const lines=readline.createInterface({input:child.stdout}),info=await within(new Promise((resolve,reject)=>{lines.once('line',l=>{try{resolve(JSON.parse(l))}catch(e){reject(e)}});child.once('exit',()=>reject(Error('fixture stopped')))}))
  const folder=path.join(process.env.RUNNER_TEMP||os.tmpdir(),'ms-collab-artifact-session');fs.mkdirSync(folder,{recursive:true})
  browser=await chromium.launch({headless:true});const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1440,height:1100}}),page=await context.newPage(),errors=[],outside=[],lists=[],writes=[]
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{assert.ok(!r.headers().authorization);const u=new URL(r.url());if(![info.origin,new URL(info.provider).origin].includes(u.origin))outside.push(u.origin);if(u.pathname.endsWith('/scopes'))lists.push(u.pathname);if(r.method()==='POST'&&/\/(refresh|approve)$/.test(u.pathname))writes.push(u.pathname)})
  await context.addInitScript(()=>{const original=fetch;window.fetch=(input,options={})=>{const {signal,...rest}=options;return original(input,rest)}})
  router=createScenarioRouter({origin:info.origin,onExternal:()=>outside.push('routed external')});await context.route('**/api/**',r=>router.handle(r))
  const ctrl=action=>page.evaluate(async action=>{const response=await fetch('/synthetic/artifact/'+action,{method:'POST'});if(!response.ok)throw Error(`Synthetic control ${action}: ${response.status}`);return response.json()},action)
  const ready=()=>page.waitForFunction(()=>!document.querySelector('#session').hidden&&!document.querySelector('#artifact-picker').hidden)
  const load=async(w='alpha')=>{await page.locator('#artifact-picker-workspace').selectOption(w);await page.locator('#artifact-picker-load').click();await page.waitForFunction(()=>!document.querySelector('#artifact-picker-load').disabled&&document.querySelector('#artifact-picker-status').textContent.includes('Select an exact scope'))}
  const open=async(bucket='bucket')=>{const value=await page.locator('#artifact-picker-scope option').evaluateAll((options,bucket)=>options.find(o=>o.textContent.startsWith('source / '+bucket+' ·'))?.value,bucket);assert.notEqual(value,undefined);await page.locator('#artifact-picker-scope').selectOption(value);await page.locator('#artifact-picker-use').click()}
  await page.goto(info.origin+'/artifacts/');await page.waitForFunction(()=>!document.querySelector('#app-sign-in').disabled);await page.locator('#app-sign-in').click();await page.waitForFunction(()=>document.querySelector('#app-principal')?.textContent==='admin');await page.locator('#app-artifacts-link').click();await ready()
  assert.equal(lists.length,0);await load();assert.equal(await page.locator('#artifact-picker-scope option').count(),2);assert.ok(!(await page.locator('#artifact-picker').textContent()).includes('unpublished'))
  await readable(page,['#artifact-picker-title','#artifact-picker-workspace','#artifact-picker-scope','#artifact-picker-load','#artifact-picker-use','#artifact-picker-status'])
  await page.screenshot({path:path.join(folder,'scopes-reader-desktop.png'),fullPage:true});await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(folder,'scopes-reader-mobile.png'),fullPage:true});await page.setViewportSize({width:1440,height:1100})
  await open('empty');await page.waitForFunction(()=>document.querySelector('#status').textContent.startsWith('No quarters'));assert.equal(await page.locator('#catalog').isVisible(),true)
  await open();await page.getByRole('button',{name:'builds/',exact:true}).click();assert.ok(!(await page.locator('#results').textContent()).includes('private-candidate'))
  for(const w of ['beta','commons']){await load(w);assert.equal(await page.locator('#catalog').isVisible(),false);assert.equal(await page.locator('#workspace').inputValue(),'');await open();await page.waitForFunction(w=>document.querySelector('#results').textContent.includes(w+'-only.txt'),w);assert.ok(!(await page.locator('#results').textContent()).includes('builds'))}
  // Capture then hold real response. A workspace change wins even if abort is ignored.
  function held(routePath){let seen,release,finished;const started=new Promise(r=>seen=r),barrier=new Promise(r=>release=r),done=new Promise(r=>finished=r);releases.push(release);router.set(routePath,async route=>{const response=await route.fetch();seen();await barrier;await route.fulfill({response});finished()});return {started,release,done}}
  const readerPath='/api/workspaces/alpha/artifacts/scopes';await page.locator('#artifact-picker-workspace').selectOption('alpha');let hold=held(readerPath),before=lists.length
  await page.evaluate(()=>{document.querySelector('#artifact-picker-load').click();document.querySelector('#artifact-picker-load').click()});await within(hold.started);assert.equal(lists.length,before+1)
  await page.locator('#artifact-picker-workspace').selectOption('beta');hold.release();await within(hold.done);await frames(page);router.remove(readerPath);assert.equal(await page.locator('#artifact-picker-scope option').count(),0);assert.equal(await page.locator('#catalog').isVisible(),false)
  // Manual entry remains available and also invalidates an outstanding directory.
  await page.locator('#artifact-picker-workspace').selectOption('alpha');hold=held(readerPath);await page.locator('#artifact-picker-load').click();await within(hold.started)
  for(const [id,value]of Object.entries({workspace:'beta',connection:'source',bucket:'bucket'}))await page.locator('#'+id).fill(value);await page.locator('#scope-form button').click();await page.waitForFunction(()=>document.querySelector('#results').textContent.includes('beta-only'))
  hold.release();await within(hold.done);await frames(page);router.remove(readerPath);assert.equal(await page.locator('#artifact-picker-scope option').count(),0);assert.match(await page.locator('#scope-title').textContent(),/^beta/)
  await page.locator('#app-artifact-admin-link').click();await ready();await load();assert.equal(await page.locator('#artifact-picker-scope option').count(),3)
  await readable(page,['#artifact-picker-title','#artifact-picker-workspace','#artifact-picker-scope','#artifact-picker-status','#artifact-picker-use'])
  await page.screenshot({path:path.join(folder,'scopes-admin-desktop.png'),fullPage:true});await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(folder,'scopes-admin-mobile.png'),fullPage:true});await page.setViewportSize({width:1440,height:1100})
  await open();await page.waitForFunction(()=>!document.querySelector('#panel').hidden);await page.locator('#review').click();await page.waitForFunction(()=>!document.querySelector('#preview').hidden)
  await page.locator('#acknowledge').check();await page.locator('#approve').click();await page.locator('#cancel').click();assert.equal(writes.length,0)
  await page.locator('#approve').click();await page.locator('#confirm-submit').click();await page.waitForFunction(()=>document.querySelector('#revision').textContent.startsWith('Index revision 3'));assert.equal(writes.length,1)
  // Reader directory becomes useful only through the reader's OWN cookie/grants.
  await ctrl('as-reader');await page.locator('#app-sign-out').click();await page.waitForFunction(()=>document.querySelector('#app-status').textContent.includes('server session is revoked'));assert.equal(await page.locator('#artifact-picker-scope option').count(),0)
  await page.locator('#app-sign-in').click();await page.waitForFunction(()=>document.querySelector('#app-principal')?.textContent==='reader');await page.locator('#app-artifacts-link').click();await ready();assert.equal(await page.locator('#artifact-picker-workspace option').count(),1)
  await load();await open();await page.getByRole('button',{name:'builds/',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#results').textContent.includes('private-candidate'))
  // Reader's directory survives no pagehide/Back state; delayed listing cannot revive it.
  hold=held(readerPath);await page.locator('#artifact-picker-load').click();await within(hold.started);await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide')));hold.release();await within(hold.done);await frames(page);router.remove(readerPath)
  assert.equal(await page.locator('#artifact-picker').isVisible(),false);assert.equal(await page.locator('#artifact-picker-scope option').count(),0);assert.equal(await page.locator('#catalog').isVisible(),false)
  await page.locator('#app-recheck').click();await ready();await load()
  hold=held(readerPath);await page.locator('#artifact-picker-load').click();await within(hold.started);await page.locator('#app-sign-out').click();await page.waitForFunction(()=>document.querySelector('#app-status').textContent.includes('server session is revoked'));hold.release();await within(hold.done);await frames(page);router.remove(readerPath);assert.equal(await page.locator('#artifact-picker-scope option').count(),0)
  // A non-reader approver discovers and reviews without gaining catalog read.
  await ctrl('as-second');await page.locator('#app-sign-in').click();await page.waitForFunction(()=>document.querySelector('#app-principal')?.textContent==='second');await page.locator('#app-artifact-admin-link').click();await ready();await load();assert.equal(await page.locator('#artifact-picker-scope option').count(),1);await open('hidden');await page.locator('#review').click();await page.waitForFunction(()=>document.querySelector('#rows').textContent.includes('approver-only.txt'))
  await page.locator('#app-artifacts-link').click();await ready();await page.locator('#artifact-picker-load').click();await page.waitForFunction(()=>document.querySelector('#artifact-picker-status').textContent.startsWith('No currently accessible published'));assert.equal(await page.locator('#artifact-picker-scope option').count(),0)
  // Revoked/absent cursor gives one 409 and no automatic pagination retry.
  await ctrl('as-admin');await page.locator('#app-sign-out').click();await page.waitForFunction(()=>document.querySelector('#app-status').textContent.includes('server session is revoked'));await page.locator('#app-sign-in').click();await page.waitForFunction(()=>document.querySelector('#app-principal')?.textContent==='admin');await page.locator('#app-artifacts-link').click();await ready();await ctrl('seed-pages');await load();assert.equal(await page.locator('#artifact-picker-scope option').count(),25);assert.equal(await page.locator('#artifact-picker-next').isEnabled(),true)
  await ctrl('invalidate-cursor');before=lists.length;const denied=page.waitForResponse(r=>new URL(r.url()).pathname===readerPath&&new URL(r.url()).search);await page.locator('#artifact-picker-next').click();assert.equal((await denied).status(),409);await page.waitForFunction(()=>document.querySelector('#artifact-picker-status').textContent.includes('no request was retried'));await frames(page);assert.equal(lists.length,before+1);assert.equal(await page.locator('#artifact-picker-scope option').count(),0)
  await page.locator('#app-sign-out').click();await page.waitForFunction(()=>document.querySelector('#app-status').textContent.includes('server session is revoked'))
  await router.drain();await context.unrouteAll({behavior:'wait'});await page.goto('about:blank');await page.goBack();await page.waitForFunction(()=>document.querySelector('#app-principal')?.textContent==='Not verified');assert.equal(await page.locator('#catalog').isVisible(),false);assert.equal(await page.locator('#artifact-picker').isVisible(),false)
  assert.deepEqual(await page.evaluate(()=>[localStorage.length,sessionStorage.length]),[0,0]);assert.deepEqual(errors,[]);assert.deepEqual(outside,[])
 }finally{
  const failures=[]
  for(const release of releases){try{release()}catch(error){failures.push(error)}}
  try{await browser?.close()}catch(error){failures.push(error)}
  try{await stopSyntheticFixture(child)}catch(error){failures.push(error)}
  try{fs.rmSync(temp,{recursive:true,force:true})}catch(error){failures.push(error)}
  if(failures.length)throw new AggregateError(failures,'Synthetic discovery cleanup failed')
 }
console.log('Artifact scope discovery: real HTTPS cookie directories, three isolated workspaces, published empty scope, reader approval loop, explicit selection, no automatic discovery/retry, ignored abort/manual/pagehide/logout/Back, four responsive screenshots.')
}
main().catch(e=>{console.error(e.stack);process.exitCode=1})
