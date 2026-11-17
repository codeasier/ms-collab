import {validateWorkspaces} from '/manage/client.mjs'
import {createPipelineDirectoryClient} from './pipeline-scope-client.mjs'
export function mountPipelineScopePicker({session,isOpening=()=>false,doc=document}){
 const el=id=>doc.getElementById(id),client=createPipelineDirectoryClient(session)
 let epoch=0,abort,active=false,busy=false,items=[],next=null,setting=false
 const status=text=>{el('pipeline-picker-status').textContent=text}
 function fields(scope){setting=true;try{for(const [id,key]of [['workspace','workspace_id'],['connection','connection_id'],['pipeline','pipeline_id']]){el(id).value=scope?.[key]||'';el(id).dispatchEvent(new Event('input',{bubbles:true}))}}finally{setting=false}}
 function controls(){el('pipeline-picker-use').disabled=!active||busy||isOpening()||!items.length;el('pipeline-picker-next').disabled=!active||busy||!next;el('pipeline-picker-load').disabled=!active||busy||!el('pipeline-picker-workspace').value}
 function reset(){epoch++;abort?.abort();abort=new AbortController();busy=false;items=[];next=null;el('pipeline-picker-scope').replaceChildren();controls()}
 function clear(){active=false;reset();el('pipeline-picker').hidden=true;el('pipeline-picker-workspace').replaceChildren();status('')}
 const option=(value,label)=>{const o=doc.createElement('option');o.value=value;o.textContent=label;return o}
 async function activate(){clear();const turn=epoch;const spaces=validateWorkspaces(await session.request('/api/workspaces',{signal:abort.signal}));if(turn!==epoch)return
  for(const s of spaces)el('pipeline-picker-workspace').append(option(s.id,`${s.name} · ${s.kind}`))
  active=true;el('pipeline-picker').hidden=false;controls();status(spaces.length?'Choose a discovery workspace and load its stored scopes.':'No accessible workspaces.')
 }
 async function load(after=null){
  if(!active||busy||!el('pipeline-picker-workspace').value)return
  const workspace=el('pipeline-picker-workspace').value;reset();fields();busy=true;controls();const turn=epoch;status('Checking current snapshot access…')
  try{const data=await client.list(workspace,{after,signal:abort.signal});if(turn!==epoch)return;items=data.items;next=data.next_after
   for(const [i,item]of items.entries())el('pipeline-picker-scope').append(option(String(i),`${item.scope.connection_id} / ${item.scope.pipeline_id} · snapshot revision ${item.snapshot_revision}`))
   status(items.length?'Select a stored scope. Opening rechecks current access. No CI provider request was made.':'No stored pipeline scopes are discoverable here. A valid empty snapshot would still be listed.')
  }catch(error){if(turn===epoch){reset();status(error.message+' Reload explicitly; no request was retried.')}}
  finally{if(turn===epoch){busy=false;controls()}}
 }
 el('pipeline-picker-workspace').addEventListener('change',()=>{reset();fields();status('Discovery workspace changed. Load current scopes explicitly.')})
 el('pipeline-picker-scope').addEventListener('change',()=>{fields();status('Selection changed. Open explicitly to recheck current access.');controls()})
 el('pipeline-picker-load').addEventListener('click',()=>load())
 el('pipeline-picker-next').addEventListener('click',()=>{if(next)return load(next)})
 el('pipeline-picker-use').addEventListener('click',()=>{if(!active||busy||isOpening())return;const item=items[Number(el('pipeline-picker-scope').value)];if(!item)return;fields(item.scope);el('scope-form').requestSubmit();controls()})
 for(const id of ['workspace','connection','pipeline'])el(id).addEventListener('input',()=>{if(!setting){reset();status('Manual scope edited. Discovery workspace is unchanged; reload discovery explicitly.')}})
 return {activate,clear,sync:controls}
}
