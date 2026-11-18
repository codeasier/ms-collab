const idPattern = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/
export const validId = value => typeof value === 'string' && !/[\u0000-\u0020\u007f]/.test(value) && idPattern.test(value)
export const validInitialAdminPrecondition = value => typeof value === 'string' && /^ias1-[a-f0-9]{64}$/.test(value) && value.length === 69
export function initialAdminSelectionURL(principal) {
  if (!validId(principal)) throw new Error('Invalid initial administrator ID.')
  return `/api/control/initial-admin-selections/${encodeURIComponent(principal)}`
}
export function validateInitialAdminSelection(data, principal) {
  if (!validId(principal) || !data || typeof data !== 'object' || Array.isArray(data) || Object.keys(data).sort().join(',') !== 'initial_admin,precondition' || data.initial_admin !== principal || !validInitialAdminPrecondition(data.precondition)) throw new Error('Invalid identity selection response.')
  return data
}
export function initialAdminSelectionRequest(path, method, precondition) {
  if (path.startsWith('/api/control/initial-admin-selections')) {
    const suffix = path.slice('/api/control/initial-admin-selections/'.length)
    let principal
    try { principal = decodeURIComponent(suffix) } catch { throw new Error('Invalid identity selection path.') }
    if (method !== 'GET' || !validId(principal) || path !== initialAdminSelectionURL(principal)) throw new Error('Invalid identity selection path.')
  }
  if (precondition !== undefined && (path !== '/api/control/workspaces' || method !== 'POST' || !validInitialAdminPrecondition(precondition))) throw new Error('Invalid initial administrator precondition.')
}
const revision = value => Number.isSafeInteger(value) && value > 0
export function validateWorkspaces(data) {
  if (!Array.isArray(data) || data.some(w => !w || !validId(w.id) || typeof w.name !== 'string' || w.name.length > 200 || !['product', 'shared'].includes(w.kind) || !['active', 'suspended', 'archived'].includes(w.state) || !revision(w.revision)) || new Set(data.map(w => w.id)).size !== data.length) throw new Error('Invalid service response.')
  return data
}
export function validateMembers(data, workspace) {
  if (!validId(workspace) || !Array.isArray(data) || data.some(m => !m || m.workspace_id !== workspace || !validId(m.principal_id) || !['viewer','developer','admin'].includes(m.role) || !['active','revoked'].includes(m.state) || !revision(m.revision)) || new Set(data.map(m => m.principal_id)).size !== data.length) throw new Error('Invalid service response.')
  return data
}
export function validateModules(data, workspace) {
  if (!validId(workspace) || !Array.isArray(data) || data.some(m => !m || m.workspace_id !== workspace || !validId(m.key) || !['enabled','disabling','disabled'].includes(m.state) || !revision(m.revision) || !Array.isArray(m.capabilities) || m.capabilities.some(c => !['read','write'].includes(c)) || new Set(m.capabilities).size !== m.capabilities.length) || new Set(data.map(m => m.key)).size !== data.length) throw new Error('Invalid service response.')
  return data
}
export function validateCatalog(data) {
  if (!Array.isArray(data) || data.some(m => !m || !validId(m.key) || !Array.isArray(m.capabilities) || !m.capabilities.length || m.capabilities.some(c => !['read','write'].includes(c)) || new Set(m.capabilities).size !== m.capabilities.length) || new Set(data.map(m => m.key)).size !== data.length) throw new Error('Invalid service response.')
  return data
}
export function moduleURL(workspace, key) {
  if (!validId(workspace) || (key !== undefined && !validId(key))) throw new Error('Invalid scope.')
  return `/api/workspaces/${encodeURIComponent(workspace)}/modules${key === undefined ? '' : '/' + encodeURIComponent(key)}`
}
export function membershipURL(workspace, member) {
  if (!validId(workspace) || (member !== undefined && !validId(member))) throw new Error('Invalid scope.')
  return `/api/workspaces/${encodeURIComponent(workspace)}/members${member === undefined ? '' : '/' + encodeURIComponent(member)}`
}
export function messageFor(status) {
  return ({401:'Authentication expired or was rejected. Reconnect with a valid credential.',403:'You do not have the required administrator permission.',404:'This workspace is unavailable or no longer accessible.',409:'This change conflicts with the current lifecycle, approved catalog, retry key or last administrator protection.',412:'This record changed elsewhere. Reload before editing again.',428:'A fresh record revision is required.',503:'The service is temporarily unavailable. Retry after checking its status.'})[status] || 'The service could not complete the request.'
}

