// Cookie-only transport. No session-token getter, bearer construction or storage.
import {receiveArtifact} from './artifact-downloads.mjs'
import {readPath} from '../pipelines/client.mjs'
import {scopeFrom,queryFrom,validateFields} from '../responsibility/client.mjs'
const own=(o,keys)=>o!==null&&typeof o==='object'&&!Array.isArray(o)&&Object.keys(o).sort().join(',')===[...keys].sort().join(',')
const token=/^[A-Za-z0-9_-]{43}$/
const exactToken=value=>typeof value==='string'&&value.match(token)?.[0]===value
const integer=n=>Number.isSafeInteger(n)&&n>0
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
 if(!own(data,['service','ui_modules','login_configured','authorization_endpoint'])||data.service!=='ms-collab'||!Array.isArray(data.ui_modules)||data.ui_modules.some(x=>!['workspaces','responsibility','pipelines','community','artifacts','artifact-admin','assistant','artifact-downloads'].includes(x))||new Set(data.ui_modules).size!==data.ui_modules.length||!data.ui_modules.includes('workspaces')||typeof data.login_configured!=='boolean')throw new Error('Invalid application configuration.')
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
 async function send(path,{method='GET',body,revision,requestKey,signal,login=false,authenticated=false}={}){
  if(typeof path!=='string'||path.length>2048||/[\u0000-\u0020\u007f\\#]/.test(path)||!path.startsWith('/')||path.startsWith('//')||path.includes('/../')||path.includes('/./'))throw new Error('Invalid application path.')
  if(!['GET','POST','PATCH','PUT'].includes(method)||(method==='GET'&&body!==undefined))throw new Error('Invalid method.')
  if(authenticated&&!active)throw new Error('Session recheck required.')
  const turn=epoch,headers={}
  if(body!==undefined){headers['Content-Type']='application/json';body=JSON.stringify(body);if(new TextEncoder().encode(body).length>65536)throw new Error('Request is too large.')}
  if(revision!==undefined){if(!integer(revision))throw new Error('Current revision required.');headers['If-Match']=`"${revision}"`}
  if(requestKey!==undefined){if(typeof requestKey!=='string'||requestKey.match(/^[A-Za-z0-9_-]{1,200}$/)?.[0]!==requestKey)throw new Error('Invalid retry key.');headers['Idempotency-Key']=requestKey}
  if(method!=='GET'){
   // A fresh cookie is read for each mutation. A missing/duplicate token fails locally.
   if(login){headers['X-MS-Collab-Login']='1';const raw=cookie();if(raw.split(';').some(x=>x.trim().startsWith('__Host-ms-collab-csrf=')))headers['X-CSRF-Token']=csrfCookie(raw)}
   else headers['X-CSRF-Token']=csrfCookie(cookie())
  }
  const response=await fetcher(path,{method,headers,body,signal,credentials:'same-origin',cache:'no-store',redirect:'error'})
  if(turn!==epoch)throw new Error('Stale session response discarded.')
  if(!response.ok){const error=new Error(messages[response.status]||'The request could not be verified. Nothing was retried.');error.status=response.status;if(response.status===401){clear();onInvalid(error.message)}throw error}
  const raw=await response.text();if(raw.length>2097152)throw new Error('Invalid service response.')
  const payload=JSON.parse(raw);if(!own(payload,['data']))throw new Error('Invalid service response.')
  if(turn!==epoch)throw new Error('Stale session response discarded.')
  return payload.data
 }
 return {
  clear,
  bootstrap:()=>send('/app/bootstrap'),
  async inspect(){clear();const turn=epoch,data=await send('/auth/session');if(turn!==epoch)throw new Error('Stale session response discarded.');if(!own(data,['principal_id'])||typeof data.principal_id!=='string'||!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(data.principal_id)||/[\u0000-\u0020\u007f]/.test(data.principal_id))throw new Error('Invalid session response.');active=true;return data},
  start(){clear();return send('/auth/start',{method:'POST',body:{},login:true})},
  logout(){clear();return send('/auth/logout',{method:'POST',body:{}})},
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
 return {clear:()=>session.clear(),async request(scope,action,{query,fields,revision,signal}={}){
  const parts=scope.repository.split('/');scopeFrom(scope.workspace_id,scope.connection_id,...parts)
  if(parts.length!==2||!['capabilities','config','candidates','save'].includes(action))throw new Error('Invalid responsibility request.')
  let suffix=action==='save'?'config':action
  const options={signal}
  if(action==='save'){validateFields(fields);options.method='PUT';options.body={fields};options.revision=revision}
  if(action==='candidates'){const q=queryFrom(query?.view,query?.field_id,query?.offset);if(query.limit!==20)throw new Error('Invalid request.');const params=new URLSearchParams({view:q.view,offset:String(q.offset),limit:String(q.limit)});if(q.view==='field')params.set('field_id',q.field_id);suffix+='?'+params}
  return session.request(`/api/workspaces/${scope.workspace_id}/responsibility/${scope.connection_id}/${scope.repository}/${suffix}`,options)
 }}
}

export function createPipelineSessionClient(session){
 return {clear:()=>session.clear(),request(scope,kind,{runID,signal}={}){return session.request(readPath(scope,kind,{runID}),{signal})}}
}
