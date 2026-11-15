import test from 'node:test'
import assert from 'node:assert/strict'
import {createSessionClient} from '../client.mjs'
import {createArtifactSessionClients} from '../artifacts-session.mjs'
const scope={workspace_id:'alpha',connection_id:'source',bucket_id:'bucket'}
const response=(data,status=200)=>({ok:status===200,status,text:async()=>JSON.stringify({data})})
const result={scope,quarter:'2026Q1',quarter_available:true,observed_at:'2026-01-01T00:00:00Z',catalog_revision:1,page:1,page_size:10,total:0,items:[]}
const query={scope,selected:'2026Q1',definition:{path:'',files_only:true},page:1,pageSize:10}
function fixture(){
 const calls=[],values=[response({principal_id:'admin'})];let cookie='A'.repeat(43)
 const session=createSessionClient({cookie:()=>'__Host-ms-collab-csrf='+cookie,fetcher:async(path,options)=>{calls.push({path,...options});return values.shift()}})
 return {calls,values,session,...createArtifactSessionClients(session),cookie:value=>cookie=value}
}
test('catalog POST uses actual cookie session and fresh CSRF, never bearer conversion',async()=>{
 const f=fixture();await f.session.inspect();f.values.push(response(result));await f.catalog.query(query)
 assert.equal(f.calls[1].path,'/api/workspaces/alpha/artifacts/source/bucket/query');assert.equal(f.calls[1].method,'POST');assert.equal(f.calls[1].headers['X-CSRF-Token'],'A'.repeat(43));assert.equal(f.calls[1].credentials,'same-origin');assert.equal(f.calls[1].redirect,'error');assert.ok(!('Authorization'in f.calls[1].headers));assert.ok(!('connect'in f.catalog))
 f.cookie('B'.repeat(43));f.values.push(response(result));await f.catalog.query(query);assert.equal(f.calls[2].headers['X-CSRF-Token'],'B'.repeat(43))
})
test('scope and returned scope stay exact; named views cannot fall back',async()=>{
 const f=fixture();await f.session.inspect();await assert.rejects(f.catalog.quarters({...scope,workspace_id:'alpha/../beta'}));assert.equal(f.calls.length,1)
 f.values.push(response({scope:{...scope,bucket_id:'other'},catalog_revision:1,quarters:['2026Q1']}));await assert.rejects(f.catalog.quarters(scope))
 f.values.push(response({},404));await assert.rejects(f.catalog.query({...query,view:'missing'}));assert.match(f.calls.at(-1).path,/\/views\/missing\?quarter=2026Q1/);assert.equal(f.calls.length,3)
})
test('approval keeps current revision, full body and CSRF; no automatic replay',async()=>{
 const f=fixture();await f.session.inspect();const body={candidate_digest:'a'.repeat(64),readers:['admin','reader'],confirmation:'approve_complete_candidate_to_reviewed_readers'}
 f.values.push(response({},412));await assert.rejects(f.administration.request(scope,'approve',{body,revision:2}));assert.equal(f.calls.length,2)
 assert.equal(f.calls[1].headers['If-Match'],'"2"');assert.deepEqual(JSON.parse(f.calls[1].body),body);assert.equal(f.calls[1].headers['X-CSRF-Token'],'A'.repeat(43))
 for(const action of ['download','unknown'])assert.throws(()=>f.administration.request(scope,action,{body,revision:2}))
 assert.throws(()=>f.administration.request({...scope,bucket_id:'https://other.invalid'},'status'));assert.equal(f.calls.length,2)
})
test('a missing CSRF proof rejects query and approval before network',async()=>{
 const f=fixture();await f.session.inspect();f.cookie('invalid')
 await assert.rejects(f.catalog.query(query));await assert.rejects(f.administration.request(scope,'approve',{body:{},revision:2}));assert.equal(f.calls.length,1)
})
test('shared session clearing invalidates both module clients without a cached principal',async()=>{
 const f=fixture();await f.session.inspect();f.catalog.clear();await assert.rejects(f.catalog.query(query));await assert.rejects(f.administration.request(scope,'status'));assert.equal(f.calls.length,1)
})
test('cookie response epoch rejects delayed approval after clear even when abort is ignored',async()=>{
 let resolve;const pending=new Promise(r=>resolve=r);const f=fixture();await f.session.inspect();f.values.push(pending)
 const write=f.administration.request(scope,'approve',{revision:2,body:{}});f.administration.clear();resolve(response({scope,revision:3,publication:'approved'}));await assert.rejects(write,/Stale session/)
})
