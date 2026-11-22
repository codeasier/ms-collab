import {TYPES,scopeFrom,filtersFrom,validateTypes,validateEntities,validatePage,validateDetail} from './client.mjs'

export function mountAssetPage({client,onSignOut=()=>{}}){
 if(!client||['request','clear','workspaces','catalogs'].some(name=>typeof client[name]!=='function'))throw new Error('Explicit cookie client required.')
 const el=id=>document.getElementById(id),node=(tag,value)=>{const n=document.createElement(tag);n.textContent=value;return n}
 const option=(value,label)=>{const n=node('option',label);n.value=value;return n}
 const workspacePicker=el('catalog-picker-workspace'),catalogPicker=el('catalog-picker-catalog')
 const directoryStatus=message=>{el('catalog-picker-status').textContent=message}
 let active=false,epoch=0,controller,detailEpoch=0,detailController,scope,metadata,list,filters=filtersFrom(),busy=false
 // The directory is one workspace's observations; it never grants an exact read.
 let workspaceFlight,directoryEpoch=0,directoryController,directoryBusy=false,workspaces=new Set(),catalogs=new Set(),listedWorkspace
 const workspaceChoice=()=>workspaces.has(workspacePicker.value)?workspacePicker.value:null
 const catalogChoice=()=>listedWorkspace===workspaceChoice()&&catalogs.has(catalogPicker.value)?catalogPicker.value:null
 function controls(){
  workspacePicker.disabled=!active||!workspaces.size
  el('catalog-picker-load').disabled=!active||directoryBusy||!workspaceChoice()
  catalogPicker.disabled=!active||directoryBusy||!catalogs.size
  el('catalog-picker-open').disabled=!active||busy||directoryBusy||!catalogChoice()
  el('scope-form').querySelector('button').disabled=!active||busy
  el('reload').disabled=!active||!scope||busy
  for(const x of el('filters').elements)x.disabled=!metadata||busy
  el('previous').disabled=busy||!list||list.page<=1;el('next').disabled=busy||!list||list.page*20>=list.total
 }
 function close(){detailEpoch++;detailController?.abort();detailController=undefined;el('asset-detail').close();el('detail-title').textContent='';el('detail-meta').textContent='';el('detail-body').replaceChildren()}
 function clearList(){close();list=undefined;el('assets').replaceChildren();el('page-summary').textContent='';controls()}
 function clearCatalog(){
  clearList();metadata=undefined;filters=filtersFrom();el('type').value='';el('query').value=''
  el('panel').hidden=true;for(const id of ['scope-title','revision','availability'])el(id).textContent=''
  el('type-counts').replaceChildren();el('entity').replaceChildren(option('','All entities'));controls()
 }
 function cancel(){epoch++;controller?.abort();controller=new AbortController();busy=false;return epoch}
 function retireScope(message){cancel();scope=undefined;clearCatalog();el('status').textContent=message}
 function clearManual(){el('workspace').value='';el('catalog').value=''}
 function retireDirectory(){
  directoryEpoch++;directoryController?.abort();directoryController=new AbortController();directoryBusy=false
  listedWorkspace=undefined;catalogs.clear();catalogPicker.replaceChildren();controls();return directoryEpoch
 }
 function reset(message){
  active=false;workspaceFlight?.abort();workspaceFlight=undefined;retireDirectory();workspaces.clear();workspacePicker.replaceChildren();directoryStatus('')
  retireScope(message);clearManual();el('manual-scope').open=false;el('session').hidden=true;controls()
 }
 function clear(message='Session requires an explicit recheck.'){reset(message);client.clear()}
 const current=turn=>active&&turn===epoch&&!controller.signal.aborted
 function readOptions(turn){return {signal:controller.signal,isCurrent:()=>current(turn)}}
 function failed(error,turn){
  if(!current(turn)||error.name==='AbortError')return
  busy=false;clearCatalog();retireDirectory();directoryStatus('Load published catalogs to check the directory again.')
  el('status').textContent=error.status?error.message:'Catalog response could not be validated. Reload current scope; nothing was retried.';controls()
 }
 async function loadWorkspaces(owner){
  const isCurrent=()=>active&&workspaceFlight===owner&&!owner.signal.aborted
  try{
   const spaces=await client.workspaces({signal:owner.signal,isCurrent})
   if(!isCurrent())return
   if(spaces.length)workspacePicker.append(option('','Choose a workspace'))
   for(const {id,name,state} of spaces){workspaces.add(id);workspacePicker.append(option(id,`${name} (${id})${state==='archived'?' · Archived':''}`))}
   directoryStatus(spaces.length?'Choose a workspace, then load its published catalogs.':'No accessible workspaces.')
  }catch(error){
   if(!isCurrent())return
   workspaces.clear();workspacePicker.replaceChildren()
   directoryStatus('The workspace list is unavailable. Recheck the session to try again.')
  }finally{if(isCurrent()){workspaceFlight=undefined;controls()}}
 }
 function activate(){
  if(location.protocol!=='https:')throw new Error('HTTPS required.')
  reset('No catalog open.');active=true;el('session').hidden=false;controls()
  directoryStatus('Reading your current workspaces…')
  // Directory setup must not hold the original exact-ID entry or session
  // recheck hostage when the ordinary workspace index is slow or unavailable.
  const owner=new AbortController();workspaceFlight=owner;void loadWorkspaces(owner)
 }
 async function loadDirectory(){
  const workspace=workspaceChoice()
  if(!active||directoryBusy||!workspace)return
  const turn=retireDirectory()
  clearManual();retireScope('No catalog open.')
  directoryBusy=true;controls();directoryStatus('Checking published catalogs for this workspace…')
  const isCurrent=()=>active&&turn===directoryEpoch&&!directoryController.signal.aborted&&workspaceChoice()===workspace
  try{
   const ids=await client.catalogs(workspace,{signal:directoryController.signal,isCurrent})
   if(!isCurrent())return
   listedWorkspace=workspace;catalogs=new Set(ids)
   if(ids.length)catalogPicker.append(option('','Choose a catalog'),...ids.map(id=>option(id,id)))
   directoryStatus(ids.length?'Choose a published catalog, then open it.':'No published catalogs in this workspace.')
  }catch(error){
   if(!isCurrent())return
   directoryStatus('The published catalog directory is unavailable. Load it again explicitly; nothing was retried.')
  }finally{if(isCurrent()){directoryBusy=false;controls()}}
 }
 function renderPage(){el('assets').replaceChildren();for(const asset of list.items){const item=node('li',''),title=node('h3',asset.title),meta=node('p',`${TYPES[asset.type_id].title} · ${asset.entity.title}`),summary=node('p',asset.summary||'No summary supplied.'),button=node('button','Read asset');button.type='button';button.setAttribute('aria-label',`Read ${asset.title}`);button.addEventListener('click',()=>detail(asset));item.append(title,meta,summary,button);el('assets').append(item)}
  if(!list.items.length){
   const message=!metadata.types.available?'No catalog has been published. This is not a verified empty source.':metadata.types.items.every(t=>t.count===0)?'This published catalog contains no stored assets. This is not proof of complete source coverage.':'No assets match this stored catalog view. This is not proof of complete source coverage.'
   el('assets').append(node('li',message))
  }
  el('page-summary').textContent=`${list.total} matching stored assets · Page ${list.page} of ${Math.max(1,Math.ceil(list.total/20))} · 20 per page`;controls()
 }
 async function load(){if(!active||!scope||busy)return;const selected={...scope},turn=cancel(),options=readOptions(turn);clearCatalog();busy=true;controls();el('status').textContent='Reading one authorized catalog revision…'
  try{const types=validateTypes(await client.request(selected,'types',options),selected);if(!current(turn))return;const entities=validateEntities(await client.request(selected,'entities',{...options,revision:types.revision}),selected,types);if(!current(turn))return
   const next={types,entities},query=filtersFrom(),result=validatePage(await client.request(selected,'assets',{...options,revision:types.revision,filters:query}),selected,next,query,1);if(!current(turn))return
   metadata=next;filters=query;list=result;el('scope-title').textContent=`${selected.workspace_id} / ${selected.catalog_id}`;el('revision').textContent=`Catalog revision ${types.revision} · live provider verified: no · execution verified: no`;el('availability').textContent=types.available?'Published normalized catalog; full source coverage is not independently verified.':'Not published yet. Descriptors below do not prove a successful source read.'
   for(const type of types.items)el('type-counts').append(node('li',`${type.title}: ${type.count}`));for(const entity of entities.items)el('entity').append(option(entity.id,`${entity.title} (${entity.count})`))
   busy=false;el('panel').hidden=false;renderPage();el('status').textContent='Current server permissions checked. Read-only stored evidence; no upstream refresh occurred.'
  }catch(error){failed(error,turn)}finally{if(current(turn)){busy=false;controls()}}
 }
 async function loadPage(query,page=1){if(!active||!scope||!metadata||busy)return;const selected={...scope},prior=metadata,expectedTotal=list&&['type','entity','q'].every(key=>query[key]===filters[key])?list.total:undefined,turn=cancel();clearList();busy=true;controls();el('status').textContent='Reading the same authorized catalog revision…'
  try{const result=validatePage(await client.request(selected,'assets',{...readOptions(turn),revision:prior.types.revision,filters:query,page}),selected,prior,query,page,expectedTotal);if(!current(turn))return;list=result;filters=query;busy=false;renderPage();el('status').textContent='Stored page verified at the current catalog revision.'}catch(error){failed(error,turn)}finally{if(current(turn)){busy=false;controls()}}
 }
 async function detail(selected){if(!active||!metadata||!list||busy||el('asset-detail').open||!list.items.includes(selected))return;close();const turn=epoch,dt=detailEpoch,prior=metadata,selectedScope={...scope};detailController=new AbortController();const signal=detailController.signal,isCurrent=()=>current(turn)&&dt===detailEpoch&&!signal.aborted;el('detail-title').textContent=selected.title;el('detail-meta').textContent='Checking current scope, revision and content identity…';el('asset-detail').showModal()
  try{const result=validateDetail(await client.request(selectedScope,'detail',{revision:prior.types.revision,asset:selected.id,signal,isCurrent}),selectedScope,prior,selected);if(!isCurrent())return
   el('detail-meta').textContent=`${TYPES[selected.type_id].title} · ${selected.entity.title} · catalog revision ${result.revision} · source declarations only`
   const fields=node('dl','');for(const [name,value]of Object.entries(selected.fields)){fields.append(node('dt',name),node('dd',value))}el('detail-body').append(fields)
   for(const section of result.asset.detail.sections){const part=node('section','');part.append(node('h3',section.heading),node('pre',section.text||'No text supplied.'));el('detail-body').append(part)}
  }catch(error){if(!isCurrent()||error.name==='AbortError')return;failed(error,turn)}
 }
 workspacePicker.addEventListener('change',()=>{
  if(!active)return
  retireDirectory();clearManual();retireScope('No catalog open.');directoryStatus('Workspace changed. Load its published catalogs explicitly.')
 })
 catalogPicker.addEventListener('change',()=>{
  if(!active||directoryBusy)return
  clearManual();retireScope('No catalog open.');directoryStatus('Selection changed. Open a catalog to recheck access.')
 })
 el('catalog-picker-load').addEventListener('click',loadDirectory)
 el('catalog-picker-open').addEventListener('click',()=>{
  const workspace=workspaceChoice(),catalog=catalogChoice()
  if(!active||busy||directoryBusy||!workspace||!catalog)return
  clearManual();scope=scopeFrom(workspace,catalog);load()
 })
 for(const id of ['workspace','catalog'])el(id).addEventListener('input',()=>{
  if(!active)return
  retireDirectory();directoryStatus('Manual scope edited. Load the directory again to use the picker.')
  retireScope('Scope changed. Open current authorized catalog again.')
 })
 el('scope-form').addEventListener('submit',event=>{
  event.preventDefault();if(!active||busy||location.protocol!=='https:')return
  retireDirectory();directoryStatus('Open by exact IDs selected. No directory was loaded.')
  retireScope('No catalog open.')
  try{scope=scopeFrom(el('workspace').value,el('catalog').value)}catch(error){el('status').textContent=error.message;return}
  if(workspaceFlight){
   const retired=workspaceFlight;workspaceFlight=undefined;retired.abort()
   directoryStatus('Exact catalog selected. Recheck the session to load the workspace picker.')
  }
  load()
 })
 el('reload').addEventListener('click',()=>{
  if(!active||!scope||busy)return
  retireDirectory();directoryStatus('Load published catalogs to check the directory again.');load()
 })
 el('disconnect').addEventListener('click',()=>{if(!active)return;clear();onSignOut()})
 for(const id of ['type','entity','query'])el(id).addEventListener(id==='query'?'input':'change',()=>{if(!active||!metadata||busy)return;cancel();clearList();el('status').textContent='Filter changed. Apply it to read this catalog revision.'})
 el('filters').addEventListener('submit',event=>{event.preventDefault();try{loadPage(filtersFrom(el('type').value,el('entity').value,el('query').value.trim()))}catch(error){el('status').textContent=error.message}})
 el('previous').addEventListener('click',()=>{if(list&&list.page>1)loadPage(filters,list.page-1)});el('next').addEventListener('click',()=>{if(list&&list.page*20<list.total)loadPage(filters,list.page+1)})
 el('detail-close').addEventListener('click',close);el('asset-detail').addEventListener('cancel',event=>{event.preventDefault();close()});window.addEventListener('pagehide',()=>clear())
 controls();return {activate,clear}
}
