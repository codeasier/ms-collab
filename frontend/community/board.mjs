import {scopeFrom, validateCapabilities, validateMutation, loadSnapshot} from './client.mjs'

function filterItems(items,filter){
  const query=(filter.query||'').trim().toLocaleLowerCase()
  return items.filter(item=>(!filter.kind||filter.kind===item.kind)&&(!filter.state||filter.state===item.state)&&(!query||[item.title,...item.labels].some(text=>text.toLocaleLowerCase().includes(query))))
}
const labelPageSize=10
function lastLabelPage(count){return Math.max(0,Math.ceil(count/labelPageSize)-1)}
// The caller supplies the actual view read and its completed, validated loadSnapshot result.
export function sharedLabelView(chosen,capabilities,snapshot,filter={},exactLabel=null,labelPage=0){
  if(chosen!=='items'||capabilities?.items!==true||snapshot?.available!==true||!Array.isArray(snapshot.items)||snapshot.items.length>200)return null
  const keys=new Set()
  for(const item of snapshot.items){
    if(!item||Object.hasOwn(item,'approved')||Object.hasOwn(item,'fingerprint'))return null
    const key=`${item.kind}:${item.number}`
    if(keys.has(key))return null
    keys.add(key)
  }
  const base=filterItems(snapshot.items,filter),counts=new Map()
  for(const item of base)for(const label of new Set(item.labels))counts.set(label,(counts.get(label)||0)+1)
  const ranked=Array.from(counts,([label,count])=>({label,count}))
  ranked.sort((a,b)=>b.count-a.count||(a.label<b.label?-1:a.label>b.label?1:0))
  const index=Number.isSafeInteger(labelPage)&&labelPage>0?Math.min(labelPage,lastLabelPage(ranked.length)):0
  return {baseCount:base.length,distinctCount:ranked.length,top:ranked.slice(index*labelPageSize,(index+1)*labelPageSize),
    rows:exactLabel===null?base:base.filter(item=>item.labels.includes(exactLabel))}
}
// Quote literal backslashes first, so an escaped control cannot impersonate a literal marker.
function quotedLabel(label){
  return JSON.stringify(label).replace(/[\p{White_Space}\p{Cc}\p{Cf}\p{Default_Ignorable_Code_Point}]/gu,
    char=>char===' '?' ':`\\u{${char.codePointAt(0).toString(16).toUpperCase()}}`)
}

