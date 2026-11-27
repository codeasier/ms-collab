// A document-local reader. Only the typed Cookie transport knows the network;
// no links, navigation state or storage are constructed from private values.
import {lastSevenUtcDates, makeProductRequest, productInstantMicros, validProductPresetId, validProductWindow, validateProductResult} from './artifact-product-codec.mjs'

// Shared directory outcomes never become grants and never leave this document.
function mountProductPicker({session, doc, validateWorkspaces, retireSelection, openSelection}) {
  const el=id=>doc.getElementById('pv-picker'+(id?'-'+id:''))
  const ids=['workspace','scope','view','quarter']
  let active=false,generation=0,flight=null,workspaces=[],scopes=[],views=[],quarters=[],next=null
  const status=text=>{el('status').textContent=text}
  const choose=(id,values)=>/^\d+$/.test(el(id).value)?values[Number(el(id).value)]:undefined
  function choices(id,values,label,placeholder) {
    const option=(text,value)=>{const node=doc.createElement('option');node.textContent=text;node.value=value;return node}
    el(id).replaceChildren(option(placeholder,''),...values.map((item,index)=>option(label(item),String(index))))
    el(id).value=''
  }
  function controls() {
    el('load').disabled=!active||!!flight
    for(const [i,values] of [workspaces,scopes,views,quarters].entries())el(ids[i]).disabled=!active||!values.length
    el('next').disabled=!active||!!flight||!next
    el('open').disabled=!active||!!flight||!choose('scope',scopes)||!choose('view',views)||!choose('quarter',quarters)
  }
  function reset(from) {
    generation++;flight?.abort();flight=null
    if(from<=0){workspaces=[];choices('workspace',[],'','Choose a workspace')}
    if(from<=1){scopes=[];next=null;choices('scope',[],'','Choose a bucket scope')}
    if(from<=2){views=[];quarters=[];choices('view',[],'','Choose a product view');choices('quarter',[],'','Choose a quarter')}
    controls()
  }
  function clear() {active=false;reset(0);el('').hidden=true;status('')}
  function activate() {clear();active=true;el('').hidden=false;controls()}
  async function read(selection,from,receive) {
    if(!active)return
    retireSelection();reset(from)
    const abort=new AbortController(),turn=generation
    flight=abort;controls();status('Checking current access to published metadata…')
    const isCurrent=()=>active&&generation===turn&&flight===abort&&!abort.signal.aborted
    try {
      const data=await session.readArtifactProductPicker(selection,{signal:abort.signal,isCurrent})
      if(isCurrent())receive(data)
    }catch(error){
      if(isCurrent())status(error.status===503?'Directory unavailable. Use exact IDs below.':'Published choices could not be verified. You can still use exact IDs below.')
    }finally{
      if(isCurrent()){flight=null;controls()}
    }
  }
  function loadScopes(after=null,lastTuple=null) {
    const workspace=choose('workspace',workspaces)?.id
    if(!workspace)return
    return read({kind:'scopes',workspace,after,lastTuple},1,data=>{
      scopes=data.items
      next=data.next_after
      choices('scope',scopes,item=>item.scope.connection_id+' / '+item.scope.bucket_id,'Choose a bucket scope')
      status(scopes.length?'Choose an authorized published bucket scope.':'No currently accessible published scopes.')
    })
  }
  el('load').addEventListener('click',()=>read({kind:'workspaces'},0,data=>{
    // The workspace grammar is broader than the artifact scope grammar. An
    // unrelated workspace with such an ID cannot invalidate valid choices.
    workspaces=validateWorkspaces(data).filter(w=>w.state!=='suspended'&&/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(w.id))
    choices('workspace',workspaces,w=>w.name+' · '+w.id,'Choose a workspace')
    status(workspaces.length?'Choose a current workspace.':'No accessible workspaces.')
  }))
  el('workspace').addEventListener('change',()=>{
    if(!active)return
    if(choose('workspace',workspaces))return loadScopes()
    retireSelection();reset(1);status('Choose a current workspace.')
  })
  el('next').addEventListener('click',()=>{
    if(!active||flight||!next)return
    const last=scopes.at(-1).scope
    return loadScopes(next,[last.connection_id,last.bucket_id])
  })
  el('scope').addEventListener('change',()=>{
    if(!active)return
    const scope=choose('scope',scopes)?.scope
    if(!scope){retireSelection();reset(2);status('Choose an authorized bucket scope.');return}
    return read({kind:'views',scope},2,data=>{
      views=data.views;quarters=data.quarters
      choices('view',views,v=>v.label+' · '+v.view_id,'Choose a product view')
      choices('quarter',quarters,q=>q,'Choose a quarter')
      status(!views.length?'No listed product views for this bucket. Use exact IDs if supplied.':
        !quarters.length?'This publication has no observed quarters.':'Choose a product view and a published quarter.')
    })
  })
  for(const id of ['view','quarter'])el(id).addEventListener('change',()=>{if(active){retireSelection();controls()}})
  el('open').addEventListener('click',()=>{
    if(!active||flight)return
    const scope=choose('scope',scopes)?.scope,viewId=choose('view',views)?.view_id,quarter=choose('quarter',quarters)
    if(scope&&viewId&&quarter)return openSelection(scope,viewId,quarter)
  })
  return {activate,clear,manual(){reset(1);el('workspace').value='';status('Manual selection edited. Choose a workspace again to use discovery.')}}
}

