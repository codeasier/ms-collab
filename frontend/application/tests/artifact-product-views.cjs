// Two explicitly synthetic loopback hosts. No product configuration, identities or
// browser state are imported from outside this fixture. Do not log wire secrets.
const assert = require('node:assert/strict')
const {isDeepStrictEqual} = require('node:util')
const fs = require('node:fs'), os = require('node:os'), path = require('node:path')
const {createHash} = require('node:crypto'), {spawn, execFileSync} = require('node:child_process')
const readline = require('node:readline'), {chromium} = require('playwright')
const {stopSyntheticFixture} = require('./fixture-process.cjs')

const catalog = '/artifacts/', product = '/artifacts/product-views/'
const s1 = '/api/workspaces/alpha/artifacts/source/bucket'
const s2 = s1.replace('/bucket','/second-bucket'), missing = s1.replace('/bucket','/missing-bucket')
const kit = s1+'/product-views/kit', sensor = s1+'/product-views/sensor', other = s2+'/product-views/kit'
const q2 = '2026Q2', q1 = '2026Q1'
const root = ['pkg/'], folder = name => 'pkg/'+name+'/'
const label = '<svg onload=alert(1)>', directLeaf = 'pkg/Kit_alpha', deepLeaf = 'pkg/Kit_alpha/version.bin'
const dirs = [label,'Kit_alpha','a','ab'].map(folder)
const direct = [directLeaf,...Array.from({length:10},(_,i)=>'pkg/kit_0'+i),'pkg/notes-KIT_false.txt']
const tree = [...dirs,...direct], layer = 'pkg/'
const z = '2026-04-02T00:00:00.000000Z', micro = '2026-04-02T00:00:00.000001Z'
const quick = {start_time:'2026-03-28T00:00:00Z',end_time:'2026-04-03T23:59:59.999999Z'}
const rangeRows = ['pkg/ab/KIT_two.zip',directLeaf,deepLeaf]
const body = (diff={}) => ({format:1,quarter:q2,path:'',mode:'tree',page:1,page_size:10,...diff})
const files = (diff={}) => ({path:layer,mode:'files',recursive:false,...diff})
const privateIds = ['workspace','connection','bucket','view','quarter','keyword','start','end']
const frames = page => bounded(page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))),'two real animation frames')
function defer(){let resolve,reject;const promise=new Promise((ok,no)=>{resolve=ok;reject=no});return {resolve,reject,promise}}
function bounded(p,label,ms=15000){let t;return Promise.race([p,new Promise((_,rej)=>{t=setTimeout(()=>rej(Error('Timed out: '+label)),ms)})]).finally(()=>clearTimeout(t))}
let currentCheckpoint=''
class FixtureFailure extends Error{}
function failure(label,e){
  if(e instanceof FixtureFailure)return e
  const line=/artifact-product-views[.]cjs:(\d+):\d+/.exec(String(e?.stack))?.[1]
  const kind=e?.name==='TimeoutError'||String(e?.message).startsWith('Timed out: ')?'timeout':e?.code==='ERR_ASSERTION'?'assertion':'error'
  return new FixtureFailure('Product-view '+kind+' in '+label+' / '+currentCheckpoint+(line?' at harness line '+line:''))
}
function checkpoint(label){currentCheckpoint=label;console.log('PRODUCT_VIEW_STEP '+label)}
const isProduct = p => p.startsWith('/api/')&&p.includes('/product-views/')
const visible = (page,id) => page.locator('#'+id).waitFor({state:'visible'})
const enter = async (page,id) => {const control=page.locator('#'+id);await control.focus();await page.keyboard.press('Enter')}
const status = (page,pattern) => page.waitForFunction(s=>new RegExp(s,'i').test(document.querySelector('#pv-status').textContent),pattern)
const principal = (page,value) => page.waitForFunction(v=>document.getElementById('app-principal')?.textContent===v,value)
const countApi = journal=>journal.filter(x=>x.p.startsWith('/api/')).length

