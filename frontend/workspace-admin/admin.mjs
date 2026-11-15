import {createClient, validId, validateWorkspaces, validateMembers, validateModules, validateCatalog, membershipURL, moduleURL, invitationURL, validateInvitations} from './client.mjs'
const el = id => document.getElementById(id)
const client = createClient()
const pendingCreations = new Map(), pendingInvitations = new Map()
let generation = 0, controller, workspaces = [], selection, editing, action, saving = false
let managementAvailable = false, saveGeneration = 0
function cancelRequests() { generation++; controller?.abort(); controller = new AbortController(); return generation }
function closeEditor() {
  editing = undefined; el('editor').close()
  for (const id of ['editing','edit-status']) el(id).textContent = ''
  for (const id of ['role','member-state']) el(id).selectedIndex = -1
}
function closeCreate() { el('create-dialog').close(); el('create-form').reset(); el('create-status').textContent = '' }
function closeAction() {
  action = undefined; el('action-dialog').close()
  for (const id of ['action-title','action-description','action-status']) el(id).textContent = ''
}
function closeInvite() { el('invite-dialog').close(); el('invite-form').reset(); el('invite-status').textContent = '' }
function closeDialogs() { closeEditor(); closeCreate(); closeAction(); closeInvite() }
function clearPanel() {
  closeDialogs(); el('panel').hidden = true; el('members').replaceChildren()
  el('title').textContent = ''; el('state').textContent = ''
  el('module-panel').hidden = true; el('modules').replaceChildren(); el('module-key').replaceChildren()
  el('module-status').textContent = ''; el('install-form').hidden = true
  el('invitation-panel').hidden = true; el('invitations').replaceChildren(); el('invitation-status').textContent = ''; el('invite-open').hidden = true
}
function busy(value, ticket) {
  if (!value && ticket !== undefined && ticket !== saveGeneration) return
  saveGeneration++
  saving = value
  for (const control of document.querySelectorAll('dialog button,dialog input,dialog select')) control.disabled = value
  return saveGeneration
}
function disconnect(message='Disconnected. Any already accepted change may have completed; reload after reconnecting to verify.') {
  cancelRequests(); client.clear(); clearPanel(); busy(false); workspaces = []; selection = undefined
  managementAvailable = false; pendingCreations.clear(); pendingInvitations.clear()
  el('inbox-panel').hidden = true; el('inbox').replaceChildren(); el('inbox-status').textContent = ''
  el('workspace').replaceChildren(); el('control-spaces').replaceChildren(); el('operator-panel').hidden = true
  el('session').hidden = true; el('connect').hidden = false; el('credential').value = ''; el('status').textContent = message
}
function failed(error, current) {
  if (current !== generation || error.name === 'AbortError') return
  if (error.status === 401) { disconnect(error.message); return }
  el('status').textContent = error.status ? error.message : 'Unable to load a valid response. Reload to retry.'
}
function openAction(value) {
  action = value; el('action-title').textContent = value.title
  el('action-description').textContent = value.description; el('action-dialog').showModal()
}
function renderControls(spaces) {
  el('control-spaces').replaceChildren()
  for (const space of spaces) {
    const row = document.createElement('li'), label = document.createElement('span'), buttons = document.createElement('div')
    label.textContent = `${space.name} · ${space.kind} · ${space.state} · ${space.id} · revision ${space.revision}`
    buttons.className = 'toolbar'
    const transitions = space.state === 'active' ? ['suspended','archived'] : space.state === 'suspended' ? ['active','archived'] : []
    for (const state of transitions) {
      const button = document.createElement('button'); button.type = 'button'
      button.textContent = {suspended:'Suspend',archived:'Archive',active:'Reactivate'}[state]
      button.setAttribute('aria-label', `${button.textContent} ${space.name}`)
      button.addEventListener('click', () => openAction({kind:'lifecycle', title:`${button.textContent} workspace`,
        description: state === 'archived' ? `Archive ${space.name}. This is irreversible: this workspace cannot be reactivated. Authorized retained local reads remain possible; no data is deleted.` : `${button.textContent} ${space.name}. Suspension denies business-data access. This does not cancel already executed external side effects.`,
        path:`/api/control/workspaces/${encodeURIComponent(space.id)}`, body:{state}, revision:space.revision, id:space.id}))
      buttons.append(button)
    }
    row.append(label,buttons); el('control-spaces').append(row)
  }
  el('operator-panel').hidden = false
}
async function loadModules(space, current) {
  if (!managementAvailable) return
  const signal = controller.signal
  try {
    const modules = validateModules(await client.request(moduleURL(space.id), {signal}), space.id)
    if (current !== generation) return
    const catalog = space.state === 'active' ? validateCatalog(await client.request(`/api/workspaces/${encodeURIComponent(space.id)}/module-catalog`, {signal})) : []
    if (current !== generation) return
    for (const module of modules) {
      const row = document.createElement('li'), label = document.createElement('span')
      label.textContent = `${module.key} · ${module.state} · ${module.capabilities.join(', ')} · revision ${module.revision}`
      row.append(label)
      const next = {enabled:'disabling',disabling:'disabled',disabled:'enabled'}[module.state]
      if (space.state === 'active') {
        const button = document.createElement('button'); button.type = 'button'
        button.textContent = {enabled:'Enable',disabling:'Begin disabling',disabled:'Finish disabling'}[next]
        button.setAttribute('aria-label', `${button.textContent} ${module.key}`)
        button.addEventListener('click', () => openAction({kind:'module', title:`${button.textContent} module`,
          description:`Set ${module.key} in ${space.name} to ${next}. Only this workspace is affected. New local module access is denied as soon as disabling begins; external jobs and providers are not controlled by this slice.`,
          path:moduleURL(space.id,module.key), body:{state:next}, revision:module.revision, scope:space.id, id:module.key}))
        row.append(button)
      }
      el('modules').append(row)
    }
    for (const template of catalog.filter(item => !modules.some(module => module.key === item.key))) {
      const option = document.createElement('option'); option.value = template.key
      option.textContent = `${template.key} (${template.capabilities.join(', ')})`; el('module-key').append(option)
    }
    el('install-form').hidden = el('module-key').options.length === 0
    el('module-panel').hidden = false
    el('module-status').textContent = space.state === 'archived' ? 'Archived workspace: retained module metadata is read-only.' : `${modules.length} installed modules. Platform-approved capabilities cannot be edited here.`
  } catch (error) {
    if (current !== generation || error.name === 'AbortError') return
    if (error.status === 401) { disconnect(error.message); return }
    el('module-panel').hidden = false
    el('module-status').textContent = error.status ? error.message : 'Unable to load valid module state. Reload to retry.'
  }
}
async function loadInbox(current) {
  try {
    const invitations = validateInvitations(await client.request(invitationURL(), {signal:controller.signal}))
    if (current !== generation) return
    for (const item of invitations) {
      if (item.state !== 'pending') throw new Error('Invalid invitation response.')
      const row = document.createElement('li'), label = document.createElement('span')
      label.textContent = `${item.workspace_name} · ${item.role} · expires ${new Date(item.expires*1000).toISOString()}`
      row.append(label)
      for (const state of ['accepted','declined']) {
        const button = document.createElement('button'); button.type = 'button'; button.textContent = state === 'accepted' ? 'Join' : 'Decline'
        button.setAttribute('aria-label', `${button.textContent} ${item.workspace_name}`)
        button.addEventListener('click', () => openAction({kind:'invitation-response',title:state === 'accepted' ? 'Join workspace' : 'Decline invitation',
          description:`${state === 'accepted' ? 'Join' : 'Decline'} ${item.workspace_name} as ${item.role}. Only the verified identity addressed by this invitation can respond. Joining grants the stated workspace role; this does not accept an external provider agreement.`,
          path:invitationURL(undefined,item.id),body:{state},revision:item.revision,id:item.id,invitationScope:item.workspace_id}))
        row.append(button)
      }
      el('inbox').append(row)
    }
    el('inbox-panel').hidden = false; el('inbox-status').textContent = `${invitations.length} actionable invitations (up to 100). No membership is granted until you join.`
  } catch (error) {
    if (current !== generation || error.name === 'AbortError') return
    if (error.status === 401) { disconnect(error.message); return }
    if (error.status === 404) return // Optional extension, no fake fallback data.
    el('inbox').replaceChildren(); el('inbox-panel').hidden = false; el('inbox-status').textContent = 'Unable to load valid invitations. Reload to retry.'
  }
}
async function loadInvitations(space,current) {
  try {
    const invitations = validateInvitations(await client.request(invitationURL(space.id), {signal:controller.signal}),space.id)
    if (current !== generation) return
    for (const item of invitations) {
      const row = document.createElement('li'), label = document.createElement('span')
      label.textContent = `${item.principal_id} · ${item.role} · ${item.state} · expires ${new Date(item.expires*1000).toISOString()}`
      row.append(label)
      if (['pending','expired','invalidated'].includes(item.state)) {
        const button = document.createElement('button'); button.type = 'button'; button.textContent = 'Revoke'
        button.setAttribute('aria-label',`Revoke invitation for ${item.principal_id}`)
        button.addEventListener('click',()=>openAction({kind:'invitation-revoke',title:'Revoke invitation',
          description:`Revoke the invitation for ${item.principal_id} in ${space.name}. This only cancels this pending invitation, not an already accepted membership.`,
          path:invitationURL(space.id,item.id),body:{state:'revoked'},revision:item.revision,scope:space.id,id:item.id,invitationScope:space.id}))
        row.append(button)
      }
      el('invitations').append(row)
    }
    el('invitation-panel').hidden = false; el('invite-open').hidden = space.state !== 'active'
    el('invitation-status').textContent = `${invitations.length} recent invitations (up to 100, actionable first). Roles are granted only after explicit acceptance.`
  } catch (error) {
    if (current !== generation || error.name === 'AbortError') return
    if (error.status === 401) { disconnect(error.message); return }
    if (error.status === 404) return
    el('invitations').replaceChildren(); el('invitation-panel').hidden = false; el('invitation-status').textContent = 'Unable to load valid invitation state. Reload to retry.'
  }
}
async function loadMembers(id, notice='') {
  const current = cancelRequests(); clearPanel(); selection = id
  const space = workspaces.find(w => w.id === id)
  if (!space) { el('status').textContent = notice || 'No accessible workspaces. Platform controls do not grant data access.'; return }
  el('status').textContent = 'Loading members…'
  try {
    const members = validateMembers(await client.request(membershipURL(id), {signal:controller.signal}), id)
    if (current !== generation) return
    el('title').textContent = space.name
    el('state').textContent = `${space.kind === 'shared' ? 'Shared · explicit membership' : 'Product workspace'} · ${space.state}`
    for (const member of members) {
      const row = document.createElement('li'), label = document.createElement('span'), button = document.createElement('button')
      label.textContent = `${member.principal_id} · ${member.role} · ${member.state} · revision ${member.revision}`
      button.type = 'button'; button.textContent = 'Edit'; button.disabled = member.state !== 'active'; button.setAttribute('aria-label', `Edit ${member.principal_id}`)
      button.addEventListener('click', () => {editing = {...member}; el('editing').textContent = `${member.principal_id} in ${space.name}`; el('role').value = member.role; el('member-state').value = member.state; el('editor').showModal()})
      row.append(label,button); el('members').append(row)
    }
    el('panel').hidden = false; el('status').textContent = notice || `${members.length} memberships. Only server-authorized operations are available.`
    await loadModules(space,current)
    if (current !== generation) return
    await loadInvitations(space,current)
  } catch (error) { failed(error,current) }
}
async function reload(notice='') {
  const current = cancelRequests(); clearPanel(); el('workspace').disabled = true
  const signal = controller.signal
  el('operator-panel').hidden = true; el('control-spaces').replaceChildren(); managementAvailable = false
  el('inbox-panel').hidden = true; el('inbox').replaceChildren(); el('inbox-status').textContent = ''
  el('status').textContent = 'Loading workspaces…'
  try {
    try {
      const capabilities = await client.request('/api/control/capabilities', {signal})
      if (!capabilities || typeof capabilities.manage_workspaces !== 'boolean') throw new Error('Invalid service response')
      if (current !== generation) return
      managementAvailable = true
      if (capabilities.manage_workspaces) {
        const controls = validateWorkspaces(await client.request('/api/control/workspaces', {signal}))
        if (current !== generation) return
        renderControls(controls)
      }
    } catch (error) { if (error.status !== 404) throw error }
    if (current !== generation) return
    await loadInbox(current)
    if (current !== generation) return
    const result = validateWorkspaces(await client.request('/api/workspaces', {signal}))
    if (current !== generation) return
    workspaces = result; el('workspace').replaceChildren()
    for (const space of result) { const option = document.createElement('option'); option.value = space.id; option.textContent = space.name; el('workspace').append(option) }
    selection = result.some(w => w.id === selection) ? selection : result[0]?.id
    el('workspace').value = selection || ''; el('workspace').disabled = !result.length
    await loadMembers(selection,notice)
  } catch (error) { failed(error,current) }
}
async function mutationError(error, current, statusId, scope) {
  if (current !== generation || error.name === 'AbortError') return
  if (error.status === 401) { disconnect(error.message); return }
  if ([400,409,415,428].includes(error.status)) { el(statusId).textContent = error.message; return }
  closeDialogs()
  const notice = [403,404,412].includes(error.status) ? error.message : 'Result uncertain. Current state reloaded; verify before retrying. A repeated identical creation in this page uses the same retry key.'
  if (scope) await loadMembers(scope,notice); else await reload(notice)
}
el('connect').addEventListener('submit', event => {
  event.preventDefault()
  try { client.connect(el('credential').value) } catch { el('status').textContent = 'Enter a valid opaque credential.'; return }
  el('credential').value = ''; el('connect').hidden = true; el('session').hidden = false; reload()
})
el('disconnect').addEventListener('click', () => disconnect())
el('reload').addEventListener('click', () => reload())
el('workspace').addEventListener('change', event => loadMembers(event.target.value))
for (const [dialog,button,close] of [['editor','cancel',closeEditor],['create-dialog','create-cancel',closeCreate],['action-dialog','action-cancel',closeAction],['invite-dialog','invite-cancel',closeInvite]]) {
  el(button).addEventListener('click', close)
  el(dialog).addEventListener('cancel', event => { if (saving) event.preventDefault(); else close() })
}
el('edit-form').addEventListener('submit', async event => {
  event.preventDefault(); if (!editing || saving) return
  const member = {...editing}, current = generation, scope = selection
  const body = {role:el('role').value,state:el('member-state').value}
  const saveTurn = busy(true); el('edit-status').textContent = 'Saving… Do not close the page until the result is known.'
  try {
    const updated = await client.request(membershipURL(scope,member.principal_id), {method:'PATCH',body,revision:member.revision,signal:controller.signal})
    if (current !== generation) return
    validateMembers([updated],scope)
    if (updated.principal_id !== member.principal_id) throw new Error('Invalid result')
    closeEditor(); await loadMembers(scope,'Membership saved. Current access rechecked.')
  } catch (error) { await mutationError(error,current,'edit-status',scope) }
  finally { busy(false,saveTurn) }
})
el('create-open').addEventListener('click', () => { closeCreate(); el('create-dialog').showModal() })
el('create-form').addEventListener('submit', async event => {
  event.preventDefault(); if (saving) return
  const body = {name:el('space-name').value.trim(),kind:el('space-kind').value,initial_admin:el('initial-admin').value.trim()}
  if (!body.name || !validId(body.initial_admin) || !['product','shared'].includes(body.kind)) { el('create-status').textContent = 'Enter a name and an existing verified principal ID.'; return }
  const signature = JSON.stringify(body)
  if (!pendingCreations.has(signature)) pendingCreations.set(signature,crypto.randomUUID())
  const current = generation, requestKey = pendingCreations.get(signature)
  const saveTurn = busy(true); el('create-status').textContent = 'Creating… Do not close the page until the result is known.'
  try {
    const created = await client.request('/api/control/workspaces', {method:'POST',body,requestKey,signal:controller.signal})
    if (current !== generation) return
    validateWorkspaces([created]); pendingCreations.delete(signature); closeCreate()
    await reload(`Workspace created: ${created.name} (${created.id}). Only its nominated administrator receives membership.`)
  } catch (error) { await mutationError(error,current,'create-status') }
  finally { busy(false,saveTurn) }
})
el('install-form').addEventListener('submit', event => {
  event.preventDefault(); const key = el('module-key').value
  if (!validId(key) || !validId(selection)) return
  openAction({kind:'install',title:'Install disabled module',description:`Install the platform-approved ${key} module in this workspace. It starts disabled and grants no connector or execution authority. Enable it separately after checking the configuration.`,path:moduleURL(selection),body:{key},scope:selection,id:key,requestKey:crypto.randomUUID()})
})
el('action-form').addEventListener('submit', async event => {
  event.preventDefault(); if (!action || saving) return
  const item = {...action}, current = generation
  const saveTurn = busy(true); el('action-status').textContent = 'Saving… Do not close the page until the result is known.'
  try {
    const updated = await client.request(item.path, {method:item.kind === 'install' ? 'POST' : 'PATCH',body:item.body,revision:item.revision,requestKey:item.requestKey,signal:controller.signal})
    if (current !== generation) return
    if (item.kind === 'lifecycle') { validateWorkspaces([updated]); if (updated.id !== item.id) throw new Error('Invalid result') }
    else if (item.kind.startsWith('invitation-')) { validateInvitations([updated],item.invitationScope); if (updated.id !== item.id || updated.state !== item.body.state) throw new Error('Invalid result') }
    else { validateModules([updated],item.scope); if (updated.key !== item.id) throw new Error('Invalid result') }
    closeAction()
    if (item.kind === 'invitation-response') await reload(item.body.state === 'accepted' ? 'Workspace joined. Current memberships refreshed.' : 'Invitation declined.')
    else if (item.kind === 'invitation-revoke') await loadMembers(item.scope,'Invitation revoked.')
    else if (item.kind === 'lifecycle') await reload('Workspace lifecycle updated. Current access rechecked.')
    else await loadMembers(item.scope,'Module configuration updated. Current access rechecked.')
  } catch (error) { await mutationError(error,current,'action-status',item.scope) }
  finally { busy(false,saveTurn) }
})
el('invite-open').addEventListener('click',()=>{ closeInvite(); el('invite-dialog').showModal() })
el('invite-form').addEventListener('submit',async event=>{
  event.preventDefault(); if (saving || !validId(selection)) return
  const body = {principal_id:el('invite-principal').value.trim(),role:el('invite-role').value}
  if (!validId(body.principal_id) || !['viewer','developer'].includes(body.role)) { el('invite-status').textContent = 'Enter an existing verified principal ID and bounded role.'; return }
  const scope = selection, current = generation, signature = JSON.stringify([scope,body])
  if (!pendingInvitations.has(signature)) pendingInvitations.set(signature,crypto.randomUUID())
  const saveTurn = busy(true); el('invite-status').textContent = 'Inviting… Verify the result before closing the page.'
  try {
    const item = await client.request(invitationURL(scope),{method:'POST',body,requestKey:pendingInvitations.get(signature),signal:controller.signal})
    if (current !== generation) return
    validateInvitations([item],scope)
    if (item.principal_id !== body.principal_id || item.role !== body.role) throw new Error('Invalid result')
    pendingInvitations.delete(signature); closeInvite(); await loadMembers(scope,'Invitation recorded. The target must sign in and explicitly join; no message or secret link was sent.')
  } catch (error) { await mutationError(error,current,'invite-status',scope) }
  finally { busy(false,saveTurn) }
})
window.addEventListener('pagehide', () => disconnect('Disconnected. Reconnect and verify any uncertain creation before submitting another request.'))
if (location.protocol !== 'https:' && !(location.protocol === 'http:' && location.hostname === '127.0.0.1')) {
  el('connect').hidden = true; el('status').textContent = 'HTTPS is required outside numeric loopback.'
}
