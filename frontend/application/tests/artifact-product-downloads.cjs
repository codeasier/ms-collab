// Separate synthetic process and fresh Chromium. The two previous browser journeys
// own their own fixtures; neither one enables product-file downloads.
const assert=require('node:assert/strict')
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),readline=require('node:readline')
const {createHash}=require('node:crypto'),{spawn,execFileSync}=require('node:child_process')
const {chromium}=require('playwright'),{stopSyntheticFixture}=require('./fixture-process.cjs')

const scope={workspace_id:'alpha',connection_id:'source',bucket_id:'bucket'}
const viewPage='/artifacts/product-views/', product='/api/workspaces/alpha/artifacts/source/bucket/product-views/kit'
const byteBase='/api/workspaces/alpha/artifact-download/source/bucket/', available=byteBase+'availability', binary=byteBase+'download'
const leaf='pkg/KIT_report.txt', unbound='pkg/KIT_unbound.txt', filename='artifact-KIT_report.txt'
const bytes=Buffer.from('Inert synthetic product-file receipt.\n'.repeat(4)), sha=b=>createHash('sha256').update(b).digest('hex')
const checksum=sha(bytes), zero={loads:0,calls:0,active:0,leases:[]}, delivered={loads:1,calls:1,active:0,leases:[['complete',bytes.length]]}
const body={format:1,quarter:'2026Q2',path:'',mode:'tree',page:1,page_size:10}
const work=path.join(process.env.RUNNER_TEMP||os.tmpdir(),'ms-collab-download-session')
async function bounded(promise,ms=20000){let timer;try{return await Promise.race([promise,new Promise((_,r)=>{timer=setTimeout(()=>r(Error('fixture timeout')),ms)})])}finally{clearTimeout(timer)}}
const frames=p=>p.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))))
function pngEvidence(name,width,height,exact=true){
 const buf=fs.readFileSync(path.join(work,name))
 assert.equal(buf.subarray(0,8).toString('hex'),'89504e470d0a1a0a');assert.equal(buf.toString('ascii',12,16),'IHDR')
 const w=buf.readUInt32BE(16),h=buf.readUInt32BE(20);assert.equal(w,width);assert.ok(exact?h===height:h>=height)
 assert.ok(buf.length>4096);return {name,width:w,height:h,bytes:buf.length,sha256:sha(buf)}
}