async function fixture(enabled,test){
  const temp=fs.mkdtempSync(path.join(os.tmpdir(),'ms-collab-product-view-')), failures=[]
  let child,browser,fixtureFailure=''
  const contexts=[],releaseAll=[]
  try{
    const cert=path.join(temp,'cert.pem'),key=path.join(temp,'key.pem')
    execFileSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-days','1','-keyout',key,'-out',cert,'-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'],{stdio:'ignore'})
    child=spawn('python3',['-m','packages.application_ui.tests.artifact_browser_server',cert,key],{
      env:{...process.env,MS_COLLAB_SYNTHETIC_ARTIFACT_SESSION:'1',MS_COLLAB_SYNTHETIC_ARTIFACT_DISCOVERY:'0',MS_COLLAB_SYNTHETIC_ARTIFACT_PRODUCT_VIEWS:enabled?'1':'0'},
      stdio:['ignore','pipe','pipe']})
    // Only allow known exception *class names*, never traceback lines, paths,
    // URLs, messages, parameter values or headers, even when startup fails.
    const known=/^(AssertionError|ValueError|TypeError|RuntimeError|ImportError|ModuleNotFoundError|[\w.]*StorageError|[\w.]*PolicyError|sqlite3\.OperationalError)(?::|$)/
    readline.createInterface({input:child.stderr}).on('line',line=>{const m=known.exec(line);if(m)fixtureFailure=m[1].split('.').at(-1)})
    const reader=readline.createInterface({input:child.stdout})
    const info=await bounded(new Promise((res,rej)=>{
      reader.once('line',line=>{try{res(JSON.parse(line))}catch{rej(Error('Invalid fixture greeting'))}})
      child.once('error',rej);child.once('exit',()=>rej(Error('Fixture exited before ready')))
    }),'loopback TLS ready')
    assert.match(info.origin,/^https:\/\/127\.0\.0\.1:\d+$/)
    assert.equal(info.provider,info.origin.replace('127.0.0.1','localhost')+'/synthetic-provider')
    browser=await chromium.launch({headless:true,ignoreDefaultArgs:['--disable-back-forward-cache']})
    await bounded(test({info,browser,contexts,releaseAll}),'synthetic fixture journey',240000)
  }catch(e){failures.push(failure('fixture journey',e))}
  finally{
    checkpoint('cleanup: release barriers')
    for(const release of releaseAll){try{release()}catch(e){failures.push(failure('fixture cleanup',e))}}
    for(const owner of contexts){
      if(owner.closed)continue
      if(owner.gate)try{await bounded(owner.gate.close(),'response interception stopped')}catch(e){failures.push(failure('fixture cleanup',e))}
      try{await bounded(owner.ctx.close(),'context closed');owner.closed=true}catch(e){failures.push(failure('fixture cleanup',e))}
    }
    checkpoint('cleanup: close Chromium and stop TLS fixture')
    try{if(browser)await bounded(browser.close(),'browser closed')}catch(e){failures.push(failure('fixture cleanup',e))}
    try{await stopSyntheticFixture(child)}catch(e){failures.push(failure('fixture cleanup',e))}
    try{fs.rmSync(temp,{recursive:true,force:true})}catch(e){failures.push(failure('fixture cleanup',e))}
  }
  if(failures.length&&fixtureFailure)console.error('Synthetic fixture exception class: '+fixtureFailure)
  if(failures.length===1)throw failures[0]
  if(failures.length)throw new AggregateError(failures,'Product fixture or cleanup failed')
}
// Only the three named synthetic HTTPS responses are intercepted, after the
// real browser request has been served by the single-threaded WSGI fixture.
// No route.fetch() replay/second TLS client; no auth/header/body collection.
async function syntheticGate(ctx,page,info,errors){
  const cdp=await ctx.newCDPSession(page),armed=new Map(),active=new Set()
  const script='/app/artifact-product-view.mjs',allowed=new Map([[kit,'POST'],[s1+'/quarters','GET'],[script,'GET']])
  let closing=false
  const send=(method,args)=>bounded(cdp.send(method,args),'fixture '+method)
  // Ordered absolute URLPatterns: fail closed before networking, without a
  // second Fetch request owner. Fail rather than falling back if unsupported.
  await send('Network.enable',{})
  await send('Network.setBlockedURLs',{urlPatterns:[
    {urlPattern:info.origin+'/*',block:false},{urlPattern:info.provider+'?*',block:false},
    {urlPattern:'*://*:*/*',block:true}
  ],urls:['*']})
  const handle=async e=>{
    const requestId=e.requestId,url=new URL(e.request.url),path=url.pathname
    const owner=armed.get(path)
    if(!owner)return send('Fetch.continueResponse',{requestId})
    armed.delete(path)
    try{
      assert.ok(!closing&&allowed.get(path)===e.request.method&&url.href===info.origin+path,'unexpected fixture target')
      assert.equal(e.responseStatusCode,200,'fault injection requires a real successful response')
      const {body:raw,base64Encoded}=await send('Fetch.getResponseBody',{requestId})
      const original=Buffer.from(raw,base64Encoded?'base64':'utf8')
      assert.ok(original.length>0&&original.length<=2097152,'invalid synthetic fixture body')
      // Forward policy/MIME only: no challenge, authentication or cookie header.
      const headers=e.responseHeaders.filter(h=>['cache-control','pragma','referrer-policy','vary','content-security-policy','x-content-type-options','content-type'].includes(h.name.toLowerCase()))
      assert.ok(headers.some(h=>h.name.toLowerCase()==='cache-control'&&h.value==='no-store'))
      const mime=headers.find(h=>h.name.toLowerCase()==='content-type')?.value
      assert.ok(path===script?/^text\/javascript/.test(mime):/^application\/json/.test(mime))
      const changed=await bounded(owner.change(original),'explicit synthetic reply release')
      const bytes=changed?Buffer.from(JSON.stringify(changed.dto)):original,status=changed?.status??200
      assert.ok([200,401,403,409,412,503].includes(status))
      await send('Fetch.fulfillRequest',{requestId,responseCode:status,
        responseHeaders:[...headers,{name:'Content-Length',value:String(bytes.length)}],body:bytes.toString('base64')})
      owner.done.resolve()
    }catch(e){owner.done.reject(e);throw e}
  }
  const onPaused=e=>{
    const op=handle(e).catch(async()=>{
      errors.push('fixture response interception failed')
      await send('Fetch.failRequest',{requestId:e.requestId,errorReason:'Failed'}).catch(()=>{})
    })
    active.add(op);op.finally(()=>active.delete(op))
  }
  cdp.on('Fetch.requestPaused',onPaused)
  await send('Fetch.enable',{patterns:[...allowed.keys()].map(path=>({urlPattern:info.origin+path,requestStage:'Response'}))})
  return {
    once(path,change){
      assert.ok(!closing&&allowed.has(path)&&!armed.has(path),'only one fixed response may be armed')
      const done=defer();done.promise.catch(()=>{});armed.set(path,{change,done});return done.promise
    },
    async close(){
      closing=true
      for(const owner of armed.values())owner.done.reject(Error('Fixture closed before response'))
      armed.clear()
      try{
        while(active.size)await bounded(Promise.all([...active]),'finish active CDP responses')
      }finally{
        try{await send('Fetch.disable',{})}finally{cdp.off('Fetch.requestPaused',onPaused);await bounded(cdp.detach(),'fixture session detached')}
        assert.equal(errors.length,0,'fixture failed before or during teardown')
      }
    }
  }
}
async function surface(env,{clean=false}={}){
  const {info,browser,contexts}=env
  const ctx=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1440,height:1100},timezoneId:'Pacific/Honolulu'})
  const errors=[],journal=[],allowedProvider=new URL(info.provider)
  const owner={ctx,closed:false};contexts.push(owner)
  await ctx.addInitScript(()=>{
    // New on each document. Do not use browser storage, copied cookies or history
    // to retain observations. A BFCache restore reuses the original array.
    window.__pvShow=[]
    addEventListener('pageshow',e=>window.__pvShow.push({persisted:e.persisted,native:e.isTrusted}))
  })
  if(!clean)await ctx.addInitScript(()=>{
    // Deterministically lose abort, not TLS/CSRF/authorization. Only collect
    // boolean properties of fetch options. Optional timing barrier on response
    // body uses exactly the bytes actually read from the server.
    const original=window.fetch
    window.__pvNet={product:[],bodyStarted:0,bodyDone:0,transportDone:0,invalidations:0}
    // Once leaving, do not strand held promises in a cached document. The
    // synchronous navigation/pagehide guards still run before any continuation.
    addEventListener('pagehide',()=>{window.__pvNet.releaseBody?.();window.__releaseOldNamed?.()})
    window.fetch=async function(input,options={}){
      const requestPath=new URL(typeof input==='string'?input:input.url,location.origin).pathname
      const {signal,...rest}=options
      const pv=requestPath.startsWith('/api/')&&requestPath.includes('/product-views/')
      if(pv){
        const h=new Headers(options.headers)
        window.__pvNet.product.push(options.method==='POST'&&options.credentials==='same-origin'&&options.cache==='no-store'&&options.redirect==='error'&&h.has('x-csrf-token')&&!h.has('authorization'))
      }
      const response=await original.call(window,input,rest)
      if(pv&&window.__pvNet.holdBody){
        window.__pvNet.holdBody=false
        const read=response.text.bind(response)
        response.text=async()=>{
          const actual=await read()
          await new Promise(resolve=>{window.__pvNet.releaseBody=resolve;window.__pvNet.bodyStarted++})
          window.__pvNet.bodyDone++
          return actual
        }
      }
      if(pv)window.__pvNet.transportDone++
      return response
    }
    // Count externally observable shell invalidations, not just the final DOM.
    document.addEventListener('DOMContentLoaded',()=>{
      const node=document.getElementById('app-principal')
      if(node)new MutationObserver(entries=>{
        for(const e of entries)for(const n of e.addedNodes)if(n.textContent==='Not verified')window.__pvNet.invalidations++
      }).observe(node,{childList:true})
    })
  })
  const page=await ctx.newPage()
  page.setDefaultTimeout(9000)
  if(!clean)owner.gate=await syntheticGate(ctx,page,info,errors)
  page.on('dialog',async d=>{errors.push('unexpected dialog');await d.dismiss()})
  page.on('pageerror',()=>errors.push('uncaught page error'))
  page.on('download',()=>errors.push('unexpected download'))
  ctx.on('request',r=>{
    const u=new URL(r.url()),h=r.headers()
    if(![info.origin,allowedProvider.origin].includes(u.origin))errors.push('unexpected origin')
    if(u.origin===allowedProvider.origin&&u.pathname!==allowedProvider.pathname)errors.push('unexpected provider path')
    if(h.authorization)errors.push('unexpected authorization header')
    // The shared shell imports static /artifact-admin/*.mjs even here. Guard
    // authority/bytes requests, not legitimate public JavaScript dependencies.
    if(u.pathname.startsWith('/api/')&&/\/(?:artifact-download|artifact-admin)\//.test(u.pathname))errors.push('unexpected administration or byte request')
    const pv=isProduct(u.pathname)
    if(pv&&(u.search||u.hash||r.method()!=='POST'||!h['x-csrf-token']||h.referer))errors.push('unexpected product transport')
    const fromProduct=!r.isNavigationRequest()&&r.frame().url()===info.origin+product
    if(fromProduct&&u.pathname.startsWith('/api/')&&!pv)errors.push('ordinary fallback from product document')
    // Never retain cookie/header/authorization, provider URL/query or private body
    // except for the generated public test coordinates of these metadata POSTs.
    let known
    if(pv){
      try{
        const dto=r.postDataJSON(),safe=['format','quarter','path','mode','page','page_size','recursive','keyword','start_time','end_time','if_observation']
        if(!dto||typeof dto!=='object'||Array.isArray(dto)||Object.keys(dto).some(k=>!safe.includes(k)))errors.push('unexpected product request body shape')
        else known=dto
      }catch{errors.push('invalid product request body')}
    }
    journal.push({p:u.pathname,m:r.method(),...(pv?{body:known}:{}),hasQuery:Boolean(u.search)})
  })
  return {page,ctx,gate:owner.gate,owner,journal,errors,...env}
}
async function login(s,enabled){
  const {page,info,journal}=s
  const ready=page.waitForResponse(r=>new URL(r.url()).pathname==='/app/bootstrap')
  await page.goto(info.origin+catalog)
  const boot=await (await ready).json()
  if(s.gate){
    // Check URLPattern semantics in the pinned Chromium without sending probes
    // to another site. A CDP without urlPatterns receives the deny-all legacy
    // urls rule and cannot even load this trusted bootstrap.
    assert.equal(await page.evaluate(([origin,provider])=>{
      const rules=[new URLPattern(origin+'/*'),new URLPattern(provider+'?*'),new URLPattern('*://*:*/*')]
      const allowed=u=>rules.findIndex(p=>p.test(u))<2&&rules.some(p=>p.test(u))
      return allowed(origin+'/artifacts/')&&allowed(origin+'/api/x?x=1')&&allowed(provider+'?synthetic=test')
          &&[origin.replace('https:','http:')+'/',new URL(provider).origin+'/wrong','https://external.invalid/','https://external.invalid:8443/']
              .every(u=>rules[2].test(u)&&!allowed(u))
    },[info.origin,info.provider]),true)
  }
  assert.deepEqual(Object.keys(boot),['data'])
  assert.deepEqual(Object.keys(boot.data).sort(),['authorization_endpoint','login_configured','service','ui_modules'])
  assert.equal(boot.data.authorization_endpoint,info.provider);assert.equal(boot.data.service,'ms-collab')
  assert.equal(boot.data.ui_modules.includes('artifact-product-views'),enabled)
  for(const mod of boot.data.ui_modules)assert.ok(['workspaces','artifacts','artifact-admin','responsibility','pipelines','community','assistant','research-assets','gui-smoke','connections','artifact-downloads','artifact-product-views'].includes(mod))
  await page.waitForFunction(()=>!document.querySelector('#app-sign-in').disabled)
  assert.equal(await page.locator('#app-product-view-entry').isVisible(),false)
  await page.locator('#app-sign-in').click();await principal(page,'admin')
  await page.locator('#app-artifacts-link').click();await visible(page,'session');await visible(page,'artifact-picker')
  if(enabled)await visible(page,'app-product-view-entry')
  else assert.equal(await page.locator('#app-product-view-entry').isVisible(),false)
  assert.equal(journal.some(e=>isProduct(e.p)||e.p.startsWith('/app/artifact-product')),false)
}
async function ordinary(s,{named=false}={}){
  const p=s.page
  for(const [id,v]of Object.entries({workspace:'alpha',connection:'source',bucket:'bucket'}))await p.locator('#'+id).fill(v)
  await p.locator('#scope-form button').click()
  await visible(p,'catalog')
  if(named){
    await p.waitForFunction(()=>document.querySelector('#status').textContent.startsWith('Catalog loaded'))
    if(await p.locator('#quarter').inputValue()!==q2){
      await p.locator('#quarter').selectOption(q2)
      await p.waitForFunction(()=>document.querySelector('#status').textContent.startsWith('Catalog loaded'))
    }
    await p.locator('#view-id').fill('kit')
    const reply=p.waitForResponse(r=>new URL(r.url()).pathname===s1+'/views/kit')
    await p.locator('#view-form button').first().click()
    assert.equal((await reply).status(),200)
    await p.waitForFunction(()=>document.querySelector('#results').textContent.includes('ordinary-only.bin'))
    assert.match(await p.locator('#path-label').textContent(),/Named view: kit.*navigation disabled/i)
    assert.equal(await p.locator('#up').isDisabled(),true)
    await p.getByRole('button',{name:'Check download',exact:true}).waitFor()
    assert.equal(await p.locator('#results .entry-name').count(),1)
  }else await p.waitForFunction(()=>document.querySelector('#status').textContent.startsWith('Catalog loaded'))
}
async function empty(s,where){
  const p=s.page
  assert.equal(p.url(),s.info.origin+where)
  assert.deepEqual(await p.evaluate(()=>[location.search,location.hash,history.state,localStorage.length,sessionStorage.length,window.name]),['','',null,0,0,''])
  assert.equal((await p.evaluate(()=>indexedDB.databases())).length,0)
  if(where===product){
    assert.deepEqual(await p.evaluate(ids=>ids.map(id=>document.getElementById('pv-'+id).value),privateIds),privateIds.map(()=>''))
    assert.equal(await p.locator('#pv-results li').count(),0)
    assert.equal(await p.locator('#pv-evidence').textContent(),'')
    assert.equal(await p.locator('#pv-recursive').isChecked(),false)
    assert.equal(await p.locator('input[type=password],#artifact-picker,#download-panel,#view-form').count(),0)
  }else{
    for(const id of ['workspace','connection','bucket','keyword','view-id'])assert.equal(await p.locator('#'+id).inputValue(),'')
    assert.equal(await p.locator('#catalog').isVisible(),false)
    assert.equal(await p.locator('#results').textContent(),'')
    assert.equal(await p.locator('#download-key').textContent(),'')
  }
}
async function entry(s){
  const {page:p,journal,info}=s,before=countApi(journal)
  assert.equal(await p.locator('#app-product-view-entry').getAttribute('href'),product)
  await enter(p,'app-product-view-entry');await p.waitForURL(info.origin+product)
  await visible(p,'pv-controls');await frames(p);await empty(s,product)
  assert.equal(await p.evaluate(()=>document.referrer),'');assert.equal(countApi(journal),before)
  assert.equal(await p.getByRole('button',{name:/download/i}).count(),0)
  await p.locator('#pv-workspace').focus()
  for(const id of ['connection','bucket','view','quarter','open']){await p.keyboard.press('Tab');assert.equal(await p.evaluate(()=>document.activeElement.id),'pv-'+id)}
}
async function fields(s,values={}){
  const before=countApi(s.journal)
  // Even a fresh open of the same tuple is an actual identity edit, not an
  // accidental dependency on how Chromium autofill handles unchanged text.
  if(await s.page.locator('#pv-workspace').inputValue())await s.page.locator('#pv-workspace').fill('')
  for(const [id,val] of Object.entries({workspace:'alpha',connection:'source',bucket:'bucket',view:'kit',quarter:q2,...values}))await s.page.locator('#pv-'+id).fill(val)
  await frames(s.page);assert.equal(countApi(s.journal),before)
}
async function rendered(page,keys){
  await page.waitForFunction(keys=>JSON.stringify([...document.querySelectorAll('#pv-results li .pv-key')].map(e=>e.textContent))===JSON.stringify(keys),keys)
  assert.deepEqual(await page.locator('#pv-results li .pv-key').allTextContents(),keys)
  for(const e of await page.locator('#pv-results li').all())assert.equal(await e.isVisible(),true)
  assert.equal(await page.locator('#pv-results svg,#pv-results img,#pv-results a').count(),0)
  assert.equal(await page.getByRole('button',{name:/download/i}).count(),0)
}
async function wire(s,action,target,expected,statusCode=200){
  const {page,journal,info}=s,start=journal.length
  const wait=page.waitForResponse(r=>r.url()===info.origin+target&&r.request().method()==='POST')
  const [response]=await Promise.all([wait,action()])
  assert.equal(response.status(),statusCode)
  const observed=journal.slice(start).filter(r=>r.p.startsWith('/api/'))
  // Keep proofs and any unexpected caller fields out of failure logs.
  assert.ok(isDeepStrictEqual(observed,[{p:target,m:'POST',body:expected,hasQuery:false}]),'One exact product POST must match the expected body and URL')
  assert.equal(await page.evaluate(()=>!window.__pvNet||__pvNet.product.every(Boolean)),true)
  assert.equal(await page.evaluate(()=>!location.search&&!location.hash&&!document.referrer&&history.state===null&&!window.name&&!localStorage.length&&!sessionStorage.length),true)
  assert.equal(response.headers()['cache-control'],'no-store')
  return response
}
async function read(s,action,target,expected,keys,total,available=true){
  const before=countApi(s.journal),res=await wire(s,action,target,expected),data=(await bounded(res.json(),'browser decoded real product response')).data
  assert.deepEqual(data.scope,{workspace_id:'alpha',connection_id:'source',bucket_id:target===other?'second-bucket':'bucket'})
  assert.equal(data.view_id,target.split('/').at(-1));assert.equal(data.kind,'product-view')
  for(const k of ['quarter','mode','path','page','page_size'])assert.equal(data[k],expected[k])
  assert.equal(data.total,total);assert.equal(data.quarter_available,available)
  assert.deepEqual(data.items.map(row=>row.key),keys)
  assert.deepEqual(data.source,{kind:'stored_index',download_available:false,full_history_complete:false,live_provider_verified:false})
  assert.ok(/^[0-9a-f]{64}$/.test(data.observation))
  if(expected.if_observation)assert.ok(data.observation===expected.if_observation,'observation must stay bound')
  await rendered(s.page,keys)
  await visible(s.page,'pv-evidence')
  await s.page.waitForFunction(([n,stamp])=>{
    const e=document.getElementById('pv-evidence').textContent
    return e.includes(String(n))&&e.includes(stamp)
  },[data.catalog_revision,data.observed_at])
  if(!keys.length)await status(s.page,available?'no (?:files|rows)|no match':'quarter is absent')
  await frames(s.page);assert.equal(countApi(s.journal),before+1)
  assert.equal(s.page.url(),s.info.origin+product)
  return data
}
const folderButton=(s,name)=>s.page.getByRole('button',{name:'Folder: '+(name||'(empty segment)'),exact:true})
async function directory(s,name){const b=folderButton(s,name);await b.focus();await s.page.keyboard.press('Enter')}
const keypress=(s,id)=>()=>enter(s.page,'pv-'+id)

