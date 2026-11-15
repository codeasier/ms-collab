const segment=/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/
const fieldId=/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/
const principal=/^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/
const sha=/^(?:[0-9a-f]{40}|[0-9a-f]{64})$/
const exact=(pattern,value)=>typeof value==='string'&&value.match(pattern)?.[0]===value
const integer=(v,min=0,max=Number.MAX_SAFE_INTEGER)=>Number.isSafeInteger(v)&&v>=min&&v<=max
const object=(v,keys)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join(',')===[...keys].sort().join(',')
const text=(v,max)=>typeof v==='string'&&[...v].length<=max&&!/[\u0000-\u001f\u007f]/.test(v)&&![...v].some(c=>c.codePointAt(0)>=0xd800&&c.codePointAt(0)<=0xdfff)
const timestamp=v=>text(v,64)&&/(Z|[+-]\d\d:\d\d)$/.test(v)&&Number.isFinite(Date.parse(v))
const fail=()=>{throw new Error('Invalid, incomplete or changed service response. Reload current state.')}
const path=v=>text(v,1024)&&v.length>0&&!v.startsWith('/')&&!v.includes('\\')&&!v.split('/').some(x=>['','.','..'].includes(x))
const unique=a=>new Set(a).size===a.length
export function scopeFrom(workspace,connection,owner,repo){
 if(![workspace,connection,owner,repo].every(v=>exact(segment,v)))throw new Error('Enter exact workspace, connection, repository owner and name IDs.')
 return {workspace_id:workspace,connection_id:connection,repository:owner+'/'+repo}
}
export const sameScope=(a,b)=>object(a,['workspace_id','connection_id','repository'])&&['workspace_id','connection_id','repository'].every(k=>a[k]===b[k])
export function validateFields(value){
 if(!Array.isArray(value)||value.length>200)fail()
 const ids=new Set()
 for(const f of value){
  if(!object(f,['id','name','kind','paths','owner_ids','backup'])||!exact(fieldId,f.id)||ids.has(f.id)||!text(f.name,100)||!f.name.trim()||f.name!==f.name.trim()||!['developer','committer'].includes(f.kind)||!Array.isArray(f.paths)||!integer(f.paths.length,1,100)||!f.paths.every(path)||!unique(f.paths)||!Array.isArray(f.owner_ids)||!integer(f.owner_ids.length,1,f.kind==='developer'?3:100)||!f.owner_ids.every(p=>exact(principal,p))||!unique(f.owner_ids))fail()
  if(f.backup!==null&&(!object(f.backup,['principal_id','field_id'])||f.kind!=='developer'||!exact(principal,f.backup.principal_id)||!exact(fieldId,f.backup.field_id)))fail()
  ids.add(f.id)
 }
 for(const f of value){if(f.backup){const source=value.find(x=>x.id===f.backup.field_id);if(!source||source.id===f.id||source.kind!=='developer'||!source.owner_ids.includes(f.backup.principal_id)||f.owner_ids.includes(f.backup.principal_id))fail()}}
 return value
}
const common=['scope','revision','config_version','identity_namespace','workflow_enabled']
function validateCommon(data,scope){
 if(!sameScope(data.scope,scope)||!integer(data.revision,1)||!integer(data.config_version,1)||data.identity_namespace!=='ms-collab.principal.id'||data.workflow_enabled!==false)fail()
}
export function validateCapabilities(data,scope){
 if(!object(data,[...common,'capabilities'])||!object(data.capabilities,['read','edit'])||data.capabilities.read!==true||typeof data.capabilities.edit!=='boolean')fail()
 validateCommon(data,scope);return data
}
export function validateConfig(data,scope,expected){
 if(!object(data,[...common,'fields']))fail()
 validateCommon(data,scope);validateFields(data.fields)
 if(expected&&(data.revision!==expected.revision||data.config_version!==expected.config_version))fail()
 return data
}
export function queryFrom(view='all',field='',offset=0){
 if(!['mine','all','field','unassigned'].includes(view)||!integer(offset,0,100000)||(view==='field'?!exact(fieldId,field):field!==''))throw new Error('Select a valid candidate view.')
 return {view,field_id:field,offset,limit:20}
}
export function validateCandidates(data,scope,config,query){
 if(!object(data,[...common,'mode','review_status','approval_available','transport_executable','external_effect','items','total','offset','limit','view','incomplete_pull_count','directory_status','coverage','community_revision','snapshot_available','snapshot_observed_at','evidence_current','evidence_observed_at']))fail()
 validateCommon(data,scope)
 if(data.revision!==config.revision||data.config_version!==config.config_version||data.mode!=='readonly_foundation'||data.review_status!=='unverified'||data.approval_available!==false||data.transport_executable!==false||data.external_effect!=='not_observed'||data.directory_status!=='not_implemented'||data.coverage!=='approved_observed_subset'||data.offset!==query.offset||data.limit!==query.limit||data.view!==query.view||!integer(data.total)||!integer(data.incomplete_pull_count)||!integer(data.community_revision,1)||typeof data.snapshot_available!=='boolean'||typeof data.evidence_current!=='boolean'||(data.snapshot_available?!timestamp(data.snapshot_observed_at):data.snapshot_observed_at!==null)||(data.evidence_current?!timestamp(data.evidence_observed_at):data.evidence_observed_at!==null)||!Array.isArray(data.items)||data.items.length!==Math.min(query.limit,Math.max(0,data.total-query.offset)))fail()
 if(!data.snapshot_available&&(data.total!==0||data.incomplete_pull_count!==0||data.evidence_current))fail()
 const keys=new Set(),known=new Map(config.fields.map(f=>[f.id,f]))
 for(const row of data.items){
  if(!object(row,['kind','number','title','assignment_status','unmapped_paths','fields','my_field_ids','assignee_identity_status','review_status','approval_available','path_evidence','head_sha','base_sha'])||!['issue','pull'].includes(row.kind)||!integer(row.number,1)||!text(row.title,4096)||row.assignee_identity_status!=='unknown'||row.review_status!=='unverified'||row.approval_available!==false||!Array.isArray(row.fields)||row.fields.length>200||!Array.isArray(row.my_field_ids)||!unique(row.my_field_ids)||!Array.isArray(row.unmapped_paths)||row.unmapped_paths.length>4000||!row.unmapped_paths.every(path)||!unique(row.unmapped_paths))fail()
  const key=row.kind+':'+row.number;if(keys.has(key))fail();keys.add(key)
  const ids=new Set()
  for(const f of row.fields){if(!object(f,['id','kind','review_status'])||!known.has(f.id)||known.get(f.id).kind!==f.kind||ids.has(f.id)||f.review_status!=='unverified')fail();ids.add(f.id)}
  if(row.my_field_ids.some(id=>!ids.has(id)||known.get(id).kind!=='developer'))fail()
  if(row.kind==='issue'){
   if(row.assignment_status!=='unknown'||row.path_evidence!=='unavailable'||row.head_sha!==''||row.base_sha!==''||row.fields.length||row.unmapped_paths.length||row.my_field_ids.length)fail()
  }else if(row.path_evidence==='unavailable'){
   if(row.assignment_status!=='incomplete'||row.head_sha!==''||row.base_sha!==''||row.fields.length||row.unmapped_paths.length||row.my_field_ids.length)fail()
  }else if(row.path_evidence==='complete_unpinned_observation'){
   if(!['matched','partially_mapped','unassigned'].includes(row.assignment_status)||!exact(sha,row.head_sha)||!exact(sha,row.base_sha)||!data.evidence_current)fail()
   const developers=row.fields.filter(f=>f.kind==='developer').length
   if(row.assignment_status==='matched'&&(!developers||row.unmapped_paths.length)||row.assignment_status==='partially_mapped'&&(!developers||!row.unmapped_paths.length)||row.assignment_status==='unassigned'&&developers)fail()
  }else fail()
  if(query.view==='field'&&!ids.has(query.field_id)||query.view==='mine'&&!row.my_field_ids.length||query.view==='unassigned'&&row.kind!=='issue')fail()
 }
 if(data.incomplete_pull_count<data.items.filter(x=>x.kind==='pull'&&x.assignment_status==='incomplete').length||query.view==='all'&&data.incomplete_pull_count>data.total)fail()
 return data
}
export const messageFor=status=>({401:'Authentication expired or was rejected. Reconnect.',403:'Your current permissions do not allow this change. Reload current state.',404:'This scope or capability is unavailable to your identity.',409:'The workspace or requested configuration is unavailable. Reload current state.',412:'The revision changed. Nothing was retried. Reload and review the latest configuration before editing again.',428:'A current revision is required.',400:'The service rejected this configuration. Check field IDs, paths and current member IDs.',503:'The service is unavailable. Reload to verify any uncertain write.'})[status]||'The service could not complete the request.'
export function createClient({fetcher=fetch}={}){
 let credential=''
 return {
  connect(value){if(typeof value!=='string'||!value||value.length>8192||/[\s\u0000-\u001f\u007f]/.test(value))throw new Error('Enter a valid opaque credential.');credential=value},
  clear(){credential=''},
  async request(scope,action,{query,fields,revision,signal}={}){
   if(!credential)throw new Error('Disconnected.')
   const parts=scope.repository.split('/');scopeFrom(scope.workspace_id,scope.connection_id,...parts)
   if(parts.length!==2||!['capabilities','config','candidates','save'].includes(action))throw new Error('Invalid request.')
   const headers={Authorization:`Bearer ${credential}`},write=action==='save'
   let suffix=action==='save'?'config':action,body
   if(write){if(!integer(revision,1))throw new Error('Current revision required.');validateFields(fields);body=JSON.stringify({fields});if(new TextEncoder().encode(body).length>65536)throw new Error('Configuration exceeds 64 KiB.');headers['If-Match']=`"${revision}"`;headers['Content-Type']='application/json'}
   if(action==='candidates'){const q=queryFrom(query?.view,query?.field_id,query?.offset);if(query.limit!==20)throw new Error('Invalid request.');const params=new URLSearchParams({view:q.view,offset:String(q.offset),limit:String(q.limit)});if(q.view==='field')params.set('field_id',q.field_id);suffix+='?'+params}
   const response=await fetcher(`/api/workspaces/${scope.workspace_id}/responsibility/${scope.connection_id}/${scope.repository}/${suffix}`,{method:write?'PUT':'GET',headers,body,signal,credentials:'omit',cache:'no-store',redirect:'error'})
   if(!response.ok){const error=new Error(messageFor(response.status));error.status=response.status;throw error}
   const raw=await response.text();if(raw.length>2097152)fail()
   const payload=JSON.parse(raw);if(!object(payload,['data']))fail();return payload.data
  }
 }
}
export class LatestOperation{
 #generation=0;#controller
 begin(){this.#controller?.abort();this.#controller=new AbortController();return {generation:++this.#generation,signal:this.#controller.signal}}
 current(operation){return operation.generation===this.#generation}
 clear(){this.#generation++;this.#controller?.abort();this.#controller=undefined}
}