export function mountCommunityPage({client,sessionMode=false,onSignOut=()=>{}}) {
if(!client||typeof client.request!=='function'||typeof client.clear!=='function')throw new Error('Explicit community request client required.')
const el = id => document.getElementById(id)
const transportAllowed = location.protocol==='https:'||(!sessionMode&&location.protocol==='http:'&&location.hostname==='127.0.0.1')
let generation=0, controller, scope, capabilities, version, snapshot, view='items', page=0, pending, saving=false
let approved=new Set(), draft=new Set(), filter={query:'',kind:'',state:''}
let loadedView=null,exactLabel=null,labelPage=0,labelEvents
const optionLabels=new Map()
const current = value => value === generation
function cancelRequests() { generation++;controller?.abort();controller=new AbortController();return generation }
function closeConfirmation() {pending=undefined;el('confirmation').close();for(const id of ['confirm-title','confirm-detail','confirm-note'])el(id).textContent=''}
function clearPanel() {
  saving=false;for(const id of ['refresh','review-view','items-view','reload'])el(id).disabled=false
  loadedView=null;clearLabels()
  closeConfirmation();capabilities=undefined;version=undefined;snapshot=undefined;approved.clear();draft.clear();page=0
  el('panel').hidden=true;el('items').replaceChildren();for(const id of ['scope-title','revision','summary','approval-summary','page','boundary','view-label'])el(id).textContent=''
  for(const id of ['items-view','review-view','refresh','approval-panel'])el(id).hidden=true
  el('save').disabled=true
}
function disconnect(message='Disconnected. Any already accepted change may have completed. Reconnect and reload to verify.') {
  cancelRequests();client.clear();clearPanel();scope=undefined;el('session').hidden=true;if(el('connect'))el('connect').hidden=!transportAllowed
  for(const id of ['credential','workspace','connection','repository','query'])if(el(id))el(id).value=''
  el('kind').value='';el('state').value='';filter={query:'',kind:'',state:''};el('status').textContent=message
}
function failure(error, token) {
  if(!current(token)||error.name==='AbortError')return
  if(error.status===401){disconnect(error.message);return}
  clearPanel();el('status').textContent=error.status?error.message:'Unable to load a valid, consistent response. Open the scope again to retry.'
}
function selectionCounts(){return {added:[...draft].filter(x=>!approved.has(x)).length,removed:[...approved].filter(x=>!draft.has(x)).length}}
function updateApprovals(){
  const {added,removed}=selectionCounts()
  el('approval-summary').textContent=`${draft.size} approved in the complete snapshot · ${added} additions · ${removed} removals. Maximum 200.`
  el('save').disabled=saving||!capabilities?.approve||draft.size>200||!(added||removed)
}
function textNode(tag,text,className){const node=document.createElement(tag);node.textContent=text;if(className)node.className=className;return node}
function clearLabels(){
  labelEvents?.abort();labelEvents=undefined;optionLabels.clear();exactLabel=null;labelPage=0
  el('shared-labels').hidden=true;el('label-options').replaceChildren();el('label-input').value=''
  el('label-status').textContent='';el('label-page').textContent='';el('label-pages').hidden=true
}
// A retired form can never submit a query, even if a stale submit event is dispatched.
el('shared-labels').addEventListener('submit',event=>event.preventDefault())
function applyLabel(value){
  exactLabel=value;page=0
  // A single-line field would silently strip CR/LF. The selected raw value lives separately.
  el('label-input').value=value!==null&&!/[\r\n]/.test(value)?value:''
  render()
}
function renderLabels(facet){
  if(!labelEvents){
    labelEvents=new AbortController()
    const options={signal:labelEvents.signal}
    el('shared-labels').addEventListener('submit',()=>applyLabel(el('label-input').value||null),options)
    el('label-clear').addEventListener('click',()=>applyLabel(null),options)
    el('label-previous').addEventListener('click',()=>{labelPage=Math.max(0,labelPage-1);render()},options)
    el('label-next').addEventListener('click',()=>{labelPage++;render()},options)
    el('label-options').addEventListener('click',event=>{
      const button=event.target.closest('button')
      if(!optionLabels.has(button))return
      const value=optionLabels.get(button);applyLabel(value)
      for(const [current,name] of optionLabels)if(name===value){current.focus();break}
    },options)
  }
  const last=lastLabelPage(facet.distinctCount)
  labelPage=Math.min(labelPage,last)
  el('shared-labels').hidden=false;el('label-pages').hidden=last===0
  el('label-page').textContent=last?`Label page ${labelPage+1} of ${last+1}`:''
  el('label-previous').disabled=labelPage===0;el('label-next').disabled=labelPage===last
  optionLabels.clear();el('label-options').replaceChildren()
  for(const {label,count} of facet.top){
    const button=document.createElement('button');button.type='button'
    button.setAttribute('aria-pressed',String(label===exactLabel));optionLabels.set(button,label)
    button.append(textNode('bdi',quotedLabel(label),'label-name'),document.createTextNode(' '),textNode('span',`${count} ${count===1?'item':'items'}`,'label-count'))
    el('label-options').append(button)
  }
  const context=`${facet.baseCount} of ${snapshot.items.length} approved loaded items match the current filters. ${facet.distinctCount} distinct labels.`
  const empty=!snapshot.items.length?' No approved items loaded.':!facet.baseCount?' No items match the current filters.':!facet.distinctCount?' No labels on these items.':''
  const selection=exactLabel===null?' No exact label selected.':` Exact label ${quotedLabel(exactLabel)}: ${facet.rows.length} matching items.`
  el('label-status').textContent=context+empty+selection
}
function render(){
  if(!snapshot)return
  const facet=saving?null:sharedLabelView(loadedView,capabilities,snapshot,filter,exactLabel,labelPage)
  if(facet)renderLabels(facet);else clearLabels()
  const rows=facet?facet.rows:filterItems(snapshot.items,filter)
  const pages=Math.max(1,Math.ceil(rows.length/20));page=Math.min(page,pages-1);el('items').replaceChildren()
  for(const item of rows.slice(page*20,(page+1)*20)){
    const row=document.createElement('li'),record=textNode('div','','record')
    record.append(textNode('span',`${item.kind==='issue'?'Issue':'Pull request'} #${item.number} · ${item.state}`,'badge'),textNode('h3',item.title),textNode('p',`Updated ${item.updated_at}`))
    if(item.labels.length)record.append(textNode('p',item.labels.join(' · ')))
    row.append(record)
    if(view==='review'){
      if(capabilities.approve){
        const label=textNode('label','','approve-choice'),checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.checked=draft.has(item.fingerprint);checkbox.disabled=saving
        checkbox.setAttribute('aria-label',`Approve ${item.kind} ${item.number}`)
        checkbox.addEventListener('change',()=>{if(checkbox.checked)draft.add(item.fingerprint);else draft.delete(item.fingerprint);updateApprovals()})
        label.append(checkbox,document.createTextNode('Share'));row.append(label)
      }else record.append(textNode('p',item.approved?'Approved for configured readers':'Not shared'))
    }
    el('items').append(row)
  }
  el('summary').textContent=!capabilities[view]?'This identity cannot read or review snapshot contents. Only the explicitly granted refresh action is available.':!snapshot.available?(sessionMode&&!capabilities.refresh?'No snapshot exists yet. Provider refresh is not configured on this host. No content is inferred.':'No snapshot exists yet. An authorized refresh is required.'):`${rows.length} matching items of ${snapshot.items.length} loaded. Observed subset, not a complete repository inventory.`
  if(snapshot.available&&!rows.length)el('items').append(textNode('li','No items match this view. Unapproved provider-visible content is never included in Shared items.'))
  el('page').textContent=`Page ${page+1} of ${pages}`;el('previous').disabled=page===0;el('next').disabled=page===pages-1
  el('filters').hidden=!snapshot.available;el('approval-panel').hidden=view!=='review'||!capabilities.approve||!snapshot.available;updateApprovals()
}
async function load(requestedView=view, notice='') {
  if(!scope)return
  const selected={...scope},token=cancelRequests();clearPanel();el('status').textContent='Checking current capabilities…'
  try{
    const result=validateCapabilities(await client.request(selected,'capabilities',{signal:controller.signal}),selected)
    if(!current(token))return
    const allowed=result.capabilities, chosen=allowed[requestedView]?requestedView:allowed.items?'items':allowed.review?'review':null
    el('status').textContent='Loading a consistent authorized snapshot…'
    const data=chosen?await loadSnapshot(client,selected,chosen,result.revision,controller.signal,()=>current(token)):{available:false,items:[],observed_at:null}
    if(!current(token))return
    capabilities=allowed;version=result.revision;snapshot=data;loadedView=chosen;view=chosen||'items';approved=new Set(data.items.filter(x=>x.approved).map(x=>x.fingerprint));draft=new Set(approved)
    el('scope-title').textContent=`${selected.workspace_id} / ${selected.connection_id} / ${selected.repository}`
    el('revision').textContent=`Version ${version}${data.observed_at?' · Observed '+data.observed_at:''}`
    el('view-label').textContent=chosen==='review'?'Private review candidates':chosen==='items'?'Explicitly shared items':'Refresh-only access'
    el('boundary').textContent=chosen==='review'?'Provider-visible does not mean shared. Only checked exact content versions are shared after you explicitly confirm approval changes.':'Shared content remains scoped to configured readers and current workspace membership. There is no anonymous or automatic public feed.'
    for(const action of ['items','review']){el(`${action}-view`).hidden=!allowed[action];el(`${action}-view`).setAttribute('aria-pressed',String(view===action))}
    el('refresh').hidden=!allowed.refresh;el('panel').hidden=false;render();el('status').textContent=notice||'Current permissions verified for this read. Filters use the loaded snapshot; reload to recheck access.'
  }catch(error){failure(error,token)}
}
function confirmAction(action){
  if(saving||!scope||!capabilities?.[action]||!version)return
  const {added,removed}=selectionCounts()
  if(action==='approve'&&(!snapshot?.available||draft.size>200||!(added||removed)))return
  pending={action,scope:{...scope},version,generation,body:action==='approve'?{fingerprints:[...draft].sort()}:{} }
  el('confirm-title').textContent=action==='approve'?'Confirm exact-content sharing':'Refresh provider observation'
  el('confirm-detail').textContent=`${scope.workspace_id} / ${scope.connection_id} / ${scope.repository} · version ${version}`
  el('confirm-note').textContent=action==='approve'?`Add ${added}, remove ${removed}; ${draft.size} total approved content versions for the configured reader audience. This replaces the complete approval set, not only the visible page. It does not create anonymous public access.`:'Fetch a fresh authorized provider snapshot. Changed content loses its previous approval. This does not approve any new content. Unsaved approval choices will be discarded.'
  el('confirmation').showModal()
}
if(!sessionMode)el('connect').addEventListener('submit',event=>{event.preventDefault();if(!transportAllowed)return;try{client.connect(el('credential').value)}catch{el('status').textContent='Enter a valid opaque credential.';return}el('credential').value='';el('connect').hidden=true;el('session').hidden=false;el('status').textContent='Enter an operator-provided scope to verify access.'})
el('disconnect').addEventListener('click',()=>{if(sessionMode)onSignOut();else disconnect()})
for(const id of ['workspace','connection','repository'])el(id).addEventListener('input',()=>{cancelRequests();clearPanel();scope=undefined;el('status').textContent='Scope changed. Open it again to verify current access.'})
el('scope-form').addEventListener('submit',event=>{event.preventDefault();cancelRequests();clearPanel();try{scope=scopeFrom(el('workspace').value,el('connection').value,el('repository').value)}catch{scope=undefined;el('status').textContent='Enter valid workspace, connection and owner/name identifiers.';return}filter={query:'',kind:'',state:''};for(const id of ['query','kind','state'])el(id).value='';load('items')})
el('reload').addEventListener('click',()=>load())
for(const action of ['items','review'])el(`${action}-view`).addEventListener('click',()=>load(action))
el('refresh').addEventListener('click',()=>confirmAction('refresh'));el('save').addEventListener('click',()=>confirmAction('approve'))
el('cancel').addEventListener('click',closeConfirmation);el('confirmation').addEventListener('cancel',closeConfirmation)
el('filters').addEventListener('submit',event=>{event.preventDefault();filter={query:el('query').value,kind:el('kind').value,state:el('state').value};page=0;labelPage=0;render()})
el('previous').addEventListener('click',()=>{page--;render()});el('next').addEventListener('click',()=>{page++;render()})
el('confirm-form').addEventListener('submit',async event=>{
  event.preventDefault();if(!pending||saving||pending.generation!==generation)return
  const operation=pending;closeConfirmation();saving=true;el('status').textContent='Submitting once… If interrupted, reload to verify the outcome.'
  for(const id of ['refresh','save','review-view','items-view','reload'])el(id).disabled=true
  render()
  try{
    const result=await client.request(operation.scope,operation.action,{body:operation.body,revision:operation.version,signal:controller.signal})
    if(!current(operation.generation))return
    validateMutation(result,operation.scope,operation.version,operation.action,operation.body.fingerprints?.length)
    await load(view,operation.action==='approve'?'Approval set saved. Current permissions and content rechecked.':'Snapshot refreshed. No new content was automatically approved.')
  }catch(error){
    if(!current(operation.generation)||error.name==='AbortError')return
    if(error.status===401){disconnect(error.message);return}
    await load(view,error.status?error.message:'Result uncertain. Current state reloaded; verify before submitting again.')
  }finally{if(current(operation.generation)){saving=false;for(const id of ['refresh','review-view','items-view','reload'])el(id).disabled=false;render()}}
})
if(!sessionMode)window.addEventListener('pagehide',()=>disconnect('Disconnected. Reconnect to load current state.'))
if(!transportAllowed){if(el('connect'))el('connect').hidden=true;el('status').textContent='HTTPS is required outside numeric loopback.'}

return {activate(){
  if(!transportAllowed)throw new Error('HTTPS is required.')
  el('session').hidden=false;el('status').textContent='Enter an operator-provided scope to verify current access.'
},clear:disconnect}
}
