import {scopeFrom,loadSnapshot,validateDetail} from './client.mjs'
export function mountPipelinePage({client,sessionMode=false,onSignOut=()=>{}}){
if(!client||typeof client.request!=='function'||typeof client.clear!=='function')throw new Error('Explicit request client required.')
const el=id=>document.getElementById(id),transportAllowed=location.protocol==='https:'||(location.protocol==='http:'&&location.hostname==='127.0.0.1')
let active=false,epoch=0,controller,detailEpoch=0,detailController,scope,snapshot,page=0,filter={query:'',status:''}
function text(tag,value,className){const n=document.createElement(tag);n.textContent=value;if(className)n.className=className;return n}
function closeDetail(){detailEpoch++;detailController?.abort();detailController=undefined;el('detail').close();el('detail-title').textContent='';el('detail-status').textContent='';el('detail-body').replaceChildren()}
function clearPanel(){closeDetail();snapshot=undefined;page=0;el('panel').hidden=true;for(const id of ['scope-title','revision','summary','coverage'])el(id).textContent='';el('runs').replaceChildren();el('durations').replaceChildren()}
function cancel(){epoch++;controller?.abort();controller=new AbortController();return epoch}
function disconnect(message='Disconnected. Reconnect to read current authorized state.'){
 active=false;cancel();client.clear();clearPanel();scope=undefined;el('session').hidden=true;if(el('connect'))el('connect').hidden=!transportAllowed
 for(const id of ['credential','workspace','connection','pipeline','query'])if(el(id))el(id).value='';el('state').value='';filter={query:'',status:''};el('status').textContent=message
}
function failure(error,token){if(token!==epoch||error.name==='AbortError')return;if(error.status===401){disconnect(error.message);return}clearPanel();el('status').textContent=error.status?error.message:'Snapshot changed or could not be validated. Reopen the scope to retry.'}
function renderRuns(){
 if(!snapshot)return
 const rows=snapshot.list.items.filter(r=>(!filter.status||r.status===filter.status)&&r.id.toLocaleLowerCase().includes(filter.query.trim().toLocaleLowerCase()))
 const pages=Math.max(1,Math.ceil(rows.length/10));page=Math.min(page,pages-1);el('runs').replaceChildren()
 for(const run of rows.slice(page*10,(page+1)*10)){
  const card=text('li',''),body=text('div','','record');body.append(text('h3',run.id),text('p',run.status,'badge'),text('p',`Observed ${run.observed_at}`),text('p',run.details_available?`${run.stage_count} stages · ${run.job_count} recorded jobs`:'Stage and job details unavailable'))
  const button=text('button','View run');button.type='button';button.setAttribute('aria-label',`View ${run.id}`);button.addEventListener('click',()=>showDetail(run.id));card.append(body,button);el('runs').append(card)
 }
 if(!rows.length)el('runs').append(text('li',snapshot.list.total?'No runs match this local filter.':'No runs in the stored subset. This does not prove the provider has no history.'))
 el('summary').textContent=`${rows.length} matching runs of ${snapshot.list.total} stored · Page ${page+1} of ${pages}`
 el('previous').disabled=page===0;el('next').disabled=page===pages-1
}
function renderDurations(){
 const report=snapshot.durations,c=report.coverage
 el('coverage').textContent=`${c.selected_runs} terminal runs selected from ${c.supplied_runs} stored; ${c.runs_with_samples} runs with valid samples. Missing details: ${c.missing_details}; invalid timing/IDs: ${c.invalid_duration_jobs}; ambiguous jobs: ${c.ambiguous_jobs}; excluded non-success jobs: ${c.excluded_jobs}. Coverage diagnostics can overlap.`
 el('durations').replaceChildren()
 for(const group of report.groups){
  const card=text('article','','trend'),heading=text('h3',`${group.stage_name} / ${group.job_name}`);card.append(heading)
  const values=text('dl','','metrics');for(const [name,key]of[['Samples','count'],['Average seconds','average_seconds'],['Minimum seconds','min_seconds'],['Maximum seconds','max_seconds'],['Latest sample seconds','latest_seconds']]){const pair=text('div','');pair.append(text('dt',name),text('dd',Number(group[key]).toLocaleString(undefined,{maximumFractionDigits:3})));values.append(pair)}card.append(values)
  const disclosure=document.createElement('details');disclosure.append(text('summary',`Show ${group.samples.length} samples`));const list=text('ul','','samples')
  for(const sample of group.samples)list.append(text('li',`${sample.run_id} (${sample.run_status}) · ${sample.duration_seconds} seconds · started ${sample.started_at}`));disclosure.append(list);card.append(disclosure);el('durations').append(card)
 }
 if(!report.groups.length)el('durations').append(text('p','No valid successful-job duration samples in this stored subset. Missing or invalid evidence is not zero duration.'))
}
async function load(){
 if(!active||!scope)return
 const selected={...scope},token=cancel();clearPanel();el('status').textContent='Reading an authorized, consistent snapshot…'
 try{const data=await loadSnapshot(client,selected,controller.signal);if(token!==epoch)return;snapshot=data;el('scope-title').textContent=`${selected.workspace_id} / ${selected.connection_id} / ${selected.pipeline_id}`;el('revision').textContent=`Stored snapshot version ${data.revision} · complete provider history: no · live provider verified: no`;el('panel').hidden=false;renderRuns();renderDurations();el('status').textContent='Current access checked by the service. This reads stored data, not a live provider refresh.'}catch(error){failure(error,token)}
}
async function showDetail(runID){
 if(!active||!snapshot||!scope)return
 closeDetail();detailController=new AbortController();const token=epoch,detailToken=detailEpoch,selected={...scope},version=snapshot.revision,summary=snapshot.list.items.find(row=>row.id===runID)
 el('detail-title').textContent=runID;el('detail-status').textContent='Reading current authorized run details…';el('detail').showModal()
 try{const data=validateDetail(await client.request(selected,'detail',{runID,signal:detailController.signal}),selected,version,runID,summary);if(token!==epoch||detailToken!==detailEpoch)return
  const run=data.run;el('detail-status').textContent=`${run.status} · snapshot version ${version} · observed ${run.observed_at}`
  if(run.stages===null)el('detail-body').append(text('p','Stage and job details were not available in this observation.'))
  else if(!run.stages.length)el('detail-body').append(text('p','This observation contains no stage entries.'))
  for(const stage of run.stages||[]){const section=text('section','');section.append(text('h3',stage.name));for(const job of stage.jobs){const row=text('article','','job');row.append(text('h4',job.name),text('p',`${job.id} · ${job.status}`),text('p',`Started: ${job.started_at??'unavailable'}`),text('p',`Finished: ${job.finished_at??'unavailable'}`));section.append(row)}el('detail-body').append(section)}
 }catch(error){if(token!==epoch||detailToken!==detailEpoch||error.name==='AbortError')return;failure(error,token)}
}
if(!sessionMode)el('connect').addEventListener('submit',event=>{event.preventDefault();if(!transportAllowed)return;try{client.connect(el('credential').value)}catch{el('status').textContent='Enter a valid opaque access credential.';return}active=true;el('credential').value='';el('connect').hidden=true;el('session').hidden=false;el('status').textContent='Enter an operator-provided workspace, connection and pipeline.'})
el('disconnect').addEventListener('click',()=>{disconnect();if(sessionMode)onSignOut()})
for(const id of ['workspace','connection','pipeline'])el(id).addEventListener('input',()=>{cancel();clearPanel();scope=undefined;el('status').textContent='Scope changed. Open current authorized state again.'})
el('scope-form').addEventListener('submit',event=>{event.preventDefault();if(!active||!transportAllowed)return;cancel();clearPanel();try{scope=scopeFrom(el('workspace').value,el('connection').value,el('pipeline').value)}catch{scope=undefined;el('status').textContent='Enter valid scope identifiers.';return}filter={query:'',status:''};el('query').value='';el('state').value='';load()})
el('reload').addEventListener('click',load)
el('filters').addEventListener('submit',event=>{event.preventDefault();filter={query:el('query').value,status:el('state').value};page=0;renderRuns()})
el('previous').addEventListener('click',()=>{page--;renderRuns()});el('next').addEventListener('click',()=>{page++;renderRuns()})
el('detail-close').addEventListener('click',closeDetail);el('detail').addEventListener('cancel',event=>{event.preventDefault();closeDetail()})
window.addEventListener('pagehide',()=>disconnect())
if(!sessionMode&&!transportAllowed){el('connect').hidden=true;el('status').textContent='HTTPS is required outside numeric loopback.'}

return {activate(){if(!transportAllowed)throw new Error('HTTPS required.');active=true;el('session').hidden=false;el('status').textContent='Enter exact workspace, connection and pipeline IDs supplied by your administrator.'},clear:disconnect}
}
