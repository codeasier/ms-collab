import test from 'node:test'
import assert from 'node:assert/strict'
import {createSessionClient} from '../client.mjs'
import {createGuiSmokeSessionClient} from '../gui-smoke-session.mjs'
import {ViewController} from '../../gui-smoke/client.mjs'
import {scope,options,list,summary,detail,deferred} from '../../gui-smoke/tests/fixture.mjs'
const reply=data=>({ok:true,status:200,text:async()=>JSON.stringify({data})})
test('GUI adapter uses only the shared inspected cookie transport',async()=>{
 const calls=[];const session=createSessionClient({fetcher:async(path,opts)=>{calls.push({path,opts});return reply(path==='/auth/session'?{principal_id:'alice'}:path.includes('/summary?')?summary():path.includes('/tasks/task-1?')?detail():list())}})
 const client=createGuiSmokeSessionClient(session);await assert.rejects(()=>client.list(scope,options));assert.equal(calls.length,0)
 await session.inspect();await client.list(scope,options);await client.summary(scope,1);await client.detail(scope,'task-1',1)
 assert.deepEqual(Object.keys(client),['list','summary','detail'])
 for(const {path,opts}of calls){assert.equal(opts.credentials,'same-origin');assert.equal(opts.redirect,'error');assert.equal(opts.cache,'no-store');assert.ok(!Object.keys(opts.headers).some(k=>k.toLowerCase()==='authorization'));assert.ok(path.startsWith('/'))}
 session.clear();await assert.rejects(()=>client.detail(scope,'task-1',1))
})
test('malformed identifiers and revisions fail before network access',async()=>{
 let calls=0;const client=createGuiSmokeSessionClient({request:async()=>{calls++;return list()}})
 for(const value of ['../alpha','alpha\n','a/b','https://other.invalid'])for(const key of Object.keys(scope))await assert.rejects(()=>client.list({...scope,[key]:value},options))
 for(const v of [undefined,null,-1,1.1,'1']){await assert.rejects(()=>client.summary(scope,v));await assert.rejects(()=>client.detail(scope,'task-1',v))}
 await assert.rejects(()=>client.detail(scope,'task-1\n',1));assert.equal(calls,0)
})
test('wrong scope, revision and execution capabilities remain rejected',async()=>{
 for(const mutate of [x=>x.scope.workspace_id='beta',x=>x.source.revision=2,x=>x.capabilities.submit=true,x=>x.total=3]){
  const value=list();mutate(value);const client=createGuiSmokeSessionClient({request:async()=>value});await assert.rejects(()=>client.list(scope,{...options,revision:1}))
 }
})
test('late old-scope response never starts summary or restores content',async()=>{
 const old=deferred(),calls=[],beta={...scope,workspace_id:'beta'}
 const client=createGuiSmokeSessionClient({request:async path=>{calls.push(path);return path.includes('/alpha/')?old.promise:path.includes('/summary?')?summary(beta):list(beta)}})
 const view=new ViewController(client,()=>{}),a=view.load(scope);await view.load(beta);old.resolve(list());await a
 assert.equal(view.view.scope.workspace_id,'beta');assert.equal(calls.filter(x=>x.includes('/alpha/')&&x.includes('/summary')).length,0)
 view.clear();assert.equal(view.view,null)
})
