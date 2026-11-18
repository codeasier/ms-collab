// Cookie-only transport. No session-token getter, bearer construction or storage.
import {receiveArtifact} from './artifact-downloads.mjs'
import {readPath} from '../pipelines/client.mjs'
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
const invitationURL=workspace=>`/api/workspaces/${encodeURIComponent(workspace)}/invitations`
const validateInvitations=(items,workspace)=>{if(items.some(i=>!i||!validId(i.id)||i.workspace_id!==workspace||!validId(i.principal_id)||typeof i.workspace_name!=='string'||i.workspace_name.length>200||!['viewer','developer'].includes(i.role)||!['pending','accepted','declined','revoked','expired','invalidated'].includes(i.state)||!Number.isSafeInteger(i.created)||!Number.isSafeInteger(i.expires)||i.expires<=i.created||!integer(i.revision)))throw Error('Invalid invitation response.')}
// Invitation-only codecs mirror the standalone transport. Their asset roots are
// /app and /manage (not the filesystem names); shared Node contract cases keep
// the exact wire rules aligned without opening a new static route.
const exactFields = (value, fields) => value !== null && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).sort().join(',') === [...fields].sort().join(',')
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
 if(!own(data,['service','ui_modules','login_configured','authorization_endpoint'])||data.service!=='ms-collab'||!Array.isArray(data.ui_modules)||data.ui_modules.some(x=>!['workspaces','responsibility','pipelines','community','artifacts','artifact-admin','assistant','research-assets','gui-smoke','artifact-downloads','connections'].includes(x))||new Set(data.ui_modules).size!==data.ui_modules.length||!data.ui_modules.includes('workspaces')||typeof data.login_configured!=='boolean')throw new Error('Invalid application configuration.')
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
export function createSessionClient({fetcher=fetch,cookie=()=>document.cookie,onInvalid=()=>{}}={}){
 let active=false,epoch=0
 const transfers=new Set()
 const clear=()=>{active=false;epoch++;for(const cancel of transfers)cancel();transfers.clear()}
 async function send(path,{method='GET',body,revision,requestKey,initialAdminPrecondition,invitationRecipientPrecondition,invitationIsCurrent,signal,login=false,authenticated=false}={},moduleStatus){
  if(typeof path!=='string'||path.length>2048||/[\u0000-\u0020\u007f\\#]/.test(path)||!path.startsWith('/')||path.startsWith('//')||path.includes('/../')||path.includes('/./'))throw new Error('Invalid application path.')
  if(!['GET','POST','PATCH','PUT'].includes(method)||(method==='GET'&&body!==undefined))throw new Error('Invalid method.')
  initialAdminSelectionRequest(path,method,initialAdminPrecondition)
  const invitation=invitationSelectionRequest(path,method,body,invitationRecipientPrecondition,invitationIsCurrent)
  // Only readModuleStatus creates this private third argument. Public request
  // options cannot install a navigation guard or suppress genuine 401 handling.
  if(moduleStatus&&(typeof moduleStatus.isCurrent!=='function'||path!==moduleStatus.path||path!==moduleStatusURL(moduleStatus.workspace,moduleStatus.key)||method!=='GET'||!authenticated||login||[body,revision,requestKey,initialAdminPrecondition,invitationRecipientPrecondition,invitationIsCurrent].some(value=>value!==undefined)))throw new Error('Invalid module status request.')
  if(authenticated&&!active)throw new Error('Session recheck required.')
  const turn=epoch,headers={}
  if(body!==undefined){headers['Content-Type']='application/json';body=JSON.stringify(body);if(new TextEncoder().encode(body).length>responsibilityRequestLimit(path,method,JSON.parse(body)))throw new Error('Request is too large.')}
  if(revision!==undefined){if(!integer(revision))throw new Error('Current revision required.');headers['If-Match']=`"${revision}"`}
  if(requestKey!==undefined){if(typeof requestKey!=='string'||requestKey.match(/^[A-Za-z0-9_-]{1,200}$/)?.[0]!==requestKey)throw new Error('Invalid retry key.');headers['Idempotency-Key']=requestKey}
  if(initialAdminPrecondition!==undefined)headers['If-Initial-Admin-Match']=initialAdminPrecondition
  if(invitationRecipientPrecondition!==undefined)headers['If-Invitation-Recipient-Match']=invitationRecipientPrecondition
  if(method!=='GET'){
   // A fresh cookie is read for each mutation. A missing/duplicate token fails locally.
   if(login){headers['X-MS-Collab-Login']='1';const raw=cookie();if(raw.split(';').some(x=>x.trim().startsWith('__Host-ms-collab-csrf=')))headers['X-CSRF-Token']=csrfCookie(raw)}
   else headers['X-CSRF-Token']=csrfCookie(cookie())
  }
  const current=()=>{assertCurrentModuleStatus(moduleStatus);assertCurrentInvitation(invitationIsCurrent);if(turn!==epoch){if(moduleStatus)throw staleModuleStatus();throw new Error('Stale session response discarded.')}}
  if(moduleStatus)current()
  let response
  try{response=await fetcher(path,{method,headers,body,signal,credentials:'same-origin',cache:'no-store',redirect:'error'})}
  catch(error){if(moduleStatus)current();throw error}
  current() // Before 401 can clear the current session or call onInvalid.
  if(!response.ok){
   const error=new Error(messages[response.status]||'The request could not be verified. Nothing was retried.');error.status=response.status
   if(invitation.creation&&response.status===412)error.invitationSelectionRejected=await invitationSelectionRejected(response)
   current()
   if(response.status===401){clear();onInvalid(error.message)}throw error
  }
  if(moduleStatus&&response.status!==200)throw new Error('Invalid module status response. Check again explicitly.')
  if((invitation.review||invitation.creation)&&response.status!==200)throw new Error('Invalid invitation response.')
  let raw;try{raw=await response.text()}finally{current()}
  try{
   // Existing bound is checked after reading, not a streaming allocation limit.
   if(raw.length>2097152)throw new Error('Invalid service response.')
   const payload=JSON.parse(raw);if(!own(payload,['data']))throw new Error('Invalid service response.')
   current()
   if(moduleStatus)return validateModuleStatus(payload.data,moduleStatus.workspace,moduleStatus.key)
   if(invitation.review||invitation.creation)return validateInvitationResult(payload,invitation)
   return payload.data
  }finally{if(moduleStatus)current()}
 }
 return {
  clear,
  bootstrap:()=>send('/app/bootstrap'),
  async inspect(){clear();const turn=epoch,data=await send('/auth/session');if(turn!==epoch)throw new Error('Stale session response discarded.');if(!own(data,['principal_id'])||typeof data.principal_id!=='string'||!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(data.principal_id)||/[\u0000-\u0020\u007f]/.test(data.principal_id))throw new Error('Invalid session response.');active=true;return data},
  start(){clear();return send('/auth/start',{method:'POST',body:{},login:true})},
  logout(){clear();return send('/auth/logout',{method:'POST',body:{}})},
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
  async request(path,options={}){if(!path.startsWith('/api/'))throw new Error('Invalid service path.');return send(path,{...options,authenticated:true,login:false})}
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
