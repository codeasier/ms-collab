// A document-local reader. Only the typed Cookie transport knows the network;
// no links, navigation state or storage are constructed from private values.
import {lastSevenUtcDates, makeProductRequest, productInstantMicros} from './artifact-product-codec.mjs'

export function mountArtifactProductView({session, now=()=>new Date(), doc=document}){
  if(typeof session?.readArtifactProductView!=='function')throw Error('Product view reader required.')
  const el=id=>doc.getElementById('pv-'+id)
  const make=(tag,text)=>{const node=doc.createElement(tag);if(text!==undefined)node.textContent=text;return node}
  const status=text=>{el('status').textContent=text}
  // A pin survives local refinements and quarter changes, never a change of
  // identity. "opened" is a verified location, not the values being edited.
  let active=false, serial=0, flight=null, pin=null, opened=null, shown=null, recovery=false
  const identityFields=['workspace','connection','bucket','view']
  const fields=()=>({scope:{workspace_id:el('workspace').value,connection_id:el('connection').value,bucket_id:el('bucket').value},viewId:el('view').value})
  const navigation=(body,path)=>({format:1,quarter:body.quarter,path,mode:'tree',page:1,page_size:body.page_size})

  function controls(){
    el('controls').hidden=!active
    el('browse').hidden=!opened
    el('reopen').hidden=!active||!recovery
    el('reopen').disabled=!!flight
    el('open').disabled=!active||recovery||!!flight
    el('up').disabled=!opened?.body.path||!!flight
    for(const id of ['apply','range7','tree'])el(id).disabled=!opened||!!flight
    el('page-size').disabled=!shown
    el('prev').disabled=!shown||shown.request.body.page<=1
    el('next').disabled=!shown||shown.request.body.page*shown.request.body.page_size>=shown.total
  }
  function discardRows(){
    shown=null
    el('results').replaceChildren()
    el('evidence').textContent=''
    el('page').textContent=''
  }
  function retire(){
    serial++
    const previous=flight;flight=null
    previous?.abort()
    discardRows()
  }
  function resetFilters(){
    for(const id of ['keyword','start','end'])el(id).value=''
    el('recursive').checked=false
    el('range7').setAttribute('aria-pressed','false')
  }
  function forgetLocation(){
    opened=null
    el('location').textContent=''
    el('crumbs').replaceChildren()
    el('page-size').value='10'
    resetFilters()
  }
  function clear(){
    if(el('controls').contains?.(doc.activeElement))doc.activeElement?.blur?.()
    active=false
    retire()
    pin=null;recovery=false
    for(const id of [...identityFields,'quarter'])el(id).value=''
    forgetLocation();status('');controls()
  }
  function activate(){
    clear();active=true;controls()
    status('Enter the exact IDs and quarter to open a product view.')
    el('workspace').focus()
  }
  function editSetup(identity){
    if(!active)return
    retire()
    if(identity){pin=null;recovery=false}
    forgetLocation();controls()
    if(recovery){status('Reopen the product view to check the selected quarter at the root.');return}
    status(identity?'Selection changed. Open the exact product view to read its root.':'Quarter changed. Open the product view to read that quarter at the root.')
  }
  function editFilters(id){
    if(!active||!opened)return
    retire()
    if(id==='start'||id==='end')el('range7').setAttribute('aria-pressed','false')
    controls()
    status('Filters changed. Apply file filters to see matching files.')
  }
  function fail(statusCode){
    discardRows()
    if(statusCode===401){clear();return} // The real transport also notified the shell.
    if(statusCode===400){
      controls()
      status('Invalid request. Check the exact IDs, quarter and file filters before applying again.')
      return
    }
    pin=null;recovery=true;forgetLocation();controls()
    const message={
      403:'This product view is unavailable or inaccessible.',
      404:'This product view is unavailable or inaccessible.',
      409:'The workspace or artifacts module is unavailable.',
      412:'The publication or view changed.'
    }[statusCode]||'Could not verify this response. No results were loaded.'
    status(message+' Reopen the product view to start at the root.')
  }
  // Work only with a literal, already verified directory. An empty segment
  // contributes a slash of its own; "a//" has parent "a/", not the root.
  function goTo(path){
    if(!opened||!active||flight)return
    const {scope,viewId,body}=opened
    resetFilters()
    return read(scope,viewId,navigation(body,path),{focusLocation:true})
  }
  function drawLocation(){
    const location=opened
    const path=location.body.path, holder=el('crumbs')
    el('location').textContent='Folder: /'+path
    holder.replaceChildren()
    const parts=path?path.slice(0,-1).split('/'):[]
    let prefix=''
    for(let i=0;i<=parts.length;i++){
      const segment=parts[i-1],label=i?(segment||'(empty segment)')+'/':'Root'
      if(i)prefix+=segment+'/'
      if(i===parts.length){const current=make('span',label);current.setAttribute('aria-current','location');holder.append(current);break}
      const target=prefix,button=make('button',label);button.type='button'
      button.addEventListener('click',()=>{if(location===opened)return goTo(target)})
      holder.append(button)
    }
  }
  function drawRows(data,request,ticket){
    const {body}=request
    const list=make('ul');list.className='pv-list'
    for(const row of data.items){
      const li=make('li'),summary=make('div')
      const name=make(row.is_dir?'button':'bdi',row.is_dir?'Folder: '+(row.name||'(empty segment)'):row.name)
      name.className=row.is_dir?'pv-folder':'pv-file'
      if(row.is_dir){
        name.type='button'
        name.addEventListener('click',()=>{if(ticket===serial)return goTo(row.key)})
      }
      const key=make('bdi',row.key);key.className='pv-key'
      const meta=make('span',row.is_dir?'Virtual folder · No modification time':row.size+' bytes · '+row.modified_at);meta.className='pv-meta'
      summary.append(name,key,meta)
      const kind=make('span',row.is_dir?'FOLDER':'FILE');kind.className='pv-kind'
      li.append(summary,kind);list.append(li)
    }
    let message
    const count=data.total+' '+(body.mode==='tree'?'rows at this level':'matching files')
    if(!data.quarter_available)message='This quarter is absent from the stored observation. No fallback was used.'
    else if(!data.items.length&&data.total)message='This page is past the end of the current results.'
    else if(!data.items.length)message=body.mode==='files'?'No files match these filters at this location.':'No rows at this level match the product view.'
    else message=(body.mode==='tree'?'Folder view':'Files only')+' loaded. '+count+'.'
    el('results').replaceChildren(data.items.length?list:make('p',message))
    el('evidence').textContent='Published revision '+data.catalog_revision+' · Observed '+data.observed_at+' · Stored index, not a live listing'
    const pages=Math.max(1,Math.ceil(data.total/body.page_size))
    el('page').textContent='Page '+body.page+' of '+pages+' · '+count
    el('page-size').value=String(body.page_size)
    status(message)
  }
  async function read(scope,viewId,body,{fresh=false,focusLocation=false}={}){
    if(!active||flight)return
    const expected=fresh?null:pin
    retire()
    const ticket=serial
    let request
    try{
      request=makeProductRequest(scope,viewId,expected?{...body,if_observation:expected.observation}:body)
    }catch{
      controls()
      status(body.mode==='files'?'Check the file filters. Enter a nonblank literal keyword of at most 256 characters, or leave it empty; check both time bounds.':'Enter complete valid IDs and a quarter in YYYYQ1 through YYYYQ4 format.')
      return
    }
    const abort=new AbortController();flight=abort
    const isCurrent=()=>active&&serial===ticket&&flight===abort&&!abort.signal.aborted
    controls();status('Loading the authorized product view…')
    try{
      const data=await session.readArtifactProductView(request.scope,request.viewId,request.body,{signal:abort.signal,isCurrent})
      if(!isCurrent())return
      // The typed transport checked all 17 fields against this exact request,
      // including the proof. The first receipt also pins these two witnesses.
      if(expected&&(data.observation!==expected.observation||data.observed_at!==expected.observed_at||data.catalog_revision!==expected.catalog_revision))throw Error('Inconsistent observation.')
      pin=Object.freeze({scope:request.scope,viewId:request.viewId,observation:data.observation,observed_at:data.observed_at,catalog_revision:data.catalog_revision})
      opened=request;recovery=false
      shown={request,total:data.total}
      drawLocation();drawRows(data,request,ticket)
      if(focusLocation&&doc.activeElement===doc.body)el('location').focus()
    }catch(error){
      if(isCurrent())fail(error?.status)
    }finally{
      if(isCurrent()){
        flight=null;controls()
        if(recovery&&doc.activeElement===doc.body)el('reopen').focus()
      }
    }
  }
  function open(fresh){
    if(!active||flight||recovery!==fresh)return
    const {scope,viewId}=fields()
    // Confirm the user's edited tuple. Never carry a proof to another ID even
    // if an input's value changed without its input event.
    if(pin&&(viewId!==pin.viewId||['workspace_id','connection_id','bucket_id'].some(k=>scope[k]!==pin.scope[k])))pin=null
    forgetLocation()
    return read(scope,viewId,{format:1,quarter:el('quarter').value,path:'',mode:'tree',page:1,page_size:10},{fresh})
  }
  function apply(range=false){
    if(!active||!opened||flight)return
    const {scope,viewId,body}=opened
    if(range){
      let bounds
      try{bounds=lastSevenUtcDates(now())}catch{
        retire();controls();status('Could not determine the last seven UTC dates. Enter explicit time bounds.')
        return
      }
      el('start').value=bounds.start_time;el('end').value=bounds.end_time
      el('range7').setAttribute('aria-pressed','true')
    }
    const start=el('start').value,end=el('end').value
    try{
      const lo=start?productInstantMicros(start):null,hi=end?productInstantMicros(end):null
      if(lo!==null&&hi!==null&&lo>hi)throw Error()
    }catch{
      retire();controls();status('Check the time range: use a real date, seconds and Z or an explicit offset. The start must not be after the end.')
      return
    }
    const query={format:1,quarter:body.quarter,path:body.path,mode:'files',page:1,page_size:body.page_size,recursive:el('recursive').checked}
    if(el('keyword').value!=='')query.keyword=el('keyword').value
    if(start)query.start_time=start
    if(end)query.end_time=end
    return read(scope,viewId,query)
  }
  function turnPage(delta,size){
    if(!active||!shown||flight)return
    const {request,total}=shown,{scope,viewId,body}=request
    const page=size?1:body.page+delta
    if(page<1||(!size&&delta>0&&(page-1)*body.page_size>=total))return
    return read(scope,viewId,{...body,page,page_size:size||body.page_size})
  }

  for(const id of identityFields)el(id).addEventListener('input',()=>editSetup(true))
  el('quarter').addEventListener('input',()=>editSetup(false))
  for(const id of ['keyword','start','end','recursive'])el(id).addEventListener('input',()=>editFilters(id))
  el('setup').addEventListener('submit',e=>{e.preventDefault();return open(false)})
  el('reopen').addEventListener('click',()=>open(true))
  el('filters').addEventListener('submit',e=>{e.preventDefault();return apply()})
  el('range7').addEventListener('click',()=>apply(true))
  el('tree').addEventListener('click',()=>{if(opened)return goTo(opened.body.path)})
  el('up').addEventListener('click',()=>{
    const path=opened?.body.path
    if(path)return goTo(path.slice(0,path.slice(0,-1).lastIndexOf('/')+1))
  })
  el('prev').addEventListener('click',()=>turnPage(-1))
  el('next').addEventListener('click',()=>turnPage(1))
  el('page-size').addEventListener('change',()=>turnPage(0,Number(el('page-size').value)))
  clear()
  return {activate,clear}
}