async function screenshot(s,name,width,height,{contrast,hit}){
  const p=s.page
  assert.deepEqual(p.viewportSize(),{width,height});assert.ok(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'document overflows horizontally')
  const evidence=await p.evaluate(({contrast,hit})=>{
    function target(selector){const e=document.querySelector(selector);if(!e||!e.checkVisibility()||e.disabled)throw Error('Missing usable screenshot target: '+selector);return e}
    function rgb(value){const n=value.match(/[\d.]+/g).map(Number);return {v:n.slice(0,3),a:n[3]??1}}
    function luminance(v){return v.map(x=>x/255).map(x=>x<=.04045?x/12.92:((x+.055)/1.055)**2.4).reduce((n,x,i)=>n+x*[.2126,.7152,.0722][i],0)}
    const ratios=contrast.map(selector=>{
      const e=target(selector),fg=rgb(getComputedStyle(e).color)
      let bg
      for(let n=e;n;n=n.parentElement){const c=rgb(getComputedStyle(n).backgroundColor);if(c.a===1){bg=c;break}if(c.a!==0)throw Error('Unmeasurable composited background: '+selector)}
      if(!bg||fg.a!==1)throw Error('Missing opaque paint: '+selector)
      const [dark,light]=[luminance(fg.v),luminance(bg.v)].sort((a,b)=>a-b)
      return [selector,Number(((light+.05)/(dark+.05)).toFixed(3))]
    })
    const points=hit.map(selector=>{
      const e=target(selector);e.scrollIntoView({block:'center',behavior:'instant'})
      const {left,top,width:w,height:h,right,bottom}=e.getBoundingClientRect(),x=left+w/2,y=top+h/2
      if(w<=0||h<=0||left<0||top<0||right>innerWidth||bottom>innerHeight||!e.contains(document.elementFromPoint(x,y)))throw Error('Occluded/unreachable control: '+selector)
      return [selector,Math.round(x),Math.round(y)]
    })
    return {ratios,points,rows:document.querySelectorAll('#pv-results li').length}
  },{contrast,hit})
  for(const [id,r]of evidence.ratios)assert.ok(r>=4.5,'Insufficient contrast '+id)
  await p.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));await frames(p)
  const dir=path.join(process.env.RUNNER_TEMP||os.tmpdir(),'ms-collab-artifact-session')
  fs.mkdirSync(dir,{recursive:true})
  const png=await p.screenshot({path:path.join(dir,name),fullPage:true})
  assert.equal(png.subarray(0,8).toString('hex'),'89504e470d0a1a0a');assert.equal(png.toString('ascii',12,16),'IHDR')
  assert.equal(png.readUInt32BE(16),width);assert.ok(png.readUInt32BE(20)>=height);assert.ok(png.length>4096)
  console.log('PRODUCT_VIEW_SCREEN '+JSON.stringify({name,viewport:[width,height],image:[width,png.readUInt32BE(20)],...evidence,bytes:png.length,sha256:createHash('sha256').update(png).digest('hex')}))
}
async function ctrl(s,name){
  assert.equal(await s.page.evaluate(async name=>{
    const r=await fetch('/synthetic/artifact/product-'+name,{method:'POST'})
    return r.status===200&&(await r.json()).ok===true
  },name),true,'Fixture control failed: '+name)
}
function held(s,target,injected){
  const reached=defer(),gate=defer(),script='/app/artifact-product-view.mjs'
  // Bind the old Playwright Request *before* the click; another same-URL
  // request in the ABA exercise may have completed before this reply.
  const req=s.page.waitForRequest(r=>r.url()===s.info.origin+target&&r.method()===(target===kit?'POST':'GET'))
  req.catch(()=>{});s.releaseAll.push(gate.resolve)
  let originalHash
  const done=s.gate.once(target,async bytes=>{
    originalHash=createHash('sha256').update(bytes).digest('hex')
    reached.resolve();await gate.promise
    return injected?{status:injected,dto:{error:'synthetic_protocol_fault'}}:null
  })
  const started=Promise.race([reached.promise,done]).then(()=>{});started.catch(()=>{})
  return {started,release:async()=>{
    gate.resolve()
    await bounded(done,'held response delivered')
    const response=await bounded((await req).response(),'old Chromium request received response')
    assert.equal(response?.status(),injected||200)
    assert.ok(await response.headerValue('www-authenticate')===null&&await response.headerValue('set-cookie')===null,'no challenge or cookie')
    const final=await bounded(response.body(),'old Chromium request received the full body')
    if(injected)assert.ok(isDeepStrictEqual(JSON.parse(final),{error:'synthetic_protocol_fault'}),'expected obsolete error')
    else assert.ok(createHash('sha256').update(final).digest('hex')===originalHash,'unmodified response bytes')
    if(target===script){
      assert.ok(/^text\/javascript/.test(response.headers()['content-type']))
      assert.ok(final.equals(fs.readFileSync(path.join(__dirname,'../artifact-product-view.mjs'))),'original module only')
    }
    await frames(s.page)
  }}
}
async function recheck(s,id){await enter(s.page,'app-recheck');await principal(s.page,'admin');await visible(s.page,id);await frames(s.page)}
async function preventThisNavigation(s,id){
  // The product's own click handlers on the target/document must run. Only
  // the **native default action** is vetoed at the end of the bubble so that
  // its old JS realm remains available for the late-response assertion.
  await s.page.evaluate(id=>{
    const anchor=document.getElementById(id)
    window.addEventListener('click',event=>event.preventDefault(),{once:true})
    anchor.click()
  },id)
  assert.equal(await s.page.locator('#app-principal').textContent(),'Not verified')
}
async function waitFailed(s,pattern){
  await status(s.page,pattern)
  assert.deepEqual(await s.page.locator('#pv-results li').allTextContents(),[])
  assert.equal(await s.page.locator('#pv-evidence').textContent(),'');assert.equal(await s.page.locator('#pv-page').textContent(),'')
  assert.equal(await s.page.locator('#pv-next').isEnabled(),false)
  await visible(s.page,'pv-reopen')
}
async function happy(s){
  const p=s.page
  await fields(s)
  let r=await read(s,keypress(s,'open'),kit,body(),root,1)
  const pin=r.observation
  const pinned=diff=>body({if_observation:pin,...diff})
  const branch=()=>read(s,()=>directory(s,'pkg'),kit,pinned({path:layer}),tree.slice(0,10),16)
  await branch()
  const collision=p.locator('#pv-results li').filter({has:p.locator('.pv-key',{hasText:/^pkg\/Kit_alpha$/})})
  assert.equal(await collision.count(),1);assert.equal(await collision.locator('button,a').count(),0)
  await folderButton(s,'Kit_alpha').waitFor()
  assert.match(await p.locator('#pv-page').textContent(),/16 rows at this level/i)
  await screenshot(s,'product-view-tree-desktop.png',1440,1100,{contrast:['h1','#pv-page','#pv-evidence','#pv-results .pv-key','#pv-results button'],hit:['#pv-results button','#pv-next']})
  await read(s,keypress(s,'next'),kit,pinned({path:layer,page:2}),tree.slice(10),16)
  await read(s,keypress(s,'prev'),kit,pinned({path:layer}),tree.slice(0,10),16)
  await read(s,()=>directory(s,'a'),kit,pinned({path:'pkg/a/'}),['pkg/a//'],1)
  await read(s,()=>directory(s,''),kit,pinned({path:'pkg/a//'}),['pkg/a//KiT_one.zip'],1)
  await read(s,keypress(s,'up'),kit,pinned({path:'pkg/a/'}),['pkg/a//'],1)
  await read(s,()=>p.locator('#pv-crumbs').getByRole('button',{name:'pkg/',exact:true}).click(),kit,pinned({path:layer}),tree.slice(0,10),16)
  await read(s,keypress(s,'apply'),kit,pinned(files()),direct.slice(0,10),12)
  await read(s,()=>p.locator('#pv-page-size').selectOption('20'),kit,pinned(files({page_size:20})),direct,12)
  await read(s,()=>p.locator('#pv-page-size').selectOption('10'),kit,pinned(files()),direct.slice(0,10),12)

  const before=countApi(s.journal)
  await p.locator('#pv-start').fill(z);await p.locator('#pv-end').fill(z)
  await frames(p);assert.equal(countApi(s.journal),before);assert.equal(await p.locator('#pv-results li').count(),0)
  await read(s,keypress(s,'apply'),kit,pinned(files({start_time:z,end_time:z})),[directLeaf],1)
  await p.locator('#pv-recursive').check()
  await read(s,keypress(s,'apply'),kit,pinned(files({recursive:true,start_time:z,end_time:z})),[directLeaf,deepLeaf],2)
  // Same day, exact microsecond: inclusive, not millisecond-rounded.
  await p.locator('#pv-start').fill(micro);await p.locator('#pv-end').fill(micro)
  await read(s,keypress(s,'apply'),kit,pinned(files({recursive:true,start_time:micro,end_time:micro})),['pkg/ab/KIT_two.zip'],1)
  await p.locator('#pv-start').fill('');await p.locator('#pv-end').fill('2026-04-02T01:00:00+01:00')
  await p.locator('#pv-keyword').fill('alpha')
  await read(s,keypress(s,'apply'),kit,pinned(files({recursive:true,keyword:'alpha',end_time:'2026-04-02T01:00:00+01:00'})),[directLeaf,deepLeaf],2)
  for(const keyword of ['ALPHA','%','ε']){
    await p.locator('#pv-keyword').fill(keyword)
    await read(s,keypress(s,'apply'),kit,pinned(files({recursive:true,keyword,end_time:'2026-04-02T01:00:00+01:00'})),[],0)
  }
  await p.locator('#pv-keyword').fill('')
  for(const invalid of ['2026-02-30T00:00:00Z','2026-04-02T00:00:00','2026-04-02T00:00:00+25:00',micro]){
    const n=countApi(s.journal);await p.locator('#pv-start').fill(invalid);await enter(p,'pv-apply');await frames(p)
    assert.equal(countApi(s.journal),n);assert.equal(await p.locator('#pv-results li').count(),0)
    assert.ok((await p.locator('#pv-status').textContent()).length>0)
  }
  // Clock affects page Date only, never TLS, the IdP exchange or server/session.
  await p.setViewportSize({width:390,height:844})
  // Freeze only zero-argument Date construction for this single shortcut.
  // Playwright polling, Chromium timers and real animation frames keep running.
  await p.evaluate(()=>{
    const systemDate=window.Date,fixed=systemDate.parse('2026-04-03T03:02:01Z')
    window.Date=new Proxy(systemDate,{construct:(target,args)=>Reflect.construct(target,args.length?args:[fixed])})
    window.__restoreProductDate=()=>{window.Date=systemDate;delete window.__restoreProductDate}
  })
  try{await read(s,keypress(s,'range7'),kit,pinned(files({recursive:true,...quick})),rangeRows,3)}
  finally{await bounded(p.evaluate(()=>window.__restoreProductDate()),'restore browser date')}
  assert.equal(await p.locator('#pv-range7').getAttribute('aria-pressed'),'true')
  assert.equal(await p.locator('#pv-start').inputValue(),quick.start_time);assert.equal(await p.locator('#pv-end').inputValue(),quick.end_time)
  await screenshot(s,'product-view-range-mobile.png',390,844,{contrast:['#pv-location','#pv-range7','#pv-results .pv-key','#pv-start','#pv-end'],hit:['#pv-range7','#pv-apply','#pv-tree']})
  checkpoint('range: manual override and quarter transitions')
  await p.locator('#pv-end').fill(z);assert.equal(await p.locator('#pv-range7').getAttribute('aria-pressed'),'false')
  await read(s,keypress(s,'tree'),kit,pinned({path:layer}),tree.slice(0,10),16)
  assert.deepEqual(await p.evaluate(()=>['start','end','keyword'].map(x=>document.getElementById('pv-'+x).value)),['','',''])
  assert.equal(await p.locator('#pv-recursive').isChecked(),false)
  for(const [quarter,children] of [[q1,['pkg/KIT_prev']],['2027Q1',[]],[q2,tree.slice(0,10)]]){
    await p.locator('#pv-quarter').fill(quarter)
    await read(s,keypress(s,'open'),kit,pinned({quarter}),quarter==='2027Q1'?[]:root,quarter==='2027Q1'?0:1,quarter!=='2027Q1')
    if(children.length)await read(s,()=>directory(s,'pkg'),kit,pinned({path:layer,quarter}),children,quarter===q1?1:16)
  }
  checkpoint('range: recursive aggregate and page two')
  // Aggregate fixtures are checked independently of the DOM and returned totals.
  await p.locator('#pv-recursive').check()
  const allFiles=['pkg/a//KiT_one.zip','pkg/ab/KIT_three.zip','pkg/ab/KIT_two.zip','pkg/'+label+'/KiT_safe.zip',deepLeaf,...direct].sort((a,b)=>{
    const x=a.split('/').at(-1),y=b.split('/').at(-1);return x<y?-1:x>y?1:a<b?-1:a>b?1:0
  })
  await read(s,keypress(s,'apply'),kit,pinned(files({recursive:true})),allFiles.slice(0,10),17)
  await read(s,keypress(s,'next'),kit,pinned(files({recursive:true,page:2})),allFiles.slice(10),17)
  await p.setViewportSize({width:1440,height:1100})
  return pin
}
async function selection(s){
  const p=s.page
  await p.locator('#pv-view').fill('sensor')
  let current=await read(s,keypress(s,'open'),sensor,body(),root,1)
  await read(s,()=>directory(s,'pkg'),sensor,body({path:layer,if_observation:current.observation}),['pkg/sensor_only.bin'],1)
  await fields(s,{bucket:'second-bucket'})
  current=await read(s,keypress(s,'open'),other,body(),['other/'],1)
  await read(s,()=>directory(s,'other'),other,body({path:'other/',if_observation:current.observation}),['other/KIT_second-bucket.zip'],1)
  for(const [diff,target] of [[{view:'absent'},s1+'/product-views/absent'],[{bucket:'missing-bucket'},missing+'/product-views/kit'],[{bucket:'second-bucket',view:'sensor'},s2+'/product-views/sensor']]){
    await fields(s,diff);const n=countApi(s.journal)
    await wire(s,keypress(s,'open'),target,body(),404)
    await waitFailed(s,'unavailable|not available|access');assert.equal(await p.locator('#app-principal').textContent(),'admin')
    await frames(p);assert.equal(countApi(s.journal),n+1)
  }
}

