import test from 'node:test'
import assert from 'node:assert/strict'
import {createSessionClient,validateBootstrap} from '../client.mjs'
import {createCommunitySessionClient} from '../community-client.mjs'
import {scopeFrom,loadSnapshot} from '../../community/client.mjs'
const scope=scopeFrom('alpha','source','sample/project'),csrf='A'.repeat(43)
const response=(data,status=200)=>({ok:status===200,status,text:async()=>JSON.stringify({data})})
function fixture(){const calls=[];let cookie='__Host-ms-collab-csrf='+csrf;const session=createSessionClient({cookie:()=>cookie,fetcher:async(path,options)=>{calls.push({path,options});return response(path==='/auth/session'?{principal_id:'admin'}:{ok:true})}});return {client:createCommunitySessionClient(session),session,calls,setCookie:v=>cookie=v}}
test('community session adapter requires an explicit verified session, never a bearer',async()=>{
 const {client,session,calls}=fixture();await assert.rejects(client.request(scope,'items'));await session.inspect()
 await client.request(scope,'capabilities');await client.request(scope,'review',{offset:100});await client.request(scope,'approve',{body:{fingerprints:['b'.repeat(64)]},revision:7})
 assert.equal(calls[2].path,'/api/workspaces/alpha/community/source/sample/project/review?limit=100&offset=100')
 assert.equal(calls[3].options.method,'POST');assert.equal(calls[3].options.headers['If-Match'],'"7"');assert.equal(calls[3].options.headers['X-CSRF-Token'],csrf)
 assert.deepEqual(JSON.parse(calls[3].options.body),{fingerprints:['b'.repeat(64)]})
 for(const {options} of calls){assert.equal(options.credentials,'same-origin');assert.equal(options.redirect,'error');assert.ok(!Object.hasOwn(options.headers,'Authorization'))}
 client.clear();await assert.rejects(client.request(scope,'review'));assert.equal(calls.length,4)
})
test('complete approval set validates before any write or CSRF fallback',async()=>{
 const {client,session,calls,setCookie}=fixture();await session.inspect()
 for(const body of [{fingerprints:['A'.repeat(64)]},{fingerprints:['a'.repeat(64)+'\n']},{fingerprints:['a'.repeat(64),'a'.repeat(64)]},{fingerprints:Array(201).fill('a'.repeat(64))},{fingerprints:[],scope:'other'},{}])await assert.rejects(client.request(scope,'approve',{body,revision:1}))
 for(const options of [{body:{fingerprints:[]},revision:0},{body:{fingerprints:[]},revision:1,offset:1}])await assert.rejects(client.request(scope,'approve',options))
 setCookie('');await assert.rejects(client.request(scope,'approve',{body:{fingerprints:[]},revision:1}));assert.equal(calls.length,1)
})
test('read and refresh requests cannot smuggle actions, paths or publication bodies',async()=>{
 const {client,session,calls}=fixture();await session.inspect()
 for(const action of ['delete','items?token=x','refresh/other'])await assert.rejects(client.request(scope,action))
 for(const patch of [{workspace_id:'../alpha'},{connection_id:'source?x'},{repository:'a/b/c'}])await assert.rejects(client.request({...scope,...patch},'items'))
 await assert.rejects(client.request(scope,'review',{offset:10001}));await assert.rejects(client.request(scope,'items',{body:{}}));await assert.rejects(client.request(scope,'capabilities',{offset:100}))
 await assert.rejects(client.request(scope,'refresh',{body:{token:'not allowed'},revision:1}));assert.equal(calls.length,1)
 await client.request(scope,'refresh',{body:{},revision:3});assert.equal(calls[1].options.headers['If-Match'],'"3"');assert.deepEqual(JSON.parse(calls[1].options.body),{})
})
test('401 and a completed request after logout cannot restore active authority',async()=>{
 let resolve,count=0;const session=createSessionClient({fetcher:async()=>++count===1?response({principal_id:'admin'}):new Promise(r=>resolve=r)})
 const client=createCommunitySessionClient(session);await session.inspect();const read=client.request(scope,'items');session.clear();resolve(response({items:['private']}));await assert.rejects(read,/Stale/);await assert.rejects(client.request(scope,'items'));assert.equal(count,2)
 let invalid=0;const s=createSessionClient({onInvalid:()=>invalid++,fetcher:async path=>path==='/auth/session'?response({principal_id:'admin'}):response({},401)});await s.inspect();await assert.rejects(createCommunitySessionClient(s).request(scope,'items'),e=>e.status===401);assert.equal(invalid,1)
})
test('community bootstrap is fixed configuration only and does not activate a scope',()=>{
 const value={service:'ms-collab',ui_modules:['workspaces','community'],login_configured:false,authorization_endpoint:null};assert.equal(validateBootstrap(value,'https://app.example.invalid'),value)
 assert.throws(()=>validateBootstrap({...value,ui_modules:['community']},'https://app.example.invalid'));assert.throws(()=>validateBootstrap({...value,ui_modules:['workspaces','community','community']},'https://app.example.invalid'))
})
test('session transport loads all review pages before preserving hidden approvals',async()=>{
 const rows=Array.from({length:101},(_,i)=>({...scope,kind:'issue',number:i+1,title:'Synthetic '+(i+1),state:'open',updated_at:'2026-01-01T00:00:00Z',labels:[],fingerprint:(i+1).toString(16).padStart(64,'0'),approved:[0,100].includes(i)}))
 const calls=[];const session=createSessionClient({fetcher:async(path,options)=>{calls.push({path,options});if(path==='/auth/session')return response({principal_id:'admin'});const offset=Number(new URL(path,'https://app.example.invalid').searchParams.get('offset'));return response({schema:'ms-collab/community-board/v1',workspace_id:'alpha',coverage:'observed_subset',scope,revision:2,available:true,sharing:'explicit_content_approval',view:'review',sources:[{...scope,observed_at:'2026-01-02T00:00:00Z'}],offset,limit:100,total:101,items:rows.slice(offset,offset+100)})}})
 await session.inspect();const snapshot=await loadSnapshot(createCommunitySessionClient(session),scope,'review',2);assert.equal(snapshot.items.length,101);assert.equal(snapshot.items.filter(x=>x.approved).length,2);assert.equal(calls.length,3)
})
