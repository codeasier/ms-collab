const id = value => typeof value==='string' && /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(value)
const states = new Set(['success','failure','cancelled','timed_out','skipped','queued','running','unknown'])
const integer = (value,max=10000) => Number.isSafeInteger(value)&&value>=0&&value<=max
const time = value => typeof value==='string'&&/(?:Z|[+-]\d{2}:\d{2})$/.test(value)&&Number.isFinite(Date.parse(value))
const label = value => typeof value==='string'&&value.length>0&&value.length<=256&&!/[\u0000-\u001f\u007f]/.test(value)
const invalid = () => {throw new Error('Invalid or inconsistent snapshot. Reload the scope.')}
export function scopeFrom(workspace,connection,pipeline) {
 if(![workspace,connection,pipeline].every(id))throw new Error('Invalid scope.')
 return {workspace_id:workspace,connection_id:connection,pipeline_id:pipeline}
}
export const sameScope=(a,b)=>a&&b&&['workspace_id','connection_id','pipeline_id'].every(key=>a[key]===b[key])
export function baseURL(scope){scopeFrom(scope.workspace_id,scope.connection_id,scope.pipeline_id);return `/api/workspaces/${scope.workspace_id}/pipelines/${scope.connection_id}/${scope.pipeline_id}`}
function source(data,scope,version){
 if(!data||!sameScope(data.scope,scope)||!data.source||data.source.kind!=='stored_snapshot'||!Number.isSafeInteger(data.source.revision)||data.source.revision<1||data.source.full_history_complete!==false||data.source.live_provider_verified!==false||(version!==undefined&&data.source.revision!==version))invalid()
 return data.source.revision
}
export function validateList(data,scope){
 source(data,scope)
 if(!integer(data.total,100)||data.offset!==0||data.limit!==100||!Array.isArray(data.items)||data.items.length!==data.total)invalid()
 const ids=new Set()
 for(const row of data.items){
  if(!row||!id(row.id)||ids.has(row.id)||!states.has(row.status)||!time(row.observed_at)||!integer(row.stage_count,40)||!integer(row.job_count,200)||typeof row.details_available!=='boolean')invalid()
  ids.add(row.id)
 }
 return data
}
export function validateDetail(data,scope,version,runID,summary){
 source(data,scope,version)
 const run=data.run
 if(!run||!sameScope(run,scope)||run.id!==runID||!id(run.id)||!states.has(run.status)||!time(run.observed_at)||(run.stages!==null&&(!Array.isArray(run.stages)||run.stages.length>40)))invalid()
 if(!summary||summary.id!==runID||run.status!==summary.status||run.observed_at!==summary.observed_at||(run.stages!==null)!==summary.details_available||(run.stages?.length??0)!==summary.stage_count)invalid()
 let jobs=0
 for(const stage of run.stages||[]){
  if(!stage||!label(stage.name)||!Array.isArray(stage.jobs))invalid()
  jobs+=stage.jobs.length;if(jobs>200)invalid()
  for(const job of stage.jobs)if(!job||!id(job.id)||!label(job.name)||!states.has(job.status)||![job.started_at,job.finished_at].every(t=>t===null||time(t)))invalid()
 }
 if(jobs!==summary.job_count)invalid()
 return data
}
function validGroupKey(group){try{const pair=JSON.parse(group.key);return Array.isArray(pair)&&pair.length===2&&pair[0]===group.stage_name&&pair[1]===group.job_name}catch{return false}}
export function validateDurations(data,scope,version,items){
 source(data,scope,version)
 const total=items.length,byID=new Map(items.map(item=>[item.id,item]))
 const c=data.coverage
 const counters=['supplied_runs','nonterminal_or_unknown_runs','observed_terminal_runs','selected_runs','runs_with_details','runs_with_samples','missing_details','malformed_entries','excluded_jobs','invalid_duration_jobs','ambiguous_jobs']
 if(!c||c.source!=='supplied_snapshots'||c.full_history_complete!==false||c.limit!==100||c.supplied_runs!==total||c.observed_terminal_runs+c.nonterminal_or_unknown_runs!==total||c.selected_runs>total||counters.some(k=>!integer(c[k]))||typeof c.selection_truncated!=='boolean'||!Array.isArray(data.groups)||data.groups.length>200)invalid()
 let samples=0;const keys=new Set()
 for(const group of data.groups){
  if(!group||!label(group.stage_name)||!label(group.job_name)||!validGroupKey(group)||keys.has(group.key)||!integer(group.count,100)||!group.count||!Array.isArray(group.samples)||group.samples.length!==group.count||['average_seconds','min_seconds','max_seconds','latest_seconds'].some(k=>typeof group[k]!=='number'||!Number.isFinite(group[k])||group[k]<0)||group.min_seconds>group.max_seconds||group.average_seconds<group.min_seconds||group.average_seconds>group.max_seconds)invalid()
  keys.add(group.key);samples+=group.samples.length;if(samples>200)invalid()
  const sampled=new Set()
  for(const sample of group.samples){
   if(!sample||!id(sample.run_id)||!id(sample.job_id)||!states.has(sample.run_status)||![sample.observed_at,sample.started_at,sample.finished_at].every(time)||typeof sample.duration_seconds!=='number'||!Number.isFinite(sample.duration_seconds)||sample.duration_seconds<0)invalid()
   const run=byID.get(sample.run_id)
   if(!run||sampled.has(sample.run_id)||!run.details_available||run.status!==sample.run_status||run.observed_at!==sample.observed_at||!['success','failure','cancelled','timed_out','skipped'].includes(run.status))invalid()
   sampled.add(sample.run_id)
  }
 }
 return data
}
export function readPath(scope,kind,{runID}={}){
 let path
 if(kind==='runs')path='/runs?limit=100'
 else if(kind==='durations')path='/durations?limit=100'
 else if(kind==='detail'&&id(runID))path='/runs/'+runID
 else throw new Error('Unsupported read.')
 return baseURL(scope)+path
}
function current(signal){if(signal?.aborted){const error=new Error('Read cancelled.');error.name='AbortError';throw error}}
export function createClient({fetcher=fetch}={}){
 let credential=''
 return {
  connect(value){if(typeof value!=='string'||!value||value.length>8192||/\s/.test(value))throw new Error('Enter a valid access credential.');credential=value},
  clear(){credential=''},
  async request(scope,kind,{runID,signal}={}){
   if(!credential)throw new Error('Disconnected.')
   const response=await fetcher(readPath(scope,kind,{runID}),{method:'GET',headers:{Authorization:`Bearer ${credential}`},signal,credentials:'omit',cache:'no-store',redirect:'error'})
   if(!response.ok){const error=new Error(({401:'Authentication expired or was rejected. Reconnect.',403:'Current workspace permissions deny access.',404:'The scope or run is unavailable to your current identity.',409:'The workspace or pipeline module is unavailable.',503:'Stored pipeline data or the service is unavailable.'})[response.status]||'Unable to read this snapshot.');error.status=response.status;throw error}
   const payload=await response.json();if(!payload||!Object.hasOwn(payload,'data'))invalid();return payload.data
  }
 }
}
export async function loadSnapshot(client,scope,signal){
 current(signal)
 const list=validateList(await client.request(scope,'runs',{signal}),scope)
 current(signal)
 const durations=validateDurations(await client.request(scope,'durations',{signal}),scope,list.source.revision,list.items)
 current(signal)
 return {list,durations,revision:list.source.revision}
}
