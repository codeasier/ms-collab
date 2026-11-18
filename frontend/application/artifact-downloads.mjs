// Bounded cookie-session byte transfer. No remote URL, token or persistent cache.
import {validateScope} from '../artifacts/client.mjs'
const MAX_BYTES=5*1024*1024,TIMEOUT_MS=30000
const own=(o,keys)=>o!==null&&typeof o==='object'&&!Array.isArray(o)&&Object.keys(o).sort().join(',')===[...keys].sort().join(',')
const hex=value=>typeof value==='string'&&value.match(/^[0-9a-f]{64}$/)?.[0]===value
export function validateDownloadBinding(value,{scope,key,revision}={}){
 if(!own(value,['scope','key','size','sha256','published_revision']))throw Error('Invalid byte binding.')
 const selected=validateScope(value.scope)
 if(typeof value.key!=='string'||!value.key||value.key.endsWith('/')||new TextEncoder().encode(value.key).length>1024||/[\u0000-\u001f\u007f]/.test(value.key)||!Number.isSafeInteger(value.size)||value.size<0||value.size>MAX_BYTES||!hex(value.sha256)||!Number.isSafeInteger(value.published_revision)||value.published_revision<1)throw Error('Invalid byte binding.')
 if(scope&&Object.keys(selected).some(k=>selected[k]!==scope[k])||key!==undefined&&value.key!==key||revision!==undefined&&value.published_revision!==revision)throw Error('Byte binding does not match the selected published object.')
 return Object.freeze({...value,scope:Object.freeze({...selected})})
}
export function downloadPath(scope,action){
 const s=validateScope(scope);if(!['availability','download'].includes(action))throw Error('Invalid download action.')
 return `/api/workspaces/${s.workspace_id}/artifact-download/${s.connection_id}/${s.bucket_id}/${action}`
}
export function downloadName(key){return 'artifact-'+key.split('/').at(-1).replace(/[^A-Za-z0-9._-]/gu,'_').slice(0,80)}

// A browser save suggestion, never a wire filename or a claim about package contents.
// Match whole fields in an intentionally small subset of the wheel filename format.
const decimal='(?:0|[1-9][0-9]*)'
const wheelVersion=new RegExp(`^${decimal}(?:\\.${decimal})*(?:(?:a|b|rc)${decimal})?(?:\\.post${decimal})?(?:\\.dev${decimal})?$`)
const pythonTag=/^(?:py|cp)[0-9]+$/
const abiTag=/^(?:none|abi3|cp[0-9]+(?:d|m|u|dm|du|mu|dmu|t|td)?)$/
const linuxArch='(?:x86_64|i686|aarch64|armv7l|ppc64|ppc64le|s390x|riscv64|loongarch64)'
const platformTag=new RegExp(`^(?:any|win32|win_amd64|win_arm64|linux_${linuxArch}`
 +`|manylinux(?:1|2010)_(?:x86_64|i686)|manylinux2014_(?:x86_64|i686|aarch64|armv7l|ppc64|ppc64le|s390x)`
 +`|(?:manylinux|musllinux)_${decimal}_${decimal}_${linuxArch}|macosx_${decimal}_${decimal}_(?:x86_64|arm64|universal2))$`)
