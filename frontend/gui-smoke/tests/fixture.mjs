// Synthetic observations only. No identity credentials, upstreams, or runner.
export const scope = {workspace_id:'alpha',connection_id:'panel',repository_id:'repo'};
export const options = {offset:0,limit:20};
export function envelope(s = scope, revision = 1) {
  return {scope:{...s},available:revision !== 0,
    source:{kind:'stored_observation',revision,observed_at:revision ? '2026-01-02T12:00:00Z' : null,
      live_provider_verified:false,full_history_complete:false,execution_enabled:false},
    capabilities:{read:true,submit:false,cancel:false,retry:false,reproduce:false,refresh:false,download:false}};
}
export function list(s = scope, q = options, total = 2, revision = 1) {
  return {...envelope(s,revision),total,offset:q.offset,limit:q.limit,
    items:Array.from({length:Math.min(q.limit,Math.max(0,total-q.offset))},(_,i)=>({
      id:`task-${q.offset+i+1}`,status:'success',source:'api',branch:'main',created_at:'2026-01-02T11:00:00Z',
      details_available:true,stages_available:true,summary_available:true})),
    next_offset:q.offset+q.limit < total ? q.offset+q.limit : null};
}
export function summary(s = scope, total = 2, revision = 1) {
  return {...envelope(s,revision),total,
    states:{pending:0,running:0,success:total,failed:0,cancelled:0,unknown:0},
    interpretation:'published_snapshot_only;not_live_queue_or_execution_proof'};
}
export function detail(s = scope, identity = 'task-1', revision = 1) {
  return {...envelope(s,revision),task:{id:identity,status:'success',source:'api',branch:'main',
    created_at:'2026-01-02T11:00:00Z',started_at:'2026-01-02T11:01:00Z',finished_at:'2026-01-02T11:02:00Z',
    commit:'a'.repeat(40),requested_commit:'b'.repeat(40),duration_seconds:60,
    summary:{passed:2,failed:0,skipped:1,total:3},
    stages:[{id:'gui',name:'Synthetic GUI stage',status:'success',started_at:'2026-01-02T11:01:00Z',finished_at:'2026-01-02T11:02:00Z',duration_seconds:60}]}};
}
export const response = value => new Response(JSON.stringify({data:value}),{headers:{'Content-Type':'application/json; charset=utf-8'}});
export function deferred() { let resolve,reject; const promise=new Promise((a,b)=>{resolve=a;reject=b;}); return {promise,resolve,reject}; }
