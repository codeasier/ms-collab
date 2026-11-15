// Current cookie authority only. Platform bindings and credentials never enter this page.
import {validateWorkspaces} from '/manage/client.mjs'
const keys=['reader_ids','refresher_ids','approver_ids'],labels=['Read','Refresh','Approve']
const own=(o,ks)=>o!==null&&typeof o==='object'&&!Array.isArray(o)&&Object.keys(o).sort().join(',')===[...ks].sort().join(',')
const id=x=>typeof x==='string'&&/^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(x)&&!/[\s\u0000-\u001f\u007f]/.test(x)
const segment=x=>typeof x==='string'&&/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(x)&&!/[\s\u0000-\u001f\u007f]/.test(x)
const revision=x=>Number.isSafeInteger(x)&&x>0
const invalid=()=>{throw Error('Invalid connection response. Nothing was applied.')}
export function validateConnections(data,workspace,managed=false){
 if(!id(workspace)||!own(data,['workspace_id','module','items'])||data.workspace_id!==workspace||data.module!=='community'||!Array.isArray(data.items)||data.items.length>100)invalid()
 const ids=new Set(),scopes=new Set()
 for(const item of data.items){
  if(!own(item,managed?['id','scope','revision','grants','options','snapshot_available','approved_count']:['id','scope','revision'])||!segment(item.id)||ids.has(item.id)||!revision(item.revision))invalid()
  const s=item.scope;if(!own(s,['workspace_id','connection_id','repository'])||s.workspace_id!==workspace||!segment(s.connection_id)||typeof s.repository!=='string'||s.repository.split('/').length!==2||s.repository.split('/').some(x=>!segment(x)))invalid()
  const scope=s.connection_id+'/'+s.repository;if(scopes.has(scope))invalid();scopes.add(scope);ids.add(item.id)
  if(managed){
   if(!own(item.grants,keys)||!Array.isArray(item.options)||item.options.length>300||typeof item.snapshot_available!=='boolean'||!Number.isSafeInteger(item.approved_count)||item.approved_count<0||item.approved_count>200||(!item.snapshot_available&&item.approved_count!==0))invalid()
   const people=new Set()
   for(const option of item.options){if(!own(option,['principal_id','eligible'])||!id(option.principal_id)||people.has(option.principal_id)||!own(option.eligible,keys)||keys.some(k=>typeof option.eligible[k]!=='boolean'))invalid();people.add(option.principal_id)}
   for(const key of keys){const values=item.grants[key];if(!Array.isArray(values)||values.length>100||new Set(values).size!==values.length||values.some(p=>!id(p)||!people.has(p)))invalid()}
  }
 }
 return data.items
}
const path=(workspace,suffix='')=>{if(!id(workspace))invalid();return `/api/workspaces/${workspace}/connections/community${suffix}`}
const node=(doc,tag,text)=>{const n=doc.createElement(tag);if(text!==undefined)n.textContent=text;return n}
const option=(doc,value,label)=>{const n=node(doc,'option',label);n.value=value;return n}

