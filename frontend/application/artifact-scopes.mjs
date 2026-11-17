import {validateWorkspaces} from '/manage/client.mjs'
import {createArtifactDirectoryClient} from './artifact-scope-client.mjs'
export function mountArtifactScopePicker({session,managed=false,doc=document}){
 const el=id=>doc.getElementById(id),client=createArtifactDirectoryClient(session,managed)
 let epoch=0,abort,active=false,busy=false,items=[],next=null,lastTuple=null,setting=false
 const status=text=>{el('artifact-picker-status').textContent=text}
 function fields(scope){setting=true;try{for(const [id,key]of [['workspace','workspace_id'],['connection','connection_id'],['bucket','bucket_id']]){el(id).value=scope?.[key]||'';el(id).dispatchEvent(new Event('input',{bubbles:true}))}}finally{setting=false}}
 function controls(){el('artifact-picker-use').disabled=!active||busy||!items.length;el('artifact-picker-next').disabled=!active||busy||!next;el('artifact-picker-load').disabled=!active||busy||!el('artifact-picker-workspace').value}
 // The receipt and local ordering tuple are one ephemeral pagination position.
 function reset(){epoch++;abort?.abort();abort=new AbortController();busy=false;items=[];next=null;lastTuple=null;el('artifact-picker-scope').replaceChildren();controls()}
 function clear(){active=false;reset();el('artifact-picker').hidden=true;el('artifact-picker-workspace').replaceChildren();status('')}
 const option=(value,label)=>{const o=doc.createElement('option');o.value=value;o.textContent=label;return o}
 async function activate(){clear();const turn=epoch;const spaces=validateWorkspaces(await session.request('/api/workspaces',{signal:abort.signal}));if(turn!==epoch)return
  for(const s of spaces)el('artifact-picker-workspace').append(option(s.id,`${s.name} · ${s.kind}`))
  active=true;el('artifact-picker').hidden=false;controls();status(spaces.length?'Choose a workspace and load its currently accessible scopes.':'No accessible workspaces.')
 }
 async function load(after=null,prior=null){
  if(!active||busy||!el('artifact-picker-workspace').value)return
  const workspace=el('artifact-picker-workspace').value;reset();fields();busy=true;controls();const turn=epoch;status('Checking current scoped access…')
  try{const data=await client.list(workspace,{after,lastTuple:prior,signal:abort.signal});if(turn!==epoch)return;items=data.items;next=data.next_after;lastTuple=next?[items.at(-1).scope.connection_id,items.at(-1).scope.bucket_id]:null
   for(const [i,item]of items.entries())el('artifact-picker-scope').append(option(String(i),`${item.scope.connection_id} / ${item.scope.bucket_id} · ${managed?Object.entries(item.capabilities).filter(([,v])=>v).map(([k])=>k).join(', '):'published revision '+item.catalog_revision}`))
   status(items.length?'Select an exact scope. Opening rechecks current authority. No provider request was made.':managed?'No currently accessible administration scopes.':'No currently accessible published scopes. A published empty catalog is still listed.')
  }catch(error){if(turn===epoch){reset();status((error.status===409?'The scope directory changed.':error.status===503?'The scope directory is unavailable.':error.message)+' Reload the directory explicitly; no request was retried.')}}
  finally{if(turn===epoch){busy=false;controls()}}
 }
 el('artifact-picker-workspace').addEventListener('change',()=>{reset();fields();status('Workspace changed. Load current scopes explicitly.')})
 // Selection clears the old exact-scope view, not this loaded directory context.
 el('artifact-picker-scope').addEventListener('change',()=>{fields();status('Selection changed. Open explicitly to recheck current authority.')})
 el('artifact-picker-load').addEventListener('click',()=>load())
 el('artifact-picker-next').addEventListener('click',()=>{if(next)return load(next,lastTuple)})
 el('artifact-picker-use').addEventListener('click',()=>{if(!active||busy)return;const item=items[Number(el('artifact-picker-scope').value)];if(!item)return;fields(item.scope);el('scope-form').requestSubmit()})
 for(const id of ['workspace','connection','bucket'])el(id).addEventListener('input',()=>{if(!setting){reset();status('Manual scope edited. Reload discovery explicitly; opening still rechecks access.')}})
 return {activate,clear}
}
