const {stopSyntheticFixture}=require('./fixture-process.cjs')
// Synthetic local HTTPS, real session boundary, no production credentials/provider.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),readline=require('node:readline')
const {spawn,execFileSync}=require('node:child_process'),{chromium}=require('playwright')
const {createScenarioRouter}=require('../../workspace-admin/tests/scenario-router.cjs')
async function within(p){let timer;try{return await Promise.race([p,new Promise((_,reject)=>timer=setTimeout(()=>reject(new Error('synthetic barrier timeout')),15000))])}finally{clearTimeout(timer)}}
function deferred(){let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve}}
async function assertFacetCleared(page){
 for(const name of ['shared-labels','label-pages'])assert.ok(await page.locator('#'+name).evaluate(el=>el.hidden))
 assert.equal(await page.locator('#label-options button').count(),0);assert.equal(await page.locator('#label-input').inputValue(),'')
 for(const name of ['label-options','label-status','label-page'])assert.equal(await page.locator('#'+name).textContent(),'')
}
// The prior real revocation leaves the unauthenticated Back document with old
// cookies. /auth/start explicitly rejects and expires them without starting S256.
// Observe only response kinds/status and public protocol flags, never cookie values,
// auth codes, challenge/state strings or full provider/callback URLs in evidence.
async function signInReaderAfterRevocation({page,origin,provider,login}){
 const expected=['__Host-ms-collab-csrf','__Host-ms-collab-session']
 const names=async()=>(await page.context().cookies(origin)).map(c=>c.name).filter(n=>expected.includes(n)).sort()
 const start=origin+'/auth/start',destination=new URL(provider),responses=[]
 const trace=r=>{
  const url=new URL(r.url()),method=r.request().method(),status=r.status()
  if(r.url()===start)responses.push(['start',method,status])
  else if(url.origin===destination.origin&&url.pathname===destination.pathname){
   responses.push(['provider',method,status,url.searchParams.getAll('code_challenge_method').length===1&&url.searchParams.get('code_challenge_method')==='S256',url.searchParams.get('redirect_uri')===origin+'/auth/callback'])
  }else if(url.origin===origin&&url.pathname==='/auth/callback')responses.push(['callback',method,status])
 }
 await page.waitForFunction(()=>document.querySelector('#app-principal').textContent==='Not verified'&&!document.querySelector('#app-sign-in').disabled&&!document.querySelector('#app-recheck').disabled)
 assert.equal(page.url(),origin+'/community/');await assertFacetCleared(page);assert.deepEqual(await names(),expected)
 const historyBefore=await page.evaluate(()=>history.length);page.on('response',trace)
 try{
  const [denial]=await Promise.all([page.waitForResponse(r=>r.url()===start&&r.request().method()==='POST'),page.locator('#app-sign-in').click()])
  assert.equal(denial.status(),401)
  await page.waitForFunction(()=>document.querySelector('#app-status').textContent.includes('session expired or was rejected')&&!document.querySelector('#app-sign-in').disabled)
  assert.deepEqual(responses,[['start','POST',401]]);assert.deepEqual(await names(),[])
  assert.equal(page.url(),origin+'/community/');assert.equal(await page.evaluate(()=>history.length),historyBefore)
  assert.equal(await page.locator('#app-principal').innerText(),'Not verified');assert.ok(await page.locator('#session').isHidden());await assertFacetCleared(page)
  // Only after that verified denial: an explicit new login from the cleared browser.
  await login('reader')
  assert.deepEqual(responses,[['start','POST',401],['start','POST',200],['provider','GET',303,true,true],['callback','GET',303]])
  assert.deepEqual(await names(),expected);assert.equal(await page.locator('#app-principal').innerText(),'reader')
  console.log('Community approved-label acceptance: stale start 401 cleared old cookies; explicit fresh reader start 200, S256 provider and callback 303.')
 }finally{page.off('response',trace)}
}
// A separate, pre-approved synthetic connection. Positive values are read from the real
// Cookie GETs issued by Open, never manufactured by the page, a route, or another API call.
async function approvedLabelJourney({page,origin,provider,folder,login,logout,ready,settled,control,renderTick}){
 const base='/api/workspaces/beta/community/labels/sample/project',scope={workspace_id:'beta',connection_id:'labels',repository:'sample/project'}
 const long='integration-contract-for-candidate-compatibility-with-platform-toolchains',rare='z-rare-exact',privateLabel='unshared-review-label'
 const id=name=>page.locator('#'+name),buttons=id('label-options').getByRole('button'),option=name=>buttons.filter({has:page.getByText(JSON.stringify(name),{exact:true})})
 const open=async()=>{await id('workspace').fill('beta');await id('connection').fill('labels');await id('repository').fill('sample/project');await page.locator('#scope-form button').click()}
 const filters=async(query='',kind='',state='')=>{await id('query').fill(query);await id('kind').selectOption(kind);await id('state').selectOption(state);await page.locator('#filters button').click()}
 const response=suffix=>page.waitForResponse(r=>r.url()===origin+base+suffix&&r.request().method()==='GET')
 const count=async(name,n)=>assert.equal(await option(name).locator('.label-count').innerText(),n+' '+(n===1?'item':'items'))
 const matching=async n=>assert.match(await id('summary').innerText(),new RegExp('^'+n+' matching items of 103 loaded[.]'))
 const noFacet=()=>assertFacetCleared(page)
 const footprint=()=>page.evaluate(()=>[location.href,history.length,localStorage.length,sessionStorage.length])
 const requests=[],onRequest=r=>{
  const u=new URL(r.url())
  // Count every request, but retain a query only for this fixture's fixed paging tuple;
  // in particular never keep login callback codes, Cookie headers or CSRF values.
  const paging=[base+'/items',base+'/review'].includes(u.pathname)
  requests.push([r.method(),u.origin+u.pathname+(paging&&['?limit=100&offset=0','?limit=100&offset=100'].includes(u.search)?u.search:u.search?'?<other>':'')])
 }
 const paths=action=>['/capabilities','/'+action+'?limit=100&offset=0','/'+action+'?limit=100&offset=100']
 const expectRead=(start,action)=>assert.deepEqual(requests.slice(start),paths(action).map(path=>['GET',origin+base+path]))
 const stages=name=>console.log('Community approved-label acceptance: '+name)
 async function screenshot(name,width,height,selected){
  assert.equal(await option(selected).getAttribute('aria-pressed'),'true')
  await id('shared-labels').evaluate(node=>node.scrollIntoView({block:'start',behavior:'instant'}));await renderTick()
  const layout=await page.evaluate(long=>{
   const form=document.querySelector('#shared-labels'),all=[...form.querySelectorAll('#label-options button')]
   const metric=el=>{const b=el.getBoundingClientRect();return [b.left,b.top,b.right,b.bottom,b.width,b.height]}
   const label=all.map(b=>b.querySelector('.label-name')).find(b=>b.textContent===JSON.stringify(long))
   const range=document.createRange();range.selectNodeContents(label)
   const top=Math.max(0,Math.floor(scrollY+form.getBoundingClientRect().top)-12)
   const bottom=Math.min(document.documentElement.scrollHeight,Math.ceil(scrollY+form.getBoundingClientRect().bottom)+12)
   return {width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,scrollY,
    mobileClip:{x:0,y:top,width:innerWidth,height:bottom-top},
    selected:all.filter(b=>b.getAttribute('aria-pressed')==='true').map(b=>b.querySelector('.label-name').textContent),
    lines:new Set([...range.getClientRects()].map(b=>Math.round(b.top))).size,
    controls:['label-input','label-apply','label-clear','label-status'].map(id=>metric(document.getElementById(id))),
    buttons:all.map(metric),long:metric(label),clipped:form.scrollWidth>form.clientWidth}
  },long)
  assert.deepEqual([layout.width,layout.height,layout.scrollWidth,layout.clipped],[width,height,width,false]);assert.deepEqual(layout.selected,[JSON.stringify(selected)])
  // At phone width the natural block can exceed a viewport. Capture the whole
  // form in document coordinates at its actual 390px width, without resizing.
  const mobile=width===390,clip=layout.mobileClip,pixelHeight=mobile?clip.height:height
  if(mobile)assert.ok(clip.height>=400&&clip.height<=1800)
  const frameTop=mobile?clip.y-layout.scrollY:0
  const inFrame=([left,top,right,bottom])=>left>=-.5&&top>=frameTop-.5&&right<=width+.5&&bottom<=frameTop+pixelHeight+.5
  assert.ok([...layout.controls,...layout.buttons,layout.long].every(inFrame),'All filtered label controls and status must fit this screenshot')
  for(const b of layout.buttons)assert.ok(b[4]>=44&&b[5]>=44,'Label choices must have usable hit targets')
  if(width===390)assert.ok(layout.lines>=2,'Long mobile label must visibly wrap')
  const png=await page.screenshot({path:path.join(folder,name),animations:'disabled',...(mobile?{fullPage:true,clip}:{})})
  assert.deepEqual([png.readUInt32BE(16),png.readUInt32BE(20)],[width,pixelHeight]);assert.ok(png.length>12000)
  const sha=require('node:crypto').createHash('sha256').update(png).digest('hex')
  console.log('COMMUNITY_APPROVED_LABELS_SCREEN '+name+' '+width+'x'+pixelHeight+' viewport='+width+'x'+height+' bytes='+png.length+' sha256='+sha+' longLines='+layout.lines+' minOptionHeight='+Math.min(...layout.buttons.map(b=>b[5])))
 }

 await signInReaderAfterRevocation({page,origin,provider,login});await noFacet();page.on('request',onRequest)
 const baseline=await footprint();assert.equal(baseline[0],origin+'/community/');assert.deepEqual(baseline.slice(2),[0,0])
 const [responses]=await Promise.all([Promise.all(paths('items').map(response)),open()])
 await settled();expectRead(0,'items')
 const [caps,first,last]=await Promise.all(responses.map(async r=>{
  assert.equal(r.status(),200);assert.ok((await r.request().headerValue('cookie')||'').includes('__Host-ms-collab-session='))
  const {data}=await r.json();assert.deepEqual(data.scope,scope);assert.equal(data.revision,1);return data
 }))
 assert.equal(caps.capabilities.items,true);assert.equal(caps.capabilities.review,false)
 for(const [p,offset,n] of [[first,0,100],[last,100,3]]){
  assert.deepEqual([p.available,p.sharing,p.view,p.total,p.offset,p.limit,p.items.length],
   [true,'explicit_content_approval','items',103,offset,100,n])
  assert.deepEqual(p.sources,[{...scope,observed_at:'2026-01-02T00:00:00Z'}])
 }
 const records=[...first.items,...last.items],keys=records.map(r=>r.kind+':'+r.number)
 assert.equal(new Set(keys).size,103);assert.deepEqual(last.items.map(r=>r.kind+':'+r.number),['issue:1','pull:101','pull:1'])
 assert.ok(records.every(r=>r.workspace_id==='beta'&&r.connection_id==='labels'&&r.repository==='sample/project'&&!Object.hasOwn(r,'approved')&&!Object.hasOwn(r,'fingerprint')&&!r.labels.includes(privateLabel)))
 assert.equal(records.filter(r=>r.labels.includes('build')).length,28);assert.equal(records.flatMap(r=>r.labels.filter(s=>s==='build')).length,29)
 assert.equal(await id('review-view').isVisible(),false);assert.ok(await id('shared-labels').isVisible())
 stages('current reader Cookie and real 100+3 approved GETs verified; starting local-only interaction budget')
 const localStart=requests.length
 assert.equal(await buttons.count(),10);await count('build-tools',33);await count('build',28);await count(long,22)
 assert.equal(await option(rare).count(),0);assert.match(await id('label-status').innerText(),/^103 of 103 approved loaded items.*14 distinct labels/s)
 await id('next').click();assert.equal(await id('page').innerText(),'Page 2 of 6');await count('build',28)
 await option('build').click();await matching(28);assert.equal(await id('page').innerText(),'Page 1 of 2')
 await id('next').click();assert.deepEqual((await page.locator('#items .badge').allInnerTexts()).slice(-2),['Issue #1 · closed','Pull request #1 · closed']);await count('build-tools',33)
 // A genuine second API page contributes both same-number identities. Ordinal 11 and
 // raw multi-line options remain keyboard-selectable, independently of row paging.
 await id('label-next').click()
 assert.deepEqual(await page.locator('#label-options .label-name').allInnerTexts(),['area-2','docs','z-line\r\nbreak',rare].map(s=>JSON.stringify(s)))
 const multiline=option('z-line\r\nbreak');await multiline.focus();await page.keyboard.press('Enter');await matching(1)
 assert.equal(await multiline.getAttribute('aria-pressed'),'true');assert.equal(await id('label-input').inputValue(),'')
 assert.ok((await id('label-status').innerText()).includes(JSON.stringify('z-line\r\nbreak')))
 assert.equal(await page.locator('#items .badge').innerText(),'Pull request #101 · merged')
 await id('label-previous').click();await id('label-input').fill(rare);await id('label-input').press('Enter');await matching(1)
 assert.equal(await option(rare).count(),0);assert.ok((await id('label-status').innerText()).includes(JSON.stringify(rare)))
 await id('label-input').fill('Build');await id('label-apply').click();await matching(0)
 await id('label-input').fill('');await id('label-input').press('Enter');await matching(103)
 await option('build').click();await filters('release TRAIN','issue','closed')
 await matching(2);assert.match(await id('label-status').innerText(),/^4 of 103 approved loaded items/)
 await count('build',2);await count('build-tools',1);await count('docs',1);await count(long,2)
 assert.deepEqual(await page.locator('#items .badge').allInnerTexts(),['Issue #101 · closed','Issue #1 · closed'])
 await screenshot('approved-labels-desktop.png',1440,1050,'build')
 await page.setViewportSize({width:390,height:844})
 await id('label-input').fill(long);await id('label-input').focus();await page.keyboard.press('Tab')
 assert.ok(await id('label-apply').evaluate(el=>el===document.activeElement));await page.keyboard.press('Enter');await matching(2)
 await screenshot('approved-labels-mobile.png',390,844,long)
 await id('label-clear').click();await matching(4);assert.equal(await id('query').inputValue(),'release TRAIN')
 assert.equal(await id('kind').inputValue(),'issue');assert.equal(await id('state').inputValue(),'closed')
 await option('build').click();await filters('unlabeled');await matching(0);assert.match(await id('label-status').innerText(),/No labels on these items/)
 await id('label-clear').click();await matching(1);assert.equal(await buttons.count(),0)
 await filters('no such maintenance item');await matching(0);assert.match(await id('label-status').innerText(),/No items match the current filters[.]/)
 await filters();await option('build').click();await page.setViewportSize({width:1440,height:1050});await renderTick()
 assert.equal(requests.length,localStart,'Local filters, ranking, paging and screenshots must generate zero requests')
 assert.deepEqual(await footprint(),baseline);stages('reader: real 100+3 rows at revision 1; local counts, exact filters and screenshots validated')

 // No old-scope labels during or after a delayed genuine second page. Hold before
 // dispatch; do not block the single-threaded HTTPS service or alter a response.
 async function heldLast(action){
  const endpoint=origin+base+paths('items')[2],seen=deferred(),release=deferred()
  const match=url=>url.href===endpoint,handler=async route=>{seen.resolve();await release.promise;await route.continue()}
  await page.route(match,handler)
  try{
   const [lastResponse]=await Promise.all([response(paths('items')[2]),(async()=>{
    await id('reload').click();await within(seen.promise);try{await noFacet();await action()}finally{release.resolve()}
   })()])
   assert.equal(lastResponse.status(),200);const data=(await lastResponse.json()).data;await renderTick();return data
  }finally{release.resolve();await page.unroute(match,handler)}
 }
 const stale=await heldLast(()=>ready('beta'));assert.equal(stale.revision,1)
 assert.equal(await id('scope-title').innerText(),'beta / source / sample/project');assert.equal(await buttons.count(),1)
 assert.equal(await option('<b>plain label</b>').count(),1);assert.equal(await id('label-input').inputValue(),'');assert.equal(await option('build').count(),0)
 await open();await settled();await option('build').click()
 const drift=await heldLast(()=>control('labels-conflict'));assert.equal(drift.revision,2);assert.equal(drift.offset,100)
 await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('valid, consistent'))
 assert.ok(await id('panel').isHidden());assert.equal(await id('items').textContent(),'');await noFacet()
 await ready('empty');assert.match(await id('summary').innerText(),/No snapshot/);await noFacet()
 await logout();await noFacet();await login('admin');await open();await settled();await option('build').click()
 const dual=requests.length;await id('review-view').click();await settled();expectRead(dual,'review');await noFacet()
 assert.ok((await id('items').innerText()).includes(privateLabel));assert.equal(await id('approval-panel').isVisible(),true)
 await id('items-view').click();await settled();await matching(103);assert.equal(await id('label-input').inputValue(),'');assert.ok(!(await id('shared-labels').innerText()).includes(privateLabel))
 await option('build').click();await logout();await noFacet();await login('reviewer')
 // An Open requesting shared items falls back to the user's real review-only grant.
 const fallback=requests.length;await open();await settled();expectRead(fallback,'review');await noFacet()
 assert.ok(await id('items-view').isHidden());assert.ok((await id('items').innerText()).includes(privateLabel))
 await logout();await noFacet();page.off('request',onRequest)
 assert.ok(requests.filter(([,url])=>new URL(url).pathname.includes('/community/labels/')).every(([method])=>method==='GET'))
 stages('real drift 1→2, stale scope, private review and reviewer fallback verified')
}