export function mountConnectionsPage({session,doc=document}){
 const el=id=>doc.getElementById(id)
 let epoch=0,abort,active=false,pending,writing=false
 const setStatus=text=>{el('connection-status').textContent=text}
 const clearResults=()=>{el('connection-results').hidden=true;el('connection-directory').replaceChildren();el('connection-managed').replaceChildren();pending=undefined;el('connection-dialog').close();el('connection-warning').textContent=''}
 function cancel(){epoch++;abort?.abort();abort=new AbortController();writing=false;clearResults()}
 function clear(message='Recheck the current session to continue. Interrupted changes may already have completed.'){
  cancel();active=false;el('connection-workspace').replaceChildren();el('connection-controls').hidden=true;setStatus(message)
 }
 async function activate(){clear('Loading your current workspaces…');const turn=epoch;const spaces=validateWorkspaces(await session.request('/api/workspaces',{signal:abort.signal}));if(turn!==epoch)return
  for(const space of spaces.filter(s=>s.state==='active'))el('connection-workspace').append(option(doc,space.id,`${space.name} · ${space.kind}`))
  active=true;el('connection-controls').hidden=false;setStatus(spaces.length?'Select a workspace and load current connections.':'No accessible workspaces.')
 }
 const listDirectory=items=>{for(const item of items){el('connection-directory').append(node(doc,'li',`${item.scope.connection_id} · ${item.scope.repository} · revision ${item.revision}`))}if(!items.length)el('connection-directory').append(node(doc,'li','No registered readable scopes. This is not a provider repository listing.'))}
 function render(items){
  if(!items.length)el('connection-managed').append(node(doc,'p','No connections are delegated to you for management in this workspace.'))
  for(const item of items){
   const form=node(doc,'form');form.className='connection-card';form.append(node(doc,'h3',`${item.scope.connection_id} / ${item.scope.repository}`),node(doc,'p',`Revision ${item.revision} · ${item.snapshot_available?'Snapshot present':'No snapshot'} · ${item.approved_count} approvals`))
   const fieldset=node(doc,'fieldset');fieldset.append(node(doc,'legend','Approved principal choices'));const boxes=[]
   for(const p of item.options){const row=node(doc,'div');row.className='grant-row';row.append(node(doc,'span',p.principal_id))
    keys.forEach((key,i)=>{const label=node(doc,'label'),box=node(doc,'input');box.type='checkbox';box.dataset.principal=p.principal_id;box.dataset.grant=key;box.checked=item.grants[key].includes(p.principal_id);box.disabled=!p.eligible[key];if(box.disabled)box.checked=false;label.append(box,node(doc,'span',labels[i]+(p.eligible[key]?'':' (unavailable)')));row.append(label);boxes.push(box)})
    fieldset.append(row)
   }
   const button=node(doc,'button','Review grant changes');button.type='submit';form.append(fieldset,button)
   const selected=()=>Object.fromEntries(keys.map(key=>[key,boxes.filter(b=>b.dataset.grant===key&&b.checked&&!b.disabled).map(b=>b.dataset.principal).sort()]))
   const changed=()=>keys.some(k=>JSON.stringify([...item.grants[k]].sort())!==JSON.stringify(selected()[k]))
   button.disabled=!changed();form.addEventListener('change',()=>{button.disabled=writing||!changed()})
   form.addEventListener('submit',event=>{event.preventDefault();if(!active||writing||!changed())return;pending={item,grants:selected(),turn:epoch};el('connection-warning').textContent=`${item.scope.workspace_id} / ${item.scope.connection_id} / ${item.scope.repository}, revision ${item.revision}. `+keys.map((key,i)=>{const added=pending.grants[key].filter(p=>!item.grants[key].includes(p)),removed=item.grants[key].filter(p=>!pending.grants[key].includes(p));return `${labels[i]}: add ${added.join(', ')||'none'}; remove ${removed.join(', ')||'none'}.`}).join(' ');el('connection-save').disabled=false;el('connection-dialog').showModal()})
   el('connection-managed').append(form)
  }
 }
 async function load(){
  if(!active||writing)return;const workspace=el('connection-workspace').value;cancel();const turn=epoch;setStatus('Loading current scoped policy…')
  try{
   const visible=validateConnections(await session.request(path(workspace),{signal:abort.signal}),workspace);if(turn!==epoch)return
   let managed=[];try{managed=validateConnections(await session.request(path(workspace,'/manage'),{signal:abort.signal}),workspace,true)}catch(error){if(error.status!==403)throw error}if(turn!==epoch)return
   listDirectory(visible);render(managed);el('connection-results').hidden=false;setStatus('Current grants loaded. Every change rechecks your identity, workspace role, delegation and revision.')
  }catch(error){if(turn===epoch){clearResults();setStatus(error.message)}}
 }
 el('connection-scope').addEventListener('submit',event=>{event.preventDefault();return load()})
 el('connection-workspace').addEventListener('change',()=>{cancel();setStatus('Workspace changed. Load current connections explicitly.')})
 el('connection-cancel').addEventListener('click',()=>{if(writing)return;pending=undefined;el('connection-dialog').close();el('connection-warning').textContent=''})
 el('connection-dialog').addEventListener('cancel',event=>{if(writing)event.preventDefault();else{pending=undefined;el('connection-warning').textContent=''}})
 el('connection-confirm').addEventListener('submit',async event=>{
  event.preventDefault();if(!active||writing||!pending||pending.turn!==epoch)return
  const request=pending;clearResults();writing=true;el('connection-save').disabled=true;const turn=epoch;setStatus('Replacing grants. Do not assume an interrupted request was cancelled.')
  try{
   const result=await session.request(path(request.item.scope.workspace_id,`/manage/${request.item.id}`),{method:'PATCH',body:request.grants,revision:request.item.revision,signal:abort.signal});if(turn!==epoch)return
   validateConnections({workspace_id:request.item.scope.workspace_id,module:'community',items:[result]},request.item.scope.workspace_id,true)
   if(result.id!==request.item.id||result.scope.connection_id!==request.item.scope.connection_id||result.scope.repository!==request.item.scope.repository||result.revision!==request.item.revision+1||result.snapshot_available||result.approved_count!==0||keys.some(k=>JSON.stringify([...result.grants[k]].sort())!==JSON.stringify(request.grants[k])))invalid()
   cancel();setStatus('Grants replaced. Old snapshot and approvals removed. Reload current connections; no provider fetch was run.')
  }catch(error){if(turn===epoch){cancel();setStatus(error.message+' Reload current policy before making another change.')}}
 })
 return {activate,clear}
}

