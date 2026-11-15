import { visibleNavigation } from './navigation.mjs'
import { boardURL, validateBoard } from './board.mjs'

const element = id => document.getElementById(id)
let workspaces = []
let generation = 0
let request
let selectedWorkspace
let boardGeneration = 0
let boardRequest
let boardOffset = 0
let appliedBoardFilters = { kind: '', state: '', q: '', limit: 10 }
let boardAvailable = false

function resetPanel() {
  boardGeneration += 1
  boardRequest?.abort()
  boardAvailable = false
  element('community-board').hidden = true
  clearBoard()

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
  selectedWorkspace = id
  element('board-filters').reset()
  boardOffset = 0
  appliedBoardFilters = { kind: '', state: '', q: '', limit: 10 }
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
    boardAvailable = navigation.some(item => item.id === 'community-board')
    element('community-board').hidden = !boardAvailable
    element('workspace-title').textContent = workspace.name
    element('workspace-kind').textContent = workspace.kind === 'public'
      ? 'Shared · membership required' : 'Product workspace'
    element('workspace-panel').hidden = false
    element('status').textContent = 'Synthetic demo loaded. Nothing is connected to production.'
    if (boardAvailable) await showBoard()
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

function clearBoard() {
  element('board-items').replaceChildren()
  element('board-sources').replaceChildren()
  element('board-page').textContent = ''
  element('board-status').textContent = ''
  element('board-prev').disabled = true
  element('board-next').disabled = true
}

function boardFilters() {
  return { kind: element('board-kind').value, state: element('board-state').value,
    q: element('board-query').value, limit: Number(element('board-limit').value), offset: boardOffset }
}

async function showBoard() {
  const current = ++boardGeneration
  boardRequest?.abort()
  boardRequest = new AbortController()
  const id = selectedWorkspace
  clearBoard()
  if (!boardAvailable || !id) return
  element('board-status').textContent = 'Loading observed items…'
  try {
    const filters = { ...appliedBoardFilters, offset: boardOffset }
    const page = validateBoard(await read(boardURL(id, filters), boardRequest.signal), id, filters)
    if (current !== boardGeneration || id !== selectedWorkspace) return
    for (const source of page.sources) {
      const item = document.createElement('li')
      item.textContent = `${source.repository} · ${source.connection_id} · observed ${source.observed_at}`
      element('board-sources').append(item)
    }
    for (const item of page.items) {
      const row = document.createElement('li')
      const heading = document.createElement('h4')
      heading.textContent = `${item.kind === 'pull' ? 'PR' : 'Issue'} #${item.number}: ${item.title}`
      const detail = document.createElement('p')
      detail.textContent = `${item.repository} · ${item.connection_id} · ${item.state} · updated ${item.updated_at}`
      const labels = document.createElement('p')
      labels.className = 'board-labels'
      labels.textContent = item.labels.join(' · ')
      row.append(heading, detail, labels)
      element('board-items').append(row)
    }
    element('board-status').textContent = page.items.length ? `${page.total} matching observed items. Not a live or complete repository inventory.` : 'No matching observed items.'
    element('board-page').textContent = page.total ? `${page.offset + 1}–${Math.min(page.offset + page.items.length, page.total)} of ${page.total}` : '0 items'
    element('board-prev').disabled = page.offset === 0
    element('board-next').disabled = page.offset + page.limit >= page.total
  } catch (error) {
    if (current !== boardGeneration || error.name === 'AbortError') return
    clearBoard()
    element('board-status').textContent = `${error.message} Use Apply filters to retry.`
  }
}

element('board-filters').addEventListener('submit', event => {
  event.preventDefault()
  boardOffset = 0
  appliedBoardFilters = boardFilters()
  showBoard()
})
element('board-prev').addEventListener('click', () => {
  boardOffset = Math.max(0, boardOffset - appliedBoardFilters.limit)
  showBoard()
})
element('board-next').addEventListener('click', () => {
  boardOffset += appliedBoardFilters.limit
  showBoard()
})

element('workspace').addEventListener('change', event => showWorkspace(event.target.value))
element('retry').addEventListener('click', initialize)
window.addEventListener('pagehide', () => { generation += 1; boardGeneration += 1; request?.abort(); boardRequest?.abort() })
initialize()
