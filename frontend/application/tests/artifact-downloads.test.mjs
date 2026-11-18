import test from 'node:test'
import assert from 'node:assert/strict'
import {createHash,webcrypto} from 'node:crypto'
import {execFileSync} from 'node:child_process'
import {createSessionClient} from '../client.mjs'
import {mountArtifactDownload} from '../artifact-download-ui.mjs'
import {downloadName,receiveArtifact,validateDownloadBinding} from '../artifact-downloads.mjs'
const data=new TextEncoder().encode('synthetic bytes'),hash=createHash('sha256').update(data).digest('hex')
const binding={scope:{workspace_id:'alpha',connection_id:'source',bucket_id:'bucket'},key:'builds/<svg>.bin',size:data.length,sha256:hash,published_revision:3}
const headers={'Content-Type':'application/octet-stream','Content-Length':String(data.length),'Content-Disposition':`attachment; filename="${downloadName(binding.key)}"`,'X-Artifact-SHA256':hash}
const reply=(bytes=data,extra={})=>new Response(bytes,{status:200,headers:{...headers,...extra}})
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve}}
const flush=()=>new Promise(r=>setImmediate(r))
function fixture(fetcher){const calls=[];const session=createSessionClient({cookie:()=>'__Host-ms-collab-csrf='+'A'.repeat(43),fetcher:async(path,options)=>{calls.push({path,options});return path==='/auth/session'?new Response(JSON.stringify({data:{principal_id:'admin'}})):fetcher(path,options)}});return {session,calls}}
const context=overrides=>({binding,fetcher:async()=>reply(),csrf:()=>'A'.repeat(43),guard:()=>{},onUnauthorized:()=>{},pending:new Set(),digest:bytes=>webcrypto.subtle.digest('SHA-256',bytes),...overrides})
test('actual session client sends exact byte precondition and fresh CSRF, never bearer or URL',async()=>{
 const f=fixture(async()=>reply());await f.session.inspect();const r=await f.session.downloadArtifact(binding)
 assert.deepEqual(r.bytes,data);assert.equal(r.name,'artifact-_svg_.bin')
 const {path,options}=f.calls[1];assert.equal(path,'/api/workspaces/alpha/artifact-download/source/bucket/download');assert.equal(options.headers['If-Match'],'"3"');assert.equal(options.headers['X-CSRF-Token'],'A'.repeat(43));assert.equal(options.credentials,'same-origin');assert.equal(options.redirect,'error');assert.ok(!options.headers.Authorization);assert.deepEqual(JSON.parse(options.body),{key:binding.key,expected_sha256:hash})
})
test('invalid scope/binding or uninspected session sends no byte request',async()=>{
 const f=fixture(async()=>reply());await assert.rejects(f.session.downloadArtifact(binding));await f.session.inspect()
 for(const value of [{...binding,size:5242881},{...binding,sha256:'A'.repeat(64)},{...binding,published_revision:0},{...binding,scope:{...binding.scope,bucket_id:'http://evil'}}])await assert.rejects(f.session.downloadArtifact(value))
 assert.equal(f.calls.length,1);assert.throws(()=>validateDownloadBinding(binding,{scope:binding.scope,key:'other'}))
})
test('header mismatches cancel unread response before accumulating bytes',async()=>{
 for(const changed of [{'Content-Length':'99999999'},{'Content-Type':'text/html'},{'Content-Disposition':'attachment; filename="../../bad"'},{'X-Artifact-SHA256':'0'.repeat(64)},{'Content-Encoding':'gzip'}]){
  let canceled=0;const body=new ReadableStream({cancel(){canceled++}}),response=reply(body,changed)
  await assert.rejects(receiveArtifact(context({fetcher:async()=>response})),/headers/);assert.equal(canceled,1)
 }
})
test('truncated, excessive and wrong-hash streams are rejected, never returned for saving',async()=>{
 await assert.rejects(receiveArtifact(context({fetcher:async()=>reply(data.slice(1))})),/Incomplete/)
 await assert.rejects(receiveArtifact(context({fetcher:async()=>reply(new Uint8Array(data.length+1))})),/length/)
 await assert.rejects(receiveArtifact(context({fetcher:async()=>reply(new Uint8Array(data.length))})),/checksum/)
})
test('clear during fetch cancels even a late body when transport ignores AbortSignal',async()=>{
 const late=deferred();let canceled=0;const f=fixture(()=>late.promise);await f.session.inspect()
 const pending=f.session.downloadArtifact(binding);await flush();f.session.clear();await assert.rejects(pending)
 late.resolve(reply(new ReadableStream({cancel(){canceled++}})));await flush();assert.equal(canceled,1)
})
test('clear during a pending read cancels the stream and rejects immediately',async()=>{
 let canceled=0;const f=fixture(async()=>reply(new ReadableStream({cancel(){canceled++}})));await f.session.inspect()
 const pending=f.session.downloadArtifact(binding);await flush();f.session.clear();await assert.rejects(pending);assert.equal(canceled,1)
})
test('current 401 clears session; old late 401 cannot clear a rechecked session',async()=>{
 const f=fixture(async()=>new Response('{}',{status:401}));await f.session.inspect();await assert.rejects(f.session.downloadArtifact(binding));await assert.rejects(f.session.downloadArtifact(binding),/Stale session/)
 const late=deferred(),g=fixture(()=>late.promise);await g.session.inspect();const old=g.session.downloadArtifact(binding);await flush();g.session.clear();await g.session.inspect();late.resolve(new Response('{}',{status:401}));await assert.rejects(old);assert.equal((await g.session.inspect()).principal_id,'admin')
})
test('cancel and deadline cover asynchronous digest; late digest cannot return bytes',async()=>{
 const digest=deferred(),controller=new AbortController();let started=false
 const r=receiveArtifact(context({signal:controller.signal,digest:()=>{started=true;return digest.promise}}));await flush();assert.ok(started);controller.abort();await assert.rejects(r);digest.resolve(await webcrypto.subtle.digest('SHA-256',data));await flush()
 await assert.rejects(receiveArtifact(context({deadline:10,digest:()=>new Promise(()=>{})})),/deadline/)
})
test('deadline includes ignored-abort fetch and releases its later body',async()=>{
 const late=deferred();let canceled=0;await assert.rejects(receiveArtifact(context({deadline:10,fetcher:()=>late.promise})),/deadline/)
 late.resolve(reply(new ReadableStream({cancel(){canceled++}})));await flush();assert.equal(canceled,1)
})

