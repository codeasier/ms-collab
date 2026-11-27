// Handwritten metadata only. The Cookie/client tests below exercise the real session client;
// their delayed responses model scheduling, not server publication or authorization.
import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import {createSessionClient, validateBootstrap} from '../client.mjs'
import {makeProductRequest, validateProductResult, productInstantMicros, lastSevenUtcDates} from '../artifact-product-codec.mjs'
import {validateWorkspaces} from '../../workspace-admin/client.mjs'

const scope = {workspace_id:'alpha', connection_id:'source', bucket_id:'bucket'}
const route = '/api/workspaces/alpha/artifacts/source/bucket/product-views/kit'
const body = {format:1, quarter:'2026Q2', path:'pkg/', mode:'tree', page:1, page_size:10}
const obs = 'b'.repeat(64)
const t = '2026-04-02T00:00:00.000000Z'
const seen = '2026-04-12T10:00:00Z'
const request = (changes={}) => makeProductRequest(scope, 'kit', {...body,...changes})
const file = (key, modified_at=t) => ({key, name:key.slice(key.lastIndexOf('/')+1),
  parent:key.slice(0,key.lastIndexOf('/')+1), modified_at, size:7, is_dir:false, virtual:false})
function folder(key) {
  const stem=key.slice(0,-1)
  return {key,name:stem.slice(stem.lastIndexOf('/')+1),parent:stem.slice(0,stem.lastIndexOf('/')+1),
    modified_at:null,size:0,is_dir:true,virtual:true}
}
function rows(req=request(), items=[]) {
  const {quarter,path='',mode='tree',page=1,page_size=50}=req.body
  return {format:1, kind:'product-view', match:'relative-key-ascii-substring-v1',scope:{...req.scope},
    view_id:req.viewId,quarter,path,mode,page,page_size,observed_at:seen,quarter_available:true,
    catalog_revision:3,observation:obs,total:items.length,items,
    ...(req.body.relative_range?{publication_observation:obs}:{}),
    source:{kind:'stored_index',full_history_complete:false,live_provider_verified:false,download_available:false}}
}

test('product factory capability is unique, depends on artifacts, and reveals no per-view authority',()=>{
  const origin='https://app.example.invalid', base={service:'ms-collab',login_configured:false,authorization_endpoint:null}
  const validate=mod=>validateBootstrap({...base,ui_modules:mod},origin)
  assert.deepEqual(validate(['workspaces','artifacts','artifact-product-views']).ui_modules,
    ['workspaces','artifacts','artifact-product-views'])
  for(const modules of [['workspaces','artifact-product-views'],['workspaces','artifacts','artifact-product-views','artifact-product-views'],
    ['workspaces','artifacts','artifact-product-views/kit']])assert.throws(()=>validate(modules))
  assert.throws(()=>validateBootstrap({...base,ui_modules:['workspaces','artifacts'],product_views:['kit']},origin))
})

test('product request is an exact detached immutable selection; path and keyword stay literal body values',()=>{
  const s={...scope}, relative="pkg/图/%2F/%_'/a//", keyword=' \uFEFF测 '
  const b={...body,path:relative,mode:'files',keyword,recursive:false}
  const r=makeProductRequest(s,'kit',b)
  assert.deepEqual(Object.keys(r).sort(),['body','path','scope','viewId'])
  assert.equal(r.path,route)
  assert.ok([r,r.scope,r.body].every(Object.isFrozen))
  b.path='another/';b.keyword='changed';s.bucket_id='second'
  assert.deepEqual(r.scope,scope);assert.equal(r.body.path,relative);assert.equal(r.body.keyword,keyword)
  for(const path of ['a/'.repeat(31), 'x'+'é'.repeat(511)+'/'])assert.equal(request({path}).body.path,path)
  for(const keyword of ['😀'.repeat(256),'\uFEFF',' x '])assert.equal(request({mode:'files',keyword}).body.keyword,keyword)
  assert.equal(makeProductRequest(scope,'v'.repeat(128),{format:1,quarter:'2026Q2'}).body.path,undefined)
  assert.equal(request({page:1_000_000,page_size:50,if_observation:obs}).body.page,1_000_000)
  for(const bad of [{...scope,bucket_id:'a'.repeat(129)},{...scope,bucket_id:'桶'},{...scope,x:true},{workspace_id:'alpha'}])
    assert.throws(()=>makeProductRequest(bad,'kit',body))
  for(const id of ['a/b','','id\n','a'.repeat(129),'Ω'])assert.throws(()=>makeProductRequest(scope,id,body))
})

test('product request rejects unavailable modes, implicit authority, invalid scalars and malformed paths',()=>{
  for(const override of [{format:true},{format:2},{quarter:'0200Q1'},{quarter:'2026Q5'},{quarter:'2026Q2\n'},
    {mode:'all'},{mode:null},{page:true},{page:0},{page:1_000_001},{page:2},{page_size:15},{page_size:100},
    {if_observation:null},{if_observation:'B'.repeat(64)},{path:null},{path:'/a/'},{path:'a'},
    {path:'a/../'},{path:'a/./'},{path:'a\\b/'},{path:'scheme://x/'},{path:'a/\n/'},
    {path:'a/\udfff/'},{path:'a/'.repeat(32)},{path:'é'.repeat(512)+'/'},{recursive:false},{keyword:null},{end_time:t},
    {mode:'files',keyword:''},{mode:'files',keyword:'\u0085\u2003'},{mode:'files',keyword:'😀'.repeat(257)},
    {mode:'files',keyword:'\ud800'},{mode:'files',recursive:'true'},{mode:'files',start_time:null},
    {mode:'files',start_time:'2026-04-02T00:00:00.000001Z',end_time:t},
    {scope},{product_token:'kit_'},{workspace_id:'other'},{order:'asc'},{conditions:[]},{files_only:true}])
    assert.throws(()=>request(override))
})

test('Gregorian instants retain six digits, offsets and years 0001 through 0099 without local timezone coercion',()=>{
  assert.equal(productInstantMicros('1970-01-01T00:00:00Z'),0n)
  assert.equal(productInstantMicros('1969-12-31T23:59:59.999999Z'),-1n)
  assert.equal(productInstantMicros('0001-01-01T00:00:00Z'),-62135596800000000n)
  assert.equal(productInstantMicros('9999-12-31T23:59:59.999999Z'),253402300799999999n)
  assert.equal(productInstantMicros('0100-01-01T00:00:00+01:00'),productInstantMicros('0099-12-31T23:00:00Z'))
  assert.equal(productInstantMicros('2026-04-02T08:00:00.000001+08:00'),productInstantMicros(t)+1n)
  assert.equal(productInstantMicros('2000-02-28T23:30:00-01:00'),productInstantMicros('2000-02-29T00:30:00Z'))
  assert.equal(productInstantMicros('2026-04-02T00:00:00.1Z')-productInstantMicros(t),100000n)
  for(const invalid of ['0000-01-01T00:00:00Z','0001-01-01T00:00:00+00:01','9999-12-31T23:59:59-00:01',
    '1900-02-29T00:00:00Z','2026-04-31T00:00:00Z','2026-04-02T24:00:00Z','2026-04-02T15:60:00Z',
    '2026-04-02T00:00:60Z','2026-04-02T00:00:00+00:60','2026-04-02T00:00:00+24:00',
    '2026-04-02T00:00:00.1234567Z','2026-04-02T00:00:00','2026-04-02 00:00:00Z',t+'\n'])
    assert.throws(()=>productInstantMicros(invalid))
  const date=new Date('2026-04-03T00:00:01Z'), copy=date.getTime()
  assert.deepEqual(lastSevenUtcDates(date),{start_time:'2026-03-28T00:00:00Z',end_time:'2026-04-03T23:59:59.999999Z'})
  assert.deepEqual(lastSevenUtcDates(new Date('2024-01-02T23:59:59Z')),
    {start_time:'2023-12-27T00:00:00Z',end_time:'2024-01-02T23:59:59.999999Z'})
  assert.equal(date.getTime(),copy)
})

