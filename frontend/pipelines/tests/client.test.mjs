import test from 'node:test'
import assert from 'node:assert/strict'
import {scopeFrom,baseURL,createClient,validateList,validateDetail,validateDurations,loadSnapshot} from '../client.mjs'
const scope=scopeFrom('alpha','ci','build'),source={kind:'stored_snapshot',revision:2,full_history_complete:false,live_provider_verified:false}
const run={id:'run-1',status:'failure',observed_at:'2026-01-01T12:00:00Z',stage_count:1,job_count:1,details_available:true}
const list=()=>({scope,source,total:1,offset:0,limit:100,items:[{...run}]})
const detail=()=>({scope,source,run:{...scope,id:'run-1',status:'failure',observed_at:run.observed_at,stages:[{name:'<b>Checks</b>',jobs:[{id:'unit',name:'<img src=x>',status:'success',started_at:'2026-01-01T10:00:00Z',finished_at:'2026-01-01T10:00:12Z'}]}]}})
const report=()=>({scope,source,coverage:{source:'supplied_snapshots',full_history_complete:false,limit:100,supplied_runs:1,nonterminal_or_unknown_runs:0,observed_terminal_runs:1,selected_runs:1,runs_with_details:1,runs_with_samples:1,missing_details:0,malformed_entries:0,excluded_jobs:0,invalid_duration_jobs:0,ambiguous_jobs:0,selection_truncated:false},groups:[{key:'["Checks", "Unit"]',stage_name:'Checks',job_name:'Unit',count:1,average_seconds:12,min_seconds:12,max_seconds:12,latest_seconds:12,samples:[{run_id:'run-1',run_status:'failure',job_id:'unit',observed_at:run.observed_at,started_at:'2026-01-01T10:00:00Z',finished_at:'2026-01-01T10:00:12Z',duration_seconds:12}]}]})
test('scope cannot inject URL, query or traversal',()=>{assert.equal(baseURL(scope),'/api/workspaces/alpha/pipelines/ci/build');for(const values of [['../','ci','build'],['alpha','https://evil','build'],['alpha','ci','x?token=y'],['alpha','ci','x/y'],['alpha','ci','%2e%2e']])assert.throws(()=>scopeFrom(...values))})
test('client only sends scoped GET, no cookies/redirects/storage authority',async()=>{
 const calls=[],client=createClient({fetcher:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({data:{}})}}});client.connect('fictional-token');await client.request(scope,'runs');await client.request(scope,'detail',{runID:'run-1'});await client.request(scope,'durations');for(const c of calls){assert.equal(c.options.method,'GET');assert.equal(c.options.credentials,'omit');assert.equal(c.options.redirect,'error');assert.equal(c.options.headers.Authorization,'Bearer fictional-token');assert.ok(!c.url.includes('fictional-token'));assert.equal(c.options.body,undefined)}for(const kind of ['trigger','retry','logs','refresh'])await assert.rejects(client.request(scope,kind));await assert.rejects(client.request(scope,'detail',{runID:'../secret'}));client.clear();await assert.rejects(client.request(scope,'runs'));assert.equal(calls.length,3)
})
test('complete bounded run list, canonical statuses and exact scope required',()=>{validateList(list(),scope);for(const change of [{scope:{...scope,workspace_id:'beta'}},{source:{...source,live_provider_verified:true}},{total:101},{offset:1},{limit:20},{items:[run,run]},{items:[{...run,status:'COMPLETED'}]},{items:[{...run,observed_at:'2026-01-01'}]}])assert.throws(()=>validateList({...list(),...change},scope))})
test('details require same scope/run/version and bounded allowlisted shape',()=>{validateDetail(detail(),scope,2,'run-1',run);for(const data of [{...detail(),source:{...source,revision:3}},{...detail(),run:{...detail().run,id:'run-2'}},{...detail(),run:{...detail().run,workspace_id:'beta'}},{...detail(),run:{...detail().run,stages:'logs'}}])assert.throws(()=>validateDetail(data,scope,2,'run-1',run));const missing=detail();missing.run.stages=null;validateDetail(missing,scope,2,'run-1',{...run,stage_count:0,job_count:0,details_available:false})})
test('duration report retains failed-run successful-job semantics and JSON group keys',()=>{const r=report();validateDurations(r,scope,2,[run]);assert.equal(r.groups[0].samples[0].run_status,'failure');for(const mutate of [r=>r.source={...source,revision:3},r=>r.coverage.supplied_runs=2,r=>r.coverage.full_history_complete=true,r=>r.groups[0].key='["Other", "Unit"]',r=>r.groups[0].average_seconds=Infinity,r=>r.groups[0].samples[0].duration_seconds=-1,r=>r.groups.push(r.groups[0])]){const value=report();mutate(value);assert.throws(()=>validateDurations(value,scope,2,[run]))}})
test('list and durations cannot join different revisions or scopes',async()=>{const client={request:async(_scope,kind)=>kind==='runs'?list():report()};assert.equal((await loadSnapshot(client,scope)).revision,2);for(const data of [{...report(),source:{...source,revision:3}},{...report(),scope:{...scope,connection_id:'other'}}])await assert.rejects(loadSnapshot({request:async(_scope,kind)=>kind==='runs'?list():data},scope))})
test('no upstream error body is read or echoed and abort signal is forwarded',async()=>{let seen;const client=createClient({fetcher:async(_u,o)=>{seen=o.signal;return {ok:false,status:503,json:()=>{throw Error('private body')}}}});client.connect('fictional');const c=new AbortController();await assert.rejects(client.request(scope,'runs',{signal:c.signal}),/unavailable/);assert.equal(seen,c.signal)})

test('details and duration samples must agree with the fully loaded run summaries',()=>{
 for(const change of [{status:'success'},{observed_at:'2026-01-01T13:00:00Z'},{details_available:false},{stage_count:2},{job_count:2}])assert.throws(()=>validateDetail(detail(),scope,2,'run-1',{...run,...change}))
 for(const change of [{run_id:'absent'},{run_status:'success'},{observed_at:'2026-01-01T13:00:00Z'}]){const r=report();Object.assign(r.groups[0].samples[0],change);assert.throws(()=>validateDurations(r,scope,2,[run]))}
 const r=report();r.groups[0].count=2;r.groups[0].samples.push(r.groups[0].samples[0]);assert.throws(()=>validateDurations(r,scope,2,[run]))
})

test('ineffective transport abort still fences chained reads and delayed durations',async()=>{
 for(const kind of ['runs','durations']){
  const controller=new AbortController(),calls=[]
  const client={request:async(_scope,action)=>{calls.push(action);if(action===kind)controller.abort();return action==='runs'?list():report()}}
  await assert.rejects(loadSnapshot(client,scope,controller.signal),error=>error.name==='AbortError')
  assert.deepEqual(calls,kind==='runs'?['runs']:['runs','durations'])
 }
})

test('duration mean accepts 0.1 at both bounds and rejects its finite neighbors',()=>{
 const data=report(),group=data.groups[0]
 Object.assign(group,{average_seconds:0.1,min_seconds:0.1,max_seconds:0.1,latest_seconds:0.1})
 Object.assign(group.samples[0],{finished_at:'2026-01-01T10:00:00.100Z',duration_seconds:0.1})
 assert.doesNotThrow(()=>validateDurations(data,scope,2,[run]))
 // Adjacent binary64 values: every other field stays identical to the valid response.
 for(const average_seconds of [0.09999999999999999,0.10000000000000002]){
  const invalid={...data,groups:[{...group,average_seconds}]}
  assert.throws(()=>validateDurations(invalid,scope,2,[run]))
 }
})