async function main(){
 const temp=fs.mkdtempSync(path.join(os.tmpdir(),'ms-collab-community-session-'));let browser,child
 try{
  execFileSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-days','1','-keyout',path.join(temp,'key.pem'),'-out',path.join(temp,'cert.pem'),'-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'],{stdio:'ignore'})
  child=spawn('python3',['-m','packages.application_ui.tests.community_browser_server',path.join(temp,'cert.pem'),path.join(temp,'key.pem')],{env:{...process.env,MS_COLLAB_SYNTHETIC_COMMUNITY_SESSION_UI:'1'},stdio:['ignore','pipe','pipe']});child.stderr.pipe(process.stderr)
  const lines=readline.createInterface({input:child.stdout}),fixture=await within(new Promise((resolve,reject)=>{lines.once('line',line=>{try{resolve(JSON.parse(line))}catch(e){reject(e)}});child.once('exit',()=>reject(new Error('synthetic fixture stopped')))}))
  const {origin,provider}=fixture,base='/api/workspaces/alpha/community/source/sample/project',folder=path.join(process.env.RUNNER_TEMP||os.tmpdir(),'ms-collab-community-session');fs.mkdirSync(folder,{recursive:true})
  browser=await chromium.launch({headless:true});const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1440,height:1050}}),page=await context.newPage(),errors=[],external=[],dialogs=[],writes=[]
  page.on('pageerror',error=>errors.push(error.message));page.on('dialog',async d=>{dialogs.push(d.message());await d.dismiss()})
  page.on('request',r=>{const url=new URL(r.url());assert.equal(r.headers().authorization,undefined);if(![origin,new URL(provider).origin].includes(url.origin))external.push(url.origin);if(r.method()==='POST'&&url.pathname.endsWith('/approve'))writes.push({path:url.pathname,headers:r.headers(),body:r.postDataJSON()})})
  // Defeat AbortSignal intentionally: generation guards, not cancellation, prove safety.
  await context.addInitScript(()=>{const fetcher=window.fetch;window.fetch=(input,options={})=>{const {signal,...rest}=options;return fetcher.call(window,input,rest)}})
  const router=createScenarioRouter({origin,onExternal:()=>external.push('routed external origin')});await context.route('**/api/**',route=>router.handle(route))
  const control=async action=>assert.equal(await page.evaluate(async action=>(await fetch('/synthetic-community/'+action,{method:'POST'})).status,action),200)
  const settled=()=>page.waitForFunction(()=>!document.querySelector('#panel').hidden&&!document.querySelector('#reload').disabled)
  const open=async(workspace='alpha')=>{await page.locator('#workspace').fill(workspace);await page.locator('#connection').fill('source');await page.locator('#repository').fill('sample/project');await page.locator('#scope-form button').click()}
  const ready=async(workspace='alpha')=>{await open(workspace);await settled()}
  const login=async subject=>{
   await control('subject/'+subject);await page.locator('#app-sign-in').click();await page.waitForFunction(subject=>document.querySelector('#app-principal')?.textContent===subject,subject)
   if(new URL(page.url()).pathname!=='/community/')await page.locator('nav a[href="/community/"]').click()
   await page.locator('#session:not([hidden])').waitFor()
  }
  const logout=async()=>{await page.locator('#app-sign-out').click();await page.waitForFunction(()=>document.querySelector('#app-status').textContent.includes('server session is revoked'))}
  const renderTick=()=>page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))))
  await page.goto(origin+'/community/');await page.waitForFunction(()=>!document.querySelector('#app-sign-in').disabled)
  assert.equal(await page.locator('input[type="password"],#credential,#connect').count(),0);assert.equal(await page.locator('#session').isVisible(),false)
  await login('reader');await ready();assert.match(await page.locator('#summary').textContent(),/2 matching/)
  assert.equal(await page.locator('#review-view').isVisible(),false);assert.equal(await page.locator('#refresh').isVisible(),false);assert.equal(await page.locator('#items img,#items b').count(),0);assert.match(await page.locator('#items').textContent(),/<img src=x/)
  const cookies=await context.cookies(origin),session=cookies.find(c=>c.name==='__Host-ms-collab-session'),csrf=cookies.find(c=>c.name==='__Host-ms-collab-csrf')
  assert.ok(session.secure&&session.httpOnly&&csrf.secure&&!csrf.httpOnly);assert.ok(!(await page.evaluate(()=>document.cookie)).includes('__Host-ms-collab-session'))
  await logout();await login('reviewer');await ready();assert.equal(await page.locator('#items-view').isVisible(),false);assert.equal(await page.locator('#refresh').isVisible(),false);assert.equal(await page.locator('#approval-panel').isVisible(),true);assert.match(await page.locator('#summary').textContent(),/101 loaded/)
  await logout();await login('admin');await ready();assert.equal(await page.locator('#refresh').isVisible(),false)
  await page.locator('#review-view').click();await settled();assert.match(await page.locator('#summary').textContent(),/101 loaded/);assert.match(await page.locator('#approval-summary').textContent(),/2 approved/)
  await page.screenshot({path:path.join(folder,'desktop-list.png'),fullPage:true})
  await page.getByRole('checkbox',{name:'Approve issue 100',exact:true}).check();await page.locator('#save').click();assert.match(await page.locator('#confirm-note').textContent(),/Add 1, remove 0; 3 total/)
  await page.screenshot({path:path.join(folder,'desktop-confirm.png'),fullPage:true});await page.locator('#cancel').click();assert.equal(writes.length,0)
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(folder,'mobile-list.png'),fullPage:true})
  await page.locator('#save').click();await page.screenshot({path:path.join(folder,'mobile-confirm.png'),fullPage:true});await page.keyboard.press('Escape');assert.equal(writes.length,0);assert.equal(await page.locator('#confirm-note').textContent(),'');await page.setViewportSize({width:1440,height:1050})
  // Filtering excludes both previously approved rows but does not discard them.
  await page.locator('#query').fill('Synthetic issue 100');await page.locator('#filters button').click();assert.match(await page.locator('#summary').textContent(),/1 matching/)
  await page.locator('#save').click();await page.evaluate(()=>{document.querySelector('#confirm-form').requestSubmit();document.querySelector('#confirm-form').requestSubmit()});await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('Approval set saved'));await settled()
  assert.equal(writes.length,1);assert.equal(writes[0].body.fingerprints.length,3);assert.match(writes[0].headers['if-match'],/^"\d+"$/);assert.ok(writes[0].headers['x-csrf-token'])
  const approved=await page.evaluate(async base=>{const data=(await(await fetch(base+'/items?limit=100')).json()).data;return data.items.map(x=>x.number)},base);assert.deepEqual(approved.sort((a,b)=>a-b),[1,100,101])
  await page.locator('#query').fill('');await page.locator('#filters button').click();await page.getByRole('checkbox',{name:'Approve issue 99',exact:true}).check();await page.locator('#save').click();await control('conflict');await page.locator('#confirm').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('revision changed'));await settled();assert.equal(writes.length,2)
  // Cookie alone is not sufficient to mutate; lack of CSRF rejects before action.
  const noCSRF=await page.evaluate(async base=>(await fetch(base+'/approve',{method:'POST',headers:{'Content-Type':'application/json','If-Match':'"1"'},body:'{"fingerprints":[]}'})).status,base);assert.equal(noCSRF,401)
  // Wrong second-page scope invalidates the entire approval set, including first page.
  router.set(base+'/review',async route=>{const r=await route.fetch();if(new URL(route.request().url()).searchParams.get('offset')==='100'){const body=await r.json();body.data.scope.workspace_id='beta';await route.fulfill({response:r,json:body})}else await route.fulfill({response:r})})
  await page.locator('#reload').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('valid, consistent'));assert.equal(await page.locator('#panel').isVisible(),false);assert.equal(await page.locator('#items').textContent(),'');router.remove(base+'/review');await ready();await page.locator('#review-view').click();await settled()
  router.set(base+'/capabilities',route=>route.fulfill({status:403,contentType:'application/json',body:'{"error":"forbidden"}'}));await page.locator('#reload').click();await page.waitForFunction(()=>document.querySelector('#panel').hidden&&document.querySelector('#status').textContent.includes('permissions'));assert.equal(await page.locator('#items').textContent(),'');router.remove(base+'/capabilities');await ready()
  await open('commons');await page.waitForFunction(()=>document.querySelector('#panel').hidden&&document.querySelector('#status').textContent.includes('unavailable'));assert.equal(await page.locator('#items').textContent(),'')
  await ready('empty');assert.match(await page.locator('#summary').textContent(),/No snapshot/)
  await ready();await page.locator('#review-view').click();await settled()
  // One routing owner and explicit barrier: stale read cannot repaint a newer scope.
  const seen=deferred(),release=deferred(),finished=deferred();router.set(base+'/review',async route=>{const r=await route.fetch();seen.resolve();await release.promise;await route.fulfill({response:r});finished.resolve()})
  await page.locator('#reload').click();await within(seen.promise);await ready('beta');assert.match(await page.locator('#scope-title').textContent(),/^beta/);release.resolve();await within(finished.promise);await renderTick();assert.match(await page.locator('#scope-title').textContent(),/^beta/);router.remove(base+'/review')
  // Actual navigation/history creates no extra login and no cached approvals/dialog.
  await page.locator('nav a[href="/"]').click();await page.waitForFunction(()=>!document.querySelector('#app-data').hidden);await page.goBack();await page.waitForFunction(()=>document.querySelector('#app-recheck')&&!document.querySelector('#app-recheck').disabled);if(await page.locator('#session').isHidden())await page.locator('#app-recheck').click();await page.locator('#session:not([hidden])').waitFor();assert.equal(await page.locator('#workspace').inputValue(),'');assert.equal(await page.locator('#items').textContent(),'');assert.equal(await page.locator('#confirmation').isVisible(),false)
  await ready();await page.locator('#review-view').click();await settled();await page.getByRole('checkbox',{name:'Approve issue 98',exact:true}).check();await page.locator('#save').click()
  const writeSeen=deferred(),writeRelease=deferred(),writeDone=deferred();router.set(base+'/approve',async route=>{const r=await route.fetch();assert.equal(r.status(),200);writeSeen.resolve();await writeRelease.promise;await route.fulfill({response:r});writeDone.resolve()})
  const before=writes.length;await page.locator('#confirm').click();await within(writeSeen.promise);await page.locator('#disconnect').click();await page.waitForFunction(()=>document.querySelector('#app-status').textContent.includes('server session is revoked'));writeRelease.resolve();await within(writeDone.promise);await renderTick()
  assert.equal(writes.length,before+1);assert.equal(await page.locator('#session').isVisible(),false);assert.equal(await page.locator('#items').textContent(),'');assert.equal(await page.locator('#workspace').inputValue(),'');router.remove(base+'/approve');await router.drain()
  await login('admin');await ready();const after=await page.evaluate(async base=>(await(await fetch(base+'/items?limit=100')).json()).data.items.map(x=>x.number),base);assert.ok(after.includes(98))
  await control('revoke-membership');await page.locator('#reload').click();await page.waitForFunction(()=>document.querySelector('#panel').hidden&&document.querySelector('#status').textContent.includes('unavailable'));assert.equal(await page.locator('#items').textContent(),'')
  await ready('beta');assert.equal(await page.locator('#scope-title').innerText(),'beta / source / sample/project')
  const revocationLabel='<b>plain label</b>',choice=page.locator('#label-options button')
  assert.equal(await choice.count(),1);assert.equal(await choice.locator('.label-name').innerText(),JSON.stringify(revocationLabel))
  assert.equal(await choice.locator('.label-count').innerText(),'2 items');await choice.click()
  assert.equal(await choice.getAttribute('aria-pressed'),'true');assert.equal(await page.locator('#label-input').inputValue(),revocationLabel)
  assert.ok((await page.locator('#label-status').innerText()).includes('Exact label '+JSON.stringify(revocationLabel)+': 2 matching items.'))
  await control('revoke-session')
  const [revokedRead]=await Promise.all([page.waitForResponse(r=>r.url()===origin+'/api/workspaces/beta/community/source/sample/project/capabilities'&&r.request().method()==='GET'),page.locator('#reload').click()])
  assert.equal(revokedRead.status(),401)
  await page.waitForFunction(()=>document.querySelector('#app-principal').textContent==='Not verified');assert.equal(await page.locator('#session').isVisible(),false);assert.equal(await page.locator('#items').textContent(),'')
  await assertFacetCleared(page) // Immediately after the genuine 401, before navigating or logging in again.
  await router.drain();await context.unrouteAll({behavior:'wait'});await page.goto('about:blank');await page.goBack();await page.waitForFunction(()=>document.querySelector('#app-principal')?.textContent==='Not verified');assert.equal(await page.locator('#session').isVisible(),false)
  assert.deepEqual(await page.evaluate(()=>[localStorage.length,sessionStorage.length]),[0,0]);assert.deepEqual(errors,[]);assert.deepEqual(dialogs,[]);assert.deepEqual(external,[])
  await approvedLabelJourney({page,origin,provider,folder,login,logout,ready,settled,control,renderTick})
  assert.deepEqual(errors,[]);assert.deepEqual(dialogs,[]);assert.deepEqual(external,[])
  console.log('Community session acceptance: real S256 cookies; reader/reviewer/admin; 101-row full approval preservation; CSRF/CAS; hostile text; late reads and accepted writes after logout; current membership/session revocation; history; four responsive screenshots. Synthetic HTTPS only.')
 }finally{await browser?.close();await stopSyntheticFixture(child);fs.rmSync(temp,{recursive:true,force:true})}
 console.log('COMMUNITY_APPROVED_LABELS_PASS real synthetic Cookie GETs, 103 approved rows in two pages, local exact-label counts with zero requests, scope/revision/review isolation, two responsive screenshots and clean shutdown. No provider access.')
}
main().catch(error=>{console.error(error.stack);process.exitCode=1})
