const segment=/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/
const principal=/^[A-Za-z0-9._:-]{1,128}$/
const hash=/^[0-9a-f]{64}$/
const exact=(pattern,value)=>typeof value==='string'&&value.match(pattern)?.[0]===value
const integer=(v,lo=0,hi=Number.MAX_SAFE_INTEGER)=>Number.isSafeInteger(v)&&v>=lo&&v<=hi
const bad=()=>{throw new Error('Invalid or incomplete service response.')}
const fields=(v,names)=>v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join(',')===[...names].sort().join(',')
const text=value=>typeof value==='string'&&!/[\u0000-\u001f\u007f]/.test(value)&&![...value].some(c=>c.codePointAt(0)>=0xd800&&c.codePointAt(0)<=0xdfff)
export function scopeFrom(workspace,connection,bucket){
 if(![workspace,connection,bucket].every(x=>exact(segment,x)))throw new Error('Enter exact workspace, connection and bucket IDs.')
 return {workspace_id:workspace,connection_id:connection,bucket_id:bucket}
}
export const sameScope=(a,b)=>fields(a,['workspace_id','connection_id','bucket_id'])&&['workspace_id','connection_id','bucket_id'].every(k=>a[k]===b[k])
export function validateStatus(data,scope){
 if(!fields(data,['scope','revision','published_revision','pending','capabilities'])||!sameScope(data.scope,scope)||!integer(data.revision,1)||!(data.published_revision===null||integer(data.published_revision,1,data.revision))||typeof data.pending!=='boolean'||!fields(data.capabilities,['refresh','review','approve'])||Object.values(data.capabilities).some(v=>typeof v!=='boolean')||!Object.values(data.capabilities).some(Boolean)||data.capabilities.approve&&!data.capabilities.review)bad()
 return data
}
function canonical(value){
 if(Array.isArray(value))return '['+value.map(canonical).join(',')+']'
 if(value!==null&&typeof value==='object')return '{'+Object.keys(value).sort().map(k=>canonical(k)+':'+canonical(value[k])).join(',')+'}'
 return JSON.stringify(value).replace(/[\u007f-\uffff]/g,c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'))
}
export async function candidateDigest(snapshot){
 const encoded=new TextEncoder().encode(canonical(snapshot))
 if(encoded.length>1048576)bad()
 return [...new Uint8Array(await crypto.subtle.digest('SHA-256',encoded))].map(x=>x.toString(16).padStart(2,'0')).join('')
}
export async function validateReview(data,scope,version){
 if(!fields(data,['scope','revision','candidate_digest','snapshot','readers','complete','count'])||!sameScope(data.scope,scope)||data.revision!==version||!integer(data.revision,1)||data.complete!==true||!integer(data.count,0,500)||!exact(hash,data.candidate_digest))bad()
 const s=data.snapshot
 if(!fields(s,['schema','scope','observed_at','offset_minutes','artifacts'])||s.schema!==1||!sameScope(s.scope,scope)||!text(s.observed_at)||s.observed_at.length>40||!/(Z|[+-]\d\d:\d\d)$/.test(s.observed_at)||!Number.isFinite(Date.parse(s.observed_at))||!integer(s.offset_minutes,-840,840)||!Array.isArray(s.artifacts)||s.artifacts.length!==data.count)bad()
 const keys=new Set()
 for(const row of s.artifacts){
  if(!fields(row,['key','modified_at','size','is_dir'])||!text(row.key)||!row.key||new TextEncoder().encode(row.key).length>1024||row.key.split('/').length>32||row.key.startsWith('/')||row.key.includes('\\')||row.key.includes('://')||row.key.split('/').some(x=>x==='.'||x==='..')||keys.has(row.key)||typeof row.is_dir!=='boolean'||row.key.endsWith('/')!==row.is_dir||!integer(row.size)||row.is_dir&&row.size!==0||!text(row.modified_at)||row.modified_at.length>40||!/(Z|[+-]\d\d:\d\d)$/.test(row.modified_at)||!Number.isFinite(Date.parse(row.modified_at))||Date.parse(row.modified_at)>Date.parse(s.observed_at))bad()
  keys.add(row.key)
 }
 if(!Array.isArray(data.readers)||!integer(data.readers.length,1,100)||data.readers.some(x=>!exact(principal,x))||new Set(data.readers).size!==data.readers.length||JSON.stringify(data.readers)!==JSON.stringify([...data.readers].sort()))bad()
 if(await candidateDigest(s)!==data.candidate_digest)bad()
 return data
}
export function validateMutation(data,scope,version,action){
 if(!sameScope(data?.scope,scope)||data.revision!==version+1||!integer(data.revision,1))bad()
 if(action==='approve'){
  if(!fields(data,['scope','revision','publication'])||data.publication!=='approved')bad()
 }else if(action==='refresh'){
  if(!fields(data,['scope','revision','publication','candidate_digest','scanned','pages','retained'])||data.publication!=='pending'||!exact(hash,data.candidate_digest)||!integer(data.scanned,0,500)||!integer(data.pages,1,10)||!integer(data.retained,0,500))bad()
 }else bad()
 return data
}
export const messageFor=status=>({401:'Authentication expired or was rejected. Reconnect.',403:'Current permissions do not allow this action.',404:'This scope, candidate or capability is unavailable to your identity.',409:'The workspace or observation is unavailable. Reload current state.',412:'The version, candidate or reader set changed. Reload and review the complete set again.',428:'A current revision is required.',502:'Provider refresh failed. The previous index was retained.',503:'The service is unavailable.'})[status]||'The service could not complete the request.'
export function createClient({fetcher=fetch}={}){
 let credential=''
 return {
  connect(value){if(typeof value!=='string'||!value||value.length>8192||/[\s\u0000-\u001f\u007f]/.test(value))throw new Error('Enter a valid opaque credential.');credential=value},
  clear(){credential=''},
  async request(scope,action,{revision,body,signal}={}){
   if(!credential)throw new Error('Disconnected.')
   scopeFrom(scope.workspace_id,scope.connection_id,scope.bucket_id)
   if(!['status','review','refresh','approve'].includes(action)||action!=='status'&&!integer(revision,1))throw new Error('Invalid request.')
   const write=action==='refresh'||action==='approve'
   if(write&&(!body||typeof body!=='object')||!write&&body!==undefined)throw new Error('Invalid request.')
   const headers={Authorization:`Bearer ${credential}`}
   if(action!=='status')headers['If-Match']=`"${revision}"`
   if(write)headers['Content-Type']='application/json'
   const response=await fetcher(`/api/workspaces/${scope.workspace_id}/artifact-admin/${scope.connection_id}/${scope.bucket_id}/${action}`,{method:write?'POST':'GET',headers,body:write?JSON.stringify(body):undefined,signal,credentials:'omit',cache:'no-store',redirect:'error'})
   if(!response.ok){const error=new Error(messageFor(response.status));error.status=response.status;throw error}
   const raw=await response.text();if(raw.length>2097152)bad()
   const payload=JSON.parse(raw);if(!fields(payload,['data']))bad()
   return payload.data
  }
 }
}
export class LatestOperation{
 #generation=0;#controller
 begin(){this.#controller?.abort();this.#controller=new AbortController();return {generation:++this.#generation,signal:this.#controller.signal}}
 current(operation){return operation.generation===this.#generation}
 clear(){this.#generation++;this.#controller?.abort();this.#controller=undefined}
}