test('exact 17-field result, four source fields and every echoed coordinate match the request',()=>{
  const req=request({if_observation:obs}), result=rows(req,[folder('pkg/a/'),file('pkg/a')])
  assert.deepEqual(validateProductResult(result,req),result)
  assert.equal(Object.keys(result).length,17)
  for(const name of Object.keys(result)) {
    const missing={...result};delete missing[name];assert.throws(()=>validateProductResult(missing,req))
  }
  for(const name of Object.keys(result.source)) {
    const source={...result.source};delete source[name];assert.throws(()=>validateProductResult({...result,source},req))
  }
  for(const patch of [{format:true},{kind:'saved-view'},{match:'full-uri'},{scope:{...scope,bucket_id:'second'}},
    {scope:{...scope,extra:1}},{view_id:'sensor'},{quarter:'2026Q3'},{path:'other/'},{mode:'files'},{page:2},{page_size:20},
    {quarter_available:false},{quarter_available:0},{catalog_revision:0},{catalog_revision:Number.MAX_SAFE_INTEGER+1},
    {observation:'a'.repeat(64)},{observation:'B'.repeat(64)},{total:501},{total:-1},{total:0},{total:3},
    {items:[result.items[1],result.items[0]]},{extra:true},{observed_at:'not an ISO instant'},
    {source:{...result.source,kind:'provider'}},{source:{...result.source,download_available:true}},
    {source:{...result.source,full_history_complete:true}},{source:{...result.source,live_provider_verified:true}},
    {source:{...result.source,url:'https://never.invalid'}}])assert.throws(()=>validateProductResult({...result,...patch},req))
  const pastPage=request({page:3,if_observation:obs})
  const empty={...rows(pastPage),total:11}
  assert.deepEqual(validateProductResult(empty,pastPage),empty)
  const absent={...rows(req),quarter_available:false}
  assert.deepEqual(validateProductResult(absent,req),absent)
})

test('row shape, scalar order, true empty segments and exact files constraints reject misleading success',()=>{
  const req=request(), good=file('pkg/a')
  for(const field of Object.keys(good)) {
    const row={...good};delete row[field];assert.throws(()=>validateProductResult(rows(req,[row]),req))
  }
  const positives=[folder('pkg/a/'),file('pkg/a'),file('pkg/\uE000'),file('pkg/𐀀')]
  assert.deepEqual(validateProductResult(rows(req,positives),req).items,positives)
  const deep=request({path:'pkg/a/'})
  assert.equal(validateProductResult(rows(deep,[folder('pkg/a//')]),deep).items[0].name,'')
  for(const items of [[good,good],[good,folder('pkg/a/')],[file('pkg/𐀀'),file('pkg/\uE000')],
    [file('pkg/deep/a')],[file('pkgwide/a')],[file('pkg/../a')],[file('pkg/a.SHa256')],[file('pkg/\ud800')],
    [file('pkg/a','2026-04-13T00:00:00Z')],[file('pkg/a','2026-04-02 00:00:00+00:00')],
    [{...good,url:'download'}],[{...good,name:'b'}],[{...good,parent:''}],[{...good,virtual:true}],
    [{...good,size:true}],[{...good,size:Number.MAX_SAFE_INTEGER+1}],[{...folder('pkg/a/'),size:7}],
    [{...folder('pkg/a/'),modified_at:t}],[{...folder('pkg/a/'),virtual:false}]])
    assert.throws(()=>validateProductResult(rows(req,items),req))
  const filters={mode:'files',keyword:'KIT_',start_time:t,end_time:t}
  const direct=request(filters), recursive=request({...filters,recursive:true})
  const nested=file('pkg/a//KIT_hit'), directHit=file('pkg/KIT_hit'), sidecarName=file('pkg/KIT_hit.sha256.txt')
  for(const [r,item] of [[direct,directHit],[direct,sidecarName],[recursive,nested]])assert.deepEqual(validateProductResult(rows(r,[item]),r).items,[item])
  for(const [r,item] of [[direct,nested],[recursive,file('pkg/kit_hit')],[recursive,folder('pkg/KIT_dir/')],
    [recursive,file('pkg2/KIT_other')],[recursive,file('pkg/KIT_hit','2026-04-02T00:00:00.000001Z')],
    [recursive,file('pkg/KIT_hit','2026-04-01T23:59:59.999999Z')]])assert.throws(()=>validateProductResult(rows(r,[item]),r))
  // Bucket offset and mandatory token are server facts, absent from the DTO.
  assert.doesNotThrow(()=>validateProductResult(rows(req,[file('pkg/plain','2026-03-31T23:59:59Z')]),req))
})

const response=(data,status=200)=>({status,ok:status>=200&&status<300,text:async()=>JSON.stringify({data})})
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}}
function harness(){
  const calls=[], effects=[], queue=[]
  let csrf='A'.repeat(43)
  const session=createSessionClient({cookie:()=>'__Host-ms-collab-csrf='+csrf,onInvalid:message=>effects.push(message),
    fetcher:(path,options)=>{
      calls.push({path,options})
      if(path==='/auth/session')return Promise.resolve(response({principal_id:'reader'}))
      if(path==='/api/workspaces'&&!queue.length)return Promise.resolve(response([]))
      const next=queue.shift()
      if(!next)throw Error('Unexpected test fetch')
      return next(path,options)
    }})
  return {session,calls,effects,queue,setCookie:value=>{csrf=value},read:(opts={isCurrent:()=>true},s=scope,b=body)=>session.readArtifactProductView(s,'kit',b,opts)}
}

test('typed product transport sends only a frozen exact POST with fresh CSRF and non-exporting fetch options',async()=>{
  const h=harness();await h.session.inspect()
  const s={...scope}, b={...body}
  h.queue.push(()=>response(rows(request())))
  const pending=h.read({isCurrent:()=>true},s,b)
  b.path='changed/';s.workspace_id='another';await pending
  h.setCookie('B'.repeat(43));h.queue.push(()=>response(rows(request())))
  await h.read()
  assert.deepEqual(h.calls.map(c=>c.path),['/auth/session',route,route])
  for(const [i,letter] of [[1,'A'],[2,'B']]){
    const o=h.calls[i].options
    assert.deepEqual(JSON.parse(o.body),body)
    assert.equal(o.method,'POST');assert.equal(o.credentials,'same-origin')
    assert.equal(o.cache,'no-store');assert.equal(o.redirect,'error');assert.equal(o.referrerPolicy,'no-referrer')
    const hdr=new Headers(o.headers)
    assert.equal(hdr.get('Content-Type'),'application/json');assert.equal(hdr.get('X-CSRF-Token'),letter.repeat(43))
    assert.equal(hdr.has('Authorization'),false)
  }
  h.setCookie('not-a-proof');await assert.rejects(h.read());assert.equal(h.calls.length,3)
})

const aborted=e=>e?.name==='AbortError'
test('product owner is strict true and remains current across the first asynchronous codec load',async()=>{
  const h=harness();await h.session.inspect()
  for(const opts of [undefined,{}, {isCurrent:()=>0},{isCurrent:()=>1},{isCurrent:()=>{throw Error('private diagnostic')}},
    {isCurrent:()=>true,signal:AbortSignal.abort()}]){
    await assert.rejects(h.session.readArtifactProductView(scope,'kit',body,opts),aborted)
  }
  let current=true
  const pending=h.read({isCurrent:()=>current});current=false
  await assert.rejects(pending,aborted)
  assert.equal(h.calls.length,1);assert.equal(h.effects.length,0)
  assert.deepEqual(await h.session.request('/api/workspaces'),[])
})

test('stale product headers or fetch rejection cannot globally invalidate a newer ABA selection',async()=>{
  for(const outcome of [200,401,412,'reject']){
    const h=harness();await h.session.inspect()
    const headers=deferred(), started=deferred();let sequence=1
    h.queue.push(()=>{started.resolve();return headers.promise})
    const pending=h.read({isCurrent:()=>sequence===1});await started.promise
    sequence=2;sequence=3 // Same visible coordinates, different owner; abort deliberately ignored.
    h.queue.push(()=>response(rows(request())));await h.read({isCurrent:()=>sequence===3})
    const rejected=assert.rejects(pending,aborted)
    if(outcome==='reject')headers.reject(Error('private diagnostic'))
    else headers.resolve(response(rows(request()),outcome))
    await rejected
    assert.deepEqual(h.effects,[]);assert.deepEqual(await h.session.request('/api/workspaces'),[])
    assert.deepEqual(h.calls.map(c=>c.path),['/auth/session',route,route,'/api/workspaces'])
  }
})