test('monotonic deadline rejects timer-starving ready reads with one bounded output buffer',async()=>{
 const count=20000,small={...binding,size:count},head={...headers,'Content-Length':String(count)}
 const saved=globalThis.performance;let now=0,i=0,canceled=0
 try{
  globalThis.performance={now:()=>now}
  const body=new ReadableStream({pull(c){if(++i===100)now=2;if(i<=count)c.enqueue(new Uint8Array([0]));else c.close()},cancel(){canceled++}})
  await assert.rejects(receiveArtifact(context({binding:small,deadline:1,fetcher:async()=>new Response(body,{headers:head}),digest:async()=>Buffer.from(hash,'hex')})),/deadline/)
  assert.ok(i>1&&i<count);assert.equal(canceled,1)
 }finally{globalThis.performance=saved}
})
test('Unicode attachment names match actual Python stream headers by code point',()=>{
 const keys=['rocket-🚀.bin','many-😀😀.bin','é.txt','<svg>.bin','x'.repeat(100)+'.bin']
 const program=`import json,sys
from types import SimpleNamespace
from packages.artifact_download.service import DownloadStream
result=[]
for key in json.loads(sys.argv[1]):
 s=object.__new__(DownloadStream);s.binding=SimpleNamespace(key=key,size=0,sha256='0'*64)
 result.append(dict(s.headers)['Content-Disposition'])
print(json.dumps(result))`
 const expected=JSON.parse(execFileSync('python3',['-c',program,JSON.stringify(keys)],{encoding:'utf8'}))
 assert.deepEqual(keys.map(key=>`attachment; filename="${downloadName(key)}"`),expected)
})

