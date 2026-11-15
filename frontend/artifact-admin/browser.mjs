import {LatestOperation,scopeFrom,validateStatus,validateReview,validateMutation} from './client.mjs'
export function mountArtifactAdministrationPage({client,sessionMode=false,onSignOut=()=>{}}){
if(!client||typeof client.request!=='function'||typeof client.clear!=='function')throw new Error('Explicit administration client required.')
const el=id=>document.getElementById(id),gate=new LatestOperation()
const secure=location.protocol==='https:'||(location.protocol==='http:'&&location.hostname==='127.0.0.1')
let scope,state,preview,pending,busy=false
function closeConfirmation(){pending=undefined;el('confirmation').close();for(const id of ['confirm-title','confirm-scope','confirm-detail'])el(id).textContent=''}
function clearPreview(){preview=undefined;el('preview').hidden=true;el('rows').replaceChildren();el('readers').replaceChildren();for(const id of ['summary','observation','digest'])el(id).textContent='';el('acknowledge').checked=false;el('approve').disabled=true}
function clearPanel(){closeConfirmation();clearPreview();state=undefined;el('panel').hidden=true;for(const id of ['scope-title','revision','publication'])el(id).textContent='';for(const id of ['refresh','review']){el(id).hidden=true;el(id).disabled=true}}
function resetRequest(){gate.clear();busy=false;clearPanel()}
function disconnect(message='Disconnected. An already accepted action may have completed. Reconnect and reload to verify.'){
 resetRequest();client.clear();scope=undefined;el('session').hidden=true;if(el('connect'))el('connect').hidden=!secure;el('reload').disabled=true
 for(const id of ['credential','workspace','connection','bucket'])if(el(id))el(id).value=''
 el('status').textContent=message
}
function controls(){
 for(const action of ['refresh','review'])el(action).disabled=busy||!state?.capabilities[action]||(action==='review'&&!state?.pending)
 el('reload').disabled=busy||!scope
 el('approve').disabled=busy||!state?.capabilities.approve||!preview||!el('acknowledge').checked
}
function failed(error,op){
 if(!gate.current(op))return
 if(error.status===401){disconnect(error.message);return}
 clearPanel();busy=false;controls()
 el('status').textContent=error.status?error.message:'Unable to verify a complete response. An accepted action may have completed. Reload current state before submitting again.'
}
function renderState(){
 el('scope-title').textContent=`${scope.workspace_id} / ${scope.connection_id} / ${scope.bucket_id}`
 el('revision').textContent=`Index revision ${state.revision} · ${state.published_revision===null?'No published snapshot':'Published revision '+state.published_revision}`
 el('publication').textContent=state.pending?'A private candidate is waiting for review. Readers still see only the previously approved snapshot.':'No candidate is pending. Refresh does not automatically approve data.'
 for(const action of ['refresh','review'])el(action).hidden=!state.capabilities[action]
 el('panel').hidden=false;controls()
}
async function loadStatus(notice=''){
 if(!scope)return
 const selected={...scope},op=gate.begin();clearPanel();busy=true;controls();el('status').textContent='Checking current administration permissions…'
 try{
  const data=validateStatus(await client.request(selected,'status',{signal:op.signal}),selected)
  if(!gate.current(op))return
  state=data;busy=false;renderState();el('status').textContent=notice||'Current administrator capabilities verified. Every action is rechecked by the service.'
 }catch(error){failed(error,op)}
}
function node(tag,text,className){const n=document.createElement(tag);n.textContent=text;if(className)n.className=className;return n}
async function loadReview(){
 if(busy||!state?.capabilities.review||!state.pending)return
 const selected={...scope},version=state.revision,op=gate.begin();closeConfirmation();clearPreview();busy=true;controls();el('status').textContent='Loading and verifying the complete candidate and reader audience…'
 try{
  const data=await validateReview(await client.request(selected,'review',{revision:version,signal:op.signal}),selected,version)
  if(!gate.current(op))return
  preview=data;busy=false
  el('summary').textContent=`Complete candidate: ${data.count} metadata rows · ${data.readers.length} reader IDs · revision ${version}.`
  el('observation').textContent=`Observed ${data.snapshot.observed_at} · Quarter offset ${data.snapshot.offset_minutes} minutes. Incremental retained metadata, not complete bucket history.`
  for(const reader of data.readers)el('readers').append(node('li',reader))
  for(const row of data.snapshot.artifacts){const item=node('li','');item.append(node('strong',row.key),node('span',`${row.is_dir?'Directory marker':'File'} · ${row.size} bytes · ${row.modified_at}`,'row-detail'));el('rows').append(item)}
  if(!data.count)el('rows').append(node('li','The complete candidate contains zero metadata rows.'))
  el('digest').textContent=`Verified SHA-256: ${data.candidate_digest}`
  el('approval').hidden=!state.capabilities.approve;el('preview').hidden=false;controls()
  el('status').textContent='Complete candidate loaded and digest verified. Nothing has been approved by loading this preview.'
 }catch(error){failed(error,op)}
}
function confirmAction(action){
 if(busy||!scope||!state?.capabilities[action]||(action==='approve'&&(!preview||!el('acknowledge').checked)))return
 const body=action==='refresh'?{}:{candidate_digest:preview.candidate_digest,readers:[...preview.readers],confirmation:'approve_complete_candidate_to_reviewed_readers'}
 pending={action,scope:{...scope},revision:state.revision,body}
 el('confirm-title').textContent=action==='refresh'?'Refresh a private candidate?':'Approve the complete candidate?'
 el('confirm-scope').textContent=`${scope.workspace_id} / ${scope.connection_id} / ${scope.bucket_id} · revision ${state.revision}`
 el('confirm-detail').textContent=action==='refresh'?'Read the configured provider and create a private candidate. Previously approved metadata remains visible; no new metadata is automatically shared.':`Publish all ${preview.count} metadata rows to exactly ${preview.readers.length} configured reader IDs: ${preview.readers.join(', ')}. Current membership is still required. Digest ${preview.candidate_digest}.`
 el('confirmation').showModal()
}
if(!sessionMode)el('connect').addEventListener('submit',event=>{event.preventDefault();if(!secure)return;const value=el('credential').value;resetRequest();try{client.connect(value)}catch{client.clear();el('credential').value='';el('status').textContent='Enter a valid opaque credential.';return}el('credential').value='';el('connect').hidden=true;el('session').hidden=false;el('status').textContent='Enter administrator-provided scope IDs to check current capabilities.'})
el('disconnect').addEventListener('click',()=>{disconnect();if(sessionMode)onSignOut()})
for(const id of ['workspace','connection','bucket'])el(id).addEventListener('input',()=>{resetRequest();scope=undefined;el('reload').disabled=true;el('status').textContent='Scope changed. Check access again before reviewing or changing anything.'})
el('scope-form').addEventListener('submit',event=>{event.preventDefault();resetRequest();try{scope=scopeFrom(el('workspace').value,el('connection').value,el('bucket').value)}catch(error){scope=undefined;el('status').textContent=error.message;return}loadStatus()})
el('reload').addEventListener('click',()=>loadStatus());el('review').addEventListener('click',loadReview)
el('refresh').addEventListener('click',()=>confirmAction('refresh'))
el('approval').addEventListener('submit',event=>{event.preventDefault();confirmAction('approve')})
el('acknowledge').addEventListener('change',controls)
el('cancel').addEventListener('click',closeConfirmation);el('confirmation').addEventListener('cancel',closeConfirmation)
el('confirm-form').addEventListener('submit',async event=>{
 event.preventDefault();if(!pending||busy)return
 const selected=pending,op=gate.begin();closeConfirmation();clearPreview();busy=true;controls();el('status').textContent='Submitting once. If interrupted, reload to verify the result; do not automatically retry.'
 try{
  const result=await client.request(selected.scope,selected.action,{revision:selected.revision,body:selected.body,signal:op.signal})
  if(!gate.current(op))return
  validateMutation(result,selected.scope,selected.revision,selected.action)
  await loadStatus(selected.action==='refresh'?'Private candidate refreshed. Load the complete candidate before deciding whether to approve.':'Complete candidate approved for the reviewed reader audience. Current state rechecked.')
 }catch(error){failed(error,op)}
})
window.addEventListener('pagehide',()=>disconnect('Disconnected. Reconnect to load current state.'))
if(!sessionMode&&!secure){el('connect').hidden=true;el('status').textContent='HTTPS is required outside numeric loopback.'}

return {activate(){el('session').hidden=false;el('status').textContent='Choose administrator-provided scope IDs. Approval does not grant anonymous or download access.'},clear:disconnect}
}