async function faults(s){
  const p=s.page
  await fields(s)
  let result=await read(s,keypress(s,'open'),kit,body(),root,1)
  // Each corruption is applied to an actual, successful, pinned server read.
  // The injected HTTP statuses are *not* claims about an actual authority decision.
  const mutations={
    'extra-field':d=>{d.download_url='/synthetic-never-follow'},
    'advertised-bytes':d=>{d.source.download_available=true},
    'wrong-scope':d=>{d.scope.bucket_id='second-bucket'},'wrong-view':d=>{d.view_id='sensor'},
    'wrong-quarter':d=>{d.quarter=q1},'wrong-mode':d=>{d.mode='files'},'wrong-path':d=>{d.path='pkg/a/'},
    'wrong-page':d=>{d.page=2},'wrong-proof':d=>{d.observation=d.observation.replace(/^./,c=>c==='0'?'1':'0')},
    'same-proof-different-revision':d=>{d.catalog_revision++},
    'same-proof-different-date':d=>{d.observed_at='2027-01-01T00:00:00Z'},
    'foreign-row':d=>{const row=d.items[0];row.parent='pkg/else/';row.key=row.parent+row.name+'/'}
  }
  for(const fault of [...Object.keys(mutations),403,409,503]){
    const injected=typeof fault==='number',n=countApi(s.journal)
    let altered
    checkpoint('protocol fault: '+fault)
    const delivered=s.gate.once(kit,original=>{
      if(injected)altered={error:'synthetic_protocol_fault'}
      else{altered=JSON.parse(original);mutations[fault](altered.data)}
      return {status:injected?fault:200,dto:altered}
    })
    const received=await wire(s,()=>directory(s,'pkg'),kit,body({path:layer,if_observation:result.observation}),injected?fault:200)
    await bounded(delivered,'injected response delivered')
    assert.ok(altered&&isDeepStrictEqual(await bounded(received.json(),'Chromium decoded injected body'),altered),'Intact injected DTO: '+fault)
    await waitFailed(s,injected&&fault!==503?'unavailable|inaccessible':'could not verify')
    assert.equal(await p.locator('#app-principal').textContent(),'admin')
    assert.doesNotMatch(await p.locator('#pv-status').textContent(),/synthetic_protocol_fault|synthetic-never-follow/i)
    await frames(p);assert.equal(countApi(s.journal),n+1)
    result=await read(s,keypress(s,'reopen'),kit,body(),root,1)
  }
  await read(s,()=>directory(s,'pkg'),kit,body({path:layer,if_observation:result.observation}),tree.slice(0,10),16)
  await p.locator('#pv-start').fill(z);await p.locator('#pv-end').fill(z)
  checkpoint('protocol fault: file outside time bound')
  const delivered=s.gate.once(kit,original=>{
    const dto=JSON.parse(original);assert.equal(dto.data.items.length,1)
    dto.data.items[0].modified_at=micro
    return {status:200,dto}
  })
  const received=await wire(s,keypress(s,'apply'),kit,body(files({if_observation:result.observation,start_time:z,end_time:z})))
  await bounded(delivered,'time fault delivered');assert.equal((await bounded(received.json(),'Chromium decoded time fault')).data.items[0].modified_at,micro)
  await waitFailed(s,'could not verify')
}
async function stale(s){
  const p=s.page
  // Force a true ABA: the replacement kit root has *identical* tuple/proof
  // to the obsolete request's view, but not the same request owner.
  for(const fault of [null,401,412]){
    await fields(s)
    checkpoint('ABA: hold '+(fault||200))
    const hold=held(s,kit,fault)
    await enter(p,'pv-open');await bounded(hold.started,'ABA began with real response')
    await p.locator('#pv-view').fill('sensor');await read(s,keypress(s,'open'),sensor,body(),root,1)
    await p.locator('#pv-view').fill('kit')
    const current=await read(s,keypress(s,'open'),kit,body(),root,1)
    await read(s,()=>directory(s,'pkg'),kit,body({path:layer,if_observation:current.observation}),tree.slice(0,10),16)
    const before=countApi(s.journal),[invalid,done]=await p.evaluate(()=>[__pvNet.invalidations,__pvNet.transportDone])
    checkpoint('ABA: release '+(fault||200));await hold.release();await p.waitForFunction(n=>__pvNet.transportDone>n,done);await frames(p)
    assert.equal(await p.evaluate(()=>__pvNet.invalidations),invalid)
    assert.equal(await p.locator('#app-principal').textContent(),'admin');assert.equal(countApi(s.journal),before)
    await rendered(p,tree.slice(0,10))
  }
  // Same race after the genuine response headers and bytes were received,
  // specifically inside response.text(), which is not a route interception.
  await fields(s);const generation=await p.evaluate(()=>{__pvNet.holdBody=true;return __pvNet.bodyStarted})
  await enter(p,'pv-open');await p.waitForFunction(previous=>__pvNet.bodyStarted===previous+1,generation)
  await p.locator('#pv-bucket').fill('second-bucket');await read(s,keypress(s,'open'),other,body(),['other/'],1)
  const before=countApi(s.journal)
  await p.evaluate(()=>__pvNet.releaseBody());await p.waitForFunction(()=>__pvNet.bodyDone===1);await frames(p)
  assert.equal(countApi(s.journal),before);await rendered(p,['other/'])
}
async function cross(s){
  const p=s.page
  // Observe the old realm after a departure intent on BOTH native return links.
  for(const id of ['pv-return','app-artifacts-link']){
    await fields(s)
    checkpoint('departure guard: '+id)
    const h=held(s,kit,401);await enter(p,'pv-open');await bounded(h.started,'real response before navigation')
    await preventThisNavigation(s,id);await empty(s,product)
    await recheck(s,'pv-controls');await fields(s,{bucket:'second-bucket'})
    await read(s,keypress(s,'open'),other,body(),['other/'],1)
    const n=countApi(s.journal),[invalid,done]=await p.evaluate(()=>[__pvNet.invalidations,__pvNet.transportDone])
    await h.release();await p.waitForFunction(n=>__pvNet.transportDone>n,done);await frames(p)
    assert.equal(await p.evaluate(()=>__pvNet.invalidations),invalid);assert.equal(countApi(s.journal),n)
    assert.equal(await p.locator('#app-principal').textContent(),'admin');await rendered(p,['other/'])
  }
  // Real (not prevented) navigation while a genuine body still belongs to the
  // old realm: losing that realm is not by itself proof of owner/401 guarding.
  await fields(s);const generation=await p.evaluate(()=>{__pvNet.holdBody=true;return __pvNet.bodyStarted})
  await enter(p,'pv-open');await p.waitForFunction(previous=>__pvNet.bodyStarted===previous+1,generation)
  await enter(p,'pv-return');await p.waitForURL(s.info.origin+catalog);await visible(p,'session');await empty(s,catalog)
  await ordinary(s,{named:true})
  // Hold a REAL old quarters read and inject a late 401 over it. The old shell
  // remains observable in this separate, prevented-navigation test.
  const quarters=s1+'/quarters',old=held(s,quarters,401)
  await p.locator('#reload').click();await bounded(old.started,'real quarters read before navigation')
  await preventThisNavigation(s,'app-product-view-entry');await empty(s,catalog)
  await recheck(s,'session');await ordinary(s)
  const n=countApi(s.journal),invalid=await p.evaluate(()=>__pvNet.invalidations)
  const delivered=p.waitForResponse(r=>new URL(r.url()).pathname===quarters&&r.status()===401)
  await old.release();await delivered;await frames(p)
  assert.equal(await p.evaluate(()=>__pvNet.invalidations),invalid);assert.equal(await p.locator('#app-principal').textContent(),'admin')
  assert.equal(countApi(s.journal),n);assert.match(await p.locator('#status').textContent(),/^Catalog loaded/)
  // Native direction in the other sense: the ordinary transport holds a real
  // Named-view response in memory until after departure (no cookie/route hack).
  await p.evaluate(path=>{
    const original=window.fetch;window.fetch=async function(input,...opts){
      const r=await original.call(window,input,...opts)
      if(new URL(input,location.href).pathname===path){window.__oldNamedPending=true;await new Promise(res=>{window.__releaseOldNamed=res})}
      return r
    }
  },s1+'/views/kit')
  await p.locator('#view-id').fill('kit');await p.locator('#view-form button').first().click()
  await p.waitForFunction(()=>window.__oldNamedPending===true)
  await entry(s);await fields(s);await read(s,keypress(s,'open'),kit,body(),root,1)
}
async function bootRace(s){
  const {page:p}=s
  // Delay the enabled page's actual module response. A stale dynamic mount
  // must not inspect the current cookie or reconstruct an already-cleared page.
  const h=held(s,'/app/artifact-product-view.mjs')
  const idx=s.journal.length
  await p.goto(s.info.origin+product,{waitUntil:'commit'});await bounded(h.started,'real response before navigation')
  await p.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide')))
  await h.release()
  assert.equal(await bounded(p.evaluate(async()=>typeof (await import('/app/artifact-product-view.mjs')).mountArtifactProductView),'real product module export'),'function');await frames(p)
  assert.equal(s.journal.slice(idx).filter(r=>r.p==='/auth/session').length,0)
  assert.equal(await p.locator('#pv-controls').isVisible(),false);assert.equal(await p.locator('#app-principal').textContent(),'Not verified')
}
async function recheckSignedOut(s){
  const p=s.page
  await p.waitForFunction(()=>document.querySelector('#app-sign-in')?.disabled===false&&!document.getElementById('app-recheck').disabled)
  const before=s.journal.length,wait=p.waitForResponse(r=>new URL(r.url()).pathname==='/auth/session')
  await enter(p,'app-recheck');assert.equal((await wait).status(),401,'current cookie must fail an explicit session recheck')
  await frames(p);await principal(p,'Not verified');await empty(s,catalog)
  assert.ok(!s.journal.slice(before).some(x=>x.p==='/auth/start'||x.p.startsWith('/api/')))
}
async function nativeHistory(env){
  // No routing, pending reads or clock overrides. Chromium is allowed to
  // cache, the production no-store response headers must not be weakened.
  const s=await surface(env,{clean:true}),p=s.page
  await login(s,true);await entry(s);await fields(s)
  let result=await read(s,keypress(s,'open'),kit,body(),root,1)
  await read(s,()=>directory(s,'pkg'),kit,body({path:layer,if_observation:result.observation}),tree.slice(0,10),16)
  await p.locator('#pv-keyword').fill('do-not-restore');await enter(p,'pv-return')
  await p.waitForURL(env.info.origin+catalog);await visible(p,'session');await empty(s,catalog)
  await ordinary(s)
  const history=[]
  for(const [method,url] of [['goBack',product],['goForward',catalog]]){
    const from=s.journal.length
    await p[method]({waitUntil:'commit'});await p.waitForURL(env.info.origin+url)
    const shows=await p.waitForFunction(()=>{
      const event=window.__pvShow?.filter(e=>e.native).at(-1)
      return event&&(event.persisted||performance.getEntriesByType('navigation')[0]?.type==='back_forward')?event:null
    })
    const {persisted}=await shows.jsonValue();history.push({method,persisted})
    if(persisted){
      await principal(p,'Not verified');await empty(s,url)
      await recheck(s,url===product?'pv-controls':'session')
    }else await visible(p,url===product?'pv-controls':'session')
    await frames(p);await empty(s,url)
    assert.ok(s.journal.slice(from).filter(r=>r.p.startsWith('/api/')).every(r=>url===catalog&&r.p==='/api/workspaces'))
  }
  // Deliberate persisted-branch event is SEPARATE from the two native events.
  await entry(s);await fields(s);await read(s,keypress(s,'open'),kit,body(),root,1)
  await p.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true})))
  await empty(s,product);await principal(p,'Not verified');await recheck(s,'pv-controls')
  await fields(s);await read(s,keypress(s,'open'),kit,body(),root,1)
  await enter(p,'app-sign-out');await p.waitForFunction(()=>document.getElementById('app-status').textContent.includes('server session is revoked'))
  await empty(s,product)
  const n=countApi(s.journal);await p.goBack({waitUntil:'commit'});await p.waitForURL(s.info.origin+catalog);await principal(p,'Not verified')
  await empty(s,catalog);await frames(p);assert.equal(countApi(s.journal),n)
  await recheckSignedOut(s);assert.equal(s.journal.filter(x=>x.p==='/auth/start').length,1)
  assert.deepEqual(s.errors,[])
  await bounded(s.ctx.close(),'close isolated history context before next cookie journey');s.owner.closed=true
  console.log('PRODUCT_VIEW_NATIVE_HISTORY '+JSON.stringify({native:history,injectedPersistedBranch:true,observedNativeBFCache:history.some(x=>x.persisted)}))
}
async function authority(s){
  const p=s.page
  // Current real 412: an ordinary product continuation retains its original
  // proof even when the approved publication behind it has changed.
  await fields(s);const first=await read(s,keypress(s,'open'),kit,body(),root,1)
  await read(s,()=>directory(s,'pkg'),kit,body({path:layer,if_observation:first.observation}),tree.slice(0,10),16)
  checkpoint('authority: real republish and pinned read');await ctrl(s,'republish');let n=countApi(s.journal)
  await wire(s,keypress(s,'next'),kit,body({path:layer,page:2,if_observation:first.observation}),412)
  await waitFailed(s,'publication or view changed');await frames(p);assert.equal(countApi(s.journal),n+1)
  await p.setViewportSize({width:390,height:844})
  await screenshot(s,'product-view-recovery-mobile.png',390,844,{contrast:['h1','#pv-status','#pv-reopen'],hit:['#pv-reopen']})
  const fresh=await read(s,keypress(s,'reopen'),kit,body(),root,1)
  assert.ok(first.observation!==fresh.observation,'newly approved observation must differ')
  assert.ok(fresh.catalog_revision>first.catalog_revision,'publication revision must advance')
  const replacement=[...dirs,'pkg/KIT_after-republish.txt',...direct]
  await read(s,()=>directory(s,'pkg'),kit,body({path:layer,if_observation:fresh.observation}),replacement.slice(0,10),17)
  checkpoint('authority: real reader withdrawal');await ctrl(s,'revoke-read');n=countApi(s.journal)
  await wire(s,keypress(s,'next'),kit,body({path:layer,page:2,if_observation:fresh.observation}),404)
  await waitFailed(s,'unavailable|inaccessible');await frames(p);assert.equal(countApi(s.journal),n+1)
  assert.equal(await p.locator('#app-principal').textContent(),'admin')
  await fields(s,{bucket:'second-bucket'})
  const second=await read(s,keypress(s,'open'),other,body(),['other/'],1)
  checkpoint('authority: real cookie expiry');await ctrl(s,'expire-session');n=countApi(s.journal)
  const auth=s.journal.filter(r=>r.p==='/auth/start').length
  // Same cookie and selected reader, but the actual session clock has now elapsed.
  await wire(s,()=>directory(s,'other'),other,body({path:'other/',if_observation:second.observation}),401)
  await principal(p,'Not verified');await empty(s,product)
  await frames(p);assert.equal(countApi(s.journal),n+1)
  assert.equal(s.journal.filter(r=>r.p==='/auth/start').length,auth)
  assert.equal(await p.locator('#pv-controls').isVisible(),false)
  assert.equal(await p.evaluate(()=>__pvNet.product.length>0&&__pvNet.product.every(Boolean)),true)
  await p.goBack({waitUntil:'commit'});await p.waitForURL(s.info.origin+catalog);await principal(p,'Not verified');await empty(s,catalog)
  await recheckSignedOut(s);assert.equal(s.journal.filter(x=>x.p==='/auth/start').length,1)
  assert.deepEqual(s.errors,[])
}
async function step(label,fn){
  checkpoint(label)
  try{return await bounded(Promise.resolve().then(fn),label,60000)}catch(e){
    // Do not pass a raw Playwright exception, which can contain callback URLs
    // or wire values, into the enclosing legacy runner's recursive reporter.
    throw failure(label,e)
  }
}
async function run(){
  await fixture(false,async env=>{
    const s=await step('default loopback surface',()=>surface(env));await step('default host',async()=>{
      await login(s,false)
      // Existing named view on the actual vanilla fixture remains usable and
      // does not cause any probing of optional product assets/endpoints.
      await ordinary(s)
      await s.page.locator('#view-id').fill('files');await s.page.locator('#view-form button').first().click()
      await s.page.waitForFunction(()=>document.querySelector('#status').textContent.startsWith('Named view loaded'))
      assert.equal(s.journal.filter(r=>isProduct(r.p)||r.p.startsWith('/app/artifact-product')).length,0)
      const r=await s.page.goto(s.info.origin+product);assert.equal(r.status(),404)
      assert.deepEqual(s.errors,[])
    })
  })
  await fixture(true,async env=>{
    await step('clean-context native history',()=>nativeHistory(env))
    const s=await step('configured loopback surface',()=>surface(env))
    await step('mounted host and legacy same-name view',async()=>{await login(s,true);await ordinary(s,{named:true});await entry(s)})
    await step('actual product tree, pages and file/date filters',()=>happy(s))
    await step('same-ID second scope, different product and exact denied scope',()=>selection(s))
    await step('current protocol fault cases',()=>faults(s))
    await step('same-document ABA and deferred body',()=>stale(s))
    await step('ordinary and product cross-document/restored session boundaries',()=>cross(s))
    await step('late module loading',()=>bootRace(s))
    // Recreate a neutral document after the controlled boot race. Use real
    // navigation so expire-session followed by Back returns to ordinary.
    await step('recreate neutral document',async()=>{await s.page.goto(env.info.origin+catalog);await visible(s.page,'session');await entry(s)})
    await step('current republish, withdrawn reader and server-side expiry',()=>authority(s))
    console.log('PRODUCT_VIEW_REQUESTS '+JSON.stringify({productMetadataPOSTs:s.journal.filter(r=>isProduct(r.p)).length,
      sameOriginContract:true,realReturnedCounts:{level:16,direct:12,recursive:17,instantDirect:1,instantRecursive:2,sevenDates:3},
      actualDecisions:[412,404,401],lateStatusesInjected:[401,412],unexpectedByteOrAdmin:0}))
  })
  console.log('PRODUCT_VIEW_COOKIE_B_PASS: default-off and configured HTTPS, real cookie-scoped index, tree and UTC range, expiry and recovery, three screenshots; both fixtures gracefully stopped.')
}
module.exports={run}
