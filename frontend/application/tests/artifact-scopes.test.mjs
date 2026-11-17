import test from 'node:test'
import assert from 'node:assert/strict'
import {createArtifactDirectoryClient,validateArtifactScopes} from '../artifact-scope-client.mjs'
import {createSessionClient} from '../client.mjs'
const scope={workspace_id:'alpha',connection_id:'source',bucket_id:'bucket'}
const item={scope,catalog_revision:1},data={workspace_id:'alpha',items:[item],next_after:null}
const response=(data,status=200)=>({ok:status===200,status,text:async()=>JSON.stringify({data})})
test('reader accepts empty publication revision, but never candidate revision or raw metadata',()=>{
 assert.deepEqual(validateArtifactScopes(data,'alpha'),data)
 for(const extra of [{revision:2},{candidate:[]},{credential_ref:'secret'},{published:null}])assert.throws(()=>validateArtifactScopes({...data,items:[{...item,...extra}]},'alpha'))
 for(const v of [0,-1,1.5,Number.MAX_SAFE_INTEGER+1])assert.throws(()=>validateArtifactScopes({...data,items:[{...item,catalog_revision:v}]},'alpha'))
})
test('exact workspace, shape, safe IDs, ordered unique tuples and usable capability matrix',()=>{
 for(const changed of [{...data,workspace_id:'beta'},{...data,total:3},{...data,items:[item,item]},{...data,items:[{...item,scope:{...scope,bucket_id:'bucket\n'}}]}])assert.throws(()=>validateArtifactScopes(changed,'alpha'))
 for(const capabilities of [{refresh:false,review:false,approve:true},{refresh:false,review:false,approve:false},{refresh:1,review:true,approve:true}])assert.throws(()=>validateArtifactScopes({...data,items:[{scope,capabilities}]},'alpha',true))
 for(const capabilities of [{refresh:true,review:false,approve:false},{refresh:false,review:true,approve:false},{refresh:false,review:true,approve:true}])assert.equal(validateArtifactScopes({...data,items:[{scope,capabilities}]},'alpha',true).items.length,1)
})
test('pagination validates visible last cursor, length and ordering against requested cursor',()=>{
 const items=Array.from({length:25},(_,i)=>({scope:{...scope,bucket_id:`b${String(i).padStart(2,'0')}`},catalog_revision:1}))
 const page={...data,items,next_after:'source~b24'};assert.equal(validateArtifactScopes(page,'alpha').next_after,'source~b24')
 for(const changed of [{...page,next_after:'source~hidden'},{...page,items:items.slice(1)},{...page,items:[...items,item]}])assert.throws(()=>validateArtifactScopes(changed,'alpha'))
 assert.throws(()=>validateArtifactScopes(page,'alpha',false,'source~b00'))
 assert.throws(()=>validateArtifactScopes(page,'alpha',false,'invalid'))
})
test('cookie-only GET adapter, no configuration/body/CSRF, latest session invalidation and no retries',async()=>{
 const calls=[],values=[response({principal_id:'reader'}),response(data)]
 const session=createSessionClient({fetcher:async(path,options)=>{calls.push({path,options});return values.shift()},cookie:()=>''})
 await session.inspect();const directory=createArtifactDirectoryClient(session);await directory.list('alpha')
 assert.equal(calls[1].path,'/api/workspaces/alpha/artifacts/scopes');assert.equal(calls[1].options.method,'GET');assert.deepEqual(calls[1].options.headers,{});assert.equal(calls[1].options.credentials,'same-origin');assert.equal(calls[1].options.cache,'no-store')
 values.push(response({},409));await assert.rejects(directory.list('alpha',{after:'source~bucket'}));assert.match(calls.at(-1).path,/\?after=source%7Ebucket$/);assert.equal(calls.length,3)
 for(const workspace of ['alpha/../beta','alpha\n','https://other.invalid'])await assert.rejects(directory.list(workspace));assert.equal(calls.length,3)
 let release;values.push(new Promise(r=>release=r));const pending=directory.list('alpha');session.clear();release(response(data));await assert.rejects(pending,/Stale session/)
})
test('admin and reader endpoints are distinct, configured by trusted page not response',async()=>{
 const calls=[],session={request:async path=>{calls.push(path);return {...data,items:[]}}}
 await createArtifactDirectoryClient(session,true).list('alpha');assert.deepEqual(calls,['/api/workspaces/alpha/artifact-admin/scopes'])
})
