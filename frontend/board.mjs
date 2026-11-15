// Copyright (c) 2026 codeasier. MIT; see ../LICENSE.
// Only request filters live here. Identity is never supplied by the UI.
export function boardURL(workspaceId, filters) {
  if (!/^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$/.test(workspaceId)) throw new Error('Invalid workspace.')
  const { kind = '', state = '', q = '', limit = 10, offset = 0 } = filters
  if (!['', 'issue', 'pull'].includes(kind) || !['', 'open', 'closed', 'merged'].includes(state) ||
      typeof q !== 'string' || q.length > 200 || !Number.isInteger(limit) || limit < 1 || limit > 100 ||
      !Number.isInteger(offset) || offset < 0 || offset > 9999999) throw new Error('Invalid board filter.')
  const params = new URLSearchParams({ kind, state, q, limit: String(limit), offset: String(offset) })
  return `/api/workspaces/${encodeURIComponent(workspaceId)}/community-board?${params}`
}

export function validateBoard(data, workspaceId, filters) {
  const integer = n => Number.isSafeInteger(n) && n >= 0
  const scoped = item => item && item.workspace_id === workspaceId &&
    typeof item.connection_id === 'string' && typeof item.repository === 'string'
  if (!data || data.mode !== 'synthetic-demo' || data.schema !== 'ms-collab/community-board/v1' ||
      data.coverage !== 'observed_subset' || data.workspace_id !== workspaceId ||
      !integer(data.total) || data.offset !== filters.offset || data.limit !== filters.limit ||
      !Array.isArray(data.sources) || !Array.isArray(data.items) || data.items.length > data.limit ||
      !data.sources.every(source => scoped(source) && typeof source.observed_at === 'string') ||
      !data.items.every(item => scoped(item) && typeof item.title === 'string' &&
        ['issue', 'pull'].includes(item.kind) && ['open', 'closed', 'merged'].includes(item.state) &&
        integer(item.number) && item.number > 0 && typeof item.updated_at === 'string' &&
        Array.isArray(item.labels) && item.labels.every(label => typeof label === 'string'))) {
    throw new Error('Invalid scoped board response.')
  }
  return data
}
