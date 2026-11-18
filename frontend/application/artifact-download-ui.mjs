// Explicit availability -> confirmation -> verified browser handoff. Session only.
import {downloadPath,validateDownloadBinding} from './artifact-downloads.mjs'
export function mountArtifactDownload({session,productFile=false,doc=document}){
 if(!session||typeof session[productFile?'readArtifactDownloadAvailability':'request']!=='function'||typeof session.downloadArtifact!=='function')throw Error('Explicit session download client required.')
 const el=id=>doc.getElementById(id);let generation=0,controller,binding,busy=false,url,timer
 const current=turn=>turn===generation
 function forgetURL(){clearTimeout(timer);if(url)URL.revokeObjectURL(url);url=undefined}
 function clear(message=''){
  generation++;controller?.abort();controller=undefined;binding=undefined;busy=false;forgetURL()
  el('download-panel').hidden=!message;el('download-confirm').disabled=true;el('download-heading').textContent='Download status'
  for(const id of ['download-scope','download-key','download-size','download-digest'])el(id).textContent=''
  el('download-status').textContent=message
 }
 async function select({scope,key,revision,size}){
  clear();const turn=generation;controller=new AbortController();busy=true;el('download-panel').hidden=false;el('download-status').textContent='Checking current byte permission. No provider is contacted by this check.'
  try{
   if(productFile)el('download-heading').focus?.()
   const data=productFile
    ?await session.readArtifactDownloadAvailability(scope,key,revision,{signal:controller.signal,isCurrent:()=>current(turn)})
    :await session.request(downloadPath(scope,'availability'),{method:'POST',body:{key},revision,signal:controller.signal})
   if(!current(turn))return
   const checked=validateDownloadBinding(data,{scope,key,revision});if(checked.size!==size)throw Error('Published metadata changed. Reload the catalog.')
   binding=checked;busy=false;el('download-heading').textContent='Confirm exact bytes'
   el('download-scope').textContent=`${scope.workspace_id} / ${scope.connection_id} / ${scope.bucket_id} · published revision ${revision}`
   el('download-key').textContent=key;el('download-size').textContent=`${size.toLocaleString()} bytes`;el('download-digest').textContent=checked.sha256
   el('download-confirm').disabled=false;el('download-status').textContent='Review this exact file and checksum. Permission and byte binding will be checked again when you confirm.'
  }catch(error){if(!current(turn))return;clear();el('download-panel').hidden=false;el('download-status').textContent=error.status===404?'Download permission or a trusted byte binding is unavailable. Metadata access is not download permission.':productFile?'Byte access could not be verified. No bytes were requested.':error.message||'Download availability could not be verified.'}
 }
 async function confirm(){
  if(busy||!binding)return
  const selected=binding,turn=generation;busy=true;binding=undefined;el('download-confirm').disabled=true;controller=new AbortController();el('download-status').textContent='Receiving and verifying bounded bytes. Nothing will be saved before complete checksum verification.'
  let result
  try{
   result=await session.downloadArtifact(selected,{signal:controller.signal,isCurrent:()=>current(turn)});if(!current(turn))return
   const blob=new Blob([result.bytes],{type:'application/octet-stream'});if(!current(turn))return
   forgetURL();url=URL.createObjectURL(blob);if(!current(turn)){forgetURL();return}
   const link=doc.createElement('a');link.href=url;link.download=result.name;link.hidden=true;doc.body.append(link)
   try{if(current(turn))link.click()}finally{link.remove()}
   if(!current(turn))return
   const delivered=url;timer=setTimeout(()=>{if(url===delivered)forgetURL()},1000);busy=false
   el('download-status').textContent='Verified bytes handed to your browser. Check your browser’s downloads; this page does not confirm that a file was saved. Already delivered bytes cannot be recalled.'
  }catch(error){if(!current(turn))return;clear();el('download-panel').hidden=false;el('download-status').textContent=productFile?'Download could not be verified. Check byte access again before retrying.':error.message||'Download interrupted. No file was handed to the browser. Check availability before retrying.'}
  finally{result?.bytes.fill(0)}
 }
 el('download-confirm').addEventListener('click',confirm)
 el('download-cancel').addEventListener('click',()=>clear('Canceled locally. An already handed-off download cannot be recalled.'))
 window.addEventListener('pagehide',()=>clear())
 return {select,clear}
}
