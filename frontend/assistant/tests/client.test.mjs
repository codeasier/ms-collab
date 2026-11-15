import test from 'node:test'
import assert from 'node:assert/strict'
import {createAssistantSessionClient,identifier,validateMetadata,validateList,validateEvents} from '../client.mjs'
import {createSessionClient} from '../../application/client.mjs'
const metadata=(patch={})=>({workspace_id:'alpha',id:'one',title:'<img src=x>',state:'active',revision:2,created_at:'2026-01-01T00:00:00+00:00',updated_at:'2026-01-02T00:00:00Z',event_count:105,capabilities:{events:true,archive:true,restore:false,execute:false,resume:false,export:false},...patch})
const page=(after=0,patch={})=>({workspace_id:'alpha',conversation_id:'one',revision:2,total:105,after,limit:50,next_after:after+50<105?after+50:null,has_more:after+50<105,history_policy:'stored_display_only;no_replay',execution_enabled:false,items:Array.from({length:Math.min(50,105-after)},(_,i)=>({sequence:after+i+1,kind:'assistant_message',text:'<script>literal</script>'})),...patch})
const response=(data,status=200)=>({ok:status===200,status,text:async()=>JSON.stringify({data})})
test('literal metadata and event projections reject expanded authority or unknown fields',()=>{
 assert.equal(validateMetadata(metadata(),'alpha').title,'<img src=x>')
 for(const patch of [{workspace_id:'beta'},{id:'../x'},{revision:0},{event_count:2001},{title:'x'.repeat(801)},{raw:'private'},{capabilities:{...metadata().capabilities,execute:true}},{capabilities:{...metadata().capabilities,restore:true}}])assert.throws(()=>validateMetadata(metadata(patch),'alpha'))
 assert.equal(validateEvents(page(),metadata(),0).items.length,50)
 for(const patch of [{workspace_id:'beta'},{conversation_id:'other'},{revision:3},{total:106},{after:1},{limit:100},{next_after:99},{has_more:false},{execution_enabled:true},{history_policy:'live'},{items:[{sequence:1,kind:'tool',text:'',tool_name:'x',status:'running'}]}])assert.throws(()=>validateEvents(page(0,patch),metadata(),0))
})
test('fixed revision pagination validates every position without overlapping pages',()=>{
 assert.equal(validateEvents(page(50),metadata(),50).next_after,100);assert.equal(validateEvents(page(100),metadata(),100).items.length,5)
 const wrong=page(50);wrong.items[2].sequence=50;assert.throws(()=>validateEvents(wrong,metadata(),50))
 const tool=page(100);tool.items[0]={sequence:101,kind:'tool',text:'',tool_name:'<b>literal</b>',status:'completed'};assert.equal(validateEvents(tool,metadata(),100).items[0].tool_name,'<b>literal</b>')
 tool.items[0].raw_output='never';assert.throws(()=>validateEvents(tool,metadata(),100))
})
test('owner listing is bounded, exact state, unique and contains no identity selector',()=>{
 const value={workspace_id:'alpha',state:'active',items:[metadata()],execution_enabled:false,history_policy:'stored_display_only;no_replay'}
 assert.equal(validateList(value,'alpha','active'),value)
 for(const patch of [{owner_id:'other'},{state:'archived'},{items:[metadata(),metadata()]},{items:Array(101).fill(metadata())},{execution_enabled:true}])assert.throws(()=>validateList({...value,...patch},'alpha','active'))
 for(const id of ['alpha\n','alpha\r','../x','a/b','a?b','a#b','a b','a%2fb',''])assert.throws(()=>identifier(id))
})
test('cookie-only client uses exact read routes, current CSRF and If-Match, never bearer',async()=>{
 const calls=[];let csrf='A'.repeat(43)
 const session=createSessionClient({cookie:()=>'__Host-ms-collab-csrf='+csrf,fetcher:async(path,options)=>{calls.push({path,options});if(path==='/auth/session')return response({principal_id:'owner'});if(options.method==='PATCH')return response(metadata({state:'archived',revision:3,capabilities:{...metadata().capabilities,archive:false,restore:true}}));return response(page(50))}})
 const client=createAssistantSessionClient(session);await assert.rejects(client.events(metadata(),50),/recheck/);await session.inspect();await client.events(metadata(),50);csrf='B'.repeat(43);await client.lifecycle(metadata(),'archived')
 assert.equal(calls[1].path,'/api/workspaces/alpha/assistant/conversations/one/events?after=50&limit=50&revision=2')
 assert.equal(calls[2].options.headers['X-CSRF-Token'],csrf);assert.equal(calls[2].options.headers['If-Match'],'"2"');assert.equal(calls[2].options.body,'{"state":"archived"}')
 for(const {options} of calls){assert.equal(options.credentials,'same-origin');assert.equal(options.headers.Authorization,undefined);assert.equal(options.redirect,'error')}
})
test('lifecycle is capability gated and rejects incomplete or mismatched mutation results',async()=>{
 let calls=0,result=metadata({state:'archived',revision:3,capabilities:{...metadata().capabilities,archive:false,restore:true}})
 const client=createAssistantSessionClient({request:async()=>{calls++;return result}})
 await assert.rejects(client.lifecycle(metadata({capabilities:{...metadata().capabilities,archive:false}}),'archived'));await assert.rejects(client.lifecycle(metadata(),'active'));assert.equal(calls,0)
 await client.lifecycle(metadata(),'archived')
 for(const patch of [{revision:4},{event_count:99},{title:'changed'},{state:'active'}]){result={...result,...patch};await assert.rejects(client.lifecycle(metadata(),'archived'))}
})
test('shared session invalidation rejects late history and mutation responses',async()=>{
 let resolve;const session=createSessionClient({cookie:()=>'__Host-ms-collab-csrf='+'A'.repeat(43),fetcher:async path=>path==='/auth/session'?response({principal_id:'owner'}):new Promise(r=>resolve=r)})
 await session.inspect();const client=createAssistantSessionClient(session),read=client.metadata('alpha','one');session.clear();resolve(response(metadata()));await assert.rejects(read,/Stale session/)
})
