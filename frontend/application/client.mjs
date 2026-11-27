// Cookie-only transport. No session-token getter, bearer construction or storage.
import {downloadPath,receiveArtifact,validateDownloadBinding} from './artifact-downloads.mjs'
import {validKey} from '../artifacts/client.mjs'
import {readPath} from '../pipelines/client.mjs'
import {createArtifactDirectoryClient} from './artifact-scope-client.mjs'
import {scopeFrom,queryFrom,validateFields,validConfirmationToken,responsibilityRequestLimit} from '../responsibility/client.mjs'
const own=(o,keys)=>o!==null&&typeof o==='object'&&!Array.isArray(o)&&Object.keys(o).sort().join(',')===[...keys].sort().join(',')
const token=/^[A-Za-z0-9_-]{43}$/
const exactToken=value=>typeof value==='string'&&value.match(token)?.[0]===value
const integer=n=>Number.isSafeInteger(n)&&n>0
function initialAdminSelectionRequest(path,method,precondition){
 if(path.startsWith('/api/control/initial-admin-selections')){
  let principal;try{principal=decodeURIComponent(path.slice('/api/control/initial-admin-selections/'.length))}catch{throw Error('Invalid identity selection path.')}
  if(method!=='GET'||!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(principal)||/[\u0000-\u0020\u007f]/.test(principal)||path!=='/api/control/initial-admin-selections/'+encodeURIComponent(principal))throw Error('Invalid identity selection path.')
 }
 if(precondition!==undefined&&(path!=='/api/control/workspaces'||method!=='POST'||typeof precondition!=='string'||precondition.length!==69||!/^ias1-[a-f0-9]{64}$/.test(precondition)))throw Error('Invalid initial administrator precondition.')
}
const validId=value=>typeof value==='string'&&!/[\u0000-\u0020\u007f]/.test(value)&&/^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(value)
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
// Exact stored-status codec. This projection is not a capability or readiness result.
export const validModuleStatusId=validId
export function moduleStatusURL(workspace,key){
 if(!validId(workspace)||!validId(key))throw new Error('Invalid module status query.')
 return `/api/workspaces/${encodeURIComponent(workspace)}/modules/${encodeURIComponent(key)}`
}
export function validateModuleStatus(data,workspace,key){
 moduleStatusURL(workspace,key)
 if(!own(data,['workspace_id','key','state','revision'])||data.workspace_id!==workspace||data.key!==key||!['enabled','disabling','disabled'].includes(data.state)||!integer(data.revision))throw new Error('Invalid module status response. Check again explicitly.')
 return data
}
function staleModuleStatus(){const error=new Error('Stale module status request discarded.');error.name='AbortError';return error}
function assertCurrentModuleStatus(context){
 if(!context)return
 try{if(context.isCurrent()===true)return}catch{}
 throw staleModuleStatus()
}
// Fixed research catalog reads retain their page owner through transport. A
// retired request cannot invalidate the fresh view's session with a late 401.
const researchReadRoute=/^\/api\/workspaces\/[A-Za-z0-9][A-Za-z0-9._-]{0,127}\/research-assets(?:\/[A-Za-z0-9][A-Za-z0-9._-]{0,127}\/(?:types|entities|assets(?:\/a_[0-9a-f]{64})?))?$/
function researchReadPath(path){
 // Research starts with the existing session's workspace index. Its owner is
 // separate from both the catalog directory and exact-scope operations.
 if(path==='/api/workspaces')return true
 if(typeof path!=='string')return false
 const [route,query,...extra]=path.split('?')
 return !extra.length&&route.match(researchReadRoute)?.[0]===route&&(query===undefined||(query.length>0&&!route.endsWith('/research-assets')))
}
function staleResearchRead(){const error=new Error('Stale research catalog read discarded.');error.name='AbortError';return error}
function assertCurrentResearchRead(context,signal){
 if(!context)return
 try{if(!signal?.aborted&&context.isCurrent()===true&&!signal?.aborted)return}catch{}
 throw staleResearchRead()
}
const invitationURL=workspace=>`/api/workspaces/${encodeURIComponent(workspace)}/invitations`
const validateInvitations=(items,workspace)=>{if(items.some(i=>!i||!validId(i.id)||i.workspace_id!==workspace||!validId(i.principal_id)||typeof i.workspace_name!=='string'||i.workspace_name.length>200||!['viewer','developer'].includes(i.role)||!['pending','accepted','declined','revoked','expired','invalidated'].includes(i.state)||!Number.isSafeInteger(i.created)||!Number.isSafeInteger(i.expires)||i.expires<=i.created||!integer(i.revision)))throw Error('Invalid invitation response.')}
// Invitation-only codecs mirror the standalone transport. Their asset roots are
// /app and /manage (not the filesystem names); shared Node contract cases keep
// the exact wire rules aligned without opening a new static route.
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
const validInvitationRecipientPrecondition = value => typeof value === 'string' && value.length === 69 && /^irs1-[a-f0-9]{64}$/.test(value)
function invitationSelectionURL(workspace, principal, role) {
  if (!validId(workspace) || !validId(principal) || !['viewer','developer'].includes(role)) throw new Error('Invalid invitation selection.')
  return `${invitationURL(workspace)}/selections/${encodeURIComponent(principal)}/${role}`
}
function validateInvitationSelection(data, workspace, principal, role) {
  invitationSelectionURL(workspace, principal, role)
  if (!exactFields(data, ['workspace_id','principal_id','role','precondition']) || data.workspace_id !== workspace || data.principal_id !== principal || data.role !== role || !validInvitationRecipientPrecondition(data.precondition)) throw new Error('Invalid invitation selection response.')
  return data
}
function invitationSelectionRequest(path, method, body, precondition, isCurrent) {
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
function assertCurrentInvitation(isCurrent) {
  if (isCurrent !== undefined && !isCurrent()) { const error = new Error('Stale invitation response discarded.'); error.name = 'AbortError'; throw error }
}
function validateInvitationResult(payload, request) {
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
async function invitationSelectionRejected(response) {
  // A post-read bound is not a streaming memory limit. Never expose raw errors.
  try {
    const raw = await response.text()
    if (raw.length > 128) return false
    const payload = JSON.parse(raw)
    return exactFields(payload,['error']) && payload.error === 'invitation_recipient_selection_changed'
  } catch { return false }
}


const messages={401:'Your session expired or was rejected. Sign in or explicitly recheck the session.',403:'Your current permissions do not allow this change.',404:'This scope or capability is unavailable to your identity.',409:'The current lifecycle or request conflicts with this change.',412:'The revision changed. Nothing was retried. Reload and review current state.',428:'A current revision is required.',503:'The configured service is unavailable. No login or result is assumed.'}
export function csrfCookie(raw){
 if(typeof raw!=='string'||raw.length>8192||/[\u0000-\u001f\u007f]/.test(raw))throw new Error('CSRF proof unavailable.')
 const values=raw.split(';').map(x=>x.trim()).filter(x=>x.startsWith('__Host-ms-collab-csrf='))
 if(values.length!==1)throw new Error('CSRF proof unavailable.')
 const value=values[0].slice('__Host-ms-collab-csrf='.length)
 if(!token.test(value)||value.match(token)?.[0]!==value)throw new Error('CSRF proof unavailable.')
 return value
}
export function validateBootstrap(data,origin){
 if(!own(data,['service','ui_modules','login_configured','authorization_endpoint'])||data.service!=='ms-collab'||!Array.isArray(data.ui_modules)||data.ui_modules.some(x=>!['workspaces','responsibility','pipelines','community','artifacts','artifact-admin','assistant','research-assets','gui-smoke','artifact-downloads','artifact-product-views','artifact-product-file-downloads','connections'].includes(x))||new Set(data.ui_modules).size!==data.ui_modules.length||!data.ui_modules.includes('workspaces')||typeof data.login_configured!=='boolean')throw new Error('Invalid application configuration.')
 if(data.ui_modules.includes('artifact-product-views')&&!data.ui_modules.includes('artifacts'))throw new Error('Invalid product view configuration.')
 if(data.ui_modules.includes('artifact-product-file-downloads')&&!['artifact-product-views','artifact-downloads'].every(name=>data.ui_modules.includes(name)))throw new Error('Invalid product file download configuration.')
 if(data.ui_modules.includes('connections')&&!data.ui_modules.includes('community'))throw new Error('Invalid connection configuration.')
 if(data.login_configured){const target=new URL(data.authorization_endpoint);if(target.protocol!=='https:'||target.username||target.password||target.search||target.hash||target.href!==data.authorization_endpoint)throw new Error('Invalid login configuration.')}
 else if(data.authorization_endpoint!==null)throw new Error('Invalid login configuration.')
 if(new URL(origin).protocol!=='https:')throw new Error('HTTPS is required.')
 return data
}
export function authorizationTarget(data,bootstrap,origin){
 if(!bootstrap.login_configured||!own(data,['authorization_url'])||typeof data.authorization_url!=='string'||data.authorization_url.length>16384)throw new Error('Login is unavailable.')
 const target=new URL(data.authorization_url),endpoint=new URL(bootstrap.authorization_endpoint)
 const params=target.searchParams,keys=['response_type','client_id','redirect_uri','state','code_challenge','code_challenge_method','scope']
 if(target.origin!==endpoint.origin||target.pathname!==endpoint.pathname||target.username||target.password||target.hash||[...params.keys()].length!==keys.length||keys.some(k=>params.getAll(k).length!==1)||params.get('response_type')!=='code'||params.get('code_challenge_method')!=='S256'||params.get('redirect_uri')!==origin+'/auth/callback'||!exactToken(params.get('state'))||!exactToken(params.get('code_challenge'))||!params.get('client_id')||!params.get('scope'))throw new Error('Invalid login destination.')
 return target.href
}
// Capture the primitive arguments *before* loading the optional codec. Do not
// call object getters/toJSON, silently drop unknown fields or borrow mutable
// form objects across the dynamic import. Full wire validation is in the codec.
function captureProductFields(value, limit, allowConditions=false) {
 const invalid=()=>new Error('Invalid product view request.')
 if(value===null||typeof value!=='object'||![Object.prototype,null].includes(Object.getPrototypeOf(value)))throw invalid()
 const keys=Reflect.ownKeys(value),copy=Object.create(null)
 if(keys.length>limit)throw invalid()
 for(const k of keys){
  const d=Object.getOwnPropertyDescriptor(value,k)
  if(typeof k!=='string'||!d.enumerable||!Object.hasOwn(d,'value'))throw invalid()
  if(allowConditions&&k==='conditions'){
   const a=d.value
   if(!Array.isArray(a)||Object.getPrototypeOf(a)!==Array.prototype||a.length<1||a.length>8||
      Reflect.ownKeys(a).length!==a.length+1)throw invalid()
   const items=[]
   for(let i=0;i<a.length;i++){
    const row=Object.getOwnPropertyDescriptor(a,String(i))
    if(!row?.enumerable||!Object.hasOwn(row,'value'))throw invalid()
    items.push(captureProductFields(row.value,2))
   }
   copy[k]=Object.freeze(items)
  }else{
   if(!['number','string','boolean'].includes(typeof d.value))throw invalid()
   copy[k]=d.value
  }
 }
 return Object.freeze(copy)
}
function staleProductView() {
 const error=new Error('Stale product view request discarded.')
 error.name='AbortError'
 return error
}
const productReadErrors={
 400:'Invalid product view request. Check the scope and filters.',
 401:messages[401],403:'This product view is unavailable.',404:'This product view is unavailable.',
 409:'This workspace or module is unavailable.',
 412:'The publication or view changed. Reopen the product view.',
}
export function createSessionClient({fetcher=fetch,cookie=()=>document.cookie,onInvalid=()=>{}}={}){
 let active=false,epoch=0
 const transfers=new Set()
 const clear=()=>{active=false;epoch++;for(const cancel of transfers)cancel();transfers.clear()}
 async function send(path,{method='GET',body,revision,requestKey,initialAdminPrecondition,invitationRecipientPrecondition,invitationIsCurrent,signal,login=false,authenticated=false}={},moduleStatus,transfer,researchRead){
  if(typeof path!=='string'||path.length>2048||/[\u0000-\u0020\u007f\\#]/.test(path)||!path.startsWith('/')||path.startsWith('//')||path.includes('/../')||path.includes('/./'))throw new Error('Invalid application path.')
  if(!['GET','POST','PATCH','PUT'].includes(method)||(method==='GET'&&body!==undefined))throw new Error('Invalid method.')
  initialAdminSelectionRequest(path,method,initialAdminPrecondition)
  const invitation=invitationSelectionRequest(path,method,body,invitationRecipientPrecondition,invitationIsCurrent)
  // Only readModuleStatus creates this private third argument. Public request
  // options cannot install a navigation guard or suppress genuine 401 handling.
  if(moduleStatus&&(typeof moduleStatus.isCurrent!=='function'||path!==moduleStatus.path||path!==moduleStatusURL(moduleStatus.workspace,moduleStatus.key)||method!=='GET'||!authenticated||login||[body,revision,requestKey,initialAdminPrecondition,invitationRecipientPrecondition,invitationIsCurrent].some(value=>value!==undefined)))throw new Error('Invalid module status request.')
  if(transfer&&(moduleStatus||path!==transfer.path||!authenticated||login||(transfer.kind==='submit'?method!=='POST':method!=='GET')))throw new Error('Invalid administrator transfer request.')
  if(researchRead&&(path!==researchRead.path||!researchReadPath(path)||!authenticated||method!=='GET'||login||moduleStatus||transfer))throw new Error('Invalid research catalog read.')
  if(authenticated&&!active)throw new Error('Session recheck required.')
  const turn=epoch,headers={}
  if(body!==undefined){headers['Content-Type']='application/json';body=JSON.stringify(body);if(new TextEncoder().encode(body).length>responsibilityRequestLimit(path,method,JSON.parse(body)))throw new Error('Request is too large.')}
  if(revision!==undefined){if(!integer(revision))throw new Error('Current revision required.');headers['If-Match']=`"${revision}"`}
  if(requestKey!==undefined){if(typeof requestKey!=='string'||requestKey.match(/^[A-Za-z0-9_-]{1,200}$/)?.[0]!==requestKey)throw new Error('Invalid retry key.');headers['Idempotency-Key']=requestKey}
  if(initialAdminPrecondition!==undefined)headers['If-Initial-Admin-Match']=initialAdminPrecondition
  if(invitationRecipientPrecondition!==undefined)headers['If-Invitation-Recipient-Match']=invitationRecipientPrecondition
  if(transfer?.kind==='submit')headers['If-Admin-Transfer-Match']=transfer.attempt.precondition
  if(method!=='GET'){
   // A fresh cookie is read for each mutation. A missing/duplicate token fails locally.
   if(login){headers['X-MS-Collab-Login']='1';const raw=cookie();if(raw.split(';').some(x=>x.trim().startsWith('__Host-ms-collab-csrf=')))headers['X-CSRF-Token']=csrfCookie(raw)}
   else headers['X-CSRF-Token']=csrfCookie(cookie())
  }
  const current=()=>{assertCurrentTransfer(transfer);assertCurrentModuleStatus(moduleStatus);assertCurrentInvitation(invitationIsCurrent);assertCurrentResearchRead(researchRead,signal);if(turn!==epoch){if(transfer)throw transferAbort();if(researchRead)throw staleResearchRead();if(moduleStatus)throw staleModuleStatus();throw new Error('Stale session response discarded.')}}
  if(moduleStatus||transfer||researchRead)current()
  let response
  try{response=await fetcher(path,{method,headers,body,signal,credentials:'same-origin',cache:'no-store',redirect:'error'})}
  catch(error){if(moduleStatus||transfer||researchRead)current();if(transfer)throw new Error('Administrator transfer response could not be verified.');throw error}
  current() // Before 401 can clear the current session or call onInvalid.
  if(!response.ok){
   const error=new Error(messages[response.status]||'The request could not be verified. Nothing was retried.');error.status=response.status
   if(invitation.creation&&response.status===412)error.invitationSelectionRejected=await invitationSelectionRejected(response)
   if(transfer)error.adminTransferRejected=await transferRejection(response)
   current()
   if(transfer&&response.status===401)error.message='Your session expired. The interrupted administrator change may already have completed.'
   if(response.status===401){
    clear() // Ordinary auth invalidation is never gated by page ownership.
    // An ordinary list refresh can follow a *verified* transfer. Forward
    // only its local request identity so the page can choose accurate copy.
    if(path==='/api/workspaces'&&method==='GET'&&signal)onInvalid(error.message,signal)
    else onInvalid(error.message)
   }throw error
  }
  if(researchRead&&response.status!==200)throw new Error('Invalid research catalog response.')
  if(transfer&&response.status!==200)throw new Error('Invalid administrator transfer response.')
  if(moduleStatus&&response.status!==200)throw new Error('Invalid module status response. Check again explicitly.')
  if((invitation.review||invitation.creation)&&response.status!==200)throw new Error('Invalid invitation response.')
  let raw;try{raw=await response.text()}catch(error){if(transfer)throw new Error('Administrator transfer response could not be verified.');throw error}finally{current()}
  try{
   // Existing bound is checked after reading, not a streaming allocation limit.
   if(raw.length>(transfer?16384:2097152))throw new Error('Invalid service response.')
   const payload=JSON.parse(raw);if(!own(payload,['data']))throw new Error('Invalid service response.')
   current()
   if(transfer)return validateTransferResponse(payload,transfer)
   if(moduleStatus)return validateModuleStatus(payload.data,moduleStatus.workspace,moduleStatus.key)
   if(invitation.review||invitation.creation)return validateInvitationResult(payload,invitation)
   return payload.data
  }catch(error){if(transfer)throw new Error('Administrator transfer response could not be verified.');throw error}finally{if(moduleStatus||transfer||researchRead)current()}
 }
 // Only fixed product reads use this owner. Public request options
 // cannot install it or suppress an ordinary (including catalog) 401.
 function productOwner(options) {
  const turn=epoch
  if(options==null)throw staleProductView()
  if(typeof options!=='object'||![Object.prototype,null].includes(Object.getPrototypeOf(options))||
     Reflect.ownKeys(options).some(k=>{
      const d=Object.getOwnPropertyDescriptor(options,k)
      return !['signal','isCurrent'].includes(k)||!d.enumerable||!Object.hasOwn(d,'value')
     }))throw new Error('Invalid product view options.')
  if(!Object.hasOwn(options,'isCurrent')||typeof options.isCurrent!=='function')throw staleProductView()
  const isCurrent=options.isCurrent,signal=Object.hasOwn(options,'signal')?options.signal:undefined
  if(signal!==undefined&&!(signal instanceof AbortSignal))throw new Error('Invalid product view signal.')
  if(signal?.aborted)throw staleProductView()
  if(!active)throw new Error('Session recheck required.')
  const guard=()=>{
   try{if(active&&turn===epoch&&!signal?.aborted&&isCurrent()===true&&active&&turn===epoch&&!signal?.aborted)return}catch{}
   throw staleProductView()
  }
  guard()
  return {signal,guard}
 }
 async function guardedProductRead(path,wire,{signal,guard},revision,limit,errors,unverified,decode) {
  guard()
  const method=wire===undefined?'GET':'POST'
  const headers=method==='GET'?{}:{'Content-Type':'application/json','X-CSRF-Token':csrfCookie(cookie())}
  if(revision!==undefined)headers['If-Match']=`"${revision}"`
  let response
  guard()
  try{
   response=await fetcher(path,{method,headers,body:wire,signal,mode:'same-origin',
    credentials:'same-origin',referrerPolicy:'no-referrer',cache:'no-store',redirect:'error'})
  }catch{guard();throw unverified()}
  guard()
  const status=response?.status
  if(status!==200||response?.ok!==true){
   const error=unverified()
   if(Number.isInteger(status)&&status>=400&&status<=599){
    error.status=status
    if(Object.hasOwn(errors,status))error.message=errors[status]
   }
   // Delayed headers (particularly a stale 401) must not touch the current
   // session. A current 401 still uses the real shell invalidation path.
   guard()
   if(status===401){clear();onInvalid(error.message)}
   throw error
  }
  let raw
  try{raw=await response.text()}catch{guard();throw unverified()}
  guard()
  let payload,data
  try{
   // Existing post-read ceiling, not a streaming memory limit.
   if(typeof raw!=='string'||raw.length>limit||new TextEncoder().encode(raw).length>limit)throw unverified()
   payload=JSON.parse(raw)
   if(payload===null||typeof payload!=='object'||Array.isArray(payload)||Object.keys(payload).length!==1||!Object.hasOwn(payload,'data'))throw unverified()
  }catch{guard();throw unverified()}
  guard()
  try{data=decode(payload.data)}catch{guard();throw unverified()}
  guard()
  return data
 }

 async function readArtifactProductPicker(selection,options) {
  const owner=productOwner(options),unverified=()=>new Error('Product discovery could not be verified.')
  const read=(path,{decode=data=>data,limit=65536}={})=>guardedProductRead(path,undefined,owner,undefined,limit,
   {...productReadErrors,503:'Authorized scope discovery is unavailable. You can still enter exact IDs.'},unverified,decode)
  const selected=(kind,other=[])=>own(selection,['kind',...other])&&Object.hasOwn(selection,'kind')&&
    other.every(key=>Object.hasOwn(selection,key))&&selection.kind===kind
  if(selected('workspaces'))return read('/api/workspaces',{limit:2097152})
  if(selected('scopes',['workspace','after','lastTuple'])){
   return createArtifactDirectoryClient({request:path=>read(path)}).list(selection.workspace,
    {after:selection.after,lastTuple:selection.lastTuple})
  }
  if(selected('views',['scope'])){
   const scope=captureProductFields(selection.scope,3)
   let codec
   try{codec=await import('./artifact-product-codec.mjs')}catch{owner.guard();throw unverified()}
   owner.guard()
   // 128 labels can each contain 80 non-BMP characters; the server uses ASCII JSON escapes.
   return read(codec.productChoicesPath(scope),{limit:192*1024,decode:data=>codec.validateProductChoices(data,scope)})
  }
  throw new Error('Invalid product discovery selection.')
 }
 async function readArtifactProductView(scope,viewId,body,options) {
  const owner=productOwner(options),{guard}=owner
  const unverified=()=>new Error('The product view response could not be verified.')
  const selection=captureProductFields(scope,3),criteria=captureProductFields(body,15,true)
  guard()
  let codec
  try{codec=await import('./artifact-product-codec.mjs')}catch{guard();throw unverified()}
  guard()
  const request=codec.makeProductRequest(selection,viewId,criteria),wire=JSON.stringify(request.body)
  if(new TextEncoder().encode(wire).length>16384)throw new Error('Product view request is too large.')
  const result=await guardedProductRead(request.path,wire,owner,undefined,2097152,productReadErrors,unverified,
   data=>codec.validateProductResult(data,request))
  guard();return result
 }
 // The exact route is derived here, never supplied by the caller. Save and
 // reopen are distinct capabilities: a malformed save cannot become a GET.
 async function productPreset(writing,scope,viewId,selected,name,body,options) {
  const owner=productOwner(options),selection=captureProductFields(scope,3)
  const criteria=writing?captureProductFields(body,12,true):undefined
  const unverified=()=>new Error('The preset response could not be verified.')
  let codec
  try{codec=await import('./artifact-product-codec.mjs')}catch{owner.guard();throw unverified()}
  owner.guard()
  const saved=writing?codec.productPresetWrite(selection,viewId,criteria):undefined
  const q=writing?saved.quarter:selected
  const path=codec.productPresetPath(selection,viewId,q,name)
  return guardedProductRead(path,writing?JSON.stringify(saved):undefined,owner,undefined,2097152,
   productReadErrors,unverified,data=>codec.validateProductPreset(data,selection,viewId,q,name,saved))
 }
 const saveArtifactProductPreset=(scope,viewId,name,body,options)=>productPreset(true,scope,viewId,null,name,body,options)
 const readArtifactProductPreset=(scope,viewId,quarter,name,options)=>productPreset(false,scope,viewId,quarter,name,null,options)
 async function readArtifactDownloadAvailability(scope,key,revision,options) {
  const owner=productOwner(options),selected=captureProductFields(scope,3)
  // Keep the object key literal in JSON; never use it as a URL, filename or
  // proof. No directory, checksum sidecar or forged product/view field.
  const ids=['workspace_id','connection_id','bucket_id']
  if(!ids.every(id=>Object.hasOwn(selected,id))||!validKey(key)||key.endsWith('/')||
     /[\ud800-\udfff]/u.test(key)||/\.[sS][hH][aA]256$/.test(key)||
     new TextEncoder().encode(key).length>1024||key.split('/').length>32||!integer(revision))
   throw new Error('Invalid product file selection.')
  const path=downloadPath(selected,'availability')
  const unverified=()=>new Error('The byte availability response could not be verified.')
  const result=await guardedProductRead(path,JSON.stringify({key}),owner,revision,16384,
   messages,unverified,data=>{
    // Require actual, separate JSON fields before using the shared binding
    // validator; delimiter-joined field names are not an equivalent shape.
    if(!data||!['scope','key','size','sha256','published_revision'].every(k=>Object.hasOwn(data,k))||
       !data.scope||!ids.every(k=>Object.hasOwn(data.scope,k)))throw unverified()
    return validateDownloadBinding(data,{scope:selected,key,revision})
   })
  owner.guard();return result
 }
 return {
  clear,readArtifactProductView,saveArtifactProductPreset,readArtifactProductPreset,readArtifactDownloadAvailability,readArtifactProductPicker,
  bootstrap:()=>send('/app/bootstrap'),
  async inspect(){clear();const turn=epoch,data=await send('/auth/session');if(turn!==epoch)throw new Error('Stale session response discarded.');if(!own(data,['principal_id'])||!validPolicyId(data.principal_id))throw new Error('Invalid session response.');active=true;return data},
  start(){clear();return send('/auth/start',{method:'POST',body:{},login:true})},
  logout(){clear();return send('/auth/logout',{method:'POST',body:{}})},
  async transferCapabilities(workspace,options={}) { const {signal,context}=transferContext('capabilities',workspace,undefined,options);return send(context.path,{signal,authenticated:true},undefined,context) },
  async reviewAdminTransfer(workspace,replacement,options={}) { const {signal,context}=transferContext('review',workspace,replacement,options);return send(context.path,{signal,authenticated:true},undefined,context) },
  async submitAdminTransfer(value,options={}) {
   const attempt=transferAttempt(value),{signal,context}=transferContext('submit',attempt.workspace_id,undefined,options,attempt)
   return send(context.path,{method:'POST',body:{replacement_id:attempt.replacement_id,replacement_revision:String(attempt.replacement_revision)},revision:attempt.actor_revision,requestKey:attempt.requestKey,signal,authenticated:true},undefined,context)
  },
  async readResearchAssets(path,options){
   if(!researchReadPath(path)||!options||Object.getPrototypeOf(options)!==Object.prototype||
      !Object.hasOwn(options,'isCurrent')||typeof options.isCurrent!=='function'||
      Reflect.ownKeys(options).some(key=>!['signal','isCurrent'].includes(key))||
      (options.signal!==undefined&&!(options.signal instanceof AbortSignal)))throw new Error('Invalid research catalog read.')
   const {signal,isCurrent}=options
   return send(path,{signal,authenticated:true},undefined,undefined,Object.freeze({path,isCurrent}))
  },
  async readModuleStatus(workspace,key,options={}){
   const path=moduleStatusURL(workspace,key)
   if(!options||typeof options!=='object'||Array.isArray(options)||![Object.prototype,null].includes(Object.getPrototypeOf(options))||Reflect.ownKeys(options).some(name=>!['signal','isCurrent'].includes(name))||!Object.hasOwn(options,'isCurrent')||typeof options.isCurrent!=='function'||(options.signal!==undefined&&!(options.signal instanceof AbortSignal)))throw new Error('Invalid module status options.')
   const {signal,isCurrent}=options
   return send(path,{method:'GET',signal,authenticated:true,login:false},Object.freeze({workspace,key,path,isCurrent}))
  },
  async downloadArtifact(binding,{signal,isCurrent=()=>true}={}){
   if(typeof isCurrent!=='function')throw Error('Invalid download guard.')
   const turn=epoch,guard=()=>{if(!active||turn!==epoch||!isCurrent())throw Error('Stale session download discarded.')}
   guard();return receiveArtifact({binding,fetcher,csrf:()=>csrfCookie(cookie()),guard,signal,pending:transfers,
    onUnauthorized(error){guard();clear();onInvalid(error.message)}})
  },
  async request(path,options={}){if(typeof path!=='string'||!path.startsWith('/api/')||/^\/api\/workspaces\/[^/]+\/admin-transfers(?:[/?#]|$)/.test(path))throw new Error('Invalid service path.');return send(path,{...options,authenticated:true,login:false})}
 }
}
export function createResponsibilitySessionClient(session){
 return {clear:()=>session.clear(),async request(scope,action,{query,fields,revision,confirmation_token,signal}={}){
  const parts=scope.repository.split('/');scopeFrom(scope.workspace_id,scope.connection_id,...parts)
  if(parts.length!==2||!['capabilities','config','candidates','save','reconfirm'].includes(action))throw new Error('Invalid responsibility request.')
  let suffix=action==='save'?'config':action
  const options={signal}
  if(['save','reconfirm'].includes(action)){if(!integer(revision))throw new Error('Current revision required.');validateFields(fields);if(action==='reconfirm'&&!validConfirmationToken(confirmation_token))throw new Error('Current confirmation proposal required.');options.method=action==='reconfirm'?'POST':'PUT';options.body=action==='reconfirm'?{fields,confirmation_token}:{fields};options.revision=revision}
  if(action==='candidates'){const q=queryFrom(query?.view,query?.field_id,query?.offset);if(query.limit!==20)throw new Error('Invalid request.');const params=new URLSearchParams({view:q.view,offset:String(q.offset),limit:String(q.limit)});if(q.view==='field')params.set('field_id',q.field_id);suffix+='?'+params}
  return session.request(`/api/workspaces/${scope.workspace_id}/responsibility/${scope.connection_id}/${scope.repository}/${suffix}`,options)
 }}
}

export function createPipelineSessionClient(session){
 return {clear:()=>session.clear(),request(scope,kind,{signal,...options}={}){return session.request(readPath(scope,kind,options),{signal})}}
}