// Invitation-only codecs. The Cookie transport has the same exact wire rules;
// both executed Node roots register the shared invitation contract cases.
const exactFields = (value, fields) => value !== null && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).sort().join(',') === [...fields].sort().join(',')
export const validInvitationRecipientPrecondition = value => typeof value === 'string' && value.length === 69 && /^irs1-[a-f0-9]{64}$/.test(value)
export function invitationSelectionURL(workspace, principal, role) {
  if (!validId(workspace) || !validId(principal) || !['viewer','developer'].includes(role)) throw new Error('Invalid invitation selection.')
  return `${invitationURL(workspace)}/selections/${encodeURIComponent(principal)}/${role}`
}
export function validateInvitationSelection(data, workspace, principal, role) {
  invitationSelectionURL(workspace, principal, role)
  if (!exactFields(data, ['workspace_id','principal_id','role','precondition']) || data.workspace_id !== workspace || data.principal_id !== principal || data.role !== role || !validInvitationRecipientPrecondition(data.precondition)) throw new Error('Invalid invitation selection response.')
  return data
}
export function invitationSelectionRequest(path, method, body, precondition, isCurrent) {
  let review, workspace
  const parts = path.split('/')
  if (parts[1] === 'api' && parts[2] === 'workspaces' && parts[4] === 'invitations' && (parts[5]?.startsWith('selections') || (method === 'POST' && parts.length === 5))) {
    try { workspace = decodeURIComponent(parts[3]) } catch { throw new Error('Invalid invitation path.') }
    if (!validId(workspace) || parts[3] !== encodeURIComponent(workspace)) throw new Error('Invalid invitation path.')
    if (parts[5]?.startsWith('selections')) {
      let principal
      try { principal = decodeURIComponent(parts[6]) } catch { throw new Error('Invalid invitation selection path.') }
      if (method !== 'GET' || body !== undefined || path !== invitationSelectionURL(workspace, principal, parts[7])) throw new Error('Invalid invitation selection path.')
      review = {workspace, principal, role:parts[7]}
    }
  }
  const creation = validId(workspace) && method === 'POST' && path === invitationURL(workspace)
  if (creation && (!exactFields(body,['principal_id','role']) || !validId(body.principal_id) || !['viewer','developer'].includes(body.role))) throw new Error('Invalid invitation body.')
  if (precondition !== undefined && (!creation || !validInvitationRecipientPrecondition(precondition))) throw new Error('Invalid invitation recipient precondition.')
  if (isCurrent !== undefined && ((!creation && !review) || typeof isCurrent !== 'function')) throw new Error('Invalid invitation response guard.')
  return {review, creation, workspace, recipient:creation ? {principal_id:body.principal_id,role:body.role} : undefined}
}
export function assertCurrentInvitation(isCurrent) {
  if (isCurrent !== undefined && !isCurrent()) { const error = new Error('Stale invitation response discarded.'); error.name = 'AbortError'; throw error }
}
export function validateInvitationResult(payload, request) {
  if (!exactFields(payload,['data'])) throw new Error('Invalid invitation response.')
  if (request.review) {
    const {workspace,principal,role} = request.review
    return validateInvitationSelection(payload.data,workspace,principal,role)
  }
  const item = payload.data
  if (!exactFields(item,['id','workspace_id','workspace_name','principal_id','role','state','created','expires','revision'])) throw new Error('Invalid invitation response.')
  validateInvitations([item],request.workspace)
  if (item.principal_id !== request.recipient.principal_id || item.role !== request.recipient.role) throw new Error('Invalid invitation response.')
  return item
}
export async function invitationSelectionRejected(response) {
  // A post-read bound is not a streaming memory limit. Never expose raw errors.
  try {
    const raw = await response.text()
    if (raw.length > 128) return false
    const payload = JSON.parse(raw)
    return exactFields(payload,['error']) && payload.error === 'invitation_recipient_selection_changed'
  } catch { return false }
}