test('retiring a product owner after headers discards late valid body, malformed JSON and body read rejection',async()=>{
  for(const end of ['valid','invalid','reject']){
    const h=harness();await h.session.inspect()
    const held=deferred(), reading=deferred();let current=true
    h.queue.push(()=>({ok:true,status:200,text(){reading.resolve();return held.promise}}))
    const pending=h.read({isCurrent:()=>current});await reading.promise
    current=false
    const rejected=assert.rejects(pending,aborted)
    if(end==='reject')held.reject(Error('private diagnostic'))
    else held.resolve(end==='valid'?JSON.stringify({data:rows(request())}):'private invalid JSON')
    await rejected;assert.deepEqual(h.effects,[]);assert.deepEqual(await h.session.request('/api/workspaces'),[])
    assert.equal(h.calls.length,3)
  }
})

test('clear and reinspection protect both ordinary-to-product and product-to-ordinary late 401',async()=>{
  for(const from of ['product','ordinary']){
    const h=harness();await h.session.inspect()
    const started=deferred(), held=deferred()
    h.queue.push(()=>{started.resolve();return held.promise})
    const ordinary='/api/workspaces/alpha/artifacts/source/bucket/quarters'
    const pending=from==='product'?h.read():h.session.request(ordinary)
    await started.promise;h.session.clear();await h.session.inspect()
    if(from==='product')assert.deepEqual(await h.session.request('/api/workspaces'),[])
    else {h.queue.push(()=>response(rows(request())));await h.read()}
    const rejected=assert.rejects(pending,from==='product'?aborted:/stale/i)
    held.resolve(response({},401));await rejected
    assert.deepEqual(h.effects,[]);assert.deepEqual(await h.session.request('/api/workspaces'),[])
    assert.equal(h.calls.length,5)
  }
})

test('current product 401 and ordinary 401 each invalidate once; generic requests cannot install a product owner',async()=>{
  for(const kind of ['product','ordinary']){
    const h=harness();await h.session.inspect();h.queue.push(()=>response({},401))
    const pending=kind==='product'?h.read():h.session.request('/api/workspaces',{isCurrent:()=>false})
    await assert.rejects(pending,e=>e.status===401)
    assert.equal(h.effects.length,1)
    await assert.rejects(h.session.request('/api/workspaces'));assert.equal(h.calls.length,2)
  }
})

test('no retries or raw diagnostics for denied, stale, malformed and non-200 success responses',async()=>{
  for(const status of [400,403,404,409,412,503,201,204,206]){
    const h=harness();await h.session.inspect();h.queue.push(()=>response({private:'PRIVATE-STRING'},status))
    await assert.rejects(h.read(),e=>!e.message.includes('PRIVATE-STRING')&&(status<400||e.status===status))
    assert.deepEqual(h.effects,[]);assert.deepEqual(await h.session.request('/api/workspaces'),[])
    assert.equal(h.calls.length,3)
  }
  for(const raw of ['PRIVATE-STRING',JSON.stringify({data:rows(request()),private:'PRIVATE-STRING'}),
    JSON.stringify({data:{...rows(request()),path:'elsewhere/'}}),' '.repeat(2097153)]){
    const h=harness();await h.session.inspect();h.queue.push(()=>({ok:true,status:200,text:async()=>raw}))
    await assert.rejects(h.read(),e=>!e.message.includes('PRIVATE-STRING'));assert.equal(h.effects.length,0)
    assert.equal(h.calls.length,2)
  }
})

class Element {
  constructor(tag,doc){
    this.tagName=tag.toUpperCase();this.doc=doc;this.attributes={};this.handlers={};this.children=[]
    this.textContent='';this.value='';this.hidden=false;this.disabled=false;this.checked=false
  }
  addEventListener(type,fn){(this.handlers[type]??=[]).push(fn)}
  append(...nodes){this.children.push(...nodes)}
  replaceChildren(...nodes){this.children=[...nodes];this.textContent=''}
  setAttribute(k,v){this.attributes[k]=String(v);if(k==='hidden')this.hidden=true}
  focus(){this.doc.activeElement=this}
  async fire(type,extra={}){
    const e={type,button:0,defaultPrevented:false,preventDefault(){this.defaultPrevented=true},...extra}
    await Promise.all((this.handlers[type]??[]).map(fn=>fn(e)))
  }
  text(){return [this.textContent,...this.children.map(n=>n.text())].join(' ')}
  all(tag){return [...(this.tagName===tag?[this]:[]),...this.children.flatMap(n=>n.all(tag))]}
}
function tinyDocument(){
  const els=new Map()
  const doc={createElement(tag){return new Element(tag,doc)},getElementById(id){return els.get(id)??null}}
  doc.body=doc.createElement('body');doc.activeElement=doc.body
  function install(id){els.set(id,doc.createElement('div'));return els.get(id)}
  return {doc,install}
}
const flush=()=>new Promise(resolve=>setImmediate(resolve))

// This harness uses the shipped controller plus shipped typed client/codec, but
// an intentionally tiny event/DOM surface. Real navigation and painting are in HTTPS acceptance.
async function controllerHarness(productFile=false){
  const {mountArtifactProductView}=await import('../artifact-product-view.mjs')
  const {doc,install}=tinyDocument()
  const page=fs.readFileSync(new URL('../artifact-product-view.html',import.meta.url),'utf8')
  for(const [,id] of page.matchAll(/\bid="([^"]+)"/g))install(id)
  const client=harness();await client.session.inspect()
  let download
  if(productFile){
    const {mountArtifactDownload}=await import('../artifact-download-ui.mjs')
    // In the browser the shell clones these inert template children only on explicit opt-in.
    for(const id of ['download-panel','download-key','download-digest','download-cancel','download-confirm'])assert.ok(doc.getElementById(id),id)
    const saved=Object.getOwnPropertyDescriptor(globalThis,'window')
    try{
      globalThis.window={addEventListener(){}}
      download=mountArtifactDownload({doc,session:client.session,productFile:true})
    }finally{if(saved)Object.defineProperty(globalThis,'window',saved);else delete globalThis.window}
  }
  const ctrl=mountArtifactProductView({doc,session:client.session,download,validateWorkspaces,now:()=>new Date('2026-04-03T00:00:01Z')})
  const el=id=>doc.getElementById('pv-'+id)
  const edit=async(id,value)=>{el(id).value=value;await el(id).fire('input')}
  async function select(view='kit'){
    for(const [id,v] of Object.entries({workspace:'alpha',connection:'source',bucket:'bucket',view,quarter:'2026Q2'}))await edit(id,v)
  }
  function plan(items,updates={},status=200){
    client.queue.push((path,options)=>{
      const b=JSON.parse(options.body), view=path.split('/').at(-1)
      return response({...rows(makeProductRequest(scope,view,b),items),...updates},status)
    })
  }
  return {ctrl,client,doc,el,edit,select,plan,download}
}

test('picker reads authorized published choices and opens the selected product with fresh exact authority',async()=>{
  const {ctrl,client,el,plan}=await controllerHarness()
  const change=async(id,value)=>{el('picker-'+id).value=value;await el('picker-'+id).fire('change')}
  const expectedPath='/api/workspaces/alpha/artifacts/source/bucket/product-views'
  ctrl.activate()
  const before=client.calls.length
  const workspace=(id,name)=>({id,name,kind:'product',state:'active',revision:1})
  client.queue.push(()=>response([workspace('team:omega','Other ID grammar'),workspace('alpha','Synthetic product'),
    workspace('w'.repeat(129),'Too long for an artifact scope')]))
  await el('picker-load').fire('click')
  assert.match(el('picker-workspace').text(),/Synthetic product/)
  assert.doesNotMatch(el('picker-workspace').text(),/Other ID grammar|Too long/)
  client.queue.push(()=>response({workspace_id:'alpha',items:[{scope,catalog_revision:3}],next_after:null}))
  await change('workspace','0')
  client.queue.push(()=>response({format:1,kind:'product-view-choices',scope,catalog_revision:3,
    views:[{view_id:'kit',label:'Synthetic Kit'}],quarters:['2026Q2']}))
  await change('scope','0')
  assert.match(el('picker-view').text(),/Synthetic Kit/)
  assert.equal(el('picker-open').disabled,true)
  await change('view','0');await change('quarter','0')
  assert.equal(el('picker-open').disabled,false)
  plan([folder('pkg/')])
  await el('picker-open').fire('click')
  assert.equal(el('workspace').value,'alpha')
  assert.equal(el('location').textContent,'Folder: /')
  assert.match(el('results').text(),/pkg/)
  const calls=client.calls.slice(before)
  assert.deepEqual(calls.map(x=>[x.path,x.options.method]),[
    ['/api/workspaces','GET'],['/api/workspaces/alpha/artifacts/scopes','GET'],
    [expectedPath,'GET'],[route,'POST'],
  ])
  for(const {options} of calls.slice(0,3)){
    assert.deepEqual(options.headers,{})
    assert.equal(options.body,undefined)
    assert.equal(options.cache,'no-store')
    assert.equal(options.referrerPolicy,'no-referrer')
  }
  assert.deepEqual(JSON.parse(calls.at(-1).options.body),{...body,path:''})
})