test('actual module generation stops a late read when even AbortController is ineffective',async()=>{
 const saved={document:globalThis.document,window:globalThis.window,AbortController:globalThis.AbortController}
 const elements=new Map(),late=deferred();let reads=0,canceled=0
 const element=id=>{if(!elements.has(id))elements.set(id,{textContent:'',hidden:true,disabled:true,handlers:{},addEventListener(event,fn){this.handlers[event]=fn}});return elements.get(id)}
 try{
  globalThis.document={getElementById:element};globalThis.window={addEventListener(){}}
  globalThis.AbortController=class{constructor(){this.signal=new saved.AbortController().signal}abort(){}}
  const session=createSessionClient({cookie:()=>'__Host-ms-collab-csrf='+'A'.repeat(43),fetcher:async path=>{
   if(path==='/auth/session')return new Response(JSON.stringify({data:{principal_id:'admin'}}))
   if(path.endsWith('/availability'))return new Response(JSON.stringify({data:binding}))
   return {ok:true,status:200,headers:new Headers(headers),body:{getReader(){return {read(){reads++;return late.promise},cancel(){canceled++;return Promise.resolve()},releaseLock(){}}}}}
  }})
  await session.inspect();const ui=mountArtifactDownload({session});await ui.select({scope:binding.scope,key:binding.key,revision:3,size:data.length})
  const transfer=element('download-confirm').handlers.click();await flush();assert.equal(reads,1);ui.clear();late.resolve({done:false,value:data});await transfer
  assert.equal(reads,1);assert.equal(canceled,1);assert.equal(element('download-panel').hidden,true);assert.equal(element('download-key').textContent,'')
 }finally{Object.assign(globalThis,saved)}
})

// Supply the known, legacy wire name separately; don't use the save-name implementation as the oracle.
const attachment=name=>`attachment; filename="${name}"`
const boundReply=(wireName,bytes=data)=>reply(bytes,{'Content-Disposition':attachment(wireName)})
function readBoundKey(key,respond,extra={}){
 return receiveArtifact(context({...extra,binding:{...binding,key},fetcher:async(path,options)=>{
  assert.equal(path,'/api/workspaces/alpha/artifact-download/source/bucket/download')
  assert.equal(options.method,'POST');assert.equal(options.headers['If-Match'],'"3"')
  assert.deepEqual(JSON.parse(options.body),{key,expected_sha256:hash})
  return respond()
 }}))
}

test('verified wheel saves preserve the exact final key segment including version, build and compressed tags',async()=>{
 for(const leaf of [
  'example-1.0-py2.py3-none-any.whl',
  'example_tools-1.2rc3.post4.dev5-4build-cp311-abi3-win_amd64.whl',
  'demo-1.0-cp311-cp311-manylinux2014_x86_64.manylinux_2_17_x86_64.whl',
  'demo-1.0-cp313-cp313t-macosx_11_0_arm64.whl',
 ]){
  const result=await readBoundKey('release/deep/'+leaf,()=>boundReply('artifact-'+leaf))
  assert.equal(result.name,leaf);assert.deepEqual(result.bytes,data);assert.equal(result.sha256,hash)
 }
})

test('wheel lookalikes, Unicode punctuation and reserved distribution names keep the legacy neutral name',async()=>{
 for(const [leaf,wireLeaf=leaf] of [
  ['demo.whl'],['demo-1.0-py3-none-any.WHL'],['demo-1.0-py3-none-any.whl.txt'],
  ['demo-1.0-py3-none-any.exe.whl'],['demo-1.0-py3-none-any.whl.exe'],
  ['demo__tools-1.0-py3-none-any.whl'],['Demo-1.0-py3-none-any.whl'],['-1.0-py3-none-any.whl'],
  ['COM1-1.0-py3-none-any.whl'],['com1-1.0-py3-none-any.whl'],['nul-1.0-py3-none-any.whl'],
  ['demo-1.0-py3-none-any．whl','demo-1.0-py3-none-any_whl'],
  ['demo–tools-1.0-py3-none-any.whl','demo_tools-1.0-py3-none-any.whl'],
  ['dir\\demo-1.0-py3-none-any.whl','dir_demo-1.0-py3-none-any.whl'],
 ]){
  const neutral='artifact-'+wireLeaf
  assert.equal((await readBoundKey('objects/'+leaf,()=>boundReply(neutral))).name,neutral,leaf)
 }
})

