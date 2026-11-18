const idPattern = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/
export const validId = value => typeof value === 'string' && !/[\u0000-\u0020\u007f]/.test(value) && idPattern.test(value)
// Policy response identities are not URL identifiers. Match Python isspace and
// code-point length exactly; FEFF, NUL and DEL remain legal opaque values.
export function validPolicyId(value) {
  if (typeof value !== 'string') return false
  const points = Array.from(value)
  return points.length >= 1 && points.length <= 200 && !points.some(c => {
    const n = c.codePointAt(0)
    return (n >= 0xd800 && n <= 0xdfff) || (n >= 9 && n <= 13) || (n >= 0x1c && n <= 0x20) || [0x85,0xa0,0x1680,0x2028,0x2029,0x202f,0x205f,0x3000].includes(n) || (n >= 0x2000 && n <= 0x200a)
  })
}
export function displayPolicyId(value) {
  if (!validPolicyId(value)) throw new Error('Invalid identity response.')
  return Array.from(value, c => {
    const n = c.codePointAt(0)
    if (c === '\\') return '\\\\'
    return n < 0x20 || (n >= 0x7f && n <= 0x9f) || /\p{Cf}/u.test(c) ? `\\u{${n.toString(16).toUpperCase()}}` : c
  }).join('')
}
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
  if (!validId(workspace) || !Array.isArray(data) || data.some(m => !m || m.workspace_id !== workspace || !validPolicyId(m.principal_id) || !['viewer','developer','admin'].includes(m.role) || !['active','revoked'].includes(m.state) || !revision(m.revision)) || new Set(data.map(m => m.principal_id)).size !== data.length) throw new Error('Invalid service response.')
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
// Transfer-only codecs are mirrored at the two existing static client roots.
const transferRevision = n => Number.isSafeInteger(n) && n > 0
export const validAdminTransferPrecondition = value => typeof value === 'string' && value.length === 69 && /^ats1-[a-f0-9]{64}$/.test(value)
const transferKey = value => typeof value === 'string' && value.length === 36 && /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(value)
export function adminTransferURL(workspace, replacement) {
  if (!validId(workspace)) throw new Error('Invalid administrator transfer scope.')
  const base = `/api/workspaces/${encodeURIComponent(workspace)}/admin-transfers`
  // Only the one-argument form names the collection. A Review or tuple
  // validator always supplies its required target, even when it is undefined.
  if (arguments.length === 1) return base
  if (!validId(replacement)) throw new Error('Invalid administrator transfer target.')
  return `${base}/selections/${encodeURIComponent(replacement)}`
}
export function validateAdminTransferSelection(data, workspace, replacement) {
  adminTransferURL(workspace,replacement)
  if (!exactFields(data,['workspace_id','actor_id','replacement_id','actor_revision','replacement_revision','precondition']) || data.workspace_id !== workspace || data.replacement_id !== replacement || !validPolicyId(data.actor_id) || data.actor_id === replacement || !transferRevision(data.actor_revision) || !transferRevision(data.replacement_revision) || !validAdminTransferPrecondition(data.precondition)) throw new Error('Invalid administrator transfer review.')
  return data
}
function transferAbort() { const error = new Error('Stale administrator transfer discarded.'); error.name = 'AbortError'; return error }
function assertCurrentTransfer(context) {
  if (!context) return
  try { if (context.isCurrent() === true) return } catch {}
  throw transferAbort()
}
function transferOptions(options) {
  if (!options || typeof options !== 'object' || Array.isArray(options) || ![Object.prototype,null].includes(Object.getPrototypeOf(options)) || Reflect.ownKeys(options).some(key => !['signal','isCurrent'].includes(key)) || (options.signal !== undefined && !(options.signal instanceof AbortSignal))) throw new Error('Invalid administrator transfer options.')
  assertCurrentTransfer(options)
  return {signal:options.signal,isCurrent:options.isCurrent}
}
function transferAttempt(value) {
  if (!exactFields(value,['workspace_id','actor_id','replacement_id','actor_revision','replacement_revision','precondition','requestKey']) || Reflect.ownKeys(value).length !== 7 || !transferKey(value.requestKey)) throw new Error('Invalid administrator transfer attempt.')
  const {requestKey,...selection} = value
  validateAdminTransferSelection(selection,value.workspace_id,value.replacement_id)
  return Object.freeze({...selection,requestKey})
}
function validateTransferResponse(payload, context) {
  if (!exactFields(payload,['data'])) throw new Error('Invalid administrator transfer response.')
  const data = payload.data
  if (context.kind === 'capabilities') {
    if (!exactFields(data,['available']) || data.available !== true) throw new Error('Invalid administrator transfer capability.')
  } else if (context.kind === 'review') validateAdminTransferSelection(data,context.workspace,context.replacement)
  else {
    const a = context.attempt
    if (!exactFields(data,['workspace_id','actor_id','replacement_id','actor_role','replacement_role','actor_revision','replacement_revision','state']) || data.workspace_id !== a.workspace_id || data.actor_id !== a.actor_id || !validPolicyId(data.actor_id) || data.replacement_id !== a.replacement_id || data.actor_role !== 'developer' || data.replacement_role !== 'admin' || data.state !== 'committed' || !transferRevision(data.actor_revision) || !transferRevision(data.replacement_revision) || data.actor_revision !== a.actor_revision + 1 || data.replacement_revision !== a.replacement_revision + 1) throw new Error('Invalid administrator transfer result.')
  }
  return data
}
async function transferRejection(response) {
  // A post-read bound is not a streaming memory limit. Never render raw errors.
  try {
    const raw = await response.text()
    if (raw.length > 256) return false
    const payload = JSON.parse(raw)
    return exactFields(payload,['error']) && response.status === 412 && ['stale_revision','admin_transfer_selection_changed'].includes(payload.error)
  } catch { return false }
}
function transferContext(kind, workspace, replacement, options, attempt) {
  const {signal,isCurrent} = transferOptions(options)
  const base = kind === 'review' ? adminTransferURL(workspace,replacement) : adminTransferURL(workspace)
  const path = kind === 'capabilities' ? base + '/capabilities' : base
  return {signal,context:Object.freeze({kind,workspace,replacement,path,isCurrent,attempt})}
}
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
  let credential = '', epoch = 0
  const clear = () => { credential = ''; epoch++ }
  async function send(path, {method='GET', body, revision, requestKey, initialAdminPrecondition, invitationRecipientPrecondition, invitationIsCurrent, signal} = {}, transfer) {
      if (!credential) throw new Error('Disconnected.')
      const invitation = typeof path === 'string' ? invitationSelectionRequest(path, method, body, invitationRecipientPrecondition, invitationIsCurrent) : {}
      if (!transfer && (typeof path !== 'string' || /[\u0000-\u0020\u007f]/.test(path) || !['GET','POST','PATCH'].includes(method) || (method === 'GET' && body !== undefined) || (!invitation.review && path !== '/api/workspaces' && !/^\/api\/invitations(?:\/[A-Za-z0-9%._:-]+)?$/.test(path) && !/^\/api\/workspaces\/[A-Za-z0-9%._:-]+\/(?:members|modules|invitations)(?:\/[A-Za-z0-9%._:-]+)?$/.test(path) && !/^\/api\/workspaces\/[A-Za-z0-9%._:-]+\/module-catalog$/.test(path) && !/^\/api\/control\/(?:capabilities|workspaces(?:\/[A-Za-z0-9%._:-]+)?|initial-admin-selections\/[A-Za-z0-9%._:-]+)$/.test(path)))) throw new Error('Invalid service path.')
      initialAdminSelectionRequest(path, method, initialAdminPrecondition)
      const turn = epoch
      const current = () => { assertCurrentTransfer(transfer); if (transfer && turn !== epoch) throw transferAbort() }
      if (transfer) current()
      const headers = {Authorization: `Bearer ${credential}`}
      if (transfer?.kind === 'submit') headers['If-Admin-Transfer-Match'] = transfer.attempt.precondition
      if (body !== undefined) headers['Content-Type'] = 'application/json'
      if (revision !== undefined) headers['If-Match'] = `"${revision}"`
      if (requestKey !== undefined) headers['Idempotency-Key'] = requestKey
      if (initialAdminPrecondition !== undefined) headers['If-Initial-Admin-Match'] = initialAdminPrecondition
      if (invitationRecipientPrecondition !== undefined) headers['If-Invitation-Recipient-Match'] = invitationRecipientPrecondition
      let response
      try { response = await fetcher(path, {method, headers, body:body === undefined ? undefined : JSON.stringify(body), signal, credentials:'omit', cache:'no-store', redirect:'error'}) }
      catch (error) { if (transfer) { current(); throw new Error('Administrator transfer response could not be verified.') } throw error }
      current()
      assertCurrentInvitation(invitationIsCurrent)
      if (!response.ok) {
        const error = new Error(messageFor(response.status)); error.status = response.status
        if (invitation.creation && response.status === 412) error.invitationSelectionRejected = await invitationSelectionRejected(response)
        assertCurrentInvitation(invitationIsCurrent)
        if (transfer) {
          error.adminTransferRejected = await transferRejection(response); current()
          if (response.status === 401) { clear(); error.message = 'Authentication expired. The interrupted administrator change may already have completed.' }
        }
        throw error
      }
      if (transfer) {
        if (response.status !== 200) throw new Error('Invalid administrator transfer response.')
        let raw
        try { raw = await response.text() } catch { throw new Error('Administrator transfer response could not be verified.') } finally { current() }
        try {
          if (raw.length > 16384) throw new Error('Invalid administrator transfer response.')
          const result = validateTransferResponse(JSON.parse(raw),transfer); current(); return result
        } catch { throw new Error('Administrator transfer response could not be verified.') } finally { current() }
      }
      if ((invitation.review || invitation.creation) && response.status !== 200) throw new Error('Invalid invitation response.')
      let payload
      try { payload = await response.json() } finally { assertCurrentInvitation(invitationIsCurrent) }
      if (invitation.review || invitation.creation) return validateInvitationResult(payload,invitation)
      if (!payload || !Object.hasOwn(payload,'data')) throw new Error('Invalid service response.')
      return payload.data
    }
  return {
    connect(value) { if (typeof value !== 'string' || !value || value.length > 8192 || /\s/.test(value)) throw new Error('Enter a valid opaque credential.'); credential = value; epoch++ },
    clear,
    request: (path,options) => send(path,options),
    async transferCapabilities(workspace,options={}) { const {signal,context} = transferContext('capabilities',workspace,undefined,options); return send(context.path,{signal},context) },
    async reviewAdminTransfer(workspace,replacement,options={}) { const {signal,context} = transferContext('review',workspace,replacement,options); return send(context.path,{signal},context) },
    async submitAdminTransfer(value,options={}) {
      const attempt = transferAttempt(value), {signal,context} = transferContext('submit',attempt.workspace_id,undefined,options,attempt)
      return send(context.path,{method:'POST',body:{replacement_id:attempt.replacement_id,replacement_revision:String(attempt.replacement_revision)},revision:attempt.actor_revision,requestKey:attempt.requestKey,signal},context)
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
