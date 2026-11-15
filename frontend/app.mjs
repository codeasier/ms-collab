import { visibleNavigation } from './navigation.mjs'

const element = id => document.getElementById(id)
let workspaces = []
let generation = 0
let request

function resetPanel() {
  element('workspace-panel').hidden = true
  element('overview').hidden = true
  element('modules').replaceChildren()
  for (const id of ['summary', 'repositories', 'open-items', 'workspace-title', 'workspace-kind']) {
    element(id).textContent = ''
  }
}

async function read(path, signal) {
  const response = await fetch(path, { signal, credentials: 'omit', cache: 'no-store' })
  if (!response.ok) throw new Error('The local demo could not load this workspace.')
  return response.json()
}

async function showWorkspace(id) {
  const current = ++generation
  request?.abort()
  request = new AbortController()
  resetPanel()
  element('status').textContent = 'Loading workspace…'
  const workspace = workspaces.find(item => item.id === id)
  if (!workspace) {
    element('status').textContent = 'Select an available workspace.'
    return
  }
  try {
    const prefix = `/api/workspaces/${encodeURIComponent(id)}`
    const modules = await read(`${prefix}/modules`, request.signal)
    if (current !== generation) return
    if (modules.workspace_id !== id || !Array.isArray(modules.items)) throw new Error('Invalid workspace response.')
    const capabilities = Object.fromEntries(modules.items.map(item => [item.capability, true]))
    const navigation = visibleNavigation(modules.items, capabilities)
    for (const item of navigation) {
      const label = document.createElement('span')
      label.className = 'module'
      label.textContent = item.name
      element('modules').append(label)
    }
    if (navigation.some(item => item.id === 'overview')) {
      const overview = await read(`${prefix}/overview`, request.signal)
      if (current !== generation) return
      if (overview.workspace_id !== id) throw new Error('Invalid workspace response.')
      element('summary').textContent = overview.data.summary
      element('repositories').textContent = overview.data.repositories
      element('open-items').textContent = overview.data.open_items
      element('overview').hidden = false
    }
    element('workspace-title').textContent = workspace.name
    element('workspace-kind').textContent = workspace.kind === 'public'
      ? 'Shared · membership required' : 'Product workspace'
    element('workspace-panel').hidden = false
    element('status').textContent = 'Synthetic demo loaded. Nothing is connected to production.'
  } catch (error) {
    if (current !== generation || error.name === 'AbortError') return
    resetPanel()
    element('status').textContent = `${error.message} Use Reload to retry.`
  }
}

async function initialize() {
  const current = ++generation
  request?.abort()
  request = new AbortController()
  resetPanel()
  element('workspace').disabled = true
  element('workspace').replaceChildren()
  element('identity').textContent = ''
  element('status').textContent = 'Loading the demo…'
  try {
    const data = await read('/api/workspaces', request.signal)
    if (current !== generation) return
    if (data.mode !== 'synthetic-demo' || !Array.isArray(data.items)) throw new Error('Invalid demo response.')
    workspaces = data.items
    element('identity').textContent = `Synthetic identity: ${data.user}`
    for (const workspace of workspaces) {
      const option = document.createElement('option')
      option.value = workspace.id
      option.textContent = workspace.name
      element('workspace').append(option)
    }
    element('workspace').disabled = workspaces.length === 0
    await showWorkspace(workspaces[0]?.id)
  } catch (error) {
    if (current !== generation || error.name === 'AbortError') return
    element('status').textContent = `${error.message} Use Reload to retry.`
  }
}

element('workspace').addEventListener('change', event => showWorkspace(event.target.value))
element('retry').addEventListener('click', initialize)
window.addEventListener('pagehide', () => { generation += 1; request?.abort() })
initialize()