test('unsupported wheel version, build and incomplete or unknown tag fields keep the neutral name',async()=>{
 for(const [leaf,wireLeaf=leaf] of [
  ['demo-1!2-py3-none-any.whl','demo-1_2-py3-none-any.whl'],
  ['demo-1.0+local-py3-none-any.whl','demo-1.0_local-py3-none-any.whl'],
  ['demo-01.2-py3-none-any.whl'],['demo-1.2.dev5.post4-py3-none-any.whl'],
  ['demo-1.2-build4-py3-none-any.whl'],['demo-1.2--py3-none-any.whl'],
  ['demo-1.2-py3--any.whl'],['demo-1.2-py3..py2-none-any.whl'],
  ['demo-1.2-py3.py2-none-any.whl'],['demo-1.2-py3.py3-none-any.whl'],
  ['demo-1.0-cp311-cp311-manylinux_2_17_x86_64.manylinux2014_x86_64.whl'],
  ['demo-1.2-js3-none-any.whl'],['demo-1.2-py3-none.unknown-any.whl'],
  ['demo-1.2-cp311-cp311-win_amd64.unknown.whl'],['demo-1.2-py3-none-manylinux_2_x86_64.whl'],
 ]){
  const neutral='artifact-'+wireLeaf
  assert.equal((await readBoundKey('objects/'+leaf,()=>boundReply(neutral))).name,neutral,leaf)
 }
})

test('wheel save length uses the full ASCII basename through 200, while the wire name still stops at 80',async()=>{
 const suffix='-1.0-py3-none-any.whl'
 for(const length of [80,81,200,201]){
  const leaf='s'.repeat(length-suffix.length)+suffix,neutral='artifact-'+leaf.slice(0,80)
  const result=await readBoundKey('release/'+leaf,()=>boundReply(neutral))
  assert.equal(result.name,length<=200?leaf:neutral,`basename length ${length}`)
 }
})

test('overlapping downloads with identical first 80 characters return the wheel name of their own bound key',async()=>{
 const prefix='s'.repeat(80),neutral='artifact-'+prefix,first=prefix+'-1.0-py3-none-any.whl',second=prefix+'-2.0-py3-none-any.whl'
 const hold=deferred();let waiting=false
 const pending=readBoundKey('older/'+first,async()=>{waiting=true;await hold.promise;return boundReply(neutral)})
 try{
  await flush();assert.equal(waiting,true)
  assert.equal((await readBoundKey('newer/'+second,()=>boundReply(neutral))).name,second)
 }finally{hold.resolve()}
 assert.equal((await pending).name,first)
})

test('wheel transfers reject non-neutral, overlong or extended Content-Disposition before opening the stream',async()=>{
 const prefix='s'.repeat(80),leaf=prefix+'-1.0-py3-none-any.whl',neutral='artifact-'+prefix
 for(const disposition of [
  attachment(leaf),attachment('artifact-'+leaf),attachment('artifact-other-1.0-py3-none-any.whl'),
  `attachment; filename*=UTF-8''${leaf}`,attachment(neutral)+`; filename*=UTF-8''${leaf}`,
 ]){
  let opened=0,canceled=0,digests=0
  const response={ok:true,status:200,headers:new Headers({...headers,'Content-Disposition':disposition}),
   body:{getReader(){opened++;throw Error('unexpected reader')},async cancel(){canceled++}}}
  await assert.rejects(readBoundKey('release/'+leaf,()=>response,{digest:async()=>{digests++;throw Error('unexpected digest')}}),/Invalid download headers/)
  assert.deepEqual([opened,digests,canceled],[0,0,1],disposition)
 }
})

test('a wheel name is delivered only after the complete received bytes pass the independent SHA-256 check',async()=>{
 const leaf='demo-1.2-py3-none-any.whl',key='release/'+leaf,neutral='artifact-'+leaf
 const hold=deferred();let started=false,settled=false
 const pending=readBoundKey(key,()=>boundReply(neutral),{digest:async bytes=>{
  assert.deepEqual(bytes,data);started=true;await hold.promise;return webcrypto.subtle.digest('SHA-256',bytes)
 }})
 pending.then(()=>{settled=true},()=>{settled=true})
 try{await flush();assert.equal(started,true);assert.equal(settled,false)}finally{hold.resolve()}
 const ok=await pending;assert.equal(ok.name,leaf);assert.equal(ok.sha256,hash);assert.deepEqual(ok.bytes,data)
 const corrupt=data.slice();corrupt[0]^=1
 await assert.rejects(readBoundKey(key,()=>boundReply(neutral,corrupt)),/checksum/)
})