test('picker accepts the complete bounded ASCII-escaped Unicode listing',async()=>{
  const h=harness();await h.session.inspect()
  const data={format:1,kind:'product-view-choices',scope,catalog_revision:3,
    views:Array.from({length:128},(_,i)=>({view_id:String(i).padStart(3,'0')+'x'.repeat(125),label:'🧪'.repeat(80)})),
    quarters:Array.from({length:500},(_,i)=>(2025-i)+'Q1')}
  const raw=JSON.stringify({data}).replace(/[^\u0000-\u007f]/g,c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'))
  assert.ok(raw.length>65536);assert.ok(raw.length<192*1024)
  h.queue.push(()=>({status:200,ok:true,text:async()=>raw}))
  assert.deepEqual(await h.session.readArtifactProductPicker({kind:'views',scope},{isCurrent:()=>true}),data)
})

test('picker retires a stale 401 and rejected cross-bucket choices never repaint the current selection',async()=>{
  const {ctrl,client,el}=await controllerHarness()
  const change=async(id,value)=>{el('picker-'+id).value=value;await el('picker-'+id).fire('change')}
  ctrl.activate()
  const w=id=>({id,name:id,kind:'product',state:'active',revision:1})
  const directory=s=>({workspace_id:s.workspace_id,items:[{scope:s,catalog_revision:3}],next_after:null})
  const received=deferred()
  const beta={...scope,workspace_id:'beta',bucket_id:'other'}
  client.queue.push(()=>response([w('alpha'),w('beta')]))
  await el('picker-load').fire('click')
  client.queue.push(()=>received.promise)
  const old=change('workspace','0');await flush()
  client.queue.push(()=>response(directory(beta)))
  await change('workspace','1')
  assert.match(el('picker-scope').text(),/source \/ other/)
  client.queue.push(()=>response(directory(scope)))
  await change('workspace','0')
  received.resolve(response(undefined,401));await old
  assert.deepEqual(client.effects,[])
  assert.doesNotMatch(el('picker-scope').text(),/other/)
  client.queue.push(()=>response({format:1,kind:'product-view-choices',scope:beta,catalog_revision:3,
    views:[{view_id:'wrong',label:'Foreign product'}],quarters:['2026Q2']}))
  await change('scope','0')
  assert.equal(el('picker-view').disabled,true)
  assert.doesNotMatch(el('picker-view').text(),/Foreign/)
  assert.equal(el('picker-open').disabled,true)
  ctrl.clear()
  assert.equal(el('picker').hidden,true)
  assert.doesNotMatch(el('picker-scope').text(),/source/)
})

test('controller replaces edits immediately, preserves paths and submits the UTC shortcut exactly once',async()=>{
  const h=await controllerHarness(), {ctrl,client,el}=h
  ctrl.activate();await h.select();assert.equal(client.calls.length,1)
  h.plan([folder('pkg/')]);await el('setup').fire('submit')
  const child=el('results').all('BUTTON')[0]
  assert.ok(child)
  h.plan([folder('pkg/a/'),file('pkg/a')]);await child.fire('click')
  assert.deepEqual(JSON.parse(client.calls.at(-1).options.body),{...body,if_observation:obs})
  const oldButton=el('results').all('BUTTON')[0]
  await h.edit('keyword','KIT_')
  assert.equal(el('results').children.length,0);assert.equal(el('evidence').textContent,'');assert.ok(el('next').disabled)
  await oldButton.fire('click')
  assert.equal(client.calls.length,3)
  el('recursive').checked=true;await el('recursive').fire('input')
  h.plan([file('pkg/deep/KIT_result')])
  await el('range7').fire('click')
  const query=JSON.parse(client.calls.at(-1).options.body)
  assert.deepEqual(query,{...body,mode:'files',recursive:true,keyword:'KIT_',if_observation:obs,
    start_time:'2026-03-28T00:00:00Z',end_time:'2026-04-03T23:59:59.999999Z'})
  assert.equal(client.calls.length,4);assert.match(el('results').text(),/KIT_result/)
  assert.equal(el('results').all('BUTTON').length,0)
  await h.edit('start','2026-04-03T00:00:00.000002Z');await h.edit('end','2026-04-03T00:00:00.000001Z')
  await el('filters').fire('submit');assert.equal(client.calls.length,4);assert.ok(el('page-size').disabled)
  assert.equal(el('range7').attributes['aria-pressed'],'false')
  h.plan([]);await el('tree').fire('click')
  assert.deepEqual(JSON.parse(client.calls.at(-1).options.body),{...body,if_observation:obs})
  ctrl.clear();assert.ok(el('controls').hidden)
  for(const id of ['workspace','connection','bucket','view','quarter','keyword','start','end'])assert.equal(el(id).value,'')
})

test('controller treats inconsistent revision or observation time under an unchanged pin as failure, never empty',async()=>{
  for(const changed of [{catalog_revision:4},{observed_at:'2026-04-13T00:00:00Z'}]){
    const h=await controllerHarness(), {ctrl,client,el}=h
    ctrl.activate();await h.select()
    h.plan([folder('pkg/')]);await el('setup').fire('submit')
    const child=el('results').all('BUTTON')[0]
    h.plan([],changed);await child.fire('click')
    assert.ok(!el('reopen').hidden);assert.equal(el('results').children.length,0)
    assert.equal(el('page').textContent,'');assert.equal(el('evidence').textContent,'');assert.equal(client.calls.length,3)
    h.plan([],{quarter_available:false});await el('reopen').fire('click')
    const retry=JSON.parse(client.calls.at(-1).options.body)
    assert.equal(retry.page,1);assert.equal(retry.path,'');assert.ok(!Object.hasOwn(retry,'if_observation'))
    assert.match(el('status').textContent,/absent/i);assert.ok(el('reopen').hidden)
  }
})

test('controller ignores an old selection after edits, ABA and explicit re-open without relying on fetch abort',async()=>{
  const h=await controllerHarness(), {ctrl,client,el}=h;ctrl.activate();await h.select()
  const started=deferred(), held=deferred()
  client.queue.push(()=>{started.resolve();return held.promise})
  const pending=el('setup').fire('submit');await started.promise
  assert.ok(el('open').disabled)
  await h.edit('view','sensor');await h.edit('view','kit')
  assert.ok(!el('open').disabled)
  h.plan([file('KIT_new')]);await el('setup').fire('submit')
  held.resolve(response({},401));await pending
  assert.deepEqual(client.effects,[]);assert.match(el('results').text(),/KIT_new/)
  assert.deepEqual(client.calls.map(c=>c.path),['/auth/session',route,route])
  ctrl.clear();assert.equal(el('results').children.length,0)
})

test('shell awaiting the optional product module cannot revive inspection after document departure',async()=>{
  // Keep shell.start/clear/inspect and event handlers intact. Only substitute the
  // module loader with a controllable barrier, like a slow optional script load.
  const raw=fs.readFileSync(new URL('../shell.mjs',import.meta.url),'utf8')
  const load=/import\(\s*(['"])(?:\.\/|\/app\/)artifact-product-view\.mjs\1\s*\)/g
  assert.equal([...raw.matchAll(load)].length,1)
  const source=raw.replace(/^import .+\n/gm,'').replace(load,'loadProductPage()')
  for(const retire of [false,true]){
    const {doc,install}=tinyDocument()
    doc.body.dataset={page:'artifact-product-views'}
    for(const name of ['pv-return','app-artifacts-link','app-sign-in','app-sign-out','app-recheck','app-community-link',
      'app-principal','app-status','app-login-note','app-responsibility-link'])install(name)
    const listeners={}, started=deferred(), held=deferred(), seen=[]
    const bootstrap={service:'ms-collab',ui_modules:['workspaces','artifacts','artifact-product-views'],login_configured:false,authorization_endpoint:null}
    const session={bootstrap:async()=>bootstrap,clear:()=>seen.push('clear'),
      inspect:async()=>{seen.push('inspect');return {principal_id:'reader'}}}
    vm.runInNewContext(source,{document:doc,window:{addEventListener(k,fn){listeners[k]=fn}},
      location:{protocol:'https:',origin:'https://app.example.invalid'},validateBootstrap,validateWorkspaces,displayPolicyId:x=>x,
      createSessionClient:()=>session,loadProductPage:()=>{started.resolve();return held.promise}})
    await started.promise
    if(retire)listeners.pagehide()
    held.resolve({mountArtifactProductView(){seen.push('mount');return {clear(){},activate(){seen.push('activate')}}}})
    await flush()
    assert.deepEqual(seen.filter(s=>s!=='clear'),retire?[]:['mount','inspect','activate'])
    assert.equal(doc.getElementById('app-principal').textContent,retire?'Not verified':'reader')
  }
})

const availableRoute='/api/workspaces/alpha/artifact-download/source/bucket/availability'
const bytesRoute='/api/workspaces/alpha/artifact-download/source/bucket/download'
const fileKey='KIT_%2F#测试.bin', sha='a'.repeat(64)
const bound=(key=fileKey,extra={})=>({scope:{...scope},key,size:7,sha256:sha,published_revision:3,...extra})
const currentOwner=()=>({isCurrent:()=>true})
const checks=h=>h.el('results').all('BUTTON').filter(b=>b.className==='pv-check-download')
const panel=(h,name)=>h.doc.getElementById('download-'+name)

test('product file bootstrap flag requires both separate surfaces and never allows a per-view flag',()=>{
  const validate=modules=>validateBootstrap({service:'ms-collab',login_configured:false,authorization_endpoint:null,
    ui_modules:modules},'https://app.example.invalid')
  const gate='artifact-product-file-downloads', old=['workspaces','artifacts','artifact-product-views','artifact-downloads']
  assert.deepEqual(validate(old).ui_modules,old);assert.deepEqual(validate([...old,gate]).ui_modules,[...old,gate])
  for(const modules of [[...old,gate,gate],[...old,gate+'/kit'],...old.slice(1).map(absent=>[...old.filter(k=>k!==absent),gate])])
    assert.throws(()=>validate(modules))
})

test('typed product byte availability binds the literal key and exact scope, with fresh CSRF and no caller URL',async()=>{
  const h=harness(),s={...scope},options=currentOwner(),key='pkg//KIT_%2F#测试.bin'
  await assert.rejects(h.session.readArtifactDownloadAvailability(s,key,3,options));assert.equal(h.calls.length,0)
  await h.session.inspect()
  for(const letter of ['A','B']){
    h.setCookie(letter.repeat(43));h.queue.push(()=>response(bound(key)))
    const binding=await h.session.readArtifactDownloadAvailability(s,key,3,options)
    assert.deepEqual(binding,bound(key));assert.ok(Object.isFrozen(binding)&&Object.isFrozen(binding.scope))
    const {path,options:o}=h.calls.at(-1),headers=new Headers(o.headers)
    assert.equal(path,availableRoute);assert.equal(o.method,'POST');assert.deepEqual(JSON.parse(o.body),{key})
    assert.deepEqual([o.credentials,o.redirect,o.cache],['same-origin','error','no-store'])
    assert.equal(headers.get('If-Match'),'"3"');assert.equal(headers.get('Content-Type'),'application/json')
    assert.equal(headers.get('X-CSRF-Token'),letter.repeat(43));assert.equal(headers.has('Authorization'),false)
  }
  const before=h.calls.length
  for(const [candidate,k,revision,owner] of [
    [{...s,workspace_id:'other/workspace'},key,3,options],[{...s,bucket_id:'bucket',extra:true},key,3,options],
    [{workspace_id:'alpha',bucket_id:'bucket'},key,3,options],[s,'pkg/',3,options],[s,'',3,options],
    [s,'pkg/key.sHa256',3,options],[s,key,0,options],[s,key,3.1,options],[s,key,3,{}],
    [s,key,3,{isCurrent:()=>false}],[s,key,3,{isCurrent:()=>true,signal:AbortSignal.abort()}],
  ])await assert.rejects(h.session.readArtifactDownloadAvailability(candidate,k,revision,owner))
  assert.equal(h.calls.length,before)
})

test('availability rejects substituted success bindings or non-200 status without retrying or authorizing bytes',async()=>{
  const h=harness();await h.session.inspect()
  for(const wrong of [{key:'another.bin'},{scope:{...scope,workspace_id:'beta'}},{scope:{...scope,connection_id:'another'}},
    {scope:{...scope,bucket_id:'second'}},{scope:{...scope,extra:true}},{published_revision:4},
    {size:5242881},{size:-1},{sha256:'A'.repeat(64)},{source:'metadata'}]){
    h.queue.push(()=>response(bound(fileKey,wrong)))
    await assert.rejects(h.session.readArtifactDownloadAvailability(scope,fileKey,3,currentOwner()))
  }
  for(const status of [201,206,404,412]){
    h.queue.push(()=>response(bound(),status))
    await assert.rejects(h.session.readArtifactDownloadAvailability(scope,fileKey,3,currentOwner()))
  }
  assert.deepEqual(h.effects,[]);assert.deepEqual(h.calls.map(x=>x.path),['/auth/session',...Array(14).fill(availableRoute)])
})

test('typed availability ignores an old owner at headers or body but a current 401 invalidates the session exactly once',async()=>{
  for(const holdBody of [false,true]){
    const h=harness();await h.session.inspect()
    let epoch=1
    const started=deferred(),late=deferred()
    h.queue.push(()=>holdBody?{ok:true,status:200,text(){started.resolve();return late.promise}}:(started.resolve(),late.promise))
    const read=h.session.readArtifactDownloadAvailability(scope,fileKey,3,{isCurrent:()=>epoch===1})
    await started.promise;epoch=2;h.queue.push(()=>response(bound()))
    await h.session.readArtifactDownloadAvailability(scope,fileKey,3,{isCurrent:()=>epoch===2})
    const rejected=assert.rejects(read,e=>e.name==='AbortError')
    late.resolve(holdBody?JSON.stringify({data:bound()}):response({},401));await rejected
    assert.deepEqual(h.effects,[]);assert.deepEqual(await h.session.request('/api/workspaces'),[])
    h.queue.push(()=>response({},401));await assert.rejects(h.session.readArtifactDownloadAvailability(scope,fileKey,3,currentOwner()),e=>e.status===401)
    assert.equal(h.effects.length,1);const n=h.calls.length;await assert.rejects(h.session.request('/api/workspaces'));assert.equal(h.calls.length,n)
  }
})

async function openProductRows(h,items,updates={}){
  h.ctrl.activate();await h.select();h.plan(items,updates);await h.el('setup').fire('submit')
}

test('metadata alone never checks bytes; only explicit opt-in adds Check to eligible real rows, never folders or oversized files',async()=>{
  const items=[folder('KIT_same/'),file('KIT_same'),{...file('KIT_wide'),size:5242881}]
  for(const on of [false,true]){
    const h=await controllerHarness(on);await openProductRows(h,items)
    const li=h.el('results').all('LI');assert.equal(li.length,3)
    assert.deepEqual(li.map(row=>row.all('BUTTON').map(b=>b.textContent)),[['Folder: KIT_same'],on?['Check byte access']:[],[]])
    assert.deepEqual(h.client.calls.map(x=>x.path),['/auth/session',route])
    if(on){
      const old=checks(h)[0]
      h.plan([]);await h.el('results').all('BUTTON')[0].fire('click')
      assert.equal(checks(h).length,0);await old.fire('click');await panel(h,'confirm').fire('click')
      assert.deepEqual(h.client.calls.map(x=>x.path),['/auth/session',route,route])
    }
  }
})

test('file Check replays the exact filtered product page and observation, then only explicit confirmation requests bound bytes',async()=>{
  const h=await controllerHarness(true),key='pkg/'+fileKey,row=file(key)
  await openProductRows(h,[folder('pkg/')])
  h.plan([row]);await h.el('results').all('BUTTON')[0].fire('click')
  await h.edit('keyword','KIT_');h.el('recursive').checked=true;await h.el('recursive').fire('input')
  await h.edit('start',t);await h.edit('end',t)
  const firstPage=Array.from({length:10},(_,i)=>file('pkg/KIT_!'+i))
  h.plan(firstPage,{total:11});await h.el('filters').fire('submit')
  const detached=checks(h)[0];assert.ok(detached)
  h.plan([row],{total:11});await h.el('next').fire('click')
  const filtered={...body,mode:'files',page:2,keyword:'KIT_',recursive:true,start_time:t,end_time:t,if_observation:obs}
  assert.deepEqual(JSON.parse(h.client.calls.at(-1).options.body),filtered)
  const afterPage=h.client.calls.length;await detached.fire('click');assert.equal(h.client.calls.length,afterPage)
  // The original result's download_available:false remains unchanged. Only the later independent binding can arm Confirm.
  assert.equal(panel(h,'confirm').disabled,true);const before=h.client.calls.length
  h.plan([row],{total:11});h.client.queue.push(()=>response(bound(key)));await checks(h)[0].fire('click')
  assert.deepEqual(h.client.calls.slice(before).map(x=>x.path),[route,availableRoute])
  assert.deepEqual(JSON.parse(h.client.calls[before].options.body),filtered)
  const availability=h.client.calls[before+1].options
  assert.deepEqual(JSON.parse(availability.body),{key});assert.equal(new Headers(availability.headers).get('If-Match'),'"3"')
  assert.equal(panel(h,'key').textContent,key);assert.equal(panel(h,'digest').textContent,sha)
  assert.equal(panel(h,'confirm').disabled,false);assert.equal(h.client.calls.some(c=>c.path===bytesRoute),false)
  // A real byte request is a separate step; a denied reply must not become a save or a retry.
  h.client.queue.push(()=>response({},412));await panel(h,'confirm').fire('click')
  const byte=h.client.calls.at(-1);assert.equal(byte.path,bytesRoute)
  assert.deepEqual(JSON.parse(byte.options.body),{key,expected_sha256:sha});assert.equal(new Headers(byte.options.headers).get('If-Match'),'"3"')
  await panel(h,'confirm').fire('click');assert.equal(h.client.calls.length,before+3);assert.equal(panel(h,'confirm').disabled,true)
})

test('changed product tuple, Named response, changed row or empty result cannot reach an availability check',async()=>{
  const row=file(fileKey)
  for(const [items,patch] of [
    [[row],{view_id:'sensor'}],[[row],{scope:{...scope,bucket_id:'second'}}],[[row],{kind:'saved-view',view_revision:1}],
    [[row],{catalog_revision:4}],[[row],{observation:'c'.repeat(64)}],[[row],{observed_at:'2026-04-12T10:00:01Z'}],
    [[file('KIT_other')],{}],[[{...row,size:6}],{}],[[file(fileKey,'2026-04-02T00:00:00.000001Z')],{}],
    [[folder(fileKey+'/')],{}],[[],{}],
  ]){
    const h=await controllerHarness(true);await openProductRows(h,[row]);const old=checks(h)[0]
    h.plan(items,patch);await old.fire('click')
    assert.deepEqual(h.client.calls.map(x=>x.path),['/auth/session',route,route])
    assert.equal(JSON.parse(h.client.calls.at(-1).options.body).if_observation,obs)
    assert.equal(h.el('reopen').hidden,false);assert.equal(h.el('results').children.length,0)
    await old.fire('click');await panel(h,'confirm').fire('click')
    assert.equal(panel(h,'digest').textContent,'');assert.equal(h.client.calls.length,3)
  }
})

test('successful product recheck does not arm confirmation on denied byte authority or independent size mismatch',async()=>{
  const row=file(fileKey)
  for(const answer of [response({},404),response(bound(fileKey,{size:8}))]){
    const h=await controllerHarness(true);await openProductRows(h,[row]);h.plan([row]);h.client.queue.push(()=>answer)
    await checks(h)[0].fire('click');assert.equal(panel(h,'confirm').disabled,true);assert.equal(panel(h,'digest').textContent,'')
    await panel(h,'confirm').fire('click');assert.deepEqual(h.client.calls.map(x=>x.path),['/auth/session',route,route,availableRoute])
  }
})

test('retired product row and late recheck cannot open byte controls after a scope edit, reopen or departure',async()=>{
  for(const reset of ['scope','departure']){
    const h=await controllerHarness(true),row=file(fileKey);await openProductRows(h,[row]);const old=checks(h)[0]
    const started=deferred(),late=deferred();h.client.queue.push(()=>{started.resolve();return late.promise})
    const pending=old.fire('click');await started.promise
    if(reset==='scope'){
      await h.edit('bucket','second');await h.edit('bucket','bucket')
      h.plan([file('KIT_new')]);await h.el('setup').fire('submit')
    }else h.ctrl.clear()
    const n=h.client.calls.length;late.resolve(response({},401));await pending;await old.fire('click')
    await panel(h,'confirm').fire('click');assert.equal(h.client.calls.length,n);assert.deepEqual(h.client.effects,[])
    assert.equal(panel(h,'key').textContent,'');assert.equal(h.client.calls.some(c=>c.path===availableRoute||c.path===bytesRoute),false)
    if(reset==='scope')assert.match(h.el('results').text(),/KIT_new/);else assert.equal(h.el('controls').hidden,true)
  }
})

test('cancel or a different file selection wins over a held availability response even when fetch ignores abort',async()=>{
  const first=file('KIT_a'),second=file('KIT_b')
  for(const action of ['cancel','select another']){
    const h=await controllerHarness(true);await openProductRows(h,[first,second])
    h.plan([first,second]);const started=deferred(),late=deferred()
    h.client.queue.push(()=>{started.resolve();return late.promise})
    const old=checks(h)[0].fire('click');await started.promise;assert.equal(panel(h,'confirm').disabled,true)
    if(action==='cancel')await panel(h,'cancel').fire('click')
    else {
      h.plan([first,second]);h.client.queue.push(()=>response(bound(second.key)));await checks(h)[1].fire('click')
      assert.equal(panel(h,'key').textContent,second.key);assert.equal(panel(h,'confirm').disabled,false)
    }
    const n=h.client.calls.length
    late.resolve(action==='cancel'?response(bound(first.key)):response({},401));await old
    assert.deepEqual(h.client.effects,[]);assert.equal(h.client.calls.length,n)
    assert.equal(panel(h,'key').textContent,action==='cancel'?'':second.key)
    assert.equal(panel(h,'confirm').disabled,action==='cancel');assert.equal(h.client.calls.some(c=>c.path===bytesRoute),false)
    h.ctrl.clear();await panel(h,'confirm').fire('click');assert.equal(h.client.calls.length,n)
  }
})

test('opt-in product shell retires an in-flight byte request on same-tab navigation intent but not an alternate click or save link',async()=>{
  const h=await controllerHarness(true),doc=h.doc,origin='https://app.example.invalid',listeners={}
  const html=fs.readFileSync(new URL('../artifact-product-view.html',import.meta.url),'utf8')
  const shell=fs.readFileSync(new URL('../shell.mjs',import.meta.url),'utf8')
  doc.body.dataset={page:'artifact-product-views'}
  // Enumerate actual fixed navigation markup; dispatch target then window as in a bubbling native click.
  const anchor=(node,href)=>{
    node.setAttribute('href',href)
    Object.defineProperty(node,'href',{get(){return new URL(this.attributes.href,origin).href}})
    node.getAttribute=k=>node.attributes[k]??null;node.hasAttribute=k=>Object.hasOwn(node.attributes,k)
    node.closest=s=>s==='a[href]'?node:s==='[hidden], [inert]'&&node.hidden?node:null
    node.removeEventListener=(type,fn)=>{node.handlers[type]=(node.handlers[type]??[]).filter(f=>f!==fn)}
    return node
  }
  const byText=new Map()
  for(const [,attrs,text] of html.matchAll(/<a\b([^>]*)>([^<]+)<\/a>/g)){
    const id=attrs.match(/\bid="([^"]+)"/)?.[1],href=attrs.match(/\bhref="([^"]+)"/)?.[1]
    if(!href)continue
    const node=anchor(id?doc.getElementById(id):doc.createElement('a'),href)
    node.hidden=/\shidden\b/.test(attrs);byText.set(text,node)
  }
  doc.querySelectorAll=selector=>{
    assert.equal(selector,'header.app-shell > a.app-brand[href], header.app-shell > nav > a[href], #pv-return')
    return [...byText.values()]
  }
  const win={addEventListener(type,fn){(listeners[type]??=[]).push(fn)}}
  const template=doc.getElementById('pv-download-template'),fragment={}
  template.content={cloneNode(deep){assert.equal(deep,true);return fragment}};template.replaceWith=v=>assert.equal(v,fragment)
  const session={...h.client.session,bootstrap:async()=>({service:'ms-collab',login_configured:false,authorization_endpoint:null,
    ui_modules:['workspaces','artifacts','artifact-product-views','artifact-downloads','artifact-product-file-downloads']})}
  const source=shell.replace(/^import .+\n/gm,'').replace("import('./artifact-product-view.mjs')",'loadProductPage()')
  vm.runInNewContext(source,{document:doc,window:win,location:{origin,protocol:'https:'},createSessionClient:()=>session,
    validateBootstrap,validateWorkspaces,displayPolicyId:id=>id,mountArtifactDownload:({session:s,productFile})=>{assert.equal(s,session);assert.equal(productFile,true);return h.download},
    loadProductPage:async()=>({mountArtifactProductView:({download})=>{assert.equal(download,h.download);return h.ctrl}})})
  await flush()
  const bubble=(target,extra={})=>{
    const event={target,button:0,defaultPrevented:false,...extra}
    for(const fn of [...(target.handlers.click??[]),...(listeners.click??[])])fn(event)
  }
  for(const label of ['MS-COLLAB','Overview','Workspaces']){
    if(doc.getElementById('app-principal').textContent!=='reader')await doc.getElementById('app-recheck').fire('click')
    assert.equal(doc.getElementById('app-principal').textContent,'reader')
    await h.select();const row=file(fileKey);h.plan([row]);await h.el('setup').fire('submit')
    h.plan([row]);h.client.queue.push(()=>response(bound()));await checks(h)[0].fire('click')
    const started=deferred(),held=deferred();h.client.queue.push((_path,o)=>{started.resolve(o.signal);return held.promise})
    const pending=panel(h,'confirm').fire('click'),signal=await started.promise
    assert.equal(h.client.calls.at(-1).path,bytesRoute)
    try{
      const link=byText.get(label);assert.ok(link,label)
      for(const mod of [{ctrlKey:true},{button:1},{defaultPrevented:true}])bubble(link,mod)
      link.setAttribute('target','_blank');bubble(link);delete link.attributes.target
      const back=doc.getElementById('pv-return')
      back.setAttribute('download','test');bubble(back);delete back.attributes.download
      bubble(anchor(doc.createElement('a'),'blob:'+origin+'/synthetic'))
      assert.equal(signal.aborted,false);assert.equal(panel(h,'key').textContent,fileKey)
      assert.equal(doc.getElementById('app-principal').textContent,'reader')
      bubble(link)
      // No pagehide, timeout, promise flush or network response has happened here.
      assert.equal(signal.aborted,true);assert.equal(panel(h,'key').textContent,'');assert.equal(h.el('controls').hidden,true)
      assert.equal(doc.getElementById('app-principal').textContent,'Not verified')
    }finally{held.resolve(response({},401));await pending}
    assert.deepEqual(h.client.effects,[]);assert.equal(panel(h,'confirm').disabled,true)
  }
})

const presetId='audit-files.1', presetRoute=route+'/quarters/2026Q2/presets/'+presetId
const presetQuery={format:1,quarter:'2026Q2',mode:'files',path:'',recursive:true,keyword:'kit'}
const presetReply=(query=presetQuery)=>({format:1,kind:'product-preset',preset_id:presetId,query,
  result:rows(makeProductRequest(scope,'kit',query),[file('kit.bin')])})
const current=()=>({isCurrent:()=>true})
test('named filters use only the scoped exact route, fresh CSRF for writes, no body or CSRF for GET',async()=>{
  const h=harness();await h.session.inspect()
  const selected={...scope},saving={...presetQuery,if_observation:obs}
  h.queue.push(()=>response(presetReply()))
  const pending=h.session.saveArtifactProductPreset(selected,'kit',presetId,saving,current())
  selected.workspace_id='another';saving.keyword='drift';h.setCookie('C'.repeat(43))
  assert.deepEqual((await pending).query,presetQuery)
  h.queue.push(()=>response(presetReply()))
  await h.session.readArtifactProductPreset(scope,'kit','2026Q2',presetId,current())
  const [write,read]=h.calls.slice(1)
  for(const c of [write,read]){
    assert.equal(c.path,presetRoute)
    for(const [key,v] of Object.entries({mode:'same-origin',credentials:'same-origin',redirect:'error',referrerPolicy:'no-referrer',cache:'no-store'}))
      assert.equal(c.options[key],v)
  }
  assert.deepEqual(write.options.headers,{'Content-Type':'application/json','X-CSRF-Token':'C'.repeat(43)})
  assert.deepEqual(JSON.parse(write.options.body),{...presetQuery,if_observation:obs})
  assert.equal(write.options.method,'POST');assert.equal(read.options.method,'GET');assert.deepEqual(read.options.headers,{})
  assert.equal(read.options.body,undefined)
  for(const id of ['a/b','bad\n','../fake','one?scope=beta','x'.repeat(129),''])
    await assert.rejects(h.session.readArtifactProductPreset(scope,'kit','2026Q2',id,current()))
  for(const invalid of [undefined,presetQuery,{...presetQuery,mode:'tree',if_observation:obs},
    {...presetQuery,if_observation:obs,page:1},{...presetQuery,if_observation:obs,workspace_id:'another'}])
    await assert.rejects(h.session.saveArtifactProductPreset(scope,'kit',presetId,invalid,current()))
  assert.equal(h.calls.length,3)
})
test('named filters reject substituted selectors, non-file queries, stale observations and redirected save filters',async()=>{
  const h=harness();await h.session.inspect()
  for(const change of [data=>{data.preset_id='other'},data=>{data.query.quarter='2026Q3'},
    data=>{data.result.scope.workspace_id='different'},data=>{data.query.mode='tree'},data=>{data.query.if_observation=obs},
    data=>{data.query.extra=true},data=>{data.result.quarter_available=false},data=>{data.result.page_size=10},
    data=>{data.query.keyword='it'},data=>{data.result.observation='a'.repeat(64)}]){
    const data=presetReply({...presetQuery});change(data);h.queue.push(()=>response(data))
    await assert.rejects(h.session.saveArtifactProductPreset(scope,'kit',presetId,{...presetQuery,if_observation:obs},current()))
  }
})
test('in-place applied query save and date/keyword restore; no listing, unsaved edits or byte access',async()=>{
  const {ctrl,el,edit,select,plan,client}=await controllerHarness(true)
  ctrl.activate();await select();plan([]);await el('setup').fire('submit')
  await edit('preset-id',presetId);assert.equal(el('preset-open').disabled,false);assert.equal(el('preset-save').disabled,true)
  await edit('keyword','kit');el('recursive').checked=true
  plan([file('kit.bin')]);await el('filters').fire('submit');assert.equal(el('preset-save').disabled,false)
  const before=client.calls.length
  client.queue.push(()=>response(presetReply()))
  await el('preset-save').fire('click');assert.match(el('status').textContent,/Saved preset audit-files.1/)
  assert.deepEqual(JSON.parse(client.calls.at(-1).options.body),{...presetQuery,if_observation:obs})
  await edit('keyword','other');assert.equal(el('preset-save').disabled,true)
  await el('preset-save').fire('click');assert.equal(client.calls.length,before+1)
  const query={...presetQuery,start_time:t,end_time:seen}
  client.queue.push(()=>response(presetReply(query)))
  await el('preset-open').fire('click')
  assert.equal(el('keyword').value,'kit');assert.equal(el('recursive').checked,true);assert.equal(el('start').value,t)
  assert.equal(el('end').value,seen);assert.equal(el('page-size').value,'50')
  assert.match(el('status').textContent,/Reopened preset/);assert.match(el('results').text(),/kit.bin/)
  assert.equal(el('range7').attributes['aria-pressed'],'false')
  assert.deepEqual(client.calls.slice(before).map(c=>[c.path,c.options.method]),[[presetRoute,'POST'],[presetRoute,'GET']])
})
test('denied, missing and stale exact-ID reopen retire old rows and never retry; context edit discards held results',async()=>{
  for(const outcome of [403,404,412,'new-observation','scope-change']){
    const {ctrl,el,edit,select,plan,client}=await controllerHarness()
    ctrl.activate();await select();plan([file('old')]);await el('setup').fire('submit')
    await edit('preset-id',presetId);const before=client.calls.length
    const started=deferred(),held=deferred()
    client.queue.push(()=>{started.resolve();return held.promise})
    const pending=el('preset-open').fire('click');await started.promise
    if(outcome==='scope-change')await edit('workspace','beta')
    const data=presetReply();if(outcome==='new-observation')data.result.observation='c'.repeat(64)
    held.resolve(response(data,typeof outcome==='number'?outcome:200));await pending
    assert.equal(el('results').text().trim(),'');assert.equal(el('preset-id').value,'')
    assert.equal(el('browse').hidden,true);assert.equal(client.calls.length,before+1)
    assert.doesNotMatch(el('status').textContent,/Reopened preset|kit.bin|old/)
  }
})

const rolling={relative_range:'week',time_zone:'Asia/Shanghai'}
const rolledObs='c'.repeat(64)
test('relative filters require one explicit bounded IANA zone and exclude fixed time bounds and tree mode',()=>{
  for(const relative_range of ['today','week','month','30d'])for(const time_zone of ['UTC','America/Indiana/Indianapolis','Asia/Shanghai','Etc/GMT+8']){
    const query={...presetQuery,relative_range,time_zone}
    assert.deepEqual(makeProductRequest(scope,'kit',query).body,query)
  }
  for(const invalid of [{...rolling,mode:'tree'},{time_zone:'UTC'},{relative_range:'week'},
    {...rolling,relative_range:'7d'},{...rolling,start_time:t},{...rolling,end_time:t},
    ...['Localtime','+08:00','US/Eastern','../../UTC','Asia/../Shanghai','Asia/Shanghai\n','Asia/'+ 'A'.repeat(96)].map(time_zone=>({...rolling,time_zone}))])
    assert.throws(()=>makeProductRequest(scope,'kit',{...presetQuery,...invalid}))
})
test('rolling search, next page and named save bind one explicit zone and observation; reopen restores the exact criteria',async()=>{
  const {ctrl,el,edit,select,plan,client}=await controllerHarness(true)
  ctrl.activate();await select();plan([]);await el('setup').fire('submit')
  await edit('start',t);await edit('window','week')
  assert.equal(el('start').disabled,true)
  await edit('zone','Asia/Shanghai');await edit('keyword','kit');el('recursive').checked=true
  const ordered=Array.from({length:11},(_,i)=>file('kit_'+String(i).padStart(2,'0')))
  plan(ordered.slice(0,10),{observation:rolledObs,total:11});await el('filters').fire('submit')
  const query={...presetQuery,...rolling}
  assert.deepEqual(JSON.parse(client.calls.at(-1).options.body),{...query,page:1,page_size:10,if_publication:obs})
  assert.match(el('evidence').textContent,/Last week \(Asia\/Shanghai\)/)
  plan(ordered.slice(-1),{observation:rolledObs,total:11});await el('next').fire('click')
  assert.deepEqual(JSON.parse(client.calls.at(-1).options.body),{...query,page:2,page_size:10,if_observation:rolledObs,if_publication:obs})
  await edit('preset-id',presetId)
  const out=presetReply(query);out.result.observation=rolledObs
  client.queue.push(()=>response(out))
  await el('preset-save').fire('click');assert.match(el('status').textContent,/Saved preset/)
  assert.deepEqual(JSON.parse(client.calls.at(-1).options.body),{...query,if_observation:rolledObs,if_publication:obs})
  await edit('window','');plan([file('kit.bin')]);await el('filters').fire('submit')
  assert.deepEqual(JSON.parse(client.calls.at(-1).options.body),{...presetQuery,page:1,page_size:50,if_observation:obs})
  client.queue.push(()=>response(out))
  await el('preset-open').fire('click');assert.match(el('status').textContent,/Reopened preset/)
  assert.equal(el('window').value,'week');assert.equal(el('zone').value,'Asia/Shanghai')
  assert.equal(el('start').value,'');assert.equal(el('end').value,'')
  const read=client.calls.at(-1);assert.equal(read.path,presetRoute);assert.equal(read.options.method,'GET')
  assert.equal(read.options.body,undefined)
})
test('a rolling response cannot change publication; clearing after 412 restores focus only when stranded',async()=>{
  for(const moveFocus of [false,true]){
    const {ctrl,el,edit,select,plan,client,doc}=await controllerHarness()
    ctrl.activate();await select();plan([]);await el('setup').fire('submit')
    await edit('window','today');plan([file('kit.bin')],{observation:rolledObs})
    await el('filters').fire('submit');await edit('preset-id',presetId)
    const started=deferred(),held=deferred()
    client.queue.push(()=>{started.resolve();return held.promise})
    el('preset-save').focus()
    const pending=el('preset-save').fire('click');await started.promise
    if(moveFocus)el('workspace').focus()
    held.resolve(response({},412));await pending
    assert.equal(el('results').text().trim(),'');assert.equal(el('preset-id').value,'')
    assert.equal(el('window').value,'');assert.equal(el('zone').value,'UTC')
    assert.match(el('status').textContent,/rolling time window changed/)
    assert.equal(doc.activeElement,el(moveFocus?'workspace':'reopen'))
    plan([]);await el('reopen').fire('click')
    await edit('window','today');plan([],{observation:rolledObs,catalog_revision:4})
    await el('filters').fire('submit')
    assert.equal(el('browse').hidden,true)
  }
})


test('rolling typed transport rejects absent, wrong or internally reused root proofs; fixed shape unchanged',async()=>{
  const h=harness();await h.session.inspect()
  const r=makeProductRequest(scope,'kit',{...presetQuery,...rolling,if_publication:obs})
  for(const change of [
    data=>{delete data.publication_observation},
    data=>{data.publication_observation='1'.repeat(64)},
    data=>{data.observation=obs},
  ]){
    const data=rows(r,[file('kit.bin')]);data.observation=rolledObs;change(data)
    h.queue.push(()=>response(data))
    await assert.rejects(h.session.readArtifactProductView(scope,'kit',r.body,current()),/could not be verified/)
  }
  assert.throws(()=>validateProductResult({...rows(request()),publication_observation:obs},request()))
})

test('exact named recall verifies the publication fingerprint even when equal revision and time obscure a changed publication',async()=>{
  const {ctrl,el,edit,select,plan,client}=await controllerHarness()
  ctrl.activate();await select();plan([file('old.bin')]);await el('setup').fire('submit')
  await edit('preset-id',presetId)
  const other=presetReply({...presetQuery,...rolling})
  other.result.observation=rolledObs
  other.result.publication_observation='d'.repeat(64)
  client.queue.push(()=>response(other));await el('preset-open').fire('click')
  assert.match(el('status').textContent,/publication or view changed/)
  assert.equal(el('results').text().trim(),'')
  assert.equal(el('browse').hidden,true)
  assert.equal(el('preset-save').disabled,true)
  // A same-proof 200 that lies about revision is not reported as stale.
  plan([]);await el('reopen').fire('click')
  plan([],{catalog_revision:777});await el('tree').fire('click')
  assert.match(el('status').textContent,/Could not verify/)
  assert.doesNotMatch(el('status').textContent,/changed/)
})
