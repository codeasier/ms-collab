import {LatestOperation,scopeFrom,queryFrom,validateCapabilities,validateConfig,validateFields,sameFields,validateCandidates} from './client.mjs'
export function mountResponsibilityPage({client,sessionMode=false,onSignOut=()=>{}}) {
if(!client||typeof client.request!=='function'||typeof client.clear!=='function')throw new Error('Explicit request client required.')
const el=id=>document.getElementById(id),gate=new LatestOperation()
const secure=location.protocol==='https:'||(location.protocol==='http:'&&location.hostname==='127.0.0.1')
let scope,capabilities,configuration,candidates,query,pending,busy=false
const node=(tag,value,className)=>{const n=document.createElement(tag);n.textContent=value;if(className)n.className=className;return n}
function closeConfirmation(){pending=undefined;el('confirmation').close();el('confirm-scope').textContent='';el('confirm-summary').textContent=''}
function clearEditor(){closeConfirmation();el('editor').hidden=true;el('editor-fields').replaceChildren()}
function clearCandidates(){candidates=undefined;el('candidates').replaceChildren();for(const id of ['coverage','observation','page'])el(id).textContent='';for(const id of ['previous','next'])el(id).disabled=true}
function clearPanel(){clearEditor();clearCandidates();capabilities=configuration=undefined;el('panel').hidden=true;el('edit').hidden=true;el('configuration').replaceChildren();el('field').replaceChildren();for(const id of ['scope-title','version'])el(id).textContent=''}
function controls(){
 el('reload').disabled=busy||!scope;el('edit').disabled=busy||!capabilities?.edit;el('save').disabled=busy||!capabilities?.edit
 el('add-field').disabled=busy||!capabilities?.edit||el('editor-fields').children.length>=200;el('cancel-edit').disabled=busy
 for(const input of el('filters').elements)input.disabled=busy||!configuration
 for(const fieldset of el('editor-fields').children)fieldset.disabled=busy
 el('previous').disabled=busy||!candidates||query.offset===0
 el('next').disabled=busy||!candidates||query.offset+query.limit>=candidates.total
}
function resetRequest(){gate.clear();busy=false;clearPanel();controls()}
function disconnect(message='Disconnected. If a save was interrupted, reconnect and reload to verify its outcome.'){
 resetRequest();client.clear();scope=query=undefined;el('session').hidden=true;if(el('connect'))el('connect').hidden=!secure
 for(const id of ['credential','workspace','connection','owner','repo'])if(el(id))el(id).value=''
 el('view').value='all';el('field-label').hidden=true;controls();el('status').textContent=message
}
function failed(error,operation){
 if(!gate.current(operation))return
 if(error.status===401){disconnect(error.message);return}
 clearPanel();busy=false;controls()
 el('status').textContent=error.status?error.message:'Unable to verify a complete response. A save may already have completed. Reload current state before editing again; nothing was retried.'
}
function renderConfig(){
 el('scope-title').textContent=`${scope.workspace_id} / ${scope.connection_id} / ${scope.repository}`
 el('version').textContent=`Record revision ${configuration.revision} · Configuration version ${configuration.config_version} · ${capabilities.edit?'Editor permission verified':'Read-only access'}`
 for(const field of configuration.fields){
  const item=node('li','');item.append(node('span',field.kind+' field','tag'),node('strong',field.name),node('span',`ID: ${field.id}`,'detail'),node('span',`Ordered owner IDs: ${field.owner_ids.join(', ')}`,'detail'),node('span',`Literal paths: ${field.paths.join(' · ')}`,'detail'))
  if(field.backup)item.append(node('span',`Backup relationship: ${field.backup.principal_id} from ${field.backup.field_id}. Not automatic assignment.`,'detail'))
  el('configuration').append(item)
  const option=node('option',field.name+' ('+field.id+')');option.value=field.id;el('field').append(option)
 }
 if(!configuration.fields.length)el('configuration').append(node('li','No fields are configured in this scope.'))
 el('edit').hidden=!capabilities.edit;el('panel').hidden=false
}
function renderCandidates(){
 el('coverage').textContent=`${candidates.total} candidates in this view · ${candidates.incomplete_pull_count} approved open PRs lack complete path observations. Coverage: approved observed subset, not the entire repository.`
 el('observation').textContent=`Community revision ${candidates.community_revision} · ${candidates.snapshot_available?'Snapshot observed '+candidates.snapshot_observed_at:'No snapshot'} · ${candidates.evidence_current?'Path observations current to this snapshot at '+candidates.evidence_observed_at:'Path observations missing or invalidated'}. No review workflow is enabled.`
 const labels={unknown:'Assignment unknown',incomplete:'Path evidence incomplete',matched:'Developer-field path match',partially_mapped:'Partially mapped paths',unassigned:'No developer-field path match'}
 for(const row of candidates.items){
  const item=node('li','');item.append(node('span',`${row.kind==='pull'?'PR':'Issue'} #${row.number} · ${labels[row.assignment_status]}`,'tag'),node('strong',row.title),node('span',`Fields: ${row.fields.length?row.fields.map(f=>f.id+' ('+f.kind+')').join(', '):'none observed'}`,'detail'))
  item.append(node('span',row.path_evidence==='complete_unpinned_observation'?`Unpinned path observation only · head ${row.head_sha} · base ${row.base_sha}`:'No complete path observation. Do not infer assignment or review status.','detail'))
  item.append(node('span','Review: unverified · Approval unavailable · No external action','detail'))
  if(row.unmapped_paths.length){const details=node('details','');details.append(node('summary',`${row.unmapped_paths.length} unmapped paths`),node('p',row.unmapped_paths.join('\n')));item.append(details)}
  el('candidates').append(item)
 }
 if(!candidates.items.length)el('candidates').append(node('li','No candidates in this approved observation view. This is not proof that the repository has no work.'))
 el('page').textContent=`${candidates.items.length?query.offset+1:0}–${query.offset+candidates.items.length} of ${candidates.total}`;controls()
}
async function loadScope(notice=''){
 if(!scope)return
 const selected={...scope},operation=gate.begin();clearPanel();busy=true;controls();el('status').textContent='Checking current permissions and a consistent configuration…'
 try{
  const caps=await client.request(selected,'capabilities',{signal:operation.signal});if(!gate.current(operation))return
  validateCapabilities(caps,selected)
  const config=await client.request(selected,'config',{signal:operation.signal});if(!gate.current(operation))return
  validateConfig(config,selected,caps)
  const requested=queryFrom(),result=await client.request(selected,'candidates',{query:requested,signal:operation.signal});if(!gate.current(operation))return
  validateCandidates(result,selected,config,requested)
  capabilities=caps.capabilities;configuration=config;candidates=result;query=requested;busy=false;el('view').value='all';el('field-label').hidden=true
  renderConfig();renderCandidates();controls();el('status').textContent=notice||'Current scope loaded. These are observed matches, not assignments or review results.'
 }catch(error){failed(error,operation)}
}
async function loadCandidates(requested){
 if(!scope||!configuration||busy)return
 const selected={...scope},config=configuration,operation=gate.begin();clearCandidates();busy=true;controls();el('status').textContent='Loading this approved observation view…'
 try{
  const result=await client.request(selected,'candidates',{query:requested,signal:operation.signal});if(!gate.current(operation))return
  validateCandidates(result,selected,config,requested);candidates=result;query=requested;busy=false;renderCandidates();el('status').textContent='Observation view loaded. Evidence remains unverified for review or merge.'
 }catch(error){failed(error,operation)}
}
function inputFor(fieldset,key,label,value,{area=false,disabled=false,options}={}){
 const wrapper=node('label',label),input=document.createElement(options?'select':area?'textarea':'input');input.dataset.entry=key
 if(options)for(const choice of options){const option=node('option',choice);option.value=choice;input.append(option)}
 input.value=value;input.disabled=disabled;input.autocomplete='off';input.spellcheck=false
 if(!area&&!options)input.maxLength=key==='name'?200:key==='id'||key==='backup-field'?64:200
 wrapper.append(input);if(area)wrapper.className='wide';fieldset.querySelector('.field-grid').append(wrapper)
}
function addField(field,isNew=false){
 const card=document.createElement('fieldset');card.append(node('legend',isNew?'New field · unsaved':'Field '+field.id),node('div','','field-grid'))
 inputFor(card,'id','Stable field ID',field.id,{disabled:!isNew});inputFor(card,'name','Display name',field.name)
 inputFor(card,'kind','Kind',field.kind,{options:['developer','committer']})
 inputFor(card,'owners','Ordered owner principal IDs · one per line',field.owner_ids.join('\n'),{area:true})
 inputFor(card,'paths','Literal paths · one per line',field.paths.join('\n'),{area:true})
 inputFor(card,'backup-principal','Backup principal ID · optional',field.backup?.principal_id||'');inputFor(card,'backup-field','Backup source field ID · optional',field.backup?.field_id||'')
 if(isNew){const remove=node('button','Remove this unsaved field','secondary remove');remove.type='button';remove.addEventListener('click',()=>{if(!busy){card.remove();controls()}});card.append(remove)}
 el('editor-fields').append(card);controls()
}
function readDraft(){
 const value=[...el('editor-fields').children].map(card=>{
  const get=k=>card.querySelector(`[data-entry="${k}"]`).value
  const backup=get('backup-principal').trim(),source=get('backup-field').trim()
  return {id:get('id'),name:get('name').trim(),kind:get('kind'),paths:get('paths').split('\n').filter(x=>x!==''),owner_ids:get('owners').split('\n').map(x=>x.trim()).filter(Boolean),backup:backup||source?{principal_id:backup,field_id:source}:null}
 })
 validateFields(value);return value
}
if(!sessionMode)el('connect').addEventListener('submit',event=>{event.preventDefault();if(!secure)return;const value=el('credential').value;resetRequest();try{client.connect(value)}catch{client.clear();el('credential').value='';el('status').textContent='Enter a valid opaque credential.';return}el('credential').value='';el('connect').hidden=true;el('session').hidden=false;el('status').textContent='Enter exact scope IDs supplied by your administrator.'})
el('disconnect').addEventListener('click',()=>{disconnect();if(sessionMode)onSignOut()})
for(const id of ['workspace','connection','owner','repo'])el(id).addEventListener('input',()=>{resetRequest();scope=query=undefined;controls();el('status').textContent='Scope changed. Load current state again before viewing or editing.'})
el('scope-form').addEventListener('submit',event=>{event.preventDefault();resetRequest();try{scope=scopeFrom(el('workspace').value,el('connection').value,el('owner').value,el('repo').value)}catch(error){scope=undefined;controls();el('status').textContent=error.message;return}loadScope()})
el('reload').addEventListener('click',()=>loadScope())
for(const id of ['view','field'])el(id).addEventListener('change',()=>{gate.clear();busy=false;clearCandidates();el('field-label').hidden=el('view').value!=='field';controls();el('status').textContent='View changed. Apply it to load current observations.'})
el('filters').addEventListener('submit',event=>{event.preventDefault();try{loadCandidates(queryFrom(el('view').value,el('view').value==='field'?el('field').value:''))}catch(error){el('status').textContent=error.message}})
el('previous').addEventListener('click',()=>loadCandidates({...query,offset:Math.max(0,query.offset-query.limit)}))
el('next').addEventListener('click',()=>loadCandidates({...query,offset:query.offset+query.limit}))
el('edit').addEventListener('click',()=>{if(busy||!capabilities?.edit)return;clearEditor();for(const field of configuration.fields)addField(field);el('editor').hidden=false;el('panel').hidden=true;controls();el('editor').scrollIntoView({block:'start'})})
el('add-field').addEventListener('click',()=>{if(!busy&&capabilities?.edit&&el('editor-fields').children.length<200)addField({id:'',name:'',kind:'developer',paths:[],owner_ids:[],backup:null},true)})
el('cancel-edit').addEventListener('click',()=>{if(!busy){clearEditor();el('panel').hidden=!configuration;el('status').textContent='Unsaved edits discarded. No write was sent.'}})
el('configuration-form').addEventListener('submit',event=>{
 event.preventDefault();if(busy||!scope||!configuration||!capabilities?.edit)return
 try{
  const fields=readDraft();if(sameFields(fields,configuration.fields)){el('status').textContent='No configuration changes to save.';return}
  pending={scope:{...scope},revision:configuration.revision,config_version:configuration.config_version,fields}
  el('confirm-scope').textContent=`${scope.workspace_id} / ${scope.connection_id} / ${scope.repository} · revision ${pending.revision}`
  el('confirm-summary').textContent=`Save the complete configuration of ${fields.length} fields. Owner IDs and current permissions will be rechecked by the service.`
  el('confirmation').showModal()
 }catch{el('status').textContent='Check stable IDs, names, literal paths, unique owners and cross-field backup relationships before saving.'}
})
el('cancel').addEventListener('click',closeConfirmation);el('confirmation').addEventListener('cancel',closeConfirmation)
el('confirm-form').addEventListener('submit',async event=>{
 event.preventDefault();if(!pending||busy)return
 const selected=pending,operation=gate.begin();closeConfirmation();clearCandidates();busy=true;controls();el('status').textContent='Saving once. If interrupted, reload to verify the outcome; nothing is retried automatically.'
 try{
  const result=await client.request(selected.scope,'save',{fields:selected.fields,revision:selected.revision,signal:operation.signal});if(!gate.current(operation))return
  validateConfig(result,selected.scope,{revision:selected.revision+1,config_version:selected.config_version+1})
  if(!sameFields(result.fields,selected.fields))throw new Error('Unexpected saved configuration.')
  await loadScope('Configuration saved and current state reloaded. No assignment or review workflow was executed.')
 }catch(error){failed(error,operation)}
})
window.addEventListener('pagehide',()=>disconnect('Disconnected. Reconnect to load current state.'))
if(!sessionMode&&!secure){el('connect').hidden=true;el('status').textContent='HTTPS is required outside numeric loopback.'}

return {activate(){el('session').hidden=false;el('status').textContent='Enter exact scope IDs supplied by your administrator.'},clear:disconnect}
}
