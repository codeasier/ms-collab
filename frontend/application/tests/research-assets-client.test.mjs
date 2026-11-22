// Transport-only witnesses; the hosted research browser suite exercises real HTTPS.
import test from 'node:test'
import assert from 'node:assert/strict'
import {createSessionClient} from '../client.mjs'
const base='/api/workspaces/alpha/research-assets',detail=base+'/knowledge/assets/a_'+'0'.repeat(64)
const response=(data,status=200)=>({status,ok:status>=200&&status<300,text:async()=>JSON.stringify({data})})
function deferred(){let resolve;const wait=new Promise(r=>resolve=r);return {resolve,wait}}
async function fixture(get){
 const calls=[],invalid=[]
 const client=createSessionClient({onInvalid:x=>invalid.push(x),cookie:()=>{throw Error('research GET must not read CSRF')},fetcher:async(path,options)=>{
  if(path==='/auth/session')return response({principal_id:'reader'})
  calls.push([path,options]);return get(path,options)
 }})
 await client.inspect()
 return {client,calls,invalid}
}
test('only the fixed research GET surface can use the owner; current authorization errors remain active',async()=>{
 const {client,calls,invalid}=await fixture(path=>response({path},path===detail?401:200)),isCurrent=()=>true
 assert.deepEqual(await client.readResearchAssets(base,{isCurrent}),{path:base})
 assert.deepEqual(await client.readResearchAssets('/api/workspaces',{isCurrent}),{path:'/api/workspaces'})
 for(const path of ['https://x.invalid'+base,'/api/workspaces?override=1','/api/workspaces/alpha/artifacts',base+'?x=1',base+'/other/sync',base+'/other/types/','/api/workspaces/alpha%2fbeta/research-assets'])await assert.rejects(client.readResearchAssets(path,{isCurrent}))
 await assert.rejects(client.readResearchAssets(base,{isCurrent,method:'POST'}))
 await assert.rejects(client.readResearchAssets(detail,{isCurrent}),e=>e.status===401)
 assert.equal(invalid.length,1);assert.equal(calls.length,3)
 assert.equal(calls[0][1].method,'GET');assert.deepEqual(calls[0][1].headers,{})
 assert.equal(calls[0][1].credentials,'same-origin');assert.equal(calls[0][1].redirect,'error')
 await assert.rejects(client.readResearchAssets(base,{isCurrent}),/recheck/)
})
test('stale discovery 401, retired body and failed fetch cannot alter current research or sign it out',async()=>{
 const a=deferred(),b=deferred(),decoded=deferred(),started=deferred(),transport=deferred()
 const {client,invalid}=await fixture(path=>path===base?a.wait:path.endsWith('/entities')?transport.wait:path===detail?b.wait:path==='/api/workspaces/beta/research-assets'?response({},401):response({title:'new'}))
 let owner=1
 const rejected=assert.rejects(client.readResearchAssets(base,{isCurrent:()=>owner===1}),{name:'AbortError'})
 owner=2
 assert.deepEqual(await client.readResearchAssets(base+'/knowledge/types',{isCurrent:()=>owner===2}),{title:'new'})
 a.resolve(response({},401));await rejected;assert.deepEqual(invalid,[])
 const oldBody=assert.rejects(client.readResearchAssets(detail,{isCurrent:()=>owner===2}),{name:'AbortError'})
 b.resolve({status:200,ok:true,text:()=>{started.resolve();return decoded.wait}});await started.wait
 owner=3;decoded.resolve('{"data":{"old":true}}');await oldBody
 const abort=new AbortController(),oldError=assert.rejects(client.readResearchAssets(base+'/knowledge/entities',{signal:abort.signal,isCurrent:()=>true}),{name:'AbortError'})
 abort.abort();transport.resolve(Promise.reject(Error('late network failure')));await oldError
 assert.deepEqual(invalid,[])
 // Generic transport has no suppress-401 switch; other client surfaces are unchanged.
 await assert.rejects(client.request('/api/workspaces/beta/research-assets',{isCurrent:()=>false}),e=>e.status===401)
 assert.equal(invalid.length,1)
})

test('manual scope open can retire the pending workspace index before a delayed 401',async()=>{
 const index=deferred(),{client,invalid}=await fixture(path=>path==='/api/workspaces'?index.wait:response({catalog:'new'}))
 const abort=new AbortController(),workspace=assert.rejects(client.readResearchAssets('/api/workspaces',{
  signal:abort.signal,isCurrent:()=>!abort.signal.aborted
 }),{name:'AbortError'})
 abort.abort()
 assert.deepEqual(await client.readResearchAssets(base+'/knowledge/types',{isCurrent:()=>true}),{catalog:'new'})
 index.resolve(response({},401));await workspace;assert.deepEqual(invalid,[])
})
