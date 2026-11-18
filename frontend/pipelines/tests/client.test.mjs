import test from 'node:test'
import assert from 'node:assert/strict'
import {scopeFrom,baseURL,createClient,validateList,validateDetail,validateDurations,validateTasks,loadSnapshot} from '../client.mjs'
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

const ideograph='\u{20000}' // one printable scalar, two UTF-16 code units
function namedReport(stage,job) {
 const r=report()
 Object.assign(r.groups[0],{stage_name:stage,job_name:job,key:JSON.stringify([stage,job])})
 return r
}
function namedDetail(stage,job) {
 const d=detail()
 d.run.stages[0].name=stage;d.run.stages[0].jobs[0].name=job
 return d
}

test('duration and detail preserve 129 and 256 supplementary scalars plus the mixed and BMP limits in either name',async()=>{
 const short=ideograph.repeat(129),maximum=ideograph.repeat(255)+'\u{31350}' // assigned CJK Extension H, Unicode 15
 assert.deepEqual([short.length,maximum.length],[258,512])
 // The mixed mark is intentionally decomposed. Neither normalization nor a byte-/unit-count is a valid match.
 const mixed='e\u0301'+'a'.repeat(253)+ideograph,bmp='界'.repeat(256)
 const punctuated=ideograph.repeat(129)+' A5+!e\u0301'
 for(const [stage,job] of [[short,maximum],[maximum,short],[mixed,bmp],[bmp,punctuated],[ideograph.repeat(128),'Unit']]){
  const calls=[],snapshot=await loadSnapshot({request:async(s,kind)=>{
   assert.deepEqual(s,scope);calls.push(kind)
   return kind==='runs'?list():namedReport(stage,job)
  }},scope)
  assert.deepEqual(calls,['runs','durations'])
  const [g]=snapshot.durations.groups
  assert.deepEqual([g.stage_name,g.job_name],[stage,job]);assert.deepEqual(JSON.parse(g.key),[stage,job])
  assert.equal(g.samples[0].run_id,run.id);assert.equal(g.samples[0].run_status,'failure')
  const checked=validateDetail(namedDetail(stage,job),scope,snapshot.revision,run.id,snapshot.list.items[0]).run
  assert.equal(checked.stages[0].name,stage);assert.equal(checked.stages[0].jobs[0].name,job)
 }
 // Synthetic browser DTO only: emulate an older display engine that does not yet recognize
 // a scalar. This specific U+0378 is ordinary Cn in CI, not an accepted Python fixture or production input.
 const unknown='\u0378',unrecognized=short+unknown
 assert.match(unknown,/^\p{Cn}$/u);assert.doesNotMatch(unknown,/\p{Noncharacter_Code_Point}/u)
 assert.equal(validateDurations(namedReport(unrecognized,'Unit'),scope,2,[run]).groups[0].stage_name,unrecognized)
 assert.equal(validateDetail(namedDetail('Checks',unrecognized),scope,2,run.id,run).run.stages[0].jobs[0].name,unrecognized)
})

test('a failed job with no duration group retains its complete long stage and job labels in pinned task details',async()=>{
 const stage=ideograph.repeat(256),job='測'.repeat(255)+ideograph,d=namedDetail(stage,job)
 const failed=d.run.stages[0].jobs[0];failed.status='failure'
 const noGroups=report();noGroups.groups=[];Object.assign(noGroups.coverage,{runs_with_samples:0,excluded_jobs:1})
 const snapshot=await loadSnapshot({request:async(_s,k)=>k==='runs'?list():noGroups},scope)
 assert.deepEqual(snapshot.durations.groups,[]);assert.equal(snapshot.durations.coverage.excluded_jobs,1)
 const checked=validateDetail(d,scope,snapshot.revision,run.id,snapshot.list.items[0]).run
 const counts={success:0,failure:1,cancelled:0,timed_out:0,skipped:0,queued:0,running:0,unknown:0}
 const taskPage={scope,source,run:{id:run.id,status:run.status,observed_at:run.observed_at,details_available:true},
  coverage:{stage_count:1,task_count:1,status_counts:counts,duplicate_id_tasks:0,stage_status_supplied:false},
  stages:[{index:0,name:stage,task_count:1,status_counts:counts}],filter:{status:''},total:1,offset:0,limit:10,
  items:[{stage_index:0,job_index:0,id:'unit',name:job,status:'failure',started_at:'2026-01-01T10:00:00Z',finished_at:'2026-01-01T10:00:12Z',id_occurrences:1}]}
 const tasks=validateTasks(taskPage,scope,snapshot.revision,checked)
 assert.deepEqual([tasks.stages[0].name,tasks.items[0].name],[stage,job])
 // A task page is an exact projection of the validated detail, not a second permissive label parser.
 const wrong=structuredClone(taskPage);wrong.items[0].name='測'.repeat(255)+'\u{20001}'
 assert.throws(()=>validateTasks(wrong,scope,snapshot.revision,checked))
})

test('valid group keys cannot rescue overlong labels, control characters or ill-formed surrogate sequences',()=>{
 const long=ideograph.repeat(129)
 // Preserve the prior short browser display boundary; these non-printing strings
 // are NOT valid producer witnesses. The stricter post-256-unit range is below.
 const old=namedDetail('a\u200db','x\u00a0y')
 assert.doesNotThrow(()=>validateDetail(old,scope,2,run.id,run))
 // Change just one label in each otherwise valid pair. Every group key still agrees with the DTO.
 const badStages=['A'.repeat(257),ideograph.repeat(257),long+'\x00','s\x7f','\ud800Z',long+'\udc00',' '+long,long+'\u200dX',long+'\u00a0X',long+'\ufdd0']
 const badJobs=['界'.repeat(255)+ideograph+'a','e\u0301'.repeat(128)+ideograph,'','x\n','\udc00\ud800',long+'\ud800',long+' ',long+'\u0085X',long+'\ue000X',long+'\u{1fffe}']
 for(const [stage,job] of [...badStages.map(s=>[s,'Unit']),...badJobs.map(j=>['Checks',j])]){
  assert.throws(()=>validateDurations(namedReport(stage,job),scope,2,[run]))
  assert.throws(()=>validateDetail(namedDetail(stage,job),scope,2,run.id,run))
 }
})

test('supplementary labels do not relax exact group identity, revision or run and job identifiers',()=>{
 const long=ideograph.repeat(129),stage=long+'e\u0301',job='Unit'
 const r=namedReport(stage,job)
 assert.doesNotThrow(()=>validateDurations(r,scope,2,[run]))
 r.groups[0].key=JSON.stringify([long+'é',job]);assert.throws(()=>validateDurations(r,scope,2,[run]))
 const bound=namedReport(stage,job);assert.throws(()=>validateDurations(bound,scope,3,[run]))
 for(const k of ['run_id','job_id']){
  const d=namedReport(stage,job);d.groups[0].samples[0][k]=ideograph
  assert.throws(()=>validateDurations(d,scope,2,[run]))
 }
 const d=namedDetail(stage,job);d.run.stages[0].jobs[0].id=ideograph
 assert.throws(()=>validateDetail(d,scope,2,run.id,run))
})
