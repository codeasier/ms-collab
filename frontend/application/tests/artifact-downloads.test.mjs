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
 let i=0,canceled=0;const body=new ReadableStream({pull(c){if(i++<count)c.enqueue(new Uint8Array([0]));else c.close()},cancel(){canceled++}})
 await assert.rejects(receiveArtifact(context({binding:small,deadline:1,fetcher:async()=>new Response(body,{headers:head}),digest:async()=>Buffer.from(hash,'hex')})),/deadline/)
 assert.ok(i<count);assert.equal(canceled,1)
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
