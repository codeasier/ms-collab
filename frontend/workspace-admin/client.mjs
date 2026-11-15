const idPattern = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/
export const validId = value => typeof value === 'string' && idPattern.test(value)
const revision = value => Number.isSafeInteger(value) && value > 0
export function validateWorkspaces(data) {
  if (!Array.isArray(data) || data.some(w => !w || !validId(w.id) || typeof w.name !== 'string' || w.name.length > 200 || !['product', 'shared'].includes(w.kind) || !['active', 'suspended', 'archived'].includes(w.state) || !revision(w.revision)) || new Set(data.map(w => w.id)).size !== data.length) throw new Error('Invalid service response.')
  return data
}
export function validateMembers(data, workspace) {
  if (!validId(workspace) || !Array.isArray(data) || data.some(m => !m || m.workspace_id !== workspace || !validId(m.principal_id) || !['viewer','developer','admin'].includes(m.role) || !['active','revoked'].includes(m.state) || !revision(m.revision)) || new Set(data.map(m => m.principal_id)).size !== data.length) throw new Error('Invalid service response.')
  return data
}
export function membershipURL(workspace, member) {
  if (!validId(workspace) || (member !== undefined && !validId(member))) throw new Error('Invalid scope.')
  return `/api/workspaces/${encodeURIComponent(workspace)}/members${member === undefined ? '' : '/' + encodeURIComponent(member)}`
}
export function messageFor(status) {
  return ({401:'Authentication expired or was rejected. Reconnect with a valid credential.',403:'You do not have administrator permission.',404:'This workspace is unavailable or no longer accessible.',409:'This change is not allowed, including removing the last administrator or increasing archived access.',412:'The membership changed elsewhere. Reload before editing again.',428:'A fresh membership revision is required.',503:'The service is temporarily unavailable. Retry after checking its status.'})[status] || 'The service could not complete the request.'
}
export function createClient({fetcher = fetch} = {}) {
  let credential = ''
  return {
    connect(value) { if (typeof value !== 'string' || !value || value.length > 8192 || /\s/.test(value)) throw new Error('Enter a valid opaque credential.'); credential = value },
    clear() { credential = '' },
    async request(path, {method='GET', body, revision, signal} = {}) {
      if (!credential) throw new Error('Disconnected.')
      if (path !== '/api/workspaces' && !/^\/api\/workspaces\/[A-Za-z0-9%._:-]+\/members(?:\/[A-Za-z0-9%._:-]+)?$/.test(path)) throw new Error('Invalid service path.')
      const headers = {Authorization: `Bearer ${credential}`}
      if (body !== undefined) { headers['Content-Type'] = 'application/json'; headers['If-Match'] = `"${revision}"` }
      const response = await fetcher(path, {method, headers, body:body === undefined ? undefined : JSON.stringify(body), signal, credentials:'omit', cache:'no-store', redirect:'error'})
      if (!response.ok) { const error = new Error(messageFor(response.status)); error.status = response.status; throw error }
      const payload = await response.json()
      if (!payload || !Object.hasOwn(payload,'data')) throw new Error('Invalid service response.')
      return payload.data
    }
  }
}
