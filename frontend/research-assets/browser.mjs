import {TYPES,scopeFrom,filtersFrom,validateTypes,validateEntities,validatePage,validateDetail} from './client.mjs'
export function mountAssetPage({client,onSignOut=()=>{}}){
 if(!client||typeof client.request!=='function'||typeof client.clear!=='function')throw new Error('Explicit cookie client required.')
 const el=id=>document.getElementById(id),node=(tag,value)=>{const n=document.createElement(tag);n.textContent=value;return n}
 let active=false,epoch=0,controller,detailEpoch=0,detailController,scope,metadata,list,filters=filtersFrom(),busy=false
 function close(){detailEpoch++;detailController?.abort();detailController=undefined;el('asset-detail').close();el('detail-title').textContent='';el('detail-meta').textContent='';el('detail-body').replaceChildren()}
 function controls(){el('reload').disabled=!active||!scope||busy;for(const x of el('filters').elements)x.disabled=!metadata||busy;el('previous').disabled=busy||!list||list.page<=1;el('next').disabled=busy||!list||list.page*20>=list.total}
 function clearList(){close();list=undefined;el('assets').replaceChildren();el('page-summary').textContent='';controls()}
 function clearCatalog(){clearList();metadata=undefined;el('panel').hidden=true;for(const id of ['scope-title','revision','availability'])el(id).textContent='';el('type-counts').replaceChildren();el('entity').replaceChildren(node('option','All entities'));el('entity').firstChild.value='';controls()}
 function cancel(){epoch++;controller?.abort();controller=new AbortController();busy=false;return epoch}
 function clear(message='Session requires an explicit recheck.'){active=false;cancel();client.clear();scope=undefined;clearCatalog();el('session').hidden=true;for(const id of ['workspace','catalog','query'])el(id).value='';el('type').value='';filters=filtersFrom();el('status').textContent=message;controls()}
 function failed(error,turn){if(turn!==epoch||error.name==='AbortError')return;busy=false;clearCatalog();el('status').textContent=error.status?error.message:'Catalog response could not be validated. Reload current scope; nothing was retried.';controls()}
 const current=turn=>active&&turn===epoch&&!controller.signal.aborted
 function renderPage(){el('assets').replaceChildren();for(const asset of list.items){const item=node('li',''),title=node('h3',asset.title),meta=node('p',`${TYPES[asset.type_id].title} · ${asset.entity.title}`),summary=node('p',asset.summary||'No summary supplied.'),button=node('button','Read asset');button.type='button';button.setAttribute('aria-label',`Read ${asset.title}`);button.addEventListener('click',()=>detail(asset));item.append(title,meta,summary,button);el('assets').append(item)}
  if(!list.items.length)el('assets').append(node('li',metadata.types.available?'No assets match this stored catalog view. This is not proof of complete source coverage.':'No catalog has been published. This is not a verified empty source.'))
  el('page-summary').textContent=`${list.total} matching stored assets · Page ${list.page} of ${Math.max(1,Math.ceil(list.total/20))} · 20 per page`;controls()
 }
 async function load(){if(!active||!scope)return;const selected={...scope},turn=cancel();clearCatalog();busy=true;controls();el('status').textContent='Reading one authorized catalog revision…'
  try{const types=validateTypes(await client.request(selected,'types',{signal:controller.signal}),selected);if(!current(turn))return;const entities=validateEntities(await client.request(selected,'entities',{revision:types.revision,signal:controller.signal}),selected,types);if(!current(turn))return
   const next={types,entities},query=filtersFrom(),result=validatePage(await client.request(selected,'assets',{revision:types.revision,filters:query,signal:controller.signal}),selected,next,query,1);if(!current(turn))return
   metadata=next;filters=query;list=result;el('type').value='';el('query').value='';el('scope-title').textContent=`${selected.workspace_id} / ${selected.catalog_id}`;el('revision').textContent=`Catalog revision ${types.revision} · live provider verified: no · execution verified: no`;el('availability').textContent=types.available?'Published normalized catalog; full source coverage is not independently verified.':'Not published yet. Descriptors below do not prove a successful source read.'
   for(const type of types.items)el('type-counts').append(node('li',`${type.title}: ${type.count}`));for(const entity of entities.items){const option=node('option',`${entity.title} (${entity.count})`);option.value=entity.id;el('entity').append(option)}
   busy=false;el('panel').hidden=false;renderPage();el('status').textContent='Current server permissions checked. Read-only stored evidence; no upstream refresh occurred.'
  }catch(error){failed(error,turn)}finally{if(current(turn)){busy=false;controls()}}
 }
 async function loadPage(query,page=1){if(!active||!scope||!metadata||busy)return;const selected={...scope},prior=metadata,expectedTotal=list&&['type','entity','q'].every(key=>query[key]===filters[key])?list.total:undefined,turn=cancel();clearList();busy=true;controls();el('status').textContent='Reading the same authorized catalog revision…'
  try{const result=validatePage(await client.request(selected,'assets',{revision:prior.types.revision,filters:query,page,signal:controller.signal}),selected,prior,query,page,expectedTotal);if(!current(turn))return;list=result;filters=query;busy=false;renderPage();el('status').textContent='Stored page verified at the current catalog revision.'}catch(error){failed(error,turn)}finally{if(current(turn)){busy=false;controls()}}
 }
 async function detail(selected){if(!active||!metadata||!list||busy||!list.items.some(a=>a.id===selected.id))return;close();const turn=epoch,dt=detailEpoch,prior=metadata,selectedScope={...scope};detailController=new AbortController();el('detail-title').textContent=selected.title;el('detail-meta').textContent='Checking current scope, revision and content identity…';el('asset-detail').showModal()
  try{const result=validateDetail(await client.request(selectedScope,'detail',{revision:prior.types.revision,asset:selected.id,signal:detailController.signal}),selectedScope,prior,selected);if(turn!==epoch||dt!==detailEpoch||!active)return
   el('detail-meta').textContent=`${TYPES[selected.type_id].title} · ${selected.entity.title} · catalog revision ${result.revision} · source declarations only`
   const fields=node('dl','');for(const [name,value]of Object.entries(selected.fields)){fields.append(node('dt',name),node('dd',value))}el('detail-body').append(fields)
   for(const section of result.asset.detail.sections){const part=node('section','');part.append(node('h3',section.heading),node('pre',section.text||'No text supplied.'));el('detail-body').append(part)}
  }catch(error){if(turn!==epoch||dt!==detailEpoch||error.name==='AbortError')return;failed(error,turn)}
 }
 for(const id of ['workspace','catalog'])el(id).addEventListener('input',()=>{cancel();scope=undefined;clearCatalog();el('status').textContent='Scope changed. Open current authorized catalog again.'})
 el('scope-form').addEventListener('submit',event=>{event.preventDefault();if(!active||location.protocol!=='https:')return;cancel();clearCatalog();try{scope=scopeFrom(el('workspace').value,el('catalog').value)}catch(error){scope=undefined;el('status').textContent=error.message;controls();return}load()})
 el('reload').addEventListener('click',load);el('disconnect').addEventListener('click',()=>{clear();onSignOut()})
 for(const id of ['type','entity','query'])el(id).addEventListener(id==='query'?'input':'change',()=>{cancel();clearList();el('status').textContent='Filter changed. Apply it to read this catalog revision.'})
 el('filters').addEventListener('submit',event=>{event.preventDefault();try{loadPage(filtersFrom(el('type').value,el('entity').value,el('query').value.trim()))}catch(error){el('status').textContent=error.message}})
 el('previous').addEventListener('click',()=>{if(list)loadPage(filters,list.page-1)});el('next').addEventListener('click',()=>{if(list)loadPage(filters,list.page+1)})
 el('detail-close').addEventListener('click',close);el('asset-detail').addEventListener('cancel',event=>{event.preventDefault();close()});window.addEventListener('pagehide',()=>clear())
 controls();return {activate(){if(location.protocol!=='https:')throw new Error('HTTPS required.');active=true;el('session').hidden=false;el('status').textContent='Enter an administrator-provided workspace and catalog.';controls()},clear}
}
