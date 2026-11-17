// Real HTTPS Cookie + SQLite picker -> existing run/details/tasks/durations.
// Synthetic stored metadata only; any CI synchronization call fails the fixture.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),readline=require('node:readline')
const {spawn,execFileSync}=require('node:child_process'),{chromium}=require('playwright')
const {createScenarioRouter}=require('../../workspace-admin/tests/scenario-router.cjs')
const {stopSyntheticFixture}=require('./fixture-process.cjs')
async function within(p,label='synthetic barrier'){let timer;try{return await Promise.race([p,new Promise((_,reject)=>timer=setTimeout(()=>reject(Error(label+' timeout')),15000))])}finally{clearTimeout(timer)}}
const frames=p=>p.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))))
async function readable(page,selectors){
 const ratios=await page.evaluate(selectors=>{const rgb=v=>{const n=v.match(/[\d.]+/g).map(Number);return {rgb:n.slice(0,3),alpha:n[3]??1}},lum=a=>a.map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4}).reduce((n,v,i)=>n+v*[.2126,.7152,.0722][i],0);return selectors.map(selector=>{const e=document.querySelector(selector);if(!e?.checkVisibility()||e.disabled)throw Error('Invalid contrast probe '+selector);const fg=rgb(getComputedStyle(e).color);let n=e,bg;while(n){const b=rgb(getComputedStyle(n).backgroundColor);if(b.alpha===1){bg=b.rgb;break}if(b.alpha!==0)throw Error('Translucent background');n=n.parentElement}if(!bg||fg.alpha!==1)throw Error('Missing opaque contrast');const v=[lum(fg.rgb),lum(bg)].sort((a,b)=>a-b);return [selector,(v[1]+.05)/(v[0]+.05)]})},selectors)
 for(const [selector,ratio]of ratios)assert.ok(ratio>=4.5,`${selector}: ${ratio}<4.5`)
}
// W10's supported picker boundary only: two pages share a real session cookie,
// never a selected scope. This does not add scoped HTML/deep-link support.
async function twoTabAcceptance({page:a,context,info,router,releases,watch}){
 const b=await context.newPage();watch(b)
 const seen=new Map([[a,[]],[b,[]]]),observers=new Map(),loginRequests=[]
 for(const page of [a,b]){const observe=request=>{
  const url=new URL(request.url())
  if(url.pathname==='/auth/start')loginRequests.push(page)
  if(url.pathname.startsWith('/api/')&&url.pathname.includes('/pipelines/'))seen.get(page).push({origin:url.origin,path:url.pathname,method:request.method(),query:url.search})
 };observers.set(page,observe);page.on('request',observe)}
 const ready=page=>page.waitForFunction(()=>document.querySelector('#app-principal')?.textContent==='admin'&&!document.querySelector('#app-recheck').disabled&&!document.querySelector('#pipeline-picker').hidden)
 const settled=page=>page.waitForFunction(()=>document.querySelector('#app-recheck')&&!document.querySelector('#app-recheck').disabled)
 const taskReady=page=>page.waitForFunction(()=>document.querySelector('#task-status-filter')&&!document.querySelector('#task-status-filter').disabled)
 const detail=async page=>{await page.getByRole('button',{name:'View run-1',exact:true}).click();await taskReady(page)}
 async function blank(page){
  assert.equal(await page.locator('#panel').isVisible(),false)
  for(const id of ['scope-title','runs','durations','coverage','detail-title','detail-body'])assert.equal(await page.locator('#'+id).textContent(),'')
  assert.equal(await page.locator('#detail').isVisible(),false)
  for(const id of ['workspace','connection','pipeline'])assert.equal(await page.locator('#'+id).inputValue(),'')
  assert.equal(await page.locator('#pipeline-picker-scope option').count(),0)
 }
 async function open(page,workspace){
  await page.locator('#pipeline-picker-workspace').selectOption(workspace);await page.locator('#pipeline-picker-load').click()
  await page.waitForFunction(()=>!document.querySelector('#pipeline-picker-load').disabled&&document.querySelector('#pipeline-picker-status').textContent.startsWith('Select a stored scope'))
  const value=await page.locator('#pipeline-picker-scope option').evaluateAll(options=>options.find(o=>o.textContent.startsWith('example-ci / build ·'))?.value)
  assert.notEqual(value,undefined);await page.locator('#pipeline-picker-scope').selectOption(value);await page.locator('#pipeline-picker-use').click();await page.locator('#panel:not([hidden])').waitFor()
 }
 async function scoped(page,workspace){
  assert.equal(await page.locator('#scope-title').textContent(),workspace+' / example-ci / build')
  assert.equal(await page.locator('#workspace').inputValue(),workspace)
  assert.equal(await page.locator('#runs h3').textContent(),'run-1')
  assert.equal(await page.locator('#detail-title').textContent(),'run-1')
  assert.match(await page.locator('#tasks').textContent(),/unit.*success/s)
  for(const [id,suffix]of [['durations',' checks'],['task-stages',' checks'],['tasks',' <img']]){
   const text=await page.locator('#'+id).textContent();assert.ok(text.includes(workspace+suffix))
   for(const other of ['alpha','beta','commons'].filter(w=>w!==workspace))assert.ok(!text.includes(other+suffix))
  }
 }
 const state=page=>page.evaluate(()=>({
  fields:['workspace','connection','pipeline','pipeline-picker-workspace'].map(id=>document.getElementById(id).value),
  text:['scope-title','revision','runs','coverage','durations','detail-title','detail-status','detail-body','pipeline-picker-status'].map(id=>document.getElementById(id).textContent),
  selected:document.querySelector('#pipeline-picker-scope').value,taskFilter:document.querySelector('#task-status-filter').value,
  disabled:['task-status-filter','task-previous','task-next'].map(id=>document.getElementById(id).disabled),
  options:[...document.querySelectorAll('#pipeline-picker-scope option')].map(o=>[o.value,o.textContent]),
  dialog:document.querySelector('#detail').open,panel:document.querySelector('#panel').hidden,
 }))
 const aBase='/api/workspaces/alpha/pipelines/example-ci/build',bBase=aBase.replace('/alpha/','/beta/')
 function held(path){
  let start,release,finish;const started=new Promise(r=>start=r),barrier=new Promise(r=>release=r),done=new Promise(r=>finish=r)
  releases.push(release);router.set(path,async route=>{
   assert.equal(route.request().frame().page(),a)
   const response=await route.fetch();assert.equal(response.status(),200);start();await barrier;await route.fulfill({response});finish()
  });return {started,release,done}
 }
 // B verifies the cookie already established by A; there is no second login.
 await b.goto(info.origin+'/pipelines/');await Promise.all([ready(a),ready(b)]);await blank(b);assert.equal(seen.get(b).length,0)
 await Promise.all([open(a,'alpha'),open(b,'beta')]);await Promise.all([detail(a),detail(b)])
 await Promise.all([scoped(a,'alpha'),scoped(b,'beta')])
 for(const [page,workspace,base]of [[a,'alpha',aBase],[b,'beta',bBase]]){
  for(const path of ['/api/workspaces/'+workspace+'/pipelines/scopes',base+'/runs',base+'/durations',base+'/runs/run-1',base+'/runs/run-1/tasks'])assert.ok(seen.get(page).some(r=>r.path===path),'Missing page-specific request '+path)
 }
 // Hold a real A task response while B performs a new authorized task request.
 // Closing A must discard that old response even though transport ignores abort.
 await a.keyboard.press('Escape');let hold=held(aBase+'/runs/run-1/tasks')
 await a.getByRole('button',{name:'View run-1',exact:true}).click();await within(hold.started)
 const bTasks=seen.get(b).filter(r=>r.path===bBase+'/runs/run-1/tasks').length
 const bResponse=b.waitForResponse(r=>new URL(r.url()).pathname===bBase+'/runs/run-1/tasks')
 await b.locator('#task-status-filter').selectOption('success');assert.equal((await bResponse).status(),200);await taskReady(b);await scoped(b,'beta')
 assert.equal(seen.get(b).filter(r=>r.path===bBase+'/runs/run-1/tasks').length,bTasks+1)
 const retained=await state(b)
 await a.keyboard.press('Escape');hold.release();await within(hold.done);await frames(a);router.remove(aBase+'/runs/run-1/tasks')
 assert.equal(await a.locator('#detail').isVisible(),false);assert.equal(await a.locator('#detail-body').textContent(),'');assert.deepEqual(await state(b),retained)
 async function restored(){
  await settled(a);await frames(a);await blank(a)
  assert.equal(await a.locator('#pipeline-picker-workspace').inputValue(),(await a.locator('#pipeline-picker').isVisible())?'alpha':'')
  // A fresh document may inspect its cookie automatically; it must not load a
  // directory/snapshot, inherit B's selection or reopen A's prior scope.
  await a.locator('#app-recheck').click();await ready(a);await blank(a)
  assert.deepEqual(await state(b),retained)
 }
 let before=seen.get(a).length
 assert.equal((await a.reload()).status(),200);await restored();assert.equal(seen.get(a).length,before)
 await open(a,'alpha');await detail(a);await scoped(a,'alpha');await a.keyboard.press('Escape')
 await a.goto(info.origin+'/manage/');await settled(a);before=seen.get(a).length
 await a.goBack();assert.equal(a.url(),info.origin+'/pipelines/');await restored();assert.equal(seen.get(a).length,before)
 await open(a,'alpha');await detail(a);await scoped(a,'alpha');await a.keyboard.press('Escape')
 await a.goForward();assert.equal(a.url(),info.origin+'/manage/');await settled(a);assert.deepEqual(await state(b),retained);before=seen.get(a).length
 await a.goBack();assert.equal(a.url(),info.origin+'/pipelines/');await restored();assert.equal(seen.get(a).length,before)
 // Unsupported HTML query/scoped paths fail, rather than borrowing either tab's
 // scope. These 404 checks are not claims of OBS deep-link compatibility.
 for(const suffix of ['/pipelines/?workspace=beta&connection=example-ci&pipeline=build','/pipelines/beta/example-ci/build']){
  before=seen.get(a).length;const response=await a.goto(info.origin+suffix)
  assert.equal(response.status(),404);assert.equal(await response.text(),'not_found');assert.equal(a.url(),info.origin+suffix)
  assert.equal(await a.locator('#pipeline-picker,#panel').count(),0)
  assert.equal(seen.get(a).slice(before).filter(r=>r.path.startsWith('/api/')).length,0);assert.deepEqual(await state(b),retained)
 }
 await a.goto(info.origin+'/pipelines/');await restored();await open(a,'alpha');await detail(a);await scoped(a,'alpha')
 // Logout in B revokes the shared session. A is not expected to receive a push
 // notification: its next real request gets 401 and clears all private state.
 await a.keyboard.press('Escape');hold=held(aBase+'/runs/run-1/tasks');await a.getByRole('button',{name:'View run-1',exact:true}).click();await within(hold.started)
 await b.keyboard.press('Escape');await b.locator('#app-sign-out').click();await b.waitForFunction(()=>document.querySelector('#app-status').textContent.includes('server session is revoked'));await blank(b)
 await a.keyboard.press('Escape');const denied=a.waitForResponse(r=>new URL(r.url()).pathname==='/api/workspaces/alpha/pipelines/scopes')
 await a.locator('#pipeline-picker-load').click();assert.equal((await denied).status(),401)
 await a.waitForFunction(()=>document.querySelector('#app-principal').textContent==='Not verified');await blank(a)
 hold.release();await within(hold.done);await frames(a);router.remove(aBase+'/runs/run-1/tasks');await blank(a)
 for(const page of [a,b]){
  assert.equal(await page.locator('#pipeline-picker').isVisible(),false);assert.equal(await page.locator('#pipeline-picker-workspace option').count(),0)
  assert.deepEqual(await page.evaluate(()=>[localStorage.length,sessionStorage.length]),[0,0])
 }
 for(const [page,workspace]of [[a,'alpha'],[b,'beta']])for(const request of seen.get(page).filter(r=>r.path.startsWith('/api/'))){
  assert.equal(request.origin,info.origin);assert.equal(request.method,'GET')
  assert.ok(request.path.startsWith('/api/workspaces/'+workspace+'/pipelines/'),'Cross-tab request scope '+request.path)
  assert.ok(!new URLSearchParams(request.query).has('workspace'))
 }
 assert.deepEqual(await a.evaluate(async()=>{const r=await fetch('/synthetic/pipeline/status',{method:'POST'});if(!r.ok)throw Error('Synthetic status unavailable');return r.json()}),{ci_calls:0})
 assert.deepEqual(loginRequests,[]);for(const [page,observe]of observers)page.off('request',observe)
 await within(b.close(),'second tab close')
}
async function main(){
 const temp=fs.mkdtempSync(path.join(os.tmpdir(),'ms-collab-pipeline-scopes-'));let child,browser,router,failure;const releases=[]
 try{
  execFileSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-days','1','-keyout',path.join(temp,'key.pem'),'-out',path.join(temp,'cert.pem'),'-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'],{stdio:'ignore'})
  child=spawn('python3',['-m','packages.application_ui.tests.browser_server',path.join(temp,'cert.pem'),path.join(temp,'key.pem'),'pipeline-discovery'],{env:{...process.env,MS_COLLAB_SYNTHETIC_APPLICATION_UI:'1'},stdio:['ignore','pipe','pipe']});child.stderr.resume()
  const lines=readline.createInterface({input:child.stdout}),info=await within(new Promise((resolve,reject)=>{lines.once('line',l=>{try{resolve(JSON.parse(l))}catch(e){reject(e)}});child.once('exit',()=>reject(Error('fixture stopped')))}))
  const folder=path.join(process.env.RUNNER_TEMP||os.tmpdir(),'ms-collab-pipeline-scopes');fs.mkdirSync(folder,{recursive:true})
  browser=await chromium.launch({headless:true});const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1440,height:1100}}),page=await context.newPage(),errors=[],outside=[],requests=[],dialogs=[]
  const watch=page=>{page.on('pageerror',e=>errors.push(e.message));page.on('dialog',async d=>{dialogs.push(d.message());await d.dismiss()});page.on('request',r=>{assert.ok(!r.headers().authorization);const u=new URL(r.url());if(![info.origin,new URL(info.provider).origin].includes(u.origin))outside.push(u.origin);requests.push({path:u.pathname,method:r.method(),query:u.search})})};watch(page)
  await context.addInitScript(()=>{const original=fetch;window.fetch=(input,options={})=>{const {signal,...rest}=options;return original(input,rest)}})
  router=createScenarioRouter({origin:info.origin,onExternal:()=>outside.push('routed external')});await context.route('**/api/**',r=>router.handle(r))
  const ctrl=action=>page.evaluate(async action=>{const response=await fetch('/synthetic/pipeline/'+action,{method:'POST'});if(!response.ok)throw Error('Synthetic control failed');return response.json()},action)
  const ready=()=>page.waitForFunction(()=>document.querySelector('#app-principal')?.textContent==='admin'&&!document.querySelector('#session').hidden&&!document.querySelector('#pipeline-picker').hidden)
  const login=async()=>{await page.locator('#app-sign-in:not([disabled])').click();await page.waitForFunction(()=>document.querySelector('#app-principal')?.textContent==='admin');await page.locator('#app-pipelines-link').click();await ready()}
  const loaded=()=>page.waitForFunction(()=>!document.querySelector('#pipeline-picker-load').disabled&&document.querySelector('#pipeline-picker-status').textContent.startsWith('Select a stored scope'))
  const load=async(w='alpha')=>{await page.locator('#pipeline-picker-workspace').selectOption(w);await page.locator('#pipeline-picker-load').click();await loaded()}
  const use=async(pipeline='build')=>{const value=await page.locator('#pipeline-picker-scope option').evaluateAll((options,id)=>options.find(o=>o.textContent.startsWith('example-ci / '+id+' ·'))?.value,pipeline);assert.notEqual(value,undefined);await page.locator('#pipeline-picker-scope').selectOption(value);await page.locator('#pipeline-picker-use').click()}
  const opened=()=>page.locator('#panel:not([hidden])').waitFor()
  const open=async(pipeline='build')=>{await use(pipeline);await opened()}
  const taskReady=()=>page.waitForFunction(()=>document.querySelector('#task-status-filter')&&!document.querySelector('#task-status-filter').disabled)
  const detail=async()=>{await page.getByRole('button',{name:'View run-1',exact:true}).click();await taskReady()}
  const blank=async()=>{assert.equal(await page.locator('#panel').isVisible(),false);assert.equal(await page.locator('#runs li').count(),0);assert.equal(await page.locator('#durations').textContent(),'');assert.equal(await page.locator('#coverage').textContent(),'');assert.equal(await page.locator('#detail-body').textContent(),'');assert.equal(await page.locator('#detail').isVisible(),false)}
  const listPath='/api/workspaces/alpha/pipelines/scopes',base='/api/workspaces/alpha/pipelines/example-ci/build',beta=base.replace('/alpha/','/beta/')
  function held(routePath){let seen,release,finished;const started=new Promise(r=>seen=r),barrier=new Promise(r=>release=r),done=new Promise(r=>finished=r);releases.push(release);router.set(routePath,async route=>{const response=await route.fetch();seen();await barrier;await route.fulfill({response});finished()});return {started,release,done}}
  const count=p=>requests.filter(r=>r.path===p).length
  await page.goto(info.origin+'/pipelines/');await login();assert.equal(count(listPath),0);assert.equal(await page.locator('#pipeline-picker-workspace option').count(),3)
  await load();assert.equal(await page.locator('#pipeline-picker-scope option').count(),2);assert.equal(await page.locator('#panel').isVisible(),false)
  await readable(page,['#pipeline-picker-title','#pipeline-picker-workspace','#pipeline-picker-scope','#pipeline-picker-load','#pipeline-picker-use','#pipeline-picker-status'])
  await page.screenshot({path:path.join(folder,'desktop.png'),fullPage:true});await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.ok(await page.locator('#pipeline-picker button').evaluateAll(ns=>ns.every(n=>n.scrollWidth<=n.clientWidth)));await page.screenshot({path:path.join(folder,'mobile.png'),fullPage:true});await page.setViewportSize({width:1440,height:1100})
  await open();assert.match(await page.locator('#durations').textContent(),/alpha checks/);await detail();assert.match(await page.locator('#tasks').textContent(),/unit.*success/s);assert.match(await page.locator('#tasks').textContent(),/alpha <img src=x/);assert.equal(await page.locator('#detail img,#durations img,#pipeline-picker img').count(),0);await page.screenshot({path:path.join(folder,'tasks.png'),fullPage:false});await page.keyboard.press('Escape')
  for(const workspace of ['beta','commons']){
   await load(workspace);await blank();assert.equal(await page.locator('#workspace').inputValue(),'');await open();assert.equal(await page.locator('#scope-title').textContent(),workspace+' / example-ci / build');assert.match(await page.locator('#coverage').textContent(),/1 runs with valid samples/);assert.ok((await page.locator('#durations').textContent()).includes(workspace+' checks'));assert.ok(!(await page.locator('#durations').textContent()).includes('alpha checks'));await detail();assert.ok((await page.locator('#tasks').textContent()).includes(workspace+' <img'));assert.equal(await page.locator('#detail-title').textContent(),'run-1');await page.keyboard.press('Escape')
  }
  await load();await open('empty');assert.match(await page.locator('#runs').textContent(),/No runs in the stored subset/);assert.match(await page.locator('#coverage').textContent(),/0 terminal runs selected from 0 stored/)
  // Opening the same selected scope repeatedly does not fork runs/durations.
  let hold=held(base+'/runs'),before=count(base+'/runs');await use();await within(hold.started)
  await page.evaluate(()=>{document.querySelector('#pipeline-picker-use').click();document.querySelector('#scope-form').requestSubmit();document.querySelector('#reload').click()});assert.equal(count(base+'/runs'),before+1)
  hold.release();await within(hold.done);await opened();router.remove(base+'/runs')
  // New workspace still pending when the old directory resolves: no old page,
  // and its finally must not unlock the newer request's duplicate-click guard.
  hold=held(listPath);before=count(listPath);await page.evaluate(()=>{document.querySelector('#pipeline-picker-load').click();document.querySelector('#pipeline-picker-load').click()});await within(hold.started);assert.equal(count(listPath),before+1);await blank()
  const betaPath=listPath.replace('/alpha/','/beta/'),newHold=held(betaPath);await page.locator('#pipeline-picker-workspace').selectOption('beta');await page.locator('#pipeline-picker-load').click();await within(newHold.started)
  hold.release();await within(hold.done);await frames(page);assert.equal(await page.locator('#pipeline-picker-scope option').count(),0);assert.equal(await page.locator('#pipeline-picker-load').isDisabled(),true)
  before=count(betaPath);await page.evaluate(()=>document.querySelector('#pipeline-picker-load').click());assert.equal(count(betaPath),before);newHold.release();await within(newHold.done);await loaded();router.remove(listPath);router.remove(betaPath)
  // Manual entry is independent of Discovery workspace and invalidates its list.
  await page.locator('#pipeline-picker-workspace').selectOption('alpha');hold=held(listPath);await page.locator('#pipeline-picker-load').click();await within(hold.started)
  for(const [id,value]of Object.entries({workspace:'beta',connection:'example-ci',pipeline:'build'}))await page.locator('#'+id).fill(value)
  await page.locator('#scope-form button').click();await opened();hold.release();await within(hold.done);await frames(page);router.remove(listPath);assert.equal(await page.locator('#pipeline-picker-scope option').count(),0);assert.equal(await page.locator('#pipeline-picker-workspace').inputValue(),'alpha');assert.match(await page.locator('#scope-title').textContent(),/^beta/)
  // An old task completion cannot clear the newer task's pending guard/state.
  await load();await open();const taskPath=base+'/runs/run-1/tasks',betaTask=beta+'/runs/run-1/tasks';hold=held(taskPath);await page.getByRole('button',{name:'View run-1',exact:true}).click();await within(hold.started);await page.keyboard.press('Escape');await load('beta');await blank();await open();const newTask=held(betaTask);await page.getByRole('button',{name:'View run-1',exact:true}).click();await within(newTask.started)
  hold.release();await within(hold.done);await frames(page);assert.equal(await page.locator('#tasks article').count(),0);assert.equal(await page.locator('#task-status-filter').isDisabled(),true);before=count(betaTask);await page.evaluate(()=>document.querySelector('#task-status-filter').dispatchEvent(new Event('change',{bubbles:true})));await frames(page);assert.equal(count(betaTask),before);newTask.release();await within(newTask.done);await taskReady();assert.match(await page.locator('#tasks').textContent(),/beta <img/);await page.keyboard.press('Escape');router.remove(taskPath);router.remove(betaTask)
  // Both valid pagination and invalidated visible cursor clear old evidence.
  assert.equal((await ctrl('seed-pages')).ci_calls,0);await load();assert.equal(await page.locator('#pipeline-picker-scope option').count(),25);await open();await page.locator('#pipeline-picker-next').click();await blank();await loaded();assert.equal(await page.locator('#pipeline-picker-scope option').count(),3);assert.equal(await page.locator('#pipeline-picker-next').isDisabled(),true)
  await load();await ctrl('invalidate-cursor');before=count(listPath);const conflict=page.waitForResponse(r=>new URL(r.url()).pathname===listPath&&new URL(r.url()).search);await page.locator('#pipeline-picker-next').click();assert.equal((await conflict).status(),409);await page.waitForFunction(()=>document.querySelector('#pipeline-picker-status').textContent.includes('no request was retried'));await frames(page);assert.equal(count(listPath),before+1);assert.equal(await page.locator('#pipeline-picker-scope option').count(),0)
  // pagehide and logout both invalidate directories even when abort is ignored.
  hold=held(listPath);await page.locator('#pipeline-picker-load').click();await within(hold.started);await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide')));hold.release();await within(hold.done);await frames(page);router.remove(listPath);await blank();assert.equal(await page.locator('#pipeline-picker').isVisible(),false);assert.equal(await page.locator('#pipeline-picker-workspace option').count(),0)
  await page.locator('#app-recheck').click();await ready();hold=held(listPath);await page.locator('#pipeline-picker-load').click();await within(hold.started);await page.locator('#app-sign-out').click();await page.waitForFunction(()=>document.querySelector('#app-status').textContent.includes('server session is revoked'));hold.release();await within(hold.done);await frames(page);router.remove(listPath);await blank();assert.equal(await page.locator('#pipeline-picker-scope option').count(),0)
  await login();await twoTabAcceptance({page,context,info,router,releases,watch})
  await within(router.drain(),'scenario route drain');await within(context.unrouteAll({behavior:'wait'}),'scenario route removal');await login();await load();await open();await page.goto(info.origin+'/manage/');await page.goBack();await page.locator('#app-recheck').waitFor();await frames(page);await blank();assert.equal(await page.locator('#workspace').inputValue(),'');assert.equal(await page.locator('#pipeline-picker-scope option').count(),0)
  await page.locator('#app-recheck').click();await ready();await load();assert.equal((await ctrl('status')).ci_calls,0)
  // Discovery selection is never cached authority after membership revocation.
  assert.equal(await page.evaluate(async()=>{const r=await fetch('/synthetic/revoke-membership',{method:'POST'});return r.status}),200);await use();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('unavailable'));await blank()
  assert.equal(await page.evaluate(async()=>{const r=await fetch('/synthetic/revoke-session',{method:'POST'});return r.status}),200);await page.locator('#pipeline-picker-load').click();await page.waitForFunction(()=>document.querySelector('#app-principal').textContent==='Not verified');assert.equal(await page.locator('#pipeline-picker').isVisible(),false);assert.equal(await page.locator('#workspace').inputValue(),'')
  assert.deepEqual(requests.filter(r=>r.path.includes('/pipelines/')&&(r.method!=='GET'||/\/(logs|trigger|retry|cancel)/.test(r.path))),[]);assert.deepEqual(await page.evaluate(()=>[localStorage.length,sessionStorage.length]),[0,0]);assert.deepEqual(errors,[]);assert.deepEqual(outside,[]);assert.deepEqual(dialogs,[])
 }catch(error){failure=error}
 finally{
  const failures=failure?[failure]:[]
  for(const release of releases){try{release()}catch(error){failures.push(error)}}
  try{if(router)await within(router.drain(),'cleanup route drain')}catch(error){failures.push(error)}
  try{if(browser)await within(browser.close(),'browser close')}catch(error){failures.push(error)}
  try{await stopSyntheticFixture(child)}catch(error){failures.push(error)}
  try{fs.rmSync(temp,{recursive:true,force:true})}catch(error){failures.push(error)}
  if(failures.length===1)throw failures[0]
  if(failures.length)throw new AggregateError(failures,'Pipeline discovery scenario or cleanup failed')
 }
 console.log('Pipeline discovery: real HTTPS Cookie/SQLite, 3 isolated workspaces with identical run/task IDs, empty snapshot, explicit picker to runs/details/tasks/durations, cursor failure, ignored abort, repeated operations, manual/pagehide/logout/Back, concurrent two-tab picker scopes with reload/Back/Forward and cross-tab logout, unsupported HTML deep-link 404, revocation, XSS/contrast/mobile and 3 allowlisted screenshots. Zero CI provider calls; synthetic only.')
}
function reportFailure(error){
 console.error(error?.stack||String(error))
 if(error instanceof AggregateError)for(const nested of error.errors)reportFailure(nested)
 if(error?.cause)reportFailure(error.cause)
}
main().catch(error=>{reportFailure(error);process.exitCode=1})
