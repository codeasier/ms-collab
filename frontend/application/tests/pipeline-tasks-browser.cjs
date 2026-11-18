const {stopSyntheticFixture}=require('./fixture-process.cjs')
// Fictional task metadata only. Never requests raw logs or contacts a real CI.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),readline=require('node:readline')
const {createHash}=require('node:crypto')
const {spawn,execFileSync}=require('node:child_process'),{chromium}=require('playwright')
const {createScenarioRouter}=require('../../workspace-admin/tests/scenario-router.cjs')
async function within(p){let timer;try{return await Promise.race([p,new Promise((_,reject)=>timer=setTimeout(()=>reject(Error('synthetic barrier timeout')),15000))])}finally{clearTimeout(timer)}}
const frame=page=>page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))))
// Listen to the page's actual reads after unrouteAll; never supply a DTO.
async function checkFractionalRuns(page,origin,base){
 const ids=['timing-01','timing-02','timing-03'], selected=ids[1]
 const scope={workspace_id:'alpha',connection_id:'example-ci',pipeline_id:'build'}
 const source={kind:'stored_snapshot',revision:2,full_history_complete:false,live_provider_verified:false}
 const read=async suffix=>{
  const r=await page.waitForResponse(r=>r.url()===origin+base+suffix&&r.request().method()==='GET',{timeout:15000})
  assert.equal(r.status(),200);const {data}=await r.json();assert.deepEqual(data.scope,scope);assert.deepEqual(data.source,source);return data
 }
 const [runs,report]=await Promise.all([read('/runs?limit=100'),read('/durations?limit=100'),page.locator('#reload').click()])
 const matching=runs.items.filter(r=>ids.includes(r.id))
 assert.deepEqual(matching.map(r=>r.id).sort(),ids);assert.ok(matching.every(r=>r.status==='success'&&r.details_available&&r.stage_count===1&&r.job_count===1))
 const groups=report.groups.filter(g=>g.stage_name==='Timing'&&g.job_name==='Probe');assert.equal(groups.length,1)
 const [g]=groups;assert.deepEqual([g.count,g.min_seconds,g.average_seconds,g.max_seconds,g.latest_seconds],[3,0.1,0.1,0.1,0.1])
 assert.deepEqual(g.samples.map(s=>s.run_id).sort(),ids)
 for(const s of g.samples){
  assert.equal(s.run_status,'success');assert.equal(s.job_id,'job-'+s.run_id);assert.equal(s.duration_seconds,0.1)
  assert.equal(s.observed_at,matching.find(r=>r.id===s.run_id).observed_at)
  assert.equal(Date.parse(s.finished_at)-Date.parse(s.started_at),100)
 }
 await page.locator('#panel:not([hidden])').waitFor();assert.equal(await page.locator('#scope-title').textContent(),'alpha / example-ci / build')
 assert.match(await page.locator('#revision').textContent(),/Stored snapshot version 2/)
 const trend=page.locator('#durations article').filter({has:page.getByRole('heading',{name:'Timing / Probe',exact:true})})
 assert.deepEqual(await trend.locator('dd').allTextContents(),['3','0.1','0.1','0.1','0.1'])
 await trend.locator('summary').click();assert.equal(await trend.locator('.samples:visible li').count(),3)
 const samples=await trend.locator('.samples li').allTextContents();for(const id of ids)assert.ok(samples.some(line=>line.startsWith(id+' (success) · 0.1 seconds ·')))
 const runPath='/runs/'+selected
 const [detail,taskPage]=await Promise.all([read(runPath),read(runPath+'/tasks?revision=2&offset=0&limit=10'),page.getByRole('button',{name:'View '+selected,exact:true}).click()])
 const run=detail.run, sample=g.samples.find(s=>s.run_id===selected)
 assert.equal(run.id,selected);assert.equal(run.status,'success');assert.equal(run.observed_at,sample.observed_at)
 const job={id:sample.job_id,name:'Probe',status:'success',started_at:sample.started_at,finished_at:sample.finished_at}
 assert.deepEqual(run.stages,[{name:'Timing',jobs:[job]}])
 assert.deepEqual(taskPage.run,{id:selected,status:'success',observed_at:run.observed_at,details_available:true});assert.equal(taskPage.total,1)
 assert.deepEqual(taskPage.items,[{...job,stage_index:0,job_index:0,id_occurrences:1}])
 await page.locator('#detail[open] #tasks article').waitFor()
 assert.equal(await page.locator('#detail-title').textContent(),selected);assert.match(await page.locator('#detail-status').textContent(),/success · snapshot version 2/)
 assert.match(await page.locator('#task-stages').textContent(),/Timing · 1 recorded tasks · success: 1/)
 assert.equal(await page.locator('#tasks article h4').textContent(),job.name)
 assert.deepEqual(await page.locator('#tasks article p').allTextContents(),['Stage 1, task 1 · '+job.id+' · success','Started: '+job.started_at,'Finished: '+job.finished_at])
 await page.keyboard.press('Escape')
 return {selected,sample,latest:g.samples.at(-1).run_id,detail,taskPage}
}
// A second, independent activation with no run-list button available. Only
// browser response observers are attached; no extra service read or mock DTO.
async function fromDurationSample(page,origin,base,folder,known){
 const {selected,sample,latest,detail,taskPage}=known
 assert.equal(latest,'timing-03');assert.notEqual(selected,latest);assert.notEqual(selected,sample.job_id)
 const urls=[base+'/runs/'+selected,base+'/runs/'+selected+'/tasks?revision=2&offset=0&limit=10']
 const journal=[]
 const collect=r=>{const u=new URL(r.url());if(u.pathname.startsWith('/api/'))journal.push([r.method(),u.origin===origin,u.pathname+u.search,r.postData()===null])}
 const read=async suffix=>{
  const r=await page.waitForResponse(res=>res.url()===origin+suffix&&res.request().method()==='GET',{timeout:15000})
  assert.equal(r.status(),200);assert.equal(await r.headerValue('cache-control'),'no-store')
  assert.match(await r.headerValue('content-type'),/^application\/json(?:;|$)/)
  return r.json()
 }
 page.on('request',collect)
 try{
  await page.setViewportSize({width:390,height:844})
  await page.locator('#query').fill('no-run-matches-this-filter');await page.locator('#filters button').click()
  assert.equal(await page.locator('#runs button').count(),0);assert.match(await page.locator('#summary').textContent(),/^0 matching runs/)
  const card=page.locator('#durations article').filter({has:page.getByRole('heading',{name:'Timing / Probe',exact:true})})
  const control=card.locator('.samples li').getByRole('button',{name:'View run '+selected,exact:true})
  assert.equal(await control.count(),1);assert.equal(await control.innerText(),'View run')
  assert.ok(await control.evaluate(n=>n instanceof HTMLButtonElement&&n.type==='button'&&!n.disabled&&n.closest('details').open))
  const expectedText=selected+' ('+sample.run_status+') · '+sample.duration_seconds+' seconds · started '+sample.started_at
  assert.ok(await control.evaluate((n,s)=>n.closest('li').textContent.startsWith(s),expectedText))
  const previous=card.getByRole('button',{name:'View run timing-01',exact:true})
  await previous.focus();await page.keyboard.press('Tab')
  assert.ok(await control.evaluate(n=>document.activeElement===n&&n.matches(':focus-visible')))
  await control.evaluate(n=>n.scrollIntoView({block:'center',behavior:'instant'}))
  await frame(page)
  const geometry=await control.evaluate(n=>{
   const r=n.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2
   if(!(n===document.activeElement&&r.width>0&&r.height>0&&r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight&&
        n.contains(document.elementFromPoint(x,y))&&document.documentElement.scrollWidth<=innerWidth))throw Error('Sample button is not visibly reachable')
   return {x:Math.round(r.x),y:Math.round(r.y),width:Math.round(r.width),height:Math.round(r.height)}
  })
  const name='duration-sample-mobile.png',image=await page.screenshot({path:path.join(folder,name),animations:'disabled'})
  assert.equal(image.subarray(0,8).toString('hex'),'89504e470d0a1a0a');assert.equal(image.toString('ascii',12,16),'IHDR')
  assert.equal(image.readUInt32BE(16),390);assert.equal(image.readUInt32BE(20),844);assert.ok(image.length>4096)
  console.log('PIPELINE_DURATION_SAMPLE_SCREEN '+JSON.stringify({name,width:390,height:844,bytes:image.length,sha256:createHash('sha256').update(image).digest('hex'),button:geometry}))
  assert.deepEqual(journal,[]) // local filtering, disclosure and focus cannot trigger a read
  const [selectedDetail,selectedTasks]=await Promise.all([read(urls[0]),read(urls[1]),page.keyboard.press('Enter')])
  assert.deepEqual(selectedDetail,{data:detail});assert.deepEqual(selectedTasks,{data:taskPage})
  await page.locator('#detail[open] #tasks article').waitFor()
  assert.equal(await page.locator('#detail-title').textContent(),sample.run_id)
  assert.match(await page.locator('#detail-status').textContent(),/success · snapshot version 2/)
  assert.deepEqual(await page.locator('#tasks article p').allTextContents(),['Stage 1, task 1 · '+sample.job_id+' · success','Started: '+sample.started_at,'Finished: '+sample.finished_at])
  assert.equal(await page.locator('#tasks article h4').textContent(),'Probe')
  assert.equal(page.url(),origin+'/pipelines/');assert.deepEqual(await page.evaluate(()=>[localStorage.length,sessionStorage.length]),[0,0])
  await page.keyboard.press('Escape');assert.equal(await page.locator('#detail').isVisible(),false)
  assert.equal(await page.locator('#detail-body').textContent(),'');assert.equal(await page.locator('#runs button').count(),0)
  assert.ok(await control.evaluate(n=>document.activeElement===n&&n.closest('details').open))
  assert.equal(await page.locator('#query').inputValue(),'no-run-matches-this-filter')
  await page.locator('#query').fill('');await page.locator('#filters button').click()
  await page.setViewportSize({width:1440,height:1050})
  assert.deepEqual(journal,urls.map(url=>['GET',true,url,true]))
 }finally{page.off('request',collect)}
}
async function main(){
 const temp=fs.mkdtempSync(path.join(os.tmpdir(),'ms-collab-pipeline-tasks-'));let server,browser
 try{
  execFileSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-days','1','-keyout',path.join(temp,'key.pem'),'-out',path.join(temp,'cert.pem'),'-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'],{stdio:'ignore'})
  server=spawn('python3',['-m','packages.application_ui.tests.browser_server',path.join(temp,'cert.pem'),path.join(temp,'key.pem'),'pipeline-tasks'],{env:{...process.env,MS_COLLAB_SYNTHETIC_APPLICATION_UI:'1'},stdio:['ignore','pipe','pipe']});server.stderr.resume()
  const lines=readline.createInterface({input:server.stdout}),fixture=await within(new Promise((resolve,reject)=>{lines.once('line',line=>{try{resolve(JSON.parse(line))}catch(e){reject(e)}});server.once('exit',()=>reject(Error('fixture stopped')))}))
  const origin=fixture.origin,folder=path.join(process.env.RUNNER_TEMP||os.tmpdir(),'ms-collab-pipeline-tasks');fs.mkdirSync(folder,{recursive:true})
  browser=await chromium.launch({headless:true});const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1440,height:1050}}),page=await context.newPage(),errors=[],dialogs=[],requests=[],external=[]
  await context.addInitScript(()=>{const actual=window.fetch;window.fetch=(input,options={})=>{const {signal,...rest}=options;return actual.call(window,input,rest)}})
  const router=createScenarioRouter({origin,onExternal:()=>external.push('unexpected origin')});await context.route('**/api/**',route=>router.handle(route))
  page.on('pageerror',e=>errors.push(e.message));page.on('dialog',async d=>{dialogs.push(d.message());await d.dismiss()});page.on('request',r=>{const u=new URL(r.url());assert.equal(r.headers().authorization,undefined);if(![origin,new URL(fixture.provider).origin].includes(u.origin))external.push(u.origin);requests.push({path:u.pathname,method:r.method()})})
  const scope=async()=>{await page.locator('#workspace').fill('alpha');await page.locator('#connection').fill('example-ci');await page.locator('#pipeline').fill('build');await page.locator('#scope-form button').click();await page.locator('#panel:not([hidden])').waitFor()}
  const ready=()=>page.waitForFunction(()=>document.querySelector('#task-status-filter')&&!document.querySelector('#task-status-filter').disabled)
  const detail=async(id='run-tasks')=>{await page.getByRole('button',{name:'View '+id,exact:true}).click();await ready()}
  const activated=()=>page.waitForFunction(()=>document.querySelector('#app-principal')?.textContent==='admin'&&!document.querySelector('#session').hidden)
  await page.goto(origin+'/');await page.locator('#app-sign-in:not([disabled])').click();await page.waitForFunction(()=>document.querySelector('#app-principal')?.textContent==='admin');await page.locator('#app-pipelines-link').click();await activated();await scope();await detail()
  assert.match(await page.locator('#task-summary').textContent(),/23 matching of 23.*Page 1 of 3.*duplicate-ID observations: 3/);assert.equal(await page.locator('#tasks article').count(),10);assert.match(await page.locator('#task-stages').textContent(),/Stage result was not supplied/);assert.match(await page.locator('#tasks').textContent(),/Ambiguous provider ID: 3/);assert.equal(await page.locator('#detail-body img,#detail-body b,#detail-body script').count(),0);assert.ok(!(await page.locator('body').textContent()).includes('DISCARDED_SYNTHETIC_FIELD'))
  await page.screenshot({path:path.join(folder,'desktop.png'),fullPage:false});await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.ok(await page.locator('#detail').evaluate(n=>n.scrollWidth<=n.clientWidth))
  // Exercise the modal's real mobile scroll area and paging controls, not only
  // a full-page image whose first screen can hide the task controls.
  await page.locator('#task-status-filter').scrollIntoViewIfNeeded();await page.locator('#task-next').click();await ready();assert.match(await page.locator('#task-summary').textContent(),/Page 2 of 3/);await page.locator('#task-previous').click();await ready();assert.match(await page.locator('#task-summary').textContent(),/Page 1 of 3/);await page.locator('#task-status-filter').scrollIntoViewIfNeeded();assert.ok(await page.locator('.task-controls').evaluate(n=>n.scrollWidth<=n.clientWidth));await page.screenshot({path:path.join(folder,'mobile.png'),fullPage:false});await page.setViewportSize({width:1440,height:1050});await page.locator('#detail').evaluate(n=>{n.scrollTop=0})
  await page.locator('#task-next').click();await ready();assert.match(await page.locator('#task-summary').textContent(),/Page 2 of 3/);await page.locator('#task-next').click();await ready();assert.equal(await page.locator('#tasks article').count(),3);assert.match(await page.locator('#tasks').textContent(),/Stage 2, task 9/);await page.screenshot({path:path.join(folder,'detail.png'),fullPage:false})
  await page.locator('#task-status-filter').selectOption('failure');await ready();assert.match(await page.locator('#task-summary').textContent(),/11 matching of 23/);assert.match(await page.locator('#task-stages').textContent(),/success: 6/);await page.locator('#task-next').click();await ready();assert.equal(await page.locator('#tasks article').count(),1);await page.keyboard.press('Escape')
  await detail('run-missing');assert.match(await page.locator('#tasks').textContent(),/details unavailable/);await page.keyboard.press('Escape');await detail('run-empty');assert.match(await page.locator('#tasks').textContent(),/No recorded tasks/);await page.keyboard.press('Escape')
  const base='/api/workspaces/alpha/pipelines/example-ci/build',taskPath=base+'/runs/run-tasks/tasks'
  // Even a transport ignoring AbortSignal may not populate a newer dialog.
  let seen,release,finished;const started=new Promise(r=>seen=r),barrier=new Promise(r=>release=r),done=new Promise(r=>finished=r)
  router.set(taskPath,async route=>{const response=await route.fetch();seen();await barrier;await route.fulfill({response});finished()})
  await page.getByRole('button',{name:'View run-tasks',exact:true}).click();await within(started);await page.keyboard.press('Escape');await detail('run-empty');release();await within(done);await frame(page);assert.equal(await page.locator('#detail-title').textContent(),'run-empty');assert.equal(await page.locator('#tasks article').count(),0);await page.keyboard.press('Escape');router.remove(taskPath)
  router.set(taskPath,async route=>{const response=await route.fetch(),body=await response.json();body.data.stages[0].status='success';await route.fulfill({response,json:body})});await page.getByRole('button',{name:'View run-tasks',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#panel').hidden);assert.equal(await page.locator('#detail').isVisible(),false);router.remove(taskPath);await scope();await detail()
  // Actual stored revision changes cannot silently continue the prior page.
  assert.equal(await page.evaluate(async()=>{const r=await fetch('/synthetic/advance-pipeline-tasks',{method:'POST'});return r.status}),200);await page.locator('#task-next').click();await page.waitForFunction(()=>document.querySelector('#panel').hidden);assert.equal(await page.locator('#detail-body').textContent(),'');await scope();await detail();assert.match(await page.locator('#detail-status').textContent(),/version 2/);await page.keyboard.press('Escape')
  // Scope navigation while a task read is pending removes every older field.
  let hideSeen,hideRelease,hideFinished;const hideStarted=new Promise(r=>hideSeen=r),hideBarrier=new Promise(r=>hideRelease=r),hideDone=new Promise(r=>hideFinished=r)
  router.set(taskPath,async route=>{const response=await route.fetch();hideSeen();await hideBarrier;await route.fulfill({response});hideFinished()});await page.getByRole('button',{name:'View run-tasks',exact:true}).click();await within(hideStarted);await page.keyboard.press('Escape');await page.locator('#workspace').fill('beta');hideRelease();await within(hideDone);await frame(page);assert.equal(await page.locator('#detail-body').textContent(),'');assert.equal(await page.locator('#panel').isVisible(),false);router.remove(taskPath);await scope();await detail()
  await router.drain();await context.unrouteAll({behavior:'wait'})
  // The earlier revision change published real timestamps in the same authorized
  // temporary store. Restore the original run dialog for the revocation tests.
  await page.keyboard.press('Escape');const readStart=requests.length
  const known=await checkFractionalRuns(page,origin,base)
  assert.deepEqual(requests.slice(readStart),['/runs','/durations','/runs/timing-02','/runs/timing-02/tasks'].map(suffix=>({path:base+suffix,method:'GET'})))
  await fromDurationSample(page,origin,base,folder,known)
  await detail()
  assert.equal(await page.evaluate(async()=>{const r=await fetch('/synthetic/revoke-membership',{method:'POST'});return r.status}),200);await page.locator('#task-next').click();await page.waitForFunction(()=>document.querySelector('#panel').hidden);assert.equal(await page.locator('#detail-body').textContent(),'')
  assert.equal(await page.evaluate(async()=>{const r=await fetch('/synthetic/revoke-session',{method:'POST'});return r.status}),200);await page.locator('#scope-form button').click();await page.waitForFunction(()=>document.querySelector('#app-principal').textContent==='Not verified');assert.equal(await page.locator('#workspace').inputValue(),'')
  assert.deepEqual(requests.filter(r=>r.path.includes('/pipelines/')&&(r.method!=='GET'||r.path.includes('/logs'))),[]);assert.deepEqual(await page.evaluate(()=>[localStorage.length,sessionStorage.length]),[0,0]);assert.deepEqual(errors,[]);assert.deepEqual(dialogs,[]);assert.deepEqual(external,[])
 }finally{
  const failures=[]
  try{await browser?.close()}catch(error){failures.push(error)}
  try{await stopSyntheticFixture(server)}catch(error){failures.push(error)}
  try{fs.rmSync(temp,{recursive:true,force:true})}catch(error){failures.push(error)}
  if(failures.length)throw new AggregateError(failures,'Synthetic pipeline cleanup failed')
 }
  console.log('Pipeline task HTTPS acceptance passed: stage counts without invented results, duplicate IDs, pinned pagination/filtering, missing/empty, unsafe-field rejection, stale detail/scope/revision, current revocation, XSS and three regression screenshots, plus a separate duration-sample mobile screenshot. No raw logs or live provider.')
  console.log('PIPELINE_FRACTIONAL_MEAN_PASS revision 2: 3 distinct successful runs, 3 x 100 ms; exact minimum/mean/maximum 0.1 s; real Cookie GETs and visible trend, run detail and task. No live CI or execution.')
  console.log('PIPELINE_DURATION_SAMPLE_COOKIE_PASS: Timing / Probe, non-latest timing-02, 390px focused sample and keyboard drilldown, zero matching list rows; only authorized run detail and revision-2 task GETs. Synthetic only.')
}
main().catch(error=>{console.error(error.stack);process.exitCode=1})
