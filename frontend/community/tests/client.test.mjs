import test from 'node:test'
import assert from 'node:assert/strict'
import {scopeFrom,baseURL,createClient,validateCapabilities,validatePage,loadSnapshot,validateMutation} from '../client.mjs'
const scope=scopeFrom('alpha','source','sample/project')
const row=(number=1)=>({...scope,kind:'issue',number,title:'<img src=x onerror=alert(1)>',state:'open',updated_at:'2026-01-01T00:00:00Z',labels:['<b>plain</b>'],fingerprint:number.toString(16).padStart(64,'0'),approved:number===1})
const page=(items,offset=0,total=items.length)=>({schema:'ms-collab/community-board/v1',workspace_id:'alpha',coverage:'observed_subset',scope,revision:2,available:true,sharing:'explicit_content_approval',view:'review',sources:[{...scope,observed_at:'2026-01-02T00:00:00Z'}],offset,limit:100,total,items})
test('scope cannot add origin, query, encoded traversal or path segments',()=>{
 assert.equal(baseURL(scope),'/api/workspaces/alpha/community/source/sample/project')
 for(const args of [['../','source','a/b'],['a','https://x','b/c'],['a','c','x/y/z'],['a','c','x/%2e%2e'],['a','c','x/y?token=a']])assert.throws(()=>scopeFrom(...args))
})
test('credential is memory-only and requests are same-origin without cookies or redirects',async()=>{
 const calls=[], client=createClient({fetcher:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({data:{}})}}})
 client.connect('synthetic-token');await client.request(scope,'capabilities');await client.request(scope,'approve',{body:{fingerprints:[]},revision:2})
 assert.ok(!JSON.stringify(calls.map(c=>c.url)).includes('synthetic-token'));assert.equal(calls[0].options.headers.Authorization,'Bearer synthetic-token');assert.equal(calls[0].options.credentials,'omit');assert.equal(calls[0].options.redirect,'error');assert.equal(calls[1].options.headers['If-Match'],'"2"')
 client.clear();await assert.rejects(client.request(scope,'items'));assert.throws(()=>client.connect('a b'))
})
test('capabilities are explicit and response scope cannot be switched',()=>{
 const data={scope,revision:2,capabilities:{items:true,review:false,approve:false,refresh:false}}
 validateCapabilities(data,scope)
 for(const change of [{scope:{...scope,workspace_id:'other'}},{revision:0},{capabilities:{items:true}},{capabilities:{items:true,review:false,approve:true,refresh:false}}])assert.throws(()=>validateCapabilities({...data,...change},scope))
})
test('review requires fingerprints, consistent versions, bounded data and safe row identity',()=>{
 validatePage(page([row()]),scope,'review',2,0)
 for(const change of [{revision:3},{view:'items'},{total:10001},{offset:1},{items:[{...row(),workspace_id:'beta'}]},{items:[{...row(),fingerprint:'not-digest'}]},{items:[{...row(),kind:'issue',state:'merged'}]}])assert.throws(()=>validatePage({...page([row()]),...change},scope,'review',2,0))
})
test('all review pages load before full approval replacement and retain off-page approvals',async()=>{
 const first=Array.from({length:100},(_,i)=>row(i+1)), last={...row(101),approved:true}, calls=[]
 const snapshot=await loadSnapshot({request:async(_s,_v,{offset})=>{calls.push(offset);return offset?page([last],100,101):page(first,0,101)}},scope,'review',2)
 assert.deepEqual(calls,[0,100]);assert.equal(snapshot.items.length,101);assert.equal(snapshot.items.filter(x=>x.approved).length,2)
 await assert.rejects(loadSnapshot({request:async(_s,_v,{offset})=>offset?{...page([last],100,101),revision:3}:page(first,0,101)},scope,'review',2))
 await assert.rejects(loadSnapshot({request:async(_s,_v,{offset})=>offset?page([row(1)],100,101):page(first,0,101)},scope,'review',2))
})
test('empty snapshot and mutation results remain scoped',async()=>{
 assert.equal((await loadSnapshot({request:async()=>({scope,revision:2,available:false,items:[]})},scope,'items',2)).available,false)
 validateMutation({scope,revision:3,approved_count:1},scope,2,'approve',1)
 assert.throws(()=>validateMutation({scope,revision:3,approved_count:2},scope,2,'approve',1))
})
test('service error text is fixed and never echoes upstream private payload',async()=>{
 const client=createClient({fetcher:async()=>({ok:false,status:412,json:()=>{throw Error('must not read')}})})
 client.connect('synthetic');await assert.rejects(client.request(scope,'capabilities'),/changed elsewhere/)
})

test('abandoned snapshot stops before new page requests even if transport cancellation fails',async()=>{
 for(const abortSignal of [false,true]){
  let resolve,calls=0,current=true;const controller=new AbortController()
  const client={request:()=>{calls++;return new Promise(r=>resolve=r)}}
  const loading=loadSnapshot(client,scope,'review',2,controller.signal,()=>current)
  current=false;if(abortSignal)controller.abort()
  resolve(page(Array.from({length:100},(_,i)=>row(i+1)),0,101))
  await assert.rejects(loading,e=>e.name==='AbortError');assert.equal(calls,1)
 }
 const controller=new AbortController();controller.abort();let called=0
 await assert.rejects(loadSnapshot({request:()=>{called++}},scope,'review',2,controller.signal),e=>e.name==='AbortError');assert.equal(called,0)
})
