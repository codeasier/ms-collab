// Actual HTTPS/Cookie/API/SQLite discovery; no remote provider or production data.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),readline=require('node:readline')
const {spawn,execFileSync}=require('node:child_process'),{chromium}=require('playwright')
const {createScenarioRouter}=require('../../workspace-admin/tests/scenario-router.cjs')
const {stopSyntheticFixture}=require('./fixture-process.cjs')
async function within(p,label='barrier'){let timer;try{return await Promise.race([p,new Promise((_,reject)=>timer=setTimeout(()=>reject(Error(`Synthetic ${label} timed out`)),15000))])}finally{clearTimeout(timer)}}
const frames=p=>p.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))))
async function readable(page,selectors){
 const ratios=await page.evaluate(selectors=>{const rgb=v=>{const n=v.match(/[\d.]+/g).map(Number);return {rgb:n.slice(0,3),alpha:n[3]??1}},lum=a=>a.map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4}).reduce((n,v,i)=>n+v*[.2126,.7152,.0722][i],0);return selectors.map(selector=>{const e=document.querySelector(selector);if(!e?.checkVisibility()||e.disabled)throw Error('Invalid contrast probe '+selector);const fg=rgb(getComputedStyle(e).color);let n=e,bg;while(n){const b=rgb(getComputedStyle(n).backgroundColor);if(b.alpha===1){bg=b.rgb;break}if(b.alpha!==0)throw Error('Translucent background');n=n.parentElement}if(!bg||fg.alpha!==1)throw Error('Missing opaque contrast');const v=[lum(fg.rgb),lum(bg)].sort((a,b)=>a-b);return [selector,(v[1]+.05)/(v[0]+.05)]})},selectors)
 for(const [selector,ratio]of ratios)assert.ok(ratio>=4.5,`${selector}: ${ratio}<4.5`)
}
// Receipts below are obtained from real HTTPS/Cookie/SQLite responses. They are
// exchanged between currently authorized workspace/action contexts, not mocked.
async function receiptContexts(page,paths,key){
 const read=target=>page.evaluate(async target=>{const response=await fetch(target,{credentials:'same-origin',cache:'no-store',redirect:'error'});return {status:response.status,body:await response.json()}},target)
 const first=[]
 for(const target of paths){
  const result=await read(target);assert.equal(result.status,200);assert.equal(result.body.data.items.length,25)
  const token=result.body.data.next_after;assert.equal(token.length,48);assert.match(token,/^dcr1_[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]$/)
  assert.equal(Buffer.from(token.slice(5),'base64url').toString('base64url'),token.slice(5));first.push(result.body.data)
 }
 assert.equal(new Set(first.map(d=>d.next_after)).size,paths.length)
 for(const [i,target]of paths.entries()){
  for(const [j,other]of first.entries())if(i!==j){
   const refused=await read(target+'?after='+encodeURIComponent(other.next_after));assert.equal(refused.status,409);assert.deepEqual(refused.body,{error:'scope_directory_changed'})
  }
  const after=first[i].next_after,next=await read(target+'?after='+after)
  assert.equal(next.status,200);assert.ok(next.body.data.items.length>0);assert.equal(next.body.data.next_after,null)
  let prior=first[i].items.at(-1).scope
  for(const {scope}of next.body.data.items){assert.ok(scope.connection_id>prior.connection_id||scope.connection_id===prior.connection_id&&scope[key]>prior[key]);prior=scope}
  // A receipt can be reread in its original context; it is not an authority or
  // a one-time capability. Wrong-context attempts do not consume the receipt.
  assert.deepEqual(await read(target+'?after='+after),next)
 }
 return first
}
// A substituted cross-context request is rejected by the real server. Other
// cases deliberately corrupt a real response AFTER HTTPS/SQLite work; they are
// browser protocol-fault tests, not evidence of a server data defect.
async function receiptProtocolFaults({page,router,path,prefix,key,load,count,first,foreign,loaded}){
 // Successful UI pagination must also work for both artifact projections.
 await load();const continued=page.waitForResponse(r=>new URL(r.url()).pathname===path&&new URL(r.url()).searchParams.has('after'))
 await page.locator('#'+prefix+'-next').click();const response=await continued;assert.equal(response.status(),200);const payload=await response.json();await loaded()
 assert.equal(await page.locator('#'+prefix+'-scope option').count(),payload.data.items.length);assert.equal(await page.locator('#'+prefix+'-next').isDisabled(),true)
 const labels=await page.locator('#'+prefix+'-scope option').allTextContents()
 for(const [i,item]of payload.data.items.entries())assert.ok(labels[i].startsWith(item.scope.connection_id+' / '+item.scope[key]+' ·'))
 for(const fault of ['context','canonical','repeat','overlap','unavailable']){
  await load();const before=count(path)
  router.set(path,async route=>{
   if(fault==='context'){
    const target=new URL(route.request().url());target.searchParams.set('after',foreign)
    const refused=await route.fetch({url:target.href});assert.equal(refused.status(),409);assert.deepEqual(await refused.json(),{error:'scope_directory_changed'});await route.fulfill({response:refused});return
   }
   const response=await route.fetch();assert.equal(response.status(),200)
   const after=new URL(route.request().url()).searchParams.get('after');assert.ok(after)
   if(fault==='unavailable'){await route.fulfill({response,status:503,json:{error:'scope_directory_unavailable'}});return}
   const data=structuredClone(first)
   if(fault==='overlap')data.next_after=null
   else{
    for(const [i,item]of data.items.entries())item.scope[key]='z'+String(i).padStart(3,'0')
    data.next_after=fault==='repeat'?after:after.slice(0,-1)+'B'
   }
   await route.fulfill({response,json:{data}})
  })
  await page.locator('#'+prefix+'-next').click();await page.waitForFunction(prefix=>document.getElementById(prefix+'-status').textContent.includes('Reload the directory explicitly; no request was retried'),prefix);await frames(page)
  assert.equal(count(path),before+1);assert.equal(await page.locator('#'+prefix+'-scope option').count(),0)
  assert.equal(await page.locator('#'+prefix+'-next').isDisabled(),true);assert.equal(await page.locator('#'+prefix+'-use').isDisabled(),true)
  for(const id of ['workspace','connection',key==='pipeline_id'?'pipeline':'bucket'])assert.equal(await page.locator('#'+id).inputValue(),'')
  assert.equal(new URL(page.url()).search,'');assert.deepEqual(await page.evaluate(()=>[localStorage.length,sessionStorage.length]),[0,0])
  router.remove(path)
  const fresh=page.waitForResponse(r=>new URL(r.url()).pathname===path)
  await page.locator('#'+prefix+'-load').click();const response=await fresh;assert.equal(response.status(),200);assert.equal(new URL(response.url()).search,'');await loaded()
  assert.equal(await page.locator('#'+prefix+'-scope option').count(),25)
 }
}
async function main(){
 const temp=fs.mkdtempSync(path.join(os.tmpdir(),'ms-collab-artifact-scopes-'));let child,browser,router,failure,stderr='';const releases=[]
 try{
  execFileSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-days','1','-keyout',path.join(temp,'key.pem'),'-out',path.join(temp,'cert.pem'),'-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'],{stdio:'ignore'})
  child=spawn('python3',['-m','packages.application_ui.tests.artifact_browser_server',path.join(temp,'cert.pem'),path.join(temp,'key.pem')],{env:{...process.env,MS_COLLAB_SYNTHETIC_ARTIFACT_SESSION:'1',MS_COLLAB_SYNTHETIC_ARTIFACT_DISCOVERY:'1'},stdio:['ignore','pipe','pipe']})
  child.stderr.setEncoding('utf8');child.stderr.on('data',chunk=>{stderr=(stderr+chunk).slice(-8192)})
  const lines=readline.createInterface({input:child.stdout}),info=await within(new Promise((resolve,reject)=>{lines.once('line',l=>{try{resolve(JSON.parse(l))}catch(e){reject(e)}});child.once('error',reject);child.once('exit',()=>reject(Error('fixture stopped')))}),'artifact fixture startup')
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
  await ctrl('as-admin');await page.locator('#app-sign-out').click();await page.waitForFunction(()=>document.querySelector('#app-status').textContent.includes('server session is revoked'));await page.locator('#app-sign-in').click();await page.waitForFunction(()=>document.querySelector('#app-principal')?.textContent==='admin');await page.locator('#app-artifacts-link').click();await ready();await ctrl('seed-pages')
  const adminPath=readerPath.replace('/artifacts/','/artifact-admin/'),receiptPages=await receiptContexts(page,[readerPath,readerPath.replace('/alpha/','/beta/'),adminPath,adminPath.replace('/alpha/','/beta/')],'bucket_id')
  const loaded=()=>page.waitForFunction(()=>!document.querySelector('#artifact-picker-load').disabled&&document.querySelector('#artifact-picker-status').textContent.includes('Select an exact scope'))
  const count=target=>lists.filter(p=>p===target).length
  await receiptProtocolFaults({page,router,path:readerPath,prefix:'artifact-picker',key:'bucket_id',load,count,first:receiptPages[0],foreign:receiptPages[1].next_after,loaded})
  await page.locator('#app-artifact-admin-link').click();await ready()
  await receiptProtocolFaults({page,router,path:adminPath,prefix:'artifact-picker',key:'bucket_id',load,count,first:receiptPages[2],foreign:receiptPages[0].next_after,loaded})
  await page.locator('#app-artifacts-link').click();await ready();await load();assert.equal(await page.locator('#artifact-picker-scope option').count(),25);assert.equal(await page.locator('#artifact-picker-next').isEnabled(),true)
  await ctrl('invalidate-cursor');before=lists.length;const denied=page.waitForResponse(r=>new URL(r.url()).pathname===readerPath&&new URL(r.url()).search);await page.locator('#artifact-picker-next').click();assert.equal((await denied).status(),409);await page.waitForFunction(()=>document.querySelector('#artifact-picker-status').textContent.includes('no request was retried'));await frames(page);assert.equal(lists.length,before+1);assert.equal(await page.locator('#artifact-picker-scope option').count(),0)
  await page.locator('#app-sign-out').click();await page.waitForFunction(()=>document.querySelector('#app-status').textContent.includes('server session is revoked'))
  await within(router.drain(),'scenario route drain');await within(context.unrouteAll({behavior:'wait'}),'scenario route removal');await page.goto('about:blank');await page.goBack();await page.waitForFunction(()=>document.querySelector('#app-principal')?.textContent==='Not verified');assert.equal(await page.locator('#catalog').isVisible(),false);assert.equal(await page.locator('#artifact-picker').isVisible(),false)
  assert.deepEqual(await page.evaluate(()=>[localStorage.length,sessionStorage.length]),[0,0]);assert.deepEqual(errors,[]);assert.deepEqual(outside,[])
 }catch(error){failure=error}
 finally{
  const failures=failure?[failure]:[]
  // Release every held reply even when a scenario assertion fails. Each cleanup
  // is attempted independently; forced fixture reaping must still fail CI.
  for(const release of releases){try{release()}catch(error){failures.push(error)}}
  try{if(router)await within(router.drain(),'cleanup route drain')}catch(error){failures.push(error)}
  try{if(browser)await within(browser.close(),'browser close')}catch(error){failures.push(error)}
  try{await stopSyntheticFixture(child)}catch(error){failures.push(error)}
  try{fs.rmSync(temp,{recursive:true,force:true})}catch(error){failures.push(error)}
  if(failures.length&&stderr)console.error(`[artifact fixture stderr tail]\n${stderr}`)
  if(failures.length===1)throw failures[0]
  if(failures.length)throw new AggregateError(failures,'Artifact scenario or cleanup failed')
 }
 console.log('Artifact scope discovery: real HTTPS cookie directories, three isolated workspaces, published empty scope, reader approval loop, opaque workspace/action-bound receipts, explicit reload after receipt/canonical/repeat/order/503 faults, explicit selection, no automatic discovery/retry, ignored abort/manual/pagehide/logout/Back, four responsive screenshots.')
}
function reportFailure(error){
 console.error(error?.stack||String(error))
 if(error instanceof AggregateError)for(const nested of error.errors)reportFailure(nested)
 if(error?.cause)reportFailure(error.cause)
}
main().catch(error=>{reportFailure(error);process.exitCode=1})