// Static names and numeric evidence only: no headers, cookies, document
// contents, provider callback parameters, source paths or raw fixture errors.
async function screen(p,name,w,h,statusOnly){
 assert.deepEqual(p.viewportSize(),{width:w,height:h})
 const geometry=await p.evaluate(({statusOnly})=>{
  const panel=document.querySelector('#download-panel')
  panel.scrollIntoView({block:'center',behavior:'instant'})
  const selectors=statusOnly?['#download-heading','#download-status']:['#download-heading','#download-digest','#download-confirm']
  const painted=[]
  for(const selector of selectors){
   const e=document.querySelector(selector),r=e.getBoundingClientRect(),x=r.left+r.width/2,y=r.top+r.height/2
   if(!e.checkVisibility()||r.width<=0||r.height<=0||r.top<0||r.bottom>innerHeight+0.5||r.left<0||r.right>innerWidth+0.5||
      !e.contains(document.elementFromPoint(x,y)))throw Error('Missing visible panel evidence')
   painted.push({selector,x:Math.round(x),y:Math.round(y),width:Math.round(r.width),height:Math.round(r.height)})
  }
  if(document.documentElement.scrollWidth>innerWidth)throw Error('Document horizontal overflow')
  return painted
 },{statusOnly})
 await frames(p)
 await p.screenshot({path:path.join(work,name),animations:'disabled'})
 console.log('PRODUCT_FILE_SCREEN '+JSON.stringify({...pngEvidence(name,w,h),painted:geometry}))
}
async function run(){
 const temp=fs.mkdtempSync(path.join(os.tmpdir(),'ms-collab-product-file-'))
 let child,browser,context,cdp,checkpoint='startup',problem
 const fail=e=>{
  const n=/artifact-product-downloads[.]cjs:(\d+):\d+/.exec(String(e?.stack))?.[1]||'unknown'
  return Error('Product-file fixture failed at '+checkpoint+' (harness line '+n+').')
 }
 try{
  for(const [name,width,height] of [['catalog.png',1440,1100],['confirmation.png',1440,1100],['mobile.png',390,844],['handoff.png',1440,1100],['denied.png',1440,1100]])
   console.log('PRODUCT_FILE_PRIOR_SCREEN '+JSON.stringify(pngEvidence(name,width,height,false)))
  const cert=path.join(temp,'cert.pem'),key=path.join(temp,'key.pem')
  execFileSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-days','1','-keyout',key,'-out',cert,'-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'],{stdio:'ignore'})
  child=spawn('python3',['-m','packages.artifact_download.tests.session_browser_server',cert,key,'--product'],
   {env:{...process.env,MS_COLLAB_SYNTHETIC_DOWNLOAD_SESSION:'1'},stdio:['ignore','pipe','pipe']})
  child.stderr.resume()
  const reader=readline.createInterface({input:child.stdout})
  const info=await bounded(new Promise((resolve,reject)=>{
   reader.once('line',line=>{try{resolve(JSON.parse(line))}catch{reject(Error('bad startup'))}})
   child.once('error',reject);child.once('exit',()=>reject(Error('fixture exited')))
  }))
  assert.deepEqual(Object.keys(info).sort(),['origin','provider']);assert.match(info.origin,/^https:\/\/127\.0\.0\.1:\d+$/)
  assert.equal(info.provider,info.origin.replace('127.0.0.1','localhost')+'/synthetic-provider')
  browser=await chromium.launch({headless:true})
  context=await browser.newContext({acceptDownloads:true,ignoreHTTPSErrors:true,serviceWorkers:'block',viewport:{width:1440,height:1100}})
  // A counter witnesses actual browser-local Blob creation/cleanup; no name,
  // digest, payload, response or network outcome is replaced.
  await context.addInitScript(()=>{
   const create=URL.createObjectURL.bind(URL),revoke=URL.revokeObjectURL.bind(URL),active=new Set()
   let created=0,retired=0
   URL.createObjectURL=b=>{const u=create(b);active.add(u);created++;return u}
   URL.revokeObjectURL=u=>{if(active.delete(u))retired++;return revoke(u)}
   window.__fileURLCounts=()=>[created,retired,active.size]
  })
  const p=await context.newPage(),errors=[],events=[],downloads=[]
  cdp=await context.newCDPSession(p);await cdp.send('Network.enable')
  // Before navigation, allow only this ephemeral TLS fixture, its bound
  // same-process provider flow. Native Blob handoff needs no network exception.
  await cdp.send('Network.setBlockedURLs',{urlPatterns:[
   {urlPattern:info.origin+'/*',block:false},{urlPattern:info.provider+'?*',block:false},
   {urlPattern:'*://*:*/*',block:true}],urls:['*']})
  let watching=false
  const tracked=new WeakSet()
  p.on('pageerror',()=>errors.push('page error'))
  p.on('dialog',async d=>{errors.push('unexpected dialog');await d.dismiss()})
  p.on('download',f=>downloads.push(f))
  p.on('request',r=>{
   const u=new URL(r.url())
   if(u.origin!==info.origin&&!(u.origin===new URL(info.provider).origin&&u.pathname==='/synthetic-provider'))errors.push('external request')
   if(u.origin===info.origin&&r.headers().authorization)errors.push('bearer')
   if(!watching&&u.pathname.startsWith('/api/')&&
      ([product,available,binary].includes(u.pathname)||r.frame().url()===info.origin+viewPage))errors.push('premature product or byte read')
   if(watching&&u.pathname.startsWith('/api/')){
    if(![product,available,binary].includes(u.pathname)||u.search||r.method()!=='POST')errors.push('unexpected API')
    tracked.add(r);events.push(['request',u.pathname])
   }
  })
  p.on('response',r=>{const u=new URL(r.url());if(tracked.has(r.request()))events.push(['response',u.pathname,r.status()])})
  const state=()=>p.evaluate(async()=>{const r=await fetch('/synthetic/download/state',{method:'POST'});if(r.status!==200)throw Error('fixture state');return r.json()})
  const republish=()=>p.evaluate(async()=>{const r=await fetch('/synthetic/download/product-republish',{method:'POST'});if(r.status!==200)throw Error('fixture update');return (await r.json()).published_revision})
  const responseTo=target=>p.waitForResponse(r=>r.url()===info.origin+target)
  const checkWire=async(res,target,input,rev,status)=>{
   assert.equal(res.status(),status);assert.equal(res.url(),info.origin+target)
   const req=res.request();assert.equal(req.method(),'POST');assert.deepEqual(req.postDataJSON(),input)
   assert.equal(await req.headerValue('if-match'),rev==null?null:'"'+rev+'"')
   assert.ok(Boolean(await req.headerValue('x-csrf-token')));assert.equal(await req.headerValue('authorization'),null)
  }
  // Match the complete literal key, not a partial label or a common basename.
  const row=k=>p.locator('#pv-results li').filter({has:p.locator('bdi.pv-key').filter({hasText:new RegExp('^'+k.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'$')})})
  const select=k=>row(k).getByRole('button',{name:'Check byte access',exact:true})
  async function verifyLevel(action,input,expectedKeys){
   const before=events.length
   const [res]=await bounded(Promise.all([responseTo(product),action()]))
   await checkWire(res,product,input,null,200);const data=(await res.json()).data
   assert.deepEqual(data.scope,scope);assert.equal(data.view_id,'kit');assert.equal(data.path,input.path);assert.equal(data.quarter,input.quarter)
   assert.deepEqual(data.items.map(x=>x.key),expectedKeys);assert.match(data.observation,/^[0-9a-f]{64}$/)
   await p.waitForFunction(n=>document.querySelectorAll('#pv-results li').length===n,expectedKeys.length)
   assert.deepEqual(events.slice(before),[['request',product],['response',product,200]])
   return data
  }
  const keys=['pkg/archives/','pkg/KIT_over-limit.txt',leaf,unbound]
  const openRoot=()=>verifyLevel(()=>p.locator('#pv-open').click(),body,['pkg/'])
  const branch=(proof,extra=[])=>verifyLevel(()=>p.getByRole('button',{name:'Folder: pkg',exact:true}).click(),
    {...body,path:'pkg/',if_observation:proof},[keys[0],...keys.slice(1).concat(extra).sort()])
  const confirm=p.locator('#download-confirm')
  async function check(k,level,status){
   const start=events.length
   const [rr,ar]=await bounded(Promise.all([responseTo(product),responseTo(available),select(k).click()]))
   await checkWire(rr,product,{...body,path:'pkg/',if_observation:level.observation},null,200)
   const actual=(await rr.json()).data
   assert.equal(actual.catalog_revision,level.catalog_revision);assert.equal(actual.observation,level.observation)
   assert.deepEqual(actual.items,level.items)
   await checkWire(ar,available,{key:k},level.catalog_revision,status)
   assert.deepEqual(events.slice(start),[['request',product],['response',product,200],['request',available],['response',available,status]])
   if(status===404){
    assert.deepEqual(await ar.json(),{error:'not_found'})
    await p.waitForFunction(()=>/unavailable/i.test(document.querySelector('#download-status').textContent))
    assert.ok(await confirm.isDisabled());assert.equal(await p.locator('#download-key').textContent(),'')
   }else{
    assert.deepEqual(await ar.json(),{data:{scope,key:k,size:bytes.length,sha256:checksum,published_revision:level.catalog_revision}})
    await p.waitForFunction(()=>!document.querySelector('#download-confirm').disabled)
    assert.equal(await p.locator('#download-key').textContent(),k)
    assert.equal(await p.locator('#download-heading').textContent(),'Confirm exact bytes')
    assert.equal(await p.locator('#download-digest').textContent(),checksum)
    assert.equal(await p.locator('#download-size').textContent(),bytes.length+' bytes')
    assert.match(await p.locator('#download-scope').textContent(),new RegExp('alpha / source / bucket.*revision '+level.catalog_revision+'$'))
   }
  }
  async function privacy(){
   assert.equal(p.url(),info.origin+viewPage)
   const actual=await p.evaluate(async()=>[location.search,location.hash,history.state,localStorage.length,sessionStorage.length,
     (await indexedDB.databases()).length,document.querySelectorAll('a[download]').length])
   assert.deepEqual(actual,['','',null,0,0,0,0]);assert.deepEqual(errors,[])
  }

  async function enterSignedIn(){
   watching=false
   await p.goto(info.origin+'/artifacts/');await p.waitForFunction(()=>!document.querySelector('#app-sign-in').disabled)
   await p.locator('#app-sign-in').click();await p.waitForFunction(()=>document.querySelector('#app-principal')?.textContent==='admin')
   await p.goto(info.origin+'/artifacts/');await p.locator('#app-product-view-entry').waitFor({state:'visible'})
   await p.locator('#app-product-view-entry').click();await p.locator('#pv-controls').waitFor({state:'visible'})
   watching=true
   for(const [id,value] of Object.entries({workspace:'alpha',connection:'source',bucket:'bucket',view:'kit',quarter:'2026Q2'}))await p.locator('#pv-'+id).fill(value)
  }
  checkpoint='sign in and enter product view'
  await enterSignedIn();assert.deepEqual(await state(),zero);assert.deepEqual(events,[])
  const root=await openRoot();assert.equal(root.catalog_revision,1);assert.equal(await p.locator('.pv-check-download').count(),0)
  let level=await branch(root.observation);assert.deepEqual(await state(),zero)
  assert.equal(await select(keys[0]).count(),0);assert.equal(await select(keys[1]).count(),0)
  assert.equal(await select(leaf).count(),1);assert.equal(await select(unbound).count(),1)
  assert.equal(await p.locator('#pv-results .pv-check-download').count(),2)
  await privacy()

  checkpoint='real byte availability and desktop review'
  await check(leaf,level,200);assert.deepEqual(await state(),zero);assert.equal(downloads.length,0)
  await screen(p,'product-file-review-desktop.png',1440,1100,false)
  checkpoint='confirmed native save and exact inert bytes'
  const start=events.length
  const [wire,native]=await bounded(Promise.all([responseTo(binary),p.waitForEvent('download'),confirm.click()]))
  await checkWire(wire,binary,{key:leaf,expected_sha256:checksum},1,200)
  const h=wire.headers();assert.equal(h['content-type'],'application/octet-stream');assert.equal(h['content-length'],String(bytes.length))
  assert.equal(h['x-artifact-sha256'],checksum);assert.equal(h['content-disposition'],'attachment; filename="'+filename+'"')
  assert.equal(native.suggestedFilename(),filename);assert.ok(native.url().startsWith('blob:'+info.origin+'/'))
  const saved=path.join(temp,'inert.bin');await bounded(native.saveAs(saved));assert.equal(await native.failure(),null)
  const disk=fs.readFileSync(saved);assert.equal(disk.length,bytes.length);assert.equal(sha(disk),checksum);assert.ok(bytes.equals(disk))
  await p.waitForFunction(()=>window.__fileURLCounts().join(',')==='1,1,0')
  assert.deepEqual(events.slice(start),[['request',binary],['response',binary,200]])
  assert.deepEqual(await state(),delivered);assert.equal(downloads.length,1)
  const nativePath=await native.path();assert.ok(nativePath&&fs.existsSync(nativePath));await native.delete();fs.unlinkSync(saved)
  assert.ok(!fs.existsSync(nativePath)&&!fs.existsSync(saved));await privacy()
  checkpoint='metadata-only leaf and mobile denial'
  await p.setViewportSize({width:390,height:844});await check(unbound,level,404)
  assert.equal(downloads.length,1);assert.deepEqual(await state(),delivered)
  await screen(p,'product-file-denied-mobile.png',390,844,true)
  await p.setViewportSize({width:1440,height:1100})
  checkpoint='explicit cancel and stale availability after a scope edit'
  await check(leaf,level,200)
  const beforeCancel=events.length
  await p.locator('#download-cancel').click()
  assert.ok(await confirm.isDisabled());assert.equal(await p.locator('#download-key').textContent(),'')
  assert.equal(events.length,beforeCancel);assert.deepEqual(await state(),delivered)
  // Only this negative case delays delivery AFTER the browser consumed the
  // authentic, complete availability reply. No response bytes/status/URL are
  // constructed; subsequent native reads use the untouched implementation.
  await p.evaluate(target=>{
   const original=Response.prototype.text
   window.__fileHoldStarted=false;window.__fileHoldFinished=false
   Response.prototype.text=async function(...args){
    if(this.url!==location.origin+target)return original.apply(this,args)
    Response.prototype.text=original
    const value=await original.apply(this,args)
    await new Promise(resolve=>{window.__releaseFileReply=resolve;window.__fileHoldStarted=true})
    window.__fileHoldFinished=true
    return value
   }
  },available)
  const heldStart=events.length
  const [heldView,heldBytes]=await bounded(Promise.all([responseTo(product),responseTo(available),select(leaf).click()]))
  await checkWire(heldView,product,{...body,path:'pkg/',if_observation:level.observation},null,200)
  await checkWire(heldBytes,available,{key:leaf},1,200)
  assert.deepEqual((await heldBytes.json()).data,{scope,key:leaf,size:bytes.length,sha256:checksum,published_revision:1})
  await p.waitForFunction(()=>window.__fileHoldStarted)
  assert.ok(await confirm.isDisabled())
  await p.locator('#pv-bucket').fill('different-bucket')
  assert.ok(await confirm.isDisabled());assert.equal(await p.locator('#download-key').textContent(),'')
  await p.locator('#pv-bucket').fill('bucket')
  const nextRoot=await openRoot();level=await branch(nextRoot.observation)
  const afterABA=events.length
  await p.evaluate(()=>window.__releaseFileReply())
  await p.waitForFunction(()=>window.__fileHoldFinished);await frames(p)
  assert.equal(events.length,afterABA)
  assert.deepEqual(events.slice(heldStart,heldStart+4),[['request',product],['response',product,200],['request',available],['response',available,200]])
  assert.ok(await confirm.isDisabled());assert.equal(await p.locator('#download-panel').isVisible(),false)
  assert.equal(await p.locator('#download-key').textContent(),'')
  assert.equal(downloads.length,1);assert.deepEqual(await state(),delivered)

  checkpoint='new genuine publication invalidates old confirmation and old selected row'
  await check(leaf,level,200)
  const revision=await republish();assert.equal(revision,3)
  const failStart=events.length
  const [denied]=await bounded(Promise.all([responseTo(binary),confirm.click()]))
  await checkWire(denied,binary,{key:leaf,expected_sha256:checksum},1,412)
  await p.waitForFunction(()=>document.querySelector('#download-confirm').disabled&&!document.querySelector('#download-key').textContent)
  assert.deepEqual(events.slice(failStart),[['request',binary],['response',binary,412]])
  assert.deepEqual(await state(),delivered);assert.equal(downloads.length,1)
  const selectStart=events.length
  const [stale]=await bounded(Promise.all([responseTo(product),select(leaf).click()]))
  await checkWire(stale,product,{...body,path:'pkg/',if_observation:level.observation},null,412)
  assert.deepEqual(await stale.json(),{error:'stale_observation'})
  await p.locator('#pv-reopen').waitFor({state:'visible'})
  assert.equal(await p.locator('#pv-results li').count(),0);assert.equal(await p.locator('#pv-evidence').textContent(),'')
  assert.deepEqual(events.slice(selectStart),[['request',product],['response',product,412]])
  assert.deepEqual(await state(),delivered);assert.equal(downloads.length,1)
  const recovered=await verifyLevel(()=>p.locator('#pv-reopen').click(),body,['pkg/'])
  assert.equal(recovered.catalog_revision,3);assert.notEqual(recovered.observation,level.observation)
  const rows=await branch(recovered.observation,['pkg/KIT_publication-1.txt'])
  await check(leaf,rows,200)
  assert.deepEqual(await state(),delivered)
  await p.locator('#download-cancel').click();await privacy()
  assert.deepEqual(await p.evaluate(()=>window.__fileURLCounts()),[1,1,0])
  assert.equal(downloads.length,1);assert.ok(await confirm.isDisabled())
  checkpoint='explicit logout clears the product page'
  await p.locator('#app-sign-out').click()
  await p.waitForFunction(()=>document.querySelector('#app-status').textContent.includes('server session is revoked'))
  assert.equal(await p.locator('#download-key').textContent(),'');assert.equal(await p.locator('#pv-results li').count(),0)
  await privacy()

  checkpoint='active verified binding rejects a genuinely revoked session on confirm'
  await enterSignedIn()
  const liveRoot=await openRoot(),liveLevel=await branch(liveRoot.observation,['pkg/KIT_publication-1.txt'])
  assert.equal(liveLevel.catalog_revision,3);await check(leaf,liveLevel,200)
  assert.deepEqual(await state(),delivered);assert.equal(downloads.length,1)
  const revokedAt=events.length
  const control=await p.evaluate(async()=>{
   const r=await fetch('/synthetic/download/product-revoke-session',{method:'POST'})
   return [r.status,await r.json()]
  })
  assert.deepEqual(control,[200,{}]);assert.equal(await confirm.isEnabled(),true)
  const [unauthorized]=await bounded(Promise.all([responseTo(binary),confirm.click()]))
  await checkWire(unauthorized,binary,{key:leaf,expected_sha256:checksum},3,401)
  await p.waitForFunction(()=>document.querySelector('#app-principal').textContent==='Not verified')
  assert.equal(await p.locator('#app-status').textContent(),'Your session expired or was rejected.')
  assert.equal(await p.locator('#pv-controls').isVisible(),false);assert.equal(await p.locator('#download-panel').isVisible(),false)
  assert.equal(await p.locator('#pv-results li').count(),0);assert.ok(await confirm.isDisabled())
  for(const id of ['download-scope','download-key','download-size','download-digest','pv-evidence','pv-location'])
   assert.equal(await p.locator('#'+id).textContent(),'')
  for(const id of ['workspace','connection','bucket','view','quarter'])assert.equal(await p.locator('#pv-'+id).inputValue(),'')
  assert.deepEqual(events.slice(revokedAt),[['request',binary],['response',binary,401]])
  assert.deepEqual(await state(),delivered);assert.equal(downloads.length,1)
  assert.deepEqual(await p.evaluate(()=>window.__fileURLCounts()),[0,0,0]);await privacy()
 }catch(e){problem=fail(e)}
 finally{
  // Attempt every cleanup even if one fails. Never promote a forced reap,
  // leftover temp bytes or a teardown exception to acceptance.
  const actions=[async()=>{if(cdp)await bounded(cdp.detach())},async()=>{if(context)await bounded(context.close())},
    async()=>{if(browser)await bounded(browser.close())},async()=>{await stopSyntheticFixture(child)},
    async()=>{fs.rmSync(temp,{recursive:true,force:true});assert.ok(!fs.existsSync(temp))}]
  for(const close of actions){try{await close()}catch{problem=problem||Error('Product-file fixture cleanup failed.')}}
 }
 if(problem)throw problem
 console.log('ARTIFACT_PRODUCT_FILE_DOWNLOAD_PASS: verified scoped product reread, independent byte admission, native inert save and SHA, unbound denial, retired availability, stale publication and real active-session revocation; five prior and two product PNGs checked, temporary bytes removed and fixture stopped.')
}
module.exports={run}
