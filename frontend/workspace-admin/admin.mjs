import {createClient, validateWorkspaces, validateMembers, membershipURL} from './client.mjs'
const el = id => document.getElementById(id)
const client = createClient()
let generation = 0, controller, workspaces = [], selection, editing, saving = false
function cancelRequests() { generation++; controller?.abort(); controller = new AbortController(); return generation }
function closeEditor() { editing = undefined; el('editor').close(); for (const id of ['editing','edit-status']) el(id).textContent = ''; for (const id of ['role','member-state']) el(id).selectedIndex = -1 }
function clearPanel() { closeEditor(); el('panel').hidden = true; el('members').replaceChildren(); el('title').textContent = ''; el('state').textContent = '' }
function disconnect(message='Disconnected. Any already accepted change may have completed; reload after reconnecting to verify.') {
  cancelRequests(); client.clear(); clearPanel(); workspaces=[]; selection=undefined
  el('workspace').replaceChildren(); el('session').hidden=true; el('connect').hidden=false; el('credential').value=''; el('status').textContent=message
}
function failed(error, current) {
  if (current !== generation || error.name === 'AbortError') return
  if (error.status === 401) { disconnect(error.message); return }
  el('status').textContent = error.status ? error.message : 'Unable to load a valid response. Reload to retry.'
}
async function loadMembers(id, notice='') {
  const current = cancelRequests(); clearPanel(); selection=id
  const space = workspaces.find(w => w.id === id)
  if (!space) { el('status').textContent='No accessible workspaces.'; return }
  el('status').textContent='Loading members…'
  try {
    const members=validateMembers(await client.request(membershipURL(id), {signal:controller.signal}), id)
    if (current !== generation) return
    el('title').textContent=space.name
    el('state').textContent=`${space.kind === 'shared' ? 'Shared · explicit membership' : 'Product workspace'} · ${space.state}`
    for (const member of members) {
      const row=document.createElement('li'), label=document.createElement('span'), button=document.createElement('button')
      label.textContent=`${member.principal_id} · ${member.role} · ${member.state} · revision ${member.revision}`
      button.type='button'; button.textContent='Edit'; button.disabled=member.state!=='active'; button.setAttribute('aria-label', `Edit ${member.principal_id}`)
      button.addEventListener('click',()=>{editing={...member}; el('editing').textContent=`${member.principal_id} in ${space.name}`; el('role').value=member.role; el('member-state').value=member.state; el('editor').showModal()})
      row.append(label,button); el('members').append(row)
    }
    el('panel').hidden=false; el('status').textContent=notice || `${members.length} memberships. Only server-authorized operations are available.`
  } catch(error) { failed(error,current) }
}
async function reload() {
  const current=cancelRequests(); clearPanel(); el('workspace').disabled=true; el('status').textContent='Loading workspaces…'
  try {
    const result=validateWorkspaces(await client.request('/api/workspaces',{signal:controller.signal}))
    if(current!==generation)return
    workspaces=result; el('workspace').replaceChildren()
    for(const space of result){const option=document.createElement('option');option.value=space.id;option.textContent=space.name;el('workspace').append(option)}
    selection=result.some(w=>w.id===selection)?selection:result[0]?.id
    el('workspace').value=selection || '';el('workspace').disabled=!result.length
    await loadMembers(selection)
  }catch(error){failed(error,current)}
}
el('connect').addEventListener('submit',event=>{
  event.preventDefault()
  try{client.connect(el('credential').value)}catch{el('status').textContent='Enter a valid opaque credential.';return}
  el('credential').value='';el('connect').hidden=true;el('session').hidden=false;reload()
})
el('disconnect').addEventListener('click',()=>disconnect())
el('reload').addEventListener('click',reload)
el('workspace').addEventListener('change',event=>loadMembers(event.target.value))
el('cancel').addEventListener('click',closeEditor)
el('editor').addEventListener('cancel',event=>{if(saving)event.preventDefault();else editing=undefined})
el('edit-form').addEventListener('submit',async event=>{
  event.preventDefault();if(!editing||saving)return
  const member={...editing}, current=generation, scope=selection
  const body={role:el('role').value,state:el('member-state').value}
  saving=true;for(const id of ['save','cancel','role','member-state'])el(id).disabled=true
  el('edit-status').textContent='Saving… Do not close the page until the result is known.'
  try{
    const updated=await client.request(membershipURL(scope,member.principal_id),{method:'PATCH',body,revision:member.revision,signal:controller.signal})
    if(current!==generation)return
    validateMembers([updated],scope)
    if(updated.principal_id!==member.principal_id)throw new Error('Invalid result')
    closeEditor();await loadMembers(scope,'Membership saved. Current access rechecked.')
  }catch(error){
    if(current!==generation||error.name==='AbortError')return
    if(error.status===401){disconnect(error.message);return}
    if([403,404,412].includes(error.status)){closeEditor();await loadMembers(scope,error.message);return}
    if(error.status===409){el('edit-status').textContent=error.message;return}
    closeEditor();await loadMembers(scope,'Result uncertain. Current memberships reloaded; verify before editing again.')
  }finally{saving=false;for(const id of ['save','cancel','role','member-state'])el(id).disabled=false}
})
window.addEventListener('pagehide',()=>disconnect('Disconnected. Reconnect to reload current state.'))
// Refuse non-TLS network pages even if a hosting layer was misconfigured.
if(location.protocol!=='https:'&&!(location.protocol==='http:'&&location.hostname==='127.0.0.1')){
  el('connect').hidden=true;el('status').textContent='HTTPS is required outside numeric loopback.'
}
