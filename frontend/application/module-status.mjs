import {validModuleStatusId,validateModuleStatus} from './client.mjs'

const failureMessages={
 401:'Your session expired or was rejected. Sign in or explicitly recheck the session.',
 403:'This status is unavailable to your current identity.',
 404:'This workspace/module is unavailable to your current identity.',
 409:'The current lifecycle or request conflicts with this status check. Recheck explicitly.',
 503:'The configured service is unavailable. No module state is assumed.'
}
const failed='The module status could not be verified. Check again explicitly; nothing was retried.'

// Local observation only. No session, workspace directory or business authority
// belongs to this controller. The shell supplies its already validated list.
export function mountModuleStatus({session,doc=document}){
 const el=id=>doc.getElementById('module-status'+(id?'-'+id:''))
 let active=false,generation=0,pending,ticket,spaces=[]
 const message=text=>{el('message').textContent=text}
 const selected=()=>spaces.some(space=>space.id===el('workspace').value)
 function controls(){
  el('workspace').disabled=!active||!spaces.length
  el('key').disabled=!active||!spaces.length
  el('check').disabled=!active||Boolean(ticket)||!selected()||!validModuleStatusId(el('key').value)
  el('clear').disabled=!active
  el('form').setAttribute('aria-busy',ticket?'true':'false')
 }
 function discard(){
  generation++;const old=pending;pending=undefined;ticket=undefined;old?.abort()
  el('result').hidden=true
  for(const field of ['workspace','key','state','revision'])el('result-'+field).textContent=''
  message('')
 }
 function resetQuery(){discard();el('workspace').value='';el('key').value='';controls()}
 function clear(){
  active=false;spaces=[];resetQuery();el('workspace').replaceChildren();el('').hidden=true
 }
 function activate(workspaces){
  clear()
  // Copy only presentation inputs: callers cannot later mutate our selection.
  if(!Array.isArray(workspaces)||workspaces.some(space=>!space||!validModuleStatusId(space.id)||typeof space.name!=='string'||space.name.length>200)||new Set(workspaces.map(space=>space.id)).size!==workspaces.length)throw new Error('Invalid module status workspaces.')
  spaces=workspaces.map(({id,name})=>({id,name}))
  const option=(value,label)=>{const item=doc.createElement('option');item.value=value;item.textContent=label;return item}
  el('workspace').append(option('','Choose a workspace'))
  for(const space of spaces)el('workspace').append(option(space.id,`${space.name} · ${space.id}`))
  el('workspace').value='';active=true;el('').hidden=false;controls()
  message(spaces.length?'Choose a workspace and enter one exact module key. Check explicitly.':'No accessible workspaces. A status query requires current explicit membership.')
 }
 async function check(event){
  event.preventDefault()
  if(!active||ticket||!selected()||!validModuleStatusId(el('key').value))return
  discard()
  const workspace=el('workspace').value,key=el('key').value,turn=generation,owner=Symbol('module status')
  const abort=new AbortController();pending=abort;ticket=owner
  const isCurrent=()=>active&&turn===generation&&ticket===owner&&workspace===el('workspace').value&&key===el('key').value
  controls();message('Checking the stored lifecycle observation…')
  try{
   const data=await session.readModuleStatus(workspace,key,{signal:abort.signal,isCurrent})
   if(!isCurrent())return
   validateModuleStatus(data,workspace,key)
   if(!isCurrent())return
   for(const [field,value] of Object.entries({workspace:data.workspace_id,key:data.key,state:data.state,revision:data.revision}))el('result-'+field).textContent=String(value)
   el('result').hidden=false;message('Stored lifecycle observed for this exact workspace and module. Check again for a fresh observation.')
  }catch(error){
   if(isCurrent())message(typeof error?.status==='number'&&Object.hasOwn(failureMessages,error.status)?failureMessages[error.status]:failed)
  }finally{
   // An ignored abort or old finally must never unlock a newer request.
   if(isCurrent()){ticket=undefined;pending=undefined;controls()}
  }
 }
 el('form').addEventListener('submit',check)
 el('workspace').addEventListener('change',()=>{discard();controls()})
 el('key').addEventListener('input',()=>{discard();controls()})
 el('clear').addEventListener('click',()=>{if(active)resetQuery()})
 clear()
 return {activate,clear}
}
