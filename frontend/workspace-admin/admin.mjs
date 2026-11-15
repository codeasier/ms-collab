import {createClient, validId, validateWorkspaces, validateMembers, validateModules, validateCatalog, membershipURL, moduleURL} from './client.mjs'
const el = id => document.getElementById(id)
const client = createClient()
const pendingCreations = new Map()
let generation = 0, controller, workspaces = [], selection, editing, action, saving = false
let managementAvailable = false
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
function closeDialogs() { closeEditor(); closeCreate(); closeAction() }
function clearPanel() {
  closeDialogs(); el('panel').hidden = true; el('members').replaceChildren()
  el('title').textContent = ''; el('state').textContent = ''
  el('module-panel').hidden = true; el('modules').replaceChildren(); el('module-key').replaceChildren()
  el('module-status').textContent = ''; el('install-form').hidden = true
}
function busy(value) {
  saving = value
  for (const control of document.querySelectorAll('dialog button,dialog input,dialog select')) control.disabled = value
}
function disconnect(message='Disconnected. Any already accepted change may have completed; reload after reconnecting to verify.') {
  cancelRequests(); client.clear(); clearPanel(); workspaces = []; selection = undefined
  managementAvailable = false; pendingCreations.clear()
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
  } catch (error) { failed(error,current) }
}
async function reload(notice='') {
  const current = cancelRequests(); clearPanel(); el('workspace').disabled = true
  const signal = controller.signal
  el('operator-panel').hidden = true; el('control-spaces').replaceChildren(); managementAvailable = false
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
for (const [dialog,button,close] of [['editor','cancel',closeEditor],['create-dialog','create-cancel',closeCreate],['action-dialog','action-cancel',closeAction]]) {
  el(button).addEventListener('click', close)
  el(dialog).addEventListener('cancel', event => { if (saving) event.preventDefault(); else close() })
}
el('edit-form').addEventListener('submit', async event => {
  event.preventDefault(); if (!editing || saving) return
  const member = {...editing}, current = generation, scope = selection
  const body = {role:el('role').value,state:el('member-state').value}
  busy(true); el('edit-status').textContent = 'Saving… Do not close the page until the result is known.'
  try {
    const updated = await client.request(membershipURL(scope,member.principal_id), {method:'PATCH',body,revision:member.revision,signal:controller.signal})
    if (current !== generation) return
    validateMembers([updated],scope)
    if (updated.principal_id !== member.principal_id) throw new Error('Invalid result')
    closeEditor(); await loadMembers(scope,'Membership saved. Current access rechecked.')
  } catch (error) { await mutationError(error,current,'edit-status',scope) }
  finally { busy(false) }
})
el('create-open').addEventListener('click', () => { closeCreate(); el('create-dialog').showModal() })
el('create-form').addEventListener('submit', async event => {
  event.preventDefault(); if (saving) return
  const body = {name:el('space-name').value.trim(),kind:el('space-kind').value,initial_admin:el('initial-admin').value.trim()}
  if (!body.name || !validId(body.initial_admin) || !['product','shared'].includes(body.kind)) { el('create-status').textContent = 'Enter a name and an existing verified principal ID.'; return }
  const signature = JSON.stringify(body)
  if (!pendingCreations.has(signature)) pendingCreations.set(signature,crypto.randomUUID())
  const current = generation, requestKey = pendingCreations.get(signature)
  busy(true); el('create-status').textContent = 'Creating… Do not close the page until the result is known.'
  try {
    const created = await client.request('/api/control/workspaces', {method:'POST',body,requestKey,signal:controller.signal})
    if (current !== generation) return
    validateWorkspaces([created]); pendingCreations.delete(signature); closeCreate()
    await reload(`Workspace created: ${created.name} (${created.id}). Only its nominated administrator receives membership.`)
  } catch (error) { await mutationError(error,current,'create-status') }
  finally { busy(false) }
})
el('install-form').addEventListener('submit', event => {
  event.preventDefault(); const key = el('module-key').value
  if (!validId(key) || !validId(selection)) return
  openAction({kind:'install',title:'Install disabled module',description:`Install the platform-approved ${key} module in this workspace. It starts disabled and grants no connector or execution authority. Enable it separately after checking the configuration.`,path:moduleURL(selection),body:{key},scope:selection,id:key,requestKey:crypto.randomUUID()})
})
el('action-form').addEventListener('submit', async event => {
  event.preventDefault(); if (!action || saving) return
  const item = {...action}, current = generation
  busy(true); el('action-status').textContent = 'Saving… Do not close the page until the result is known.'
  try {
    const updated = await client.request(item.path, {method:item.kind === 'install' ? 'POST' : 'PATCH',body:item.body,revision:item.revision,requestKey:item.requestKey,signal:controller.signal})
    if (current !== generation) return
    if (item.kind === 'lifecycle') { validateWorkspaces([updated]); if (updated.id !== item.id) throw new Error('Invalid result') }
    else { validateModules([updated],item.scope); if (updated.key !== item.id) throw new Error('Invalid result') }
    closeAction()
    if (item.kind === 'lifecycle') await reload('Workspace lifecycle updated. Current access rechecked.')
    else await loadMembers(item.scope,'Module configuration updated. Current access rechecked.')
  } catch (error) { await mutationError(error,current,'action-status',item.scope) }
  finally { busy(false) }
})
window.addEventListener('pagehide', () => disconnect('Disconnected. Reconnect and verify any uncertain creation before submitting another request.'))
if (location.protocol !== 'https:' && !(location.protocol === 'http:' && location.hostname === '127.0.0.1')) {
  el('connect').hidden = true; el('status').textContent = 'HTTPS is required outside numeric loopback.'
}
