// Synthetic HTTPS/session/catalog only. No real providers, source paths or execution.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),readline=require('node:readline')
const {spawn,execFileSync}=require('node:child_process'),{chromium}=require('playwright')
const {createScenarioRouter}=require('../../workspace-admin/tests/scenario-router.cjs')
async function within(p){let timer;try{return await Promise.race([p,new Promise((_,reject)=>timer=setTimeout(()=>reject(Error('synthetic barrier timeout')),15000))])}finally{clearTimeout(timer)}}
const frame=page=>page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))))
async function main(){
 const temp=fs.mkdtempSync(path.join(os.tmpdir(),'ms-collab-research-ui-'));let server,browser
 try{
  execFileSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-days','1','-keyout',path.join(temp,'key.pem'),'-out',path.join(temp,'cert.pem'),'-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'],{stdio:'ignore'})
  server=spawn('python3',['-m','packages.application_ui.tests.browser_server',path.join(temp,'cert.pem'),path.join(temp,'key.pem'),'assets'],{env:{...process.env,MS_COLLAB_SYNTHETIC_APPLICATION_UI:'1'},stdio:['ignore','pipe','pipe']});server.stderr.resume()
  const lines=readline.createInterface({input:server.stdout}),fixture=await within(new Promise((resolve,reject)=>{lines.once('line',line=>{try{resolve(JSON.parse(line))}catch(e){reject(e)}});server.once('exit',()=>reject(Error('fixture stopped')))}))
  const origin=fixture.origin,folder=path.join(process.env.RUNNER_TEMP||os.tmpdir(),'ms-collab-research-assets-ui');fs.mkdirSync(folder,{recursive:true})
  browser=await chromium.launch({headless:true});const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1440,height:1000}}),page=await context.newPage(),errors=[],dialogs=[],requests=[],external=[]
  await context.addInitScript(()=>{const actual=window.fetch;window.fetch=(input,options={})=>{const {signal,...rest}=options;return actual.call(window,input,rest)}})
  const router=createScenarioRouter({origin,onExternal:()=>external.push('unexpected origin')});await context.route('**/api/**',route=>router.handle(route))
  page.on('pageerror',error=>errors.push(error.message));page.on('dialog',async d=>{dialogs.push(d.message());await d.dismiss()})
  page.on('request',r=>{const u=new URL(r.url());assert.equal(r.headers().authorization,undefined);if(![origin,new URL(fixture.provider).origin].includes(u.origin))external.push(u.origin);requests.push({path:u.pathname,method:r.method()})})
  const ready=()=>page.waitForFunction(()=>!document.querySelector('#panel').hidden&&!document.querySelector('#reload').disabled)
  const scope=async(catalog='knowledge',workspace='alpha')=>{const manual=page.locator('#manual-scope');if(!await manual.evaluate(e=>e.open))await manual.locator('summary').click();await page.locator('#workspace').fill(workspace);await page.locator('#catalog').fill(catalog);await page.locator('#scope-form button').click()}
  const open=async(catalog='knowledge')=>{await scope(catalog);await ready()}
  const pickerWorkspace=page.locator('#catalog-picker-workspace'),pickerCatalog=page.locator('#catalog-picker-catalog'),pickerLoad=page.locator('#catalog-picker-load'),pickerOpen=page.locator('#catalog-picker-open')
  const choices=select=>select.locator('option').evaluateAll(rows=>rows.map(r=>r.value).filter(Boolean))
  const waitCatalogs=expected=>page.waitForFunction(ids=>JSON.stringify([...document.querySelector('#catalog-picker-catalog').options].map(o=>o.value).filter(Boolean))===JSON.stringify(ids),expected)
  const discover=async(workspace,expected)=>{await pickerWorkspace.selectOption(workspace);await pickerLoad.click();await waitCatalogs(expected);assert.equal(await pickerOpen.isEnabled(),false)}
  const choose=async(catalog,{read=true}={})=>{await pickerCatalog.selectOption(catalog);if(read){await pickerOpen.click();await ready()}}
  const activated=()=>page.waitForFunction(()=>document.querySelector('#app-principal')?.textContent==='admin'&&!document.querySelector('#session').hidden)
  const login=async()=>{await page.locator('#app-sign-in').click();await page.waitForFunction(()=>document.querySelector('#app-principal')?.textContent==='admin');await page.locator('#app-research-assets-link').click();await activated()}
  const filter=async(type='',entity='',q='')=>{await page.locator('#type').selectOption(type);await page.locator('#entity').selectOption(entity);await page.locator('#query').fill(q);await page.locator('#filters button').click();await ready()}
  await page.goto(origin+'/');await page.waitForFunction(()=>!document.querySelector('#app-sign-in').disabled);await login();assert.equal(await page.locator('input[type="password"],#credential,#connect').count(),0)
  await page.waitForFunction(()=>['alpha','beta'].every(id=>[...document.querySelector('#catalog-picker-workspace').options].some(o=>o.value===id)))
  assert.deepEqual(await choices(pickerWorkspace),['alpha','beta']);assert.equal(await page.locator('#manual-scope').evaluate(e=>e.open),false)
  assert.deepEqual(requests.filter(r=>r.path.includes('/research-assets')&&r.path.startsWith('/api/')),[])
  assert.equal(await page.locator('#catalog-picker-workspace img').count(),0)
  await discover('alpha',['empty','knowledge'])
  assert.deepEqual(requests.filter(r=>r.path.includes('/research-assets')&&r.path.startsWith('/api/')).map(r=>r.path),['/api/workspaces/alpha/research-assets'])
  await choose('empty');assert.match(await page.locator('#availability').textContent(),/Published normalized/);assert.match(await page.locator('#page-summary').textContent(),/0 matching/)
  await choose('knowledge');assert.match(await page.locator('#page-summary').textContent(),/28 matching stored assets · Page 1 of 2/);assert.equal(await page.locator('#type-counts li').count(),4);assert.equal(await page.locator('#assets li').count(),20)
  assert.equal(await page.locator('#assets img,#assets script,#assets b').count(),0);assert.match(await page.locator('#assets').textContent(),/<img src=x/)
  await page.screenshot({path:path.join(folder,'desktop.png'),fullPage:true});await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.ok(await page.locator('button:visible').evaluateAll(nodes=>nodes.every(n=>n.scrollWidth<=n.clientWidth)));await page.screenshot({path:path.join(folder,'mobile.png'),fullPage:true});await page.setViewportSize({width:1440,height:1000})
  await page.locator('#next').click();await ready();assert.match(await page.locator('#page-summary').textContent(),/Page 2 of 2/);assert.equal(await page.locator('#assets li').count(),8);await page.locator('#previous').click();await ready()
  await filter('runbook');assert.match(await page.locator('#page-summary').textContent(),/1 matching/);await page.locator('#assets button').click();await page.waitForFunction(()=>document.querySelector('#detail-body').textContent.includes('verified'));assert.match(await page.locator('#asset-detail').textContent(),/source declarations only/);await page.keyboard.press('Escape')
  await filter('records');await page.locator('#assets button').first().click();await page.waitForFunction(()=>document.querySelector('#detail-body').textContent.includes('<script>'));assert.equal(await page.locator('#detail-body img,#detail-body script,#detail-body a').count(),0);await page.screenshot({path:path.join(folder,'detail.png'),fullPage:true});await page.locator('#detail-close').click()
  await filter('api');assert.match(await page.locator('#assets').textContent(),/Build <b>contract/);await filter('fault');assert.match(await page.locator('#assets').textContent(),/Fault guide/)
  await filter('','editor');assert.match(await page.locator('#page-summary').textContent(),/13 matching/);await filter('','','no-matching-synthetic-title');assert.match(await page.locator('#assets').textContent(),/No assets match/)
  await open('unpublished');assert.match(await page.locator('#availability').textContent(),/Not published yet/);assert.match(await page.locator('#assets').textContent(),/not a verified empty source/)
  await open('empty');assert.match(await page.locator('#availability').textContent(),/Published normalized/);assert.match(await page.locator('#page-summary').textContent(),/0 matching/);await open()
  // Input edits clear old evidence before submission; shared space is not public.
  await page.locator('#workspace').fill('commons');assert.equal(await page.locator('#panel').isVisible(),false);assert.equal(await page.locator('#assets li').count(),0);await scope('knowledge','commons');await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('unavailable'));await open()
  const base='/api/workspaces/alpha/research-assets/knowledge'
  let seen,release,finished;const started=new Promise(r=>seen=r),barrier=new Promise(r=>release=r),done=new Promise(r=>finished=r)
  router.set(base+'/types',async route=>{const response=await route.fetch();seen();await barrier;await route.fulfill({response});finished()})
  await page.locator('#reload').click();await within(started);await scope('knowledge','commons');await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('unavailable'));const count=requests.filter(r=>r.path===base+'/entities').length;release();await within(done);await frame(page);assert.equal(requests.filter(r=>r.path===base+'/entities').length,count);assert.equal(await page.locator('#panel').isVisible(),false);router.remove(base+'/types');await open()
  // The actual old workspace directory response is held across a switch. The
  // new workspace has an independently authorized catalog with the same ID.
  const aDirectory='/api/workspaces/alpha/research-assets'
  let dirSeen,dirRelease,dirFinished;const dirStarted=new Promise(r=>dirSeen=r),dirBarrier=new Promise(r=>dirRelease=r),dirDone=new Promise(r=>dirFinished=r)
  router.set(aDirectory,async route=>{const response=await route.fetch();assert.equal(response.status(),200);dirSeen();await dirBarrier;await route.fulfill({response});dirFinished()})
  await pickerWorkspace.selectOption('alpha');await pickerLoad.click();await within(dirStarted)
  await pickerWorkspace.selectOption('beta');assert.equal(await page.locator('#panel').isVisible(),false);assert.deepEqual(await choices(pickerCatalog),[])
  await pickerLoad.click();await waitCatalogs(['beta-only','knowledge']);await choose('knowledge')
  assert.equal(await page.locator('#scope-title').textContent(),'beta / knowledge')
  assert.match(await page.locator('#assets').textContent(),/Beta research only/)
  assert.doesNotMatch(await page.locator('#assets').textContent(),/Research 00/)
  await page.locator('#assets button').click();await page.waitForFunction(()=>document.querySelector('#detail-body').textContent.includes('independently authorized beta catalog'))
  await page.locator('#detail-close').click()
  dirRelease();await within(dirDone);await frame(page)
  assert.deepEqual(await choices(pickerCatalog),['beta-only','knowledge'])
  assert.equal(await page.locator('#scope-title').textContent(),'beta / knowledge')
  assert.match(await page.locator('#assets').textContent(),/Beta research only/)
  router.remove(aDirectory);await open()
  // Revision changes across pages fail closed; explicit reload is the only recovery.
  assert.equal(await page.evaluate(async()=>{const r=await fetch('/synthetic/advance-assets',{method:'POST'});return r.status}),200)
  await page.locator('#next').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('revision changed'));assert.equal(await page.locator('#panel').isVisible(),false);assert.equal(await page.locator('#assets li').count(),0);await page.locator('#reload').click();await ready();assert.match(await page.locator('#revision').textContent(),/revision 3/)
  // A wrong-scope envelope is not a successful empty catalog.
  router.set(base+'/entities',async route=>{const response=await route.fetch(),body=await response.json();body.data.scope.catalog_id='other';await route.fulfill({response,json:body})})
  await page.locator('#reload').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('could not be validated'));assert.equal(await page.locator('#panel').isVisible(),false);router.remove(base+'/entities');await page.locator('#reload').click();await ready()
  // Close one delayed detail and open a different asset; late bytes cannot replace it.
  const catalog=await page.evaluate(async base=>(await(await fetch(base+'/assets?revision=3&page=1&page_size=20')).json()).data.items,base),first=catalog[0],second=catalog[1]
  let detailSeen,detailRelease,detailFinished;const detailStarted=new Promise(r=>detailSeen=r),detailBarrier=new Promise(r=>detailRelease=r),detailDone=new Promise(r=>detailFinished=r)
  router.set(base+'/assets/'+first.id,async route=>{const response=await route.fetch();detailSeen();await detailBarrier;await route.fulfill({response});detailFinished()})
  await page.locator('#assets button').nth(0).click();await within(detailStarted);await page.keyboard.press('Escape');await page.locator('#assets button').nth(1).click();await page.waitForFunction(()=>document.querySelector('#detail-body').textContent.includes('Synthetic symptom'));detailRelease();await within(detailDone);await frame(page);assert.equal(await page.locator('#detail-title').textContent(),second.title);await page.keyboard.press('Escape');router.remove(base+'/assets/'+first.id)
  // A genuinely pending workspace index must not hold the existing exact-ID
  // entry or session recheck hostage. Inject a late 401 in that retired request
  // only, after witnessing the real authenticated response from the fixture.
  let wsSeen,wsRelease,wsFinished;const wsStarted=new Promise(r=>wsSeen=r),wsBarrier=new Promise(r=>wsRelease=r),wsDone=new Promise(r=>wsFinished=r)
  router.set('/api/workspaces',async route=>{const response=await route.fetch();assert.equal(response.status(),200);wsSeen();await wsBarrier;await route.fulfill({status:401,json:{error:'authentication_required'}});wsFinished()})
  await page.locator('#app-recheck').click();await within(wsStarted);await activated()
  assert.equal(await pickerWorkspace.isEnabled(),false)
  await open()
  assert.equal(await page.locator('#app-recheck').isEnabled(),true)
  wsRelease();await within(wsDone)
  await frame(page)
  assert.equal(await pickerWorkspace.isEnabled(),false)
  assert.equal(await page.locator('#scope-title').textContent(),'alpha / knowledge')
  assert.equal(await page.locator('#app-principal').textContent(),'admin')
  router.remove('/api/workspaces')
  // pagehide/recheck while a list is pending cannot restore private records.
  let hideSeen,hideRelease,hideFinished;const hideStarted=new Promise(r=>hideSeen=r),hideBarrier=new Promise(r=>hideRelease=r),hideDone=new Promise(r=>hideFinished=r)
  router.set(base+'/assets',async route=>{const response=await route.fetch();hideSeen();await hideBarrier;await route.fulfill({response});hideFinished()})
  await page.locator('#next').click();await within(hideStarted);await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide')));assert.equal(await page.locator('#workspace').inputValue(),'');await page.locator('#app-recheck').click();await activated();hideRelease();await within(hideDone);await frame(page);assert.equal(await page.locator('#assets li').count(),0);router.remove(base+'/assets');await open()
  await page.goto(origin+'/manage/');await page.goBack();await page.locator('#app-recheck').waitFor();await frame(page);assert.equal(await page.locator('#workspace').inputValue(),'');assert.equal(await page.locator('#assets li').count(),0);await page.locator('#app-recheck').click();await activated();await open()
  await router.drain();await context.unrouteAll({behavior:'wait'})
  await page.locator('#disconnect').click();await page.waitForFunction(()=>document.querySelector('#app-status').textContent.includes('server session is revoked'));assert.equal(await page.locator('#assets li').count(),0);assert.equal(await page.locator('#session').isVisible(),false);await login()
  await discover('alpha',['empty','knowledge']);await choose('knowledge',{read:false})
  assert.equal(await page.evaluate(async()=>{const r=await fetch('/synthetic/revoke-membership',{method:'POST'});return r.status}),200)
  await pickerOpen.click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('unavailable'));assert.equal(await page.locator('#panel').isVisible(),false)
  await pickerLoad.click();await page.waitForFunction(()=>document.querySelector('#catalog-picker-status').textContent.includes('unavailable'));assert.deepEqual(await choices(pickerCatalog),[])
  assert.equal(await page.evaluate(async()=>{const r=await fetch('/synthetic/revoke-session',{method:'POST'});return r.status}),200)
  await scope();await page.waitForFunction(()=>document.querySelector('#app-principal').textContent==='Not verified');assert.equal(await page.locator('#session').isVisible(),false);assert.equal(await page.locator('#workspace').inputValue(),'');assert.equal(await page.locator('#detail-body').textContent(),'')
  assert.deepEqual(requests.filter(r=>r.path.includes('/research-assets')&&r.path.startsWith('/api/')&&r.method!=='GET'),[]);assert.deepEqual(await page.evaluate(()=>[localStorage.length,sessionStorage.length]),[0,0]);assert.deepEqual(errors,[]);assert.deepEqual(dialogs,[]);assert.deepEqual(external,[])
  console.log('Research assets cookie HTTPS acceptance passed: published catalog picker with paired workspaces, exact-scope/revision pagination, plain-text details, unpublished/empty distinction, late responses, logout/revocation and three screenshots. Synthetic only.')
 }finally{await browser?.close();if(server&&server.exitCode===null){server.kill();await within(new Promise(r=>server.once('exit',r)))}fs.rmSync(temp,{recursive:true,force:true})}
}
main().catch(error=>{console.error(error.stack);process.exitCode=1})
