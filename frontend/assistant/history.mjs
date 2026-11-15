// An owner-scoped display reader with reversible local metadata only.
import {identifier} from './client.mjs'
export function mountAssistantPage({client}){
 const el=id=>document.getElementById('history-'+id)
 let active=false,generation=0,controller,scope,filter='active',rows=[],selected,events=[],next=null,busy=false,pending
 const status=message=>{el('status').textContent=message}
 function closeConfirmation(){pending=undefined;el('confirm').close();el('confirm-note').textContent=''}
 function controls(){el('reload').disabled=busy||!selected;el('more').disabled=busy||next===null;el('archive').disabled=busy;el('restore').disabled=busy;el('confirm-save').disabled=busy}
 function clearDetail(){closeConfirmation();selected=undefined;events=[];next=null;el('detail').hidden=true;el('title').textContent='';el('meta').textContent='';el('events').replaceChildren();el('archive').hidden=true;el('restore').hidden=true;controls()}
 function invalidate(){generation++;controller?.abort();controller=undefined;busy=false;return generation}
 function clearRows(){rows=[];el('list').replaceChildren();el('count').textContent='';el('list-panel').hidden=true}
 function clear(message='Verify the current session to read your saved history.'){
  invalidate();active=false;scope=undefined;filter='active';clearDetail();clearRows();el('workspace').value='';el('state').value='active';el('session').hidden=true;status(message)
 }
 const current=turn=>active&&turn===generation
 function begin(){const turn=invalidate();controller=new AbortController();busy=true;controls();return {turn,signal:controller.signal}}
 function fail(error,turn){if(!current(turn))return;invalidate();clearDetail();clearRows();scope=undefined;status(error.status===412?'Saved history changed. Reopen the workspace and review the current revision. Nothing was retried.':error.message||'Saved history is unavailable. No change was retried.')}
 function renderRows(){
  el('list').replaceChildren()
  for(const row of rows){const item=document.createElement('li'),button=document.createElement('button'),meta=document.createElement('p');button.type='button';button.textContent='Open '+row.title;button.dataset.conversation=row.id;button.addEventListener('click',()=>open(row.id));meta.textContent=`${row.state} · ${row.event_count} saved events · version ${row.revision} · ${row.id}`;item.append(button,meta);el('list').append(item)}
  el('count').textContent=rows.length?`${rows.length} authorized ${filter} histories. Only your records in ${scope}.`:`No authorized ${filter} histories in ${scope}. This does not reveal other owners' records.`
  el('list-panel').hidden=false
 }
 function renderDetail(){
  el('title').textContent=selected.title;el('meta').textContent=`${selected.workspace_id} / ${selected.id} · ${selected.state} · version ${selected.revision} · ${events.length} of ${selected.event_count} saved events · Updated ${selected.updated_at}`
  el('events').replaceChildren();const labels={user_message:'User message',assistant_message:'Assistant message',agent_thought:'User-visible agent report',tool:'Reported tool status',notice:'Notice'}
  for(const event of events){const item=document.createElement('li'),heading=document.createElement('h3'),body=document.createElement('pre');heading.textContent=`${event.sequence}. ${labels[event.kind]}`+(event.kind==='tool'?` · ${event.tool_name} · ${event.status}`:'');body.textContent=event.text;item.append(heading,body);el('events').append(item)}
  el('archive').hidden=!selected.capabilities.archive;el('restore').hidden=!selected.capabilities.restore;el('more').hidden=next===null;el('detail').hidden=false;controls()
 }
 async function list(workspace,state,notice){
  if(!active)return
  const {turn,signal}=begin();scope=workspace;filter=state;clearDetail();clearRows();status('Checking current owner access…')
  try{const result=await client.list(workspace,state,signal);if(!current(turn))return;rows=result.items;renderRows();status(notice||'Current owner access verified. Every request is authorized again.')}
  catch(error){fail(error,turn)}finally{if(current(turn)){busy=false;controls()}}
 }
 async function open(id){
  if(!active||!scope)return
  const workspace=scope,{turn,signal}=begin();clearDetail();status('Reading a revision-bound saved history…')
  try{
   const metadata=await client.metadata(workspace,id,signal);if(!current(turn))return
   const page=await client.events(metadata,0,signal);if(!current(turn))return
   selected=metadata;events=page.items;next=page.next_after;renderDetail();status('Saved display records only. No execution or replay has occurred.')
  }catch(error){fail(error,turn)}finally{if(current(turn)){busy=false;controls()}}
 }
 async function more(){
  if(!active||busy||!selected||next===null)return
  const metadata=selected,after=next,{turn,signal}=begin();closeConfirmation();status('Reading the next page at the same revision…')
  try{const page=await client.events(metadata,after,signal);if(!current(turn))return;events=[...events,...page.items];next=page.next_after;renderDetail();status('More saved display records loaded at the same revision.')}
  catch(error){fail(error,turn)}finally{if(current(turn)){busy=false;controls()}}
 }
 function confirm(state){
  if(!active||busy||!selected||!selected.capabilities[state==='active'?'restore':'archive'])return
  pending={metadata:selected,state,turn:generation};el('confirm-note').textContent=`${state==='active'?'Restore':'Archive'} ${selected.workspace_id} / ${selected.id} at version ${selected.revision}? This changes only saved history visibility. Events are retained. It does not ${state==='active'?'resume an agent or execute tools':'cancel or stop any running work'}.`;el('confirm').showModal()
 }
 el('scope').addEventListener('submit',event=>{event.preventDefault();if(!active)return;let workspace;try{workspace=identifier(el('workspace').value)}catch(error){invalidate();clearDetail();clearRows();scope=undefined;status(error.message);return}list(workspace,el('state').value)})
 for(const id of ['workspace','state'])el(id).addEventListener('input',()=>{invalidate();scope=undefined;clearDetail();clearRows();status('Scope changed. Open it explicitly to check current access.')})
 el('more').addEventListener('click',more);el('close').addEventListener('click',()=>{invalidate();clearDetail();status('Saved history closed. No running work was affected.')})
 el('reload').addEventListener('click',()=>{if(selected&&!busy)open(selected.id)})
 el('archive').addEventListener('click',()=>confirm('archived'));el('restore').addEventListener('click',()=>confirm('active'))
 el('confirm-cancel').addEventListener('click',closeConfirmation);el('confirm').addEventListener('cancel',closeConfirmation)
 el('confirm-form').addEventListener('submit',async event=>{
  event.preventDefault();if(!active||busy||!pending||pending.turn!==generation)return
  const action=pending,workspace=scope,state=filter,{turn,signal}=begin();closeConfirmation();status('Saving reversible history state. An interrupted request may already complete.')
  try{await client.lifecycle(action.metadata,action.state,signal);if(!current(turn))return;clearDetail();await list(workspace,state,'Saved history state changed. No agent was resumed, stopped or executed.')}
  catch(error){fail(error,turn)}finally{if(current(turn)){busy=false;controls()}}
 })
 clear()
 return {clear,async activate(){clear();active=true;el('session').hidden=false;status('Enter a workspace to read only your authorized saved history.')}}
}
