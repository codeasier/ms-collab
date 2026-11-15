const segment = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/
const digest = /^[0-9a-f]{64}$/
const revision = value => Number.isSafeInteger(value) && value > 0
const invalid = () => { throw new Error('Invalid service response.') }
export function scopeFrom(workspace, connection, repository) {
  if (typeof repository !== 'string') throw new Error('Invalid scope.')
  const parts = repository.split('/')
  if (parts.length !== 2 || ![workspace, connection, ...parts].every(x => typeof x === 'string' && segment.test(x))) throw new Error('Invalid scope.')
  return {workspace_id:workspace, connection_id:connection, repository}
}
export const sameScope = (a,b) => a && b && ['workspace_id','connection_id','repository'].every(key => a[key] === b[key])
export function baseURL(scope) {
  scopeFrom(scope.workspace_id, scope.connection_id, scope.repository)
  return `/api/workspaces/${scope.workspace_id}/community/${scope.connection_id}/${scope.repository}`
}
export function validateCapabilities(data, scope) {
  if (!data || !sameScope(data.scope, scope) || !revision(data.revision) || !data.capabilities || ['items','review','refresh','approve'].some(key => typeof data.capabilities[key] !== 'boolean') || !Object.values(data.capabilities).some(x => x === true) || (data.capabilities.approve && !data.capabilities.review)) invalid()
  return data
}
export function validatePage(data, scope, view, expectedRevision, offset) {
  if (!data || !sameScope(data.scope,scope) || !revision(data.revision) || data.revision !== expectedRevision || typeof data.available !== 'boolean' || !Array.isArray(data.items)) invalid()
  if (!data.available) { if (offset || data.items.length) invalid(); return data }
  if (data.schema !== 'ms-collab/community-board/v1' || data.workspace_id !== scope.workspace_id || data.coverage !== 'observed_subset' || data.sharing !== 'explicit_content_approval' || data.view !== view || data.offset !== offset || data.limit !== 100 || !Number.isSafeInteger(data.total) || data.total < 0 || data.total > 10000 || data.items.length !== Math.min(100,Math.max(0,data.total-offset)) || !Array.isArray(data.sources) || data.sources.length !== 1 || !sameScope(data.sources[0],scope) || !Number.isFinite(Date.parse(data.sources[0].observed_at))) invalid()
  for (const item of data.items) {
    if (!sameScope(item,scope) || !['issue','pull'].includes(item.kind) || !revision(item.number) || !['open','closed','merged'].includes(item.state) || (item.kind === 'issue' && item.state === 'merged') || typeof item.title !== 'string' || !item.title.length || item.title.length > 1000 || typeof item.updated_at !== 'string' || !Number.isFinite(Date.parse(item.updated_at)) || !Array.isArray(item.labels) || item.labels.length > 100 || item.labels.some(x => typeof x !== 'string' || !x.length || x.length > 100)) invalid()
    if (view === 'review' && (typeof item.fingerprint !== 'string' || !digest.test(item.fingerprint) || typeof item.approved !== 'boolean')) invalid()
  }
  return data
}
export function validateMutation(data, scope, previous, action, count) {
  if (!data || !sameScope(data.scope,scope) || data.revision !== previous + 1 || !Number.isSafeInteger(data.approved_count) || data.approved_count < 0 || data.approved_count > 200 || (action === 'approve' && data.approved_count !== count)) invalid()
  return data
}
export function messageFor(status) {
  return ({401:'Authentication expired or was rejected. Reconnect with a valid credential.',403:'Your current workspace permissions do not allow this action.',404:'This scope or capability is unavailable to your current identity.',409:'The connection or observation changed. Reload before trying again.',412:'The version changed elsewhere. Reload and review the current content before retrying.',428:'A fresh connection version is required.',502:'Provider refresh failed. No new observation was published.',503:'The service is unavailable or refresh is not configured.'})[status] || 'The service could not complete the request.'
}
export function createClient({fetcher = fetch} = {}) {
  let credential = ''
  return {
    connect(value) { if (typeof value !== 'string' || !value || value.length > 8192 || /\s/.test(value)) throw new Error('Enter a valid opaque credential.'); credential = value },
    clear() { credential = '' },
    async request(scope, action, {offset = 0, body, revision:version, signal} = {}) {
      if (!credential) throw new Error('Disconnected.')
      const write = ['refresh','approve'].includes(action)
      if (!['capabilities','items','review','refresh','approve'].includes(action) || (!write && body !== undefined) || (write && (!revision(version) || !body))) throw new Error('Invalid request.')
      if (!Number.isSafeInteger(offset) || offset < 0 || offset > 10000) throw new Error('Invalid page.')
      const url = baseURL(scope) + '/' + action + (['items','review'].includes(action) ? `?limit=100&offset=${offset}` : '')
      const headers = {Authorization:`Bearer ${credential}`}
      if (write) { headers['Content-Type'] = 'application/json'; headers['If-Match'] = `"${version}"` }
      const response = await fetcher(url,{method:write?'POST':'GET',headers,body:write?JSON.stringify(body):undefined,signal,credentials:'omit',cache:'no-store',redirect:'error'})
      if (!response.ok) { const error = new Error(messageFor(response.status)); error.status=response.status; throw error }
      const payload = await response.json()
      if (!payload || !Object.hasOwn(payload,'data')) invalid()
      return payload.data
    }
  }
}
export async function loadSnapshot(client, scope, view, version, signal) {
  const records = [], keys = new Set(), fingerprints = new Set()
  let total, observation
  for (let offset=0; offset<10000; offset+=100) {
    const page = validatePage(await client.request(scope,view,{offset,signal}),scope,view,version,offset)
    if (!page.available) return {items:[],available:false,observed_at:null}
    if (total === undefined) { total=page.total; observation=page.sources[0].observed_at }
    if (page.total !== total || page.sources[0].observed_at !== observation) invalid()
    for (const item of page.items) {
      const key = `${item.kind}:${item.number}`
      if (keys.has(key) || (view === 'review' && fingerprints.has(item.fingerprint))) invalid()
      keys.add(key); fingerprints.add(item.fingerprint); records.push(item)
    }
    if (records.length === total) {
      if (view === 'review' && records.filter(x=>x.approved).length>200) invalid()
      return {items:records,available:true,observed_at:observation}
    }
  }
  invalid()
}
