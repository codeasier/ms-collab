import test from 'node:test'
import assert from 'node:assert/strict'
import {scopeFrom,readPath,validateTasks,createClient} from '../client.mjs'
import {createPipelineSessionClient} from '../../application/client.mjs'
const scope=scopeFrom('alpha','ci','build'),source={kind:'stored_snapshot',revision:3,full_history_complete:false,live_provider_verified:false}
const counts=()=>({success:1,failure:1,cancelled:0,timed_out:0,skipped:0,queued:0,running:0,unknown:0})
const jobs=[{id:'same',name:'<img src=x>',status:'success',started_at:null,finished_at:null},{id:'same',name:'<img src=x>',status:'failure',started_at:null,finished_at:null}]
const run={...scope,id:'run-1',status:'failure',observed_at:'2026-01-01T00:00:00+00:00',stages:[{name:'Checks',jobs}]}
function page(){return {scope,source,run:{id:run.id,status:run.status,observed_at:run.observed_at,details_available:true},coverage:{stage_count:1,task_count:2,status_counts:counts(),duplicate_id_tasks:2,stage_status_supplied:false},stages:[{index:0,name:'Checks',task_count:2,status_counts:counts()}],filter:{status:''},total:2,offset:0,limit:10,items:jobs.map((j,job_index)=>({stage_index:0,job_index,...j,id_occurrences:2}))}}
test('task request must pin exact run/scope/revision and canonical pagination',()=>{
 assert.equal(readPath(scope,'tasks',{runID:'run-1',revision:3,status:'failure'}),'/api/workspaces/alpha/pipelines/ci/build/runs/run-1/tasks?revision=3&offset=0&limit=10&status=failure')
 for(const options of [{runID:'../run',revision:3},{runID:'run-1'},{runID:'run-1',revision:0},{runID:'run-1',revision:true},{runID:'run-1',revision:3,limit:0},{runID:'run-1',revision:3,status:'COMPLETED'},{runID:'run-1',revision:3,offset:-1}])assert.throws(()=>readPath(scope,'tasks',options))
 assert.throws(()=>scopeFrom('alpha\n','ci','build'));assert.throws(()=>readPath(scope,'tasks',{runID:'run\n',revision:3}))
})
test('task page exactly binds summary, stage coverage, position and duplicate identity to validated run',()=>{
 validateTasks(page(),scope,3,run)
 for(const mutate of [p=>p.scope={...scope,workspace_id:'beta'},p=>p.source={...source,revision:4},p=>p.source.live_provider_verified=true,p=>p.run.status='success',p=>p.coverage.stage_status_supplied=true,p=>p.coverage.status_counts.success=2,p=>p.coverage.duplicate_id_tasks=0,p=>p.stages[0].name='other',p=>p.stages[0].status='success',p=>p.items[0].job_index=1,p=>p.items[0].id_occurrences=1,p=>p.items[0].name='other',p=>p.items.reverse(),p=>p.items[0].logs='PRIVATE_MARKER',p=>p.total=1,p=>p.limit=20,p=>p.filter.status='failure',p=>p.extra='PRIVATE_MARKER']){
  const p=structuredClone(page());mutate(p);assert.throws(()=>validateTasks(p,scope,3,run))
 }
})
test('status filter keeps unfiltered counts and empty/unknown details remain distinct',()=>{
 const p=page();p.filter.status='failure';p.total=1;p.items=p.items.slice(1);validateTasks(p,scope,3,run,{status:'failure'})
 const end=page();end.offset=10;end.items=[];validateTasks(end,scope,3,run,{offset:10})
 const unknown={...run,stages:null},empty=page();empty.run.details_available=false;empty.coverage={stage_count:0,task_count:0,status_counts:Object.fromEntries(Object.keys(counts()).map(k=>[k,0])),duplicate_id_tasks:0,stage_status_supplied:false};empty.stages=[];empty.total=0;empty.items=[];validateTasks(empty,scope,3,unknown);assert.throws(()=>validateTasks(empty,scope,3,{...unknown,stages:[]}))
})
test('same typed path in bearer and cookie modes, with no credential substitution',async()=>{
 const calls=[],fetcher=async(path,options)=>{calls.push({path,options});return {ok:true,json:async()=>({data:page()})}}
 const bearer=createClient({fetcher});bearer.connect('synthetic-token');await bearer.request(scope,'tasks',{runID:'run-1',revision:3});assert.equal(calls[0].options.credentials,'omit');assert.equal(calls[0].options.method,'GET')
 const cookie=createPipelineSessionClient({request:async(path,options)=>{calls.push({path,options});return page()},clear(){}});await cookie.request(scope,'tasks',{runID:'run-1',revision:3});assert.equal(calls[1].path,calls[0].path);assert.equal(calls[1].options.headers,undefined);assert.equal(calls[1].options.body,undefined)
 for(const kind of ['logs','retry','trigger','task-log'])assert.throws(()=>cookie.request(scope,kind,{runID:'run-1',revision:3}))
})