function verifiedSaveName(key,fallback){
 const base=key.slice(key.lastIndexOf('/')+1)
 if(base.length>200||!base.endsWith('.whl')||/[^A-Za-z0-9._-]/.test(base))return fallback
 const fields=base.slice(0,-4).split('-')
 if(fields.length!==5&&fields.length!==6)return fallback
 const [dist,version]=fields
 if(!/^[a-z0-9]+(?:_[a-z0-9]+)*$/.test(dist)||/^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])$/.test(dist)
    ||!wheelVersion.test(version)||(fields.length===6&&!/^[0-9][A-Za-z0-9_]*$/.test(fields[2])))return fallback
 const tags=fields.slice(-3),rules=[pythonTag,abiTag,platformTag]
 const canonical=tags.every((field,i)=>field.split('.').every((tag,j,list)=>
  rules[i].test(tag)&&(j===0||list[j-1]<tag)))
 return canonical?base:fallback
}
function dispose(response){try{const p=response?.body?.cancel();p?.catch(()=>{})}catch{}}
export async function receiveArtifact({binding,fetcher,csrf,guard,onUnauthorized,pending,signal,digest=data=>crypto.subtle.digest('SHA-256',data),deadline=TIMEOUT_MS}){
 binding=validateDownloadBinding(binding);guard();if(signal?.aborted)throw Error('Download canceled.')
 // The deadline is a trusted test seam, not supplied by any HTTP/user field.
 if(!Number.isSafeInteger(deadline)||deadline<1||deadline>TIMEOUT_MS)throw Error('Invalid download deadline.')
 let response,reader,output,delivered=false,canceled=false,reason,timer,rejectCancel
 const started=performance.now(),controller=new AbortController(),cancelled=new Promise((_,reject)=>{rejectCancel=reject});cancelled.catch(()=>{})
 const cancel=(error=Error('Download canceled.'))=>{
  if(canceled)return;canceled=true;reason=error;controller.abort()
  try{reader?.cancel().catch(()=>{})}catch{}
  if(!reader)dispose(response);rejectCancel(error)
 }
 const check=()=>{if(!canceled&&performance.now()-started>=deadline)cancel(Error('Download deadline exceeded.'));if(canceled)throw reason;guard()}
 const external=()=>cancel();pending.add(cancel);signal?.addEventListener('abort',external,{once:true});timer=setTimeout(()=>cancel(Error('Download deadline exceeded.')),deadline)
 try{
  const fetching=Promise.resolve().then(()=>{check();return fetcher(downloadPath(binding.scope,'download'),{
   method:'POST',credentials:'same-origin',cache:'no-store',redirect:'error',signal:controller.signal,
   headers:{'Content-Type':'application/json','If-Match':`"${binding.published_revision}"`,'X-CSRF-Token':csrf()},
   body:JSON.stringify({key:binding.key,expected_sha256:binding.sha256})
  })}).then(r=>{try{check()}catch(error){dispose(r);throw error}return r})
  response=await Promise.race([fetching,cancelled]);check()
  if(!response.ok){const error=Error(response.status===401?'Your session expired or was rejected.':response.status===412?'The confirmed bytes or publication changed. Check availability again.':'Download unavailable. Nothing was retried.');error.status=response.status;if(response.status===401)onUnauthorized(error);throw error}
  const headers=response.headers,name=downloadName(binding.key),length=headers.get('Content-Length')
  if(response.status!==200||length!==String(binding.size)||headers.get('Content-Type')!=='application/octet-stream'||headers.get('Content-Disposition')!==`attachment; filename="${name}"`||headers.get('X-Artifact-SHA256')!==binding.sha256||!['',null,'identity'].includes(headers.get('Content-Encoding')))throw Error('Invalid download headers.')
  if(!response.body||typeof response.body.getReader!=='function')throw Error('Missing download stream.')
  reader=response.body.getReader();output=new Uint8Array(binding.size);let size=0
  while(true){
   check();const next=await Promise.race([reader.read(),cancelled]);check()
   if(next.done)break
   if(!(next.value instanceof Uint8Array)||next.value.byteLength===0||size+next.value.byteLength>binding.size||size+next.value.byteLength>MAX_BYTES)throw Error('Invalid download length.')
   output.set(next.value,size);size+=next.value.byteLength
  }
  if(size!==binding.size)throw Error('Incomplete download.')
  check();const hashed=await Promise.race([digest(output),cancelled]);check()
  const actual=Array.from(new Uint8Array(hashed),v=>v.toString(16).padStart(2,'0')).join('')
  if(actual!==binding.sha256)throw Error('Download checksum did not match.')
  const saveName=verifiedSaveName(binding.key,name)
  delivered=true;return {bytes:output,name:saveName,sha256:actual}
 }finally{
  clearTimeout(timer);signal?.removeEventListener('abort',external);pending.delete(cancel)
  controller.abort();try{if(reader)reader.cancel().catch(()=>{});else dispose(response)}catch{}
  try{reader?.releaseLock()}catch{}
  if(!delivered)output?.fill(0)
 }
}