export function mountArtifactProductView({session, download=null, validateWorkspaces, now=()=>new Date(), doc=document}){
  if(typeof session?.readArtifactProductView!=='function')throw Error('Product view reader required.')
  if(download&&(typeof download.select!=='function'||typeof download.clear!=='function'))throw Error('A content-bound download panel is required.')
  const el=id=>doc.getElementById('pv-'+id)
  const make=(tag,text)=>{const node=doc.createElement(tag);if(text!==undefined)node.textContent=text;return node}
  const status=text=>{el('status').textContent=text}
  // Fixed observations pin the publication. Rolling observations additionally
  // pin one window/zone and clock evaluation. Never pass a pin to another kind
  // of query. "opened" is a verified location, not the values being edited.
  let active=false, serial=0, flight=null, checking=null, pin=null, windowPin=null, opened=null, shown=null, recovery=false
  function verifyContinuity(data,publication,expected){
    if(!publication)return
    const root=data.publication_observation??data.observation
    const rootMatches=root===publication.observation
    // A 200 that contradicts the proof it carries is a protocol failure,
    // not evidence that publication or time actually changed.
    if((!rootMatches&&expected&&data.observation===expected.observation)||
       (rootMatches&&(data.observed_at!==publication.observed_at||
                      data.catalog_revision!==publication.catalog_revision)))throw Error('Inconsistent publication receipt.')
    if(!rootMatches||(expected&&data.observation!==expected.observation))
      throw Object.assign(Error(),{status:412})
  }
  const pinFor=body=>body.relative_range
    ?(windowPin?.range===body.relative_range&&windowPin.zone===body.time_zone?windowPin:null)
    :pin
  function witnessFor(data,request){
    const witness=Object.freeze({scope:request.scope,viewId:request.viewId,observation:data.observation,
      observed_at:data.observed_at,catalog_revision:data.catalog_revision})
    if(request.body.relative_range)windowPin=Object.freeze({...witness,range:request.body.relative_range,zone:request.body.time_zone})
    else windowPin=null
    if(!request.body.relative_range)pin=witness
    return witness
  }
  const resetPins=()=>{pin=null;windowPin=null}
  // Recover only if focus is stranded or still on the initiating control.
  // A user who moved elsewhere while the request was pending keeps focus.
  const recoverFocus=trigger=>{
    if(recovery&&(doc.activeElement===doc.body||doc.activeElement===trigger))el('reopen').focus()
  }
  let conditionRows=[]
  const identityFields=['workspace','connection','bucket','view']
  const fields=()=>({scope:{workspace_id:el('workspace').value,connection_id:el('connection').value,bucket_id:el('bucket').value},viewId:el('view').value})
  const navigation=(body,path)=>({format:1,quarter:body.quarter,path,mode:'tree',page:1,page_size:body.page_size})
  const picker=typeof session.readArtifactProductPicker==='function'&&typeof validateWorkspaces==='function'&&el('picker')
    ?mountProductPicker({session,doc,validateWorkspaces,retireSelection,openSelection}):null

  function controls(){
    el('controls').hidden=!active
    el('browse').hidden=!opened
    el('reopen').hidden=!active||!recovery
    el('reopen').disabled=!!flight
    el('open').disabled=!active||recovery||!!flight
    el('up').disabled=!opened?.body.path||!!flight
    for(const id of ['apply','range7','tree','window'])el(id).disabled=!opened||!!flight
    const advanced=el('advanced').checked,locked=!opened||!!flight
    el('advanced').disabled=locked
    el('keyword').disabled=locked||advanced
    el('conditions').hidden=!advanced
    el('condition-add').disabled=locked||!advanced||conditionRows.length>=8
    for(const {op,input,remove} of conditionRows){
      input.disabled=locked
      if(op)op.disabled=locked
      if(remove)remove.disabled=locked
    }
    const relative=!!el('window').value
    el('zone').disabled=!opened||!relative||!!flight
    for(const id of ['start','end'])el(id).disabled=!opened||relative||!!flight
    const named=!!opened&&!flight&&validProductPresetId(el('preset-id').value)
    el('preset-id').disabled=!opened||!!flight
    el('preset-open').disabled=!named
    el('preset-save').disabled=!named||!shown?.available||shown.request.body.mode!=='files'
    el('page-size').disabled=!shown
    el('prev').disabled=!shown||shown.request.body.page<=1
    el('next').disabled=!shown||shown.request.body.page*shown.request.body.page_size>=shown.total
  }
  function retireCheck(){
    const old=checking;checking=null;old?.abort()
    download?.clear() // Also cancels an outstanding byte request and revokes its local URL.
  }
  function discardRows(){
    shown=null
    retireCheck()
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
  function conditionValues(){
    return conditionRows.map(({op,input})=>({operator:op?.value??'包含',keyword:input.value}))
  }
  function renderConditions(values=[],focusIndex){
    const holder=el('condition-rows')
    if(!values.length){holder.replaceChildren();conditionRows=[];return}
    conditionRows=values.map(({operator,keyword},i)=>{
      const row=make('div');row.className='pv-fields'
      const label=make('label',i?'Condition '+(i+1)+' pattern':'Include pattern')
      const input=make('input');input.value=keyword;input.maxLength=200;input.autocomplete='off'
      input.spellcheck=false;input.setAttribute('autocapitalize','none')
      input.addEventListener('input',()=>{if(conditionRows[i]?.input===input)editFilters('conditions')})
      label.append(input)
      let op,remove
      if(i){
        const opLabel=make('label','Condition '+(i+1)+' operator')
        op=make('select');op.autocomplete='off'
        for(const name of ['AND','OR','AND NOT']){
          const option=make('option',name);option.value=name;op.append(option)
        }
        op.value=operator
        op.addEventListener('change',()=>{if(conditionRows[i]?.op===op)editFilters('conditions')})
        opLabel.append(op);row.append(opLabel)
      }
      row.append(label)
      if(i){
        remove=make('button','Remove condition '+(i+1));remove.type='button'
        remove.addEventListener('click',()=>{
          if(!active||!opened||flight||!el('advanced').checked||conditionRows[i]?.remove!==remove)return
          const draft=conditionValues()
          draft.splice(i,1);renderConditions(draft,Math.min(i,draft.length-1));editFilters('conditions')
        })
        row.append(remove)
      }
      return {row,input,op,remove}
    })
    holder.replaceChildren(...conditionRows.map(({row})=>row))
    conditionRows[focusIndex]?.input.focus()
  }
  function switchConditions(){
    if(!active||!opened||flight)return
    const advanced=el('advanced').checked
    el('keyword').value='' // A mode switch cannot leave an invisible filter.
    renderConditions(advanced?[{operator:'包含',keyword:''}]:[])
    editFilters('conditions')
  }
  function resetFilters(){
    for(const id of ['keyword','start','end'])el(id).value=''
    el('advanced').checked=false;renderConditions()
    el('recursive').checked=false
    el('window').value='';el('zone').value='UTC'
    el('range7').setAttribute('aria-pressed','false')
  }
  function forgetLocation(){
    opened=null
    el('preset-id').value=''
    el('location').textContent=''
    el('crumbs').replaceChildren()
    el('page-size').value='10'
    resetFilters()
  }
  function retireSelection(){
    retire();resetPins();recovery=false
    for(const id of [...identityFields,'quarter'])el(id).value=''
    forgetLocation();status('');controls()
  }
  function openSelection(scope,viewId,quarter){
    if(!active)return
    retireSelection()
    for(const [id,key] of [['workspace','workspace_id'],['connection','connection_id'],['bucket','bucket_id']])el(id).value=scope[key]
    el('view').value=viewId;el('quarter').value=quarter
    return read(scope,viewId,{format:1,quarter,path:'',mode:'tree',page:1,page_size:10},{fresh:true})
  }
  function clear(){
    if(el('controls').contains?.(doc.activeElement))doc.activeElement?.blur?.()
    active=false;picker?.clear()
    retire()
    resetPins();recovery=false
    for(const id of [...identityFields,'quarter'])el(id).value=''
    forgetLocation();status('');controls()
  }
  function activate(){
    clear();active=true;picker?.activate();controls()
    status(picker?'Load current workspaces to find a listed view, or open by exact IDs.':'Enter the exact IDs and quarter to open a product view.')
    el(picker?'picker-load':'workspace').focus()
  }
  function editSetup(identity){
    if(!active)return
    picker?.manual();retire()
    if(identity){resetPins();recovery=false}
    forgetLocation();controls()
    if(recovery){status('Reopen the product view to check the selected quarter at the root.');return}
    status(identity?'Selection changed. Open the exact product view to read its root.':'Quarter changed. Open the product view to read that quarter at the root.')
  }
  function editFilters(id){
    if(!active||!opened)return
    retire()
    if(id==='start'||id==='end'||id==='window')el('range7').setAttribute('aria-pressed','false')
    controls()
    status('Filters changed. Apply file filters to see matching files.')
  }
  function fail(statusCode){
    discardRows()
    if(statusCode===401){clear();return} // The real transport also notified the shell.
    if(statusCode===400){
      controls()
      status('Invalid request. Check the exact IDs, quarter, file filters and time zone before applying again.')
      return
    }
    resetPins();recovery=true;forgetLocation();controls()
    const message={
      403:'This product view is unavailable or inaccessible.',
      404:'This product view is unavailable or inaccessible.',
      409:'The workspace or artifacts module is unavailable.',
      412:'The publication or view changed, or the rolling time window changed.'
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
  async function checkFile(receipt,index,row){
    if(!download||!active||!receipt||shown!==receipt||flight)return
    retireCheck()
    const trigger=doc.activeElement,abort=new AbortController();checking=abort
    const isCurrent=()=>active&&shown===receipt&&checking===abort&&!abort.signal.aborted
    const {request,witness,total}=receipt
    // Same query, including literal filters and current page. First-page
    // results also need a positive observation precondition on this reread.
    const body={...request.body,if_observation:witness.observation}
    if(body.relative_range)body.if_publication=pin.observation
    const query=makeProductRequest(request.scope,request.viewId,body)
    status('Rechecking the selected file in the same product view. No bytes have been requested.')
    try{
      const answer=await session.readArtifactProductView(query.scope,query.viewId,query.body,{signal:abort.signal,isCurrent})
      if(!isCurrent())return
      const data=validateProductResult(answer,query),same=data.items[index]
      if(!data.quarter_available||data.catalog_revision!==witness.catalog_revision||data.observed_at!==witness.observed_at||
         data.total!==total||!same||!Object.keys(row).every(k=>same[k]===row[k]))throw Error('File no longer matches this result.')
    }catch(error){if(isCurrent()){fail(error?.status);recoverFocus(trigger)}return}
    if(!isCurrent())return
    // No view ID, filter, modified time or view proof enters byte authorization.
    // The two reads are separate. Only the byte service may supply a digest.
    status('File metadata rechecked. See the separate byte-access check below.')
    try{await download.select(Object.freeze({scope:request.scope,key:row.key,size:row.size,revision:witness.catalog_revision}))}
    finally{if(checking===abort)checking=null}
  }
  function drawRows(data,request,ticket){
    const {body}=request
    const list=make('ul');list.className='pv-list'
    for(const [index,row] of data.items.entries()){
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
      if(download&&!row.is_dir&&!row.virtual){
        if(row.size<=5*1024*1024){
          // Detached, immutable primitives from this verified receipt. Never
          // parse a displayed key, form field, URL or DOM attribute to select.
          const receipt=shown,file=Object.freeze({...row})
          const button=make('button','Check byte access');button.type='button';button.className='pv-check-download';button.setAttribute('aria-controls','download-panel')
          button.addEventListener('click',()=>checkFile(receipt,index,file))
          summary.append(button)
        }else summary.append(make('small','Metadata only: this file exceeds the 5 MiB browser transfer limit.'))
      }
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
    const windowLabels={today:'Today',week:'Last week',month:'This month','30d':'Last 30 days'}
    const window=body.relative_range?' · '+windowLabels[body.relative_range]+' ('+body.time_zone+')':''
    el('evidence').textContent='Published revision '+data.catalog_revision+' · Observed '+data.observed_at+' · Stored index, not a live listing'+window+
      (body.conditions?' · '+body.conditions.length+' advanced key conditions':'')
    const pages=Math.max(1,Math.ceil(data.total/body.page_size))
    el('page').textContent='Page '+body.page+' of '+pages+' · '+count
    el('page-size').value=String(body.page_size)
    status(message)
  }
  async function read(scope,viewId,body,{fresh=false,focusLocation=false}={}){
    if(!active||flight)return
    const expected=fresh?null:pinFor(body),publication=fresh?null:pin,trigger=doc.activeElement
    retire()
    const ticket=serial
    let request
    try{
      const query={...body};delete query.if_observation;delete query.if_publication
      if(expected)query.if_observation=expected.observation
      if(publication&&query.relative_range)query.if_publication=publication.observation
      request=makeProductRequest(scope,viewId,query)
    }catch{
      controls()
      status(body.conditions?'Check advanced key conditions: use 1 to 8 nonblank patterns of at most 100 characters.':body.mode==='files'?'Check the file filters. Enter a nonblank literal keyword of at most 256 characters, or leave it empty; check both time bounds.':'Enter complete valid IDs and a quarter in YYYYQ1 through YYYYQ4 format.')
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
      verifyContinuity(data,publication,expected)
      if(download)validateProductResult(data,request)
      const witness=witnessFor(data,request)
      opened=request;recovery=false
      shown=Object.freeze({request,total:data.total,witness,available:data.quarter_available})
      drawLocation();drawRows(data,request,ticket)
      if(focusLocation&&doc.activeElement===doc.body)el('location').focus()
    }catch(error){
      if(isCurrent())fail(error?.status)
    }finally{
      if(isCurrent()){
        flight=null;controls()
        recoverFocus(trigger)
      }
    }
  }
  async function namedFilters(saving){
    const name=el('preset-id').value,previous=shown,publication=pin,trigger=doc.activeElement
    if(!active||flight||!opened||!publication||!validProductPresetId(name)||
       (saving&&(!previous?.available||previous.request.body.mode!=='files')))return
    const {scope,viewId,body}=opened,q=body.quarter
    const current=fields()
    if(current.viewId!==viewId||el('quarter').value!==q||
       Object.keys(scope).some(k=>current.scope[k]!==scope[k])){editSetup(true);return}
    // Never collect unchecked form edits, rows, paging, selectors or grants.
    const saved=saving?{...previous.request.body,if_observation:previous.witness.observation}:null
    if(saved){
      delete saved.page;delete saved.page_size
      if(saved.relative_range)saved.if_publication=publication.observation
    }
    retire()
    const ticket=serial,abort=new AbortController();flight=abort
    const isCurrent=()=>active&&ticket===serial&&flight===abort&&!abort.signal.aborted
    controls();status(saving?'Saving the applied file filters…':'Rechecking access and reopening the named filters…')
    try{
      const options={signal:abort.signal,isCurrent}
      const answer=saving
        ?await session.saveArtifactProductPreset(scope,viewId,name,saved,options)
        :await session.readArtifactProductPreset(scope,viewId,q,name,options)
      if(!isCurrent())return
      // Both paths return the authorized query and newly computed page one.
      const {query,result}=answer
      const expected=saving?previous.witness:pinFor(query)
      verifyContinuity(result,publication,expected)
      const body={...query,path:query.path??'',page:1,page_size:50,if_observation:result.observation}
      if(query.relative_range)body.if_publication=publication.observation
      const request=makeProductRequest(scope,viewId,body)
      validateProductResult(result,request)
      resetFilters()
      el('keyword').value=query.keyword??'';el('start').value=query.start_time??'';el('end').value=query.end_time??''
      el('recursive').checked=query.recursive===true
      if(query.conditions){el('advanced').checked=true;renderConditions(query.conditions)}
      if(query.relative_range){el('window').value=query.relative_range;el('zone').value=query.time_zone}
      opened=request
      shown=Object.freeze({request,total:result.total,witness:witnessFor(result,request),available:true})
      drawLocation();drawRows(result,request,ticket)
      status((saving?'Saved':'Reopened')+' preset '+name+'. '+result.total+' matching files.')
    }catch(error){
      if(!isCurrent())return
      const code=error?.status
      if(code===401){clear();return}
      // The host deliberately does not disclose whether an inaccessible ID
      // exists. Never guess another scope, retry a write, or retain old rows.
      fail(code===400?undefined:code)
      if(code===404||code===403)status('Presets may be disabled, inaccessible or the ID unavailable. Reopen the product view to continue.')
      else if(code===409)status('Preset could not be created or opened. No overwrite was attempted. Reopen the product view to continue.')
      else if(saving&&!code)status('Save outcome not verified. Reopen the product view and check the exact ID before retrying.')
    }finally{if(isCurrent()){flight=null;controls();recoverFocus(trigger)}}
  }
  function open(fresh){
    if(!active||flight||recovery!==fresh)return
    const {scope,viewId}=fields()
    // Confirm the user's edited tuple. Never carry a proof to another ID even
    // if an input's value changed without its input event.
    if(pin&&(viewId!==pin.viewId||['workspace_id','connection_id','bucket_id'].some(k=>scope[k]!==pin.scope[k])))resetPins()
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
      el('window').value='';el('zone').value='UTC'
      el('start').value=bounds.start_time;el('end').value=bounds.end_time
      el('range7').setAttribute('aria-pressed','true')
    }
    const relative_range=el('window').value,time_zone=el('zone').value
    const query={format:1,quarter:body.quarter,path:body.path,mode:'files',page:1,page_size:body.page_size,recursive:el('recursive').checked}
    if(el('advanced').checked)query.conditions=conditionValues()
    else if(el('keyword').value!=='')query.keyword=el('keyword').value
    if(relative_range){
      if(!validProductWindow(relative_range,time_zone)){
        retire();controls();status('Enter a valid IANA time zone, such as UTC or Asia/Shanghai, before applying a quick range.')
        return
      }
      return read(scope,viewId,{...query,relative_range,time_zone})
    }
    const start=el('start').value,end=el('end').value
    try{
      const lo=start?productInstantMicros(start):null,hi=end?productInstantMicros(end):null
      if(lo!==null&&hi!==null&&lo>hi)throw Error()
    }catch{
      retire();controls();status('Check the time range: use a real date, seconds and Z or an explicit offset. The start must not be after the end.')
      return
    }
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
  for(const id of ['keyword','start','end','recursive','window','zone'])el(id).addEventListener('input',()=>editFilters(id))
  el('preset-id').addEventListener('input',controls)
  el('preset-save').addEventListener('click',()=>namedFilters(true))
  el('preset-open').addEventListener('click',()=>namedFilters(false))
  el('advanced').addEventListener('change',switchConditions)
  el('condition-add').addEventListener('click',()=>{
    if(!active||!opened||flight||!el('advanced').checked||conditionRows.length>=8)return
    const values=conditionValues()
    renderConditions([...values,{operator:'AND',keyword:''}],values.length);editFilters('conditions')
  })
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