export function mountConnectionPicker({session,doc=document}){
 const el=id=>doc.getElementById(id);let epoch=0,abort,items=[],active=false
 function reset(){epoch++;abort?.abort();abort=new AbortController();items=[];el('picker-connection').replaceChildren();el('picker-use').disabled=true}
 function clear(){reset();active=false;el('connection-picker').hidden=true;el('picker-workspace').replaceChildren();el('picker-status').textContent=''}
 async function activate(){clear();const turn=epoch;const spaces=validateWorkspaces(await session.request('/api/workspaces',{signal:abort.signal}));if(turn!==epoch)return;for(const space of spaces.filter(s=>s.state==='active'))el('picker-workspace').append(option(doc,space.id,space.name));active=true;el('connection-picker').hidden=false}
 el('picker-workspace').addEventListener('change',()=>{reset();el('picker-status').textContent='Load the selected workspace explicitly.'})
 el('picker-load').addEventListener('click',async()=>{if(!active)return;reset();const turn=epoch,workspace=el('picker-workspace').value;el('picker-status').textContent='Checking current read access…';try{const data=validateConnections(await session.request(path(workspace),{signal:abort.signal}),workspace);if(turn!==epoch)return;items=data;for(const [i,item]of items.entries())el('picker-connection').append(option(doc,String(i),`${item.scope.connection_id} / ${item.scope.repository}`));el('picker-use').disabled=!items.length;el('picker-status').textContent=items.length?'Select a scope. Its permissions will be checked again on open.':'No registered readable scopes.'}catch(error){if(turn===epoch){reset();el('picker-status').textContent=error.message}}})
 el('picker-use').addEventListener('click',()=>{if(!active)return;const item=items[Number(el('picker-connection').value)];if(!item)return;for(const [id,value]of Object.entries({workspace:item.scope.workspace_id,connection:item.scope.connection_id,repository:item.scope.repository})){el(id).value=value;el(id).dispatchEvent(new Event('input',{bubbles:true}))}el('scope-form').requestSubmit()})
 return {activate,clear}
}