export function createClient({fetcher = fetch} = {}) {
  let credential = ''
  return {
    connect(value) { if (typeof value !== 'string' || !value || value.length > 8192 || /\s/.test(value)) throw new Error('Enter a valid opaque credential.'); credential = value },
    clear() { credential = '' },
    async request(path, {method='GET', body, revision, requestKey, initialAdminPrecondition, invitationRecipientPrecondition, invitationIsCurrent, signal} = {}) {
      if (!credential) throw new Error('Disconnected.')
      const invitation = typeof path === 'string' ? invitationSelectionRequest(path, method, body, invitationRecipientPrecondition, invitationIsCurrent) : {}
      if (typeof path !== 'string' || /[\u0000-\u0020\u007f]/.test(path) || !['GET','POST','PATCH'].includes(method) || (method === 'GET' && body !== undefined) || (!invitation.review && path !== '/api/workspaces' && !/^\/api\/invitations(?:\/[A-Za-z0-9%._:-]+)?$/.test(path) && !/^\/api\/workspaces\/[A-Za-z0-9%._:-]+\/(?:members|modules|invitations)(?:\/[A-Za-z0-9%._:-]+)?$/.test(path) && !/^\/api\/workspaces\/[A-Za-z0-9%._:-]+\/module-catalog$/.test(path) && !/^\/api\/control\/(?:capabilities|workspaces(?:\/[A-Za-z0-9%._:-]+)?|initial-admin-selections\/[A-Za-z0-9%._:-]+)$/.test(path))) throw new Error('Invalid service path.')
      initialAdminSelectionRequest(path, method, initialAdminPrecondition)
      const headers = {Authorization: `Bearer ${credential}`}
      if (body !== undefined) headers['Content-Type'] = 'application/json'
      if (revision !== undefined) headers['If-Match'] = `"${revision}"`
      if (requestKey !== undefined) headers['Idempotency-Key'] = requestKey
      if (initialAdminPrecondition !== undefined) headers['If-Initial-Admin-Match'] = initialAdminPrecondition
      if (invitationRecipientPrecondition !== undefined) headers['If-Invitation-Recipient-Match'] = invitationRecipientPrecondition
      const response = await fetcher(path, {method, headers, body:body === undefined ? undefined : JSON.stringify(body), signal, credentials:'omit', cache:'no-store', redirect:'error'})
      assertCurrentInvitation(invitationIsCurrent)
      if (!response.ok) {
        const error = new Error(messageFor(response.status)); error.status = response.status
        if (invitation.creation && response.status === 412) error.invitationSelectionRejected = await invitationSelectionRejected(response)
        assertCurrentInvitation(invitationIsCurrent)
        throw error
      }
      if ((invitation.review || invitation.creation) && response.status !== 200) throw new Error('Invalid invitation response.')
      let payload
      try { payload = await response.json() } finally { assertCurrentInvitation(invitationIsCurrent) }
      if (invitation.review || invitation.creation) return validateInvitationResult(payload,invitation)
      if (!payload || !Object.hasOwn(payload,'data')) throw new Error('Invalid service response.')
      return payload.data
    }
  }
}

export function invitationURL(workspace, id) {
  if ((workspace !== undefined && !validId(workspace)) || (id !== undefined && !validId(id))) throw new Error('Invalid scope.')
  return (workspace === undefined ? '/api/invitations' : `/api/workspaces/${encodeURIComponent(workspace)}/invitations`) + (id === undefined ? '' : '/' + encodeURIComponent(id))
}
export function validateInvitations(data, workspace) {
  if ((workspace !== undefined && !validId(workspace)) || !Array.isArray(data) || data.length > 100 || data.some(i => !i || !validId(i.id) || !validId(i.workspace_id) || (workspace !== undefined && i.workspace_id !== workspace) || !validId(i.principal_id) || typeof i.workspace_name !== 'string' || i.workspace_name.length > 200 || !['viewer','developer'].includes(i.role) || !['pending','accepted','declined','revoked','expired','invalidated'].includes(i.state) || !Number.isSafeInteger(i.created) || !Number.isSafeInteger(i.expires) || i.expires <= i.created || !revision(i.revision)) || new Set(data.map(i=>i.id)).size !== data.length) throw new Error('Invalid invitation response.')
  return data
}
