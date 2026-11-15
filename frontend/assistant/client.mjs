// Saved display records only. No ACP protocol, execution, token or tool bridge.
const own=(value,keys)=>value!==null&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).sort().join(',')===[...keys].sort().join(',')
const bad=()=>{throw new Error('Invalid or inconsistent saved history. Reload explicitly.')}
const integer=(n,low,high=Number.MAX_SAFE_INTEGER)=>Number.isSafeInteger(n)&&n>=low&&n<=high
export function identifier(value){if(typeof value!=='string'||value.match(/^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/)?.[0]!==value)bad();return value}
const states=['active','archived'],kinds=['user_message','assistant_message','agent_thought','tool','notice'],statuses=['pending','in_progress','completed','failed','canceled']
const policy='stored_display_only;no_replay'
function text(value,max,empty=false){if(typeof value!=='string'||(!empty&&!value.trim())||value.includes('\0')||/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value)||new TextEncoder().encode(value).length>max)bad()}
function timestamp(value){if(typeof value!=='string'||value.length>64||!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(value)||!Number.isFinite(Date.parse(value)))bad();return Date.parse(value)}
export function validateMetadata(value,workspace,id){
 if(!own(value,['workspace_id','id','title','state','revision','created_at','updated_at','event_count','capabilities'])||value.workspace_id!==identifier(workspace)||identifier(value.id)!==(id===undefined?value.id:identifier(id))||!states.includes(value.state)||!integer(value.revision,1)||!integer(value.event_count,0,2000))bad()
 text(value.title,800);if(timestamp(value.updated_at)<timestamp(value.created_at))bad()
 const c=value.capabilities
 if(!own(c,['events','archive','restore','execute','resume','export'])||c.events!==true||[c.archive,c.restore].some(x=>typeof x!=='boolean')||c.execute!==false||c.resume!==false||c.export!==false||(value.state==='active'&&c.restore)||(value.state==='archived'&&c.archive))bad()
 return value
}
export function validateList(value,workspace,state){
 if(!own(value,['workspace_id','state','items','execution_enabled','history_policy'])||value.workspace_id!==identifier(workspace)||!states.includes(state)||value.state!==state||value.execution_enabled!==false||value.history_policy!==policy||!Array.isArray(value.items)||value.items.length>100)bad()
 const ids=new Set();for(const item of value.items){validateMetadata(item,workspace);if(item.state!==state||ids.has(item.id))bad();ids.add(item.id)}return value
}
export function validateEvents(value,metadata,after){
 const {workspace_id:workspace,id,revision,event_count:total}=metadata
 if(!own(value,['workspace_id','conversation_id','revision','items','total','after','limit','next_after','has_more','history_policy','execution_enabled'])||value.workspace_id!==workspace||value.conversation_id!==id||value.revision!==revision||value.total!==total||value.after!==after||value.limit!==50||!integer(after,0,total)||value.execution_enabled!==false||value.history_policy!==policy||!Array.isArray(value.items)||value.items.length!==Math.min(50,total-after))bad()
 for(const [index,event] of value.items.entries()){
  if(event===null||typeof event!=='object')bad()
  if(!own(event,event.kind==='tool'?['sequence','kind','text','tool_name','status']:['sequence','kind','text'])||event.sequence!==after+index+1||!kinds.includes(event.kind))bad()
  text(event.text,8192,event.kind==='tool');if(event.kind==='tool'){text(event.tool_name,200);if(!statuses.includes(event.status))bad()}
 }
 const end=after+value.items.length,more=end<total
 if(value.has_more!==more||value.next_after!==(more?end:null))bad()
 return value
}
export function createAssistantSessionClient(session){
 if(!session||typeof session.request!=='function')throw new Error('Explicit session client required.')
 const base=workspace=>`/api/workspaces/${identifier(workspace)}/assistant/conversations`
 return {
  async list(workspace,state,signal){if(!states.includes(state))bad();return validateList(await session.request(base(workspace)+'?state='+state,{signal}),workspace,state)},
  async metadata(workspace,id,signal){return validateMetadata(await session.request(base(workspace)+'/'+identifier(id),{signal}),workspace,id)},
  async events(metadata,after,signal){validateMetadata(metadata,metadata.workspace_id,metadata.id);if(!integer(after,0,metadata.event_count))bad();const suffix=`/events?after=${after}&limit=50&revision=${metadata.revision}`;return validateEvents(await session.request(base(metadata.workspace_id)+'/'+metadata.id+suffix,{signal}),metadata,after)},
  async lifecycle(metadata,state,signal){
   validateMetadata(metadata,metadata.workspace_id,metadata.id)
   if(!states.includes(state)||state===metadata.state||!metadata.capabilities[state==='active'?'restore':'archive'])bad()
   const result=validateMetadata(await session.request(base(metadata.workspace_id)+'/'+metadata.id,{method:'PATCH',body:{state},revision:metadata.revision,signal}),metadata.workspace_id,metadata.id)
   if(result.state!==state||result.revision!==metadata.revision+1||['title','created_at','updated_at','event_count'].some(k=>result[k]!==metadata[k]))bad()
   return result
  }
 }
}
