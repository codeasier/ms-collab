import {validateScope} from './client.mjs'
export function mountArtifactCatalogPage({client,sessionMode=false,onSignOut=()=>{},onDownload,clearDownload=()=>{}}){
if(!client||typeof client.quarters!=='function'||typeof client.query!=='function'||typeof client.clear!=='function')throw new Error('Explicit catalog client required.')
const $=id=>document.getElementById(id)
let generation=0, controller, scope=null, path='', view='', page=1, total=0, connected=false
const queryControls=['sort','order','keyword','recursive','files-only','apply']
function clearResults(){ $('results').replaceChildren();$('evidence').textContent='';$('page-label').textContent='';$('previous').disabled=true;$('next').disabled=true;total=0 }
function cancel(){clearDownload();generation++;controller?.abort();controller=null;clearResults();return generation}
function clearCatalog(){cancel();scope=null;path='';view='';page=1;$('catalog').hidden=true;$('quarter').replaceChildren();$('scope-title').textContent='';$('path-label').textContent='';$('view-id').value='';$('keyword').value='';$('recursive').checked=false;$('files-only').checked=false;$('sort').value='name';$('order').value='asc';setMode()}
function disconnect(message='Disconnected. Local catalog data and the credential were cleared.'){
  client.clear();connected=false;clearCatalog();if($('connect'))$('connect').hidden=false;$('session').hidden=true;if($('credential'))$('credential').value='';for(const id of ['workspace','connection','bucket'])$(id).value='';$('status').textContent=message
}
function error(error,ticket){if(ticket!==generation)return;if(error.status===401){disconnect(error.message);return}clearResults();$('status').textContent=error.status?error.message:'Unable to obtain a valid response. Check the scope and filters, then retry.'}
function setMode(){for(const id of queryControls)$(id).disabled=!!view;$('browse').hidden=!view;$('up').disabled=!!view||!path;$('path-label').textContent=view?`Named view: ${view} (navigation disabled)`:`Folder: /${path}`}
function definition(){const keyword=$('keyword').value.trim();return {path,recursive:$('recursive').checked,files_only:$('files-only').checked,sort_by:$('sort').value,order:$('order').value,conditions:keyword?[{keyword,operator:'AND'}]:[]}}
function render(data){
  const list=document.createElement('ul');list.className='rows'
  for(const row of data.items){
    const li=document.createElement('li');li.className='row';const text=document.createElement('div')
    const name=document.createElement(row.is_dir&&!view?'button':'p');name.textContent=row.name+(row.is_dir?'/':'');name.className=row.is_dir&&!view?'folder':'entry-name'
    if(name.tagName==='BUTTON'){name.type='button';name.addEventListener('click',()=>{path=row.key;page=1;run()})}
    const meta=document.createElement('div');meta.className='entry-meta';meta.textContent=`${row.key} · ${row.is_dir?'Directory':row.size.toLocaleString()+' bytes'} · ${row.modified_at||'Virtual parent; no modification time'}`
    text.append(name,meta)
    if(onDownload&&!row.is_dir&&!row.virtual){
      const selected={scope:{...scope},key:row.key,revision:data.catalog_revision,size:row.size}
      const download=document.createElement('button');download.type='button';download.className='secondary';download.textContent='Check download';download.addEventListener('click',()=>onDownload(selected));text.append(download)
    }
    const badge=document.createElement('span');badge.className='type';badge.textContent=row.virtual?'VIRTUAL':row.is_dir?'FOLDER':'FILE';li.append(text,badge);list.append(li)
  }
  if(!data.items.length){const empty=document.createElement('p');empty.className='empty';empty.textContent=data.quarter_available?'No items match this query.':'This quarter is absent from the stored observation. No fallback was used.';$('results').replaceChildren(empty)}else $('results').replaceChildren(list)
  total=data.total;$('evidence').textContent=`Observed ${data.observed_at} · Catalog revision ${data.catalog_revision}${view?' · View revision '+data.view_revision:''} · Stored metadata, not live synchronization`
  $('page-label').textContent=`Page ${page} · ${total} matching items`;$('previous').disabled=page<=1;$('next').disabled=page*Number($('page-size').value)>=total
  $('status').textContent=`${view?'Named view':'Catalog'} loaded for ${scope.workspace_id} / ${scope.connection_id} / ${scope.bucket_id}.`;setMode()
}
async function run(){
  if(!scope||!$('quarter').value)return
  if($('view-id').value.trim()!==view){cancel();$('status').textContent='Open the selected view or return to browsing before loading data.';return}
  const ticket=cancel();controller=new AbortController();setMode();$('status').textContent='Loading authorized catalog…'
  const request={scope:{...scope},selected:$('quarter').value,definition:definition(),page,pageSize:Number($('page-size').value),view}
  try{const data=await client.query(request,controller.signal);if(ticket!==generation)return;render(data)}catch(e){error(e,ticket)}
}
async function openScope(){
  const requested={workspace_id:$('workspace').value.trim(),connection_id:$('connection').value.trim(),bucket_id:$('bucket').value.trim()};clearCatalog()
  let selected;try{selected=validateScope(requested)}catch{$('status').textContent='Enter valid workspace, connection and bucket IDs.';return}
  const ticket=generation;controller=new AbortController();$('status').textContent='Loading authorized quarters…'
  try{const result=await client.quarters(selected,controller.signal);if(ticket!==generation)return;scope=selected;$('scope-title').textContent=`${scope.workspace_id} / ${scope.connection_id} / ${scope.bucket_id}`;$('catalog').hidden=false
    for(const q of result.quarters){const option=document.createElement('option');option.value=q;option.textContent=q;$('quarter').append(option)}
    setMode();if(result.quarters.length)await run();else{$('status').textContent='No quarters in this stored catalog. No fallback was used.';$('path-label').textContent='Folder: /'}
  }catch(e){error(e,ticket)}
}
if(!sessionMode)$('connect').addEventListener('submit',event=>{event.preventDefault();const value=$('credential').value;$('credential').value='';try{client.connect(value)}catch{$('status').textContent='Enter a valid opaque credential.';return}connected=true;$('connect').hidden=true;$('session').hidden=false;$('status').textContent='Credential held locally. Choose a scope to verify access.';$('workspace').focus()})
$('scope-form').addEventListener('submit',event=>{event.preventDefault();if(connected)openScope()})
for(const id of ['workspace','connection','bucket'])$(id).addEventListener('input',()=>{clearCatalog();$('status').textContent='Scope changed. Open the exact scope to load data.'})
$('disconnect').addEventListener('click',()=>{disconnect();if(sessionMode)onSignOut()})
$('reload').addEventListener('click',openScope)
$('query-form').addEventListener('submit',event=>{event.preventDefault();page=1;run()})
for(const id of ['sort','order','keyword','recursive','files-only'])$(id).addEventListener('input',()=>{cancel();$('status').textContent='Filters changed. Apply filters to load data.'})
for(const id of ['quarter','page-size'])$(id).addEventListener('change',()=>{page=1;run()})
$('view-form').addEventListener('submit',event=>{event.preventDefault();cancel();const selected=$('view-id').value.trim();if(!/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(selected)){$('status').textContent='Enter a valid named view ID.';return}view=selected;page=1;run()})
$('view-id').addEventListener('input',()=>{cancel();$('status').textContent='View selection changed. Open the exact view to load data.'})
$('browse').addEventListener('click',()=>{view='';path='';page=1;$('view-id').value='';run()})
$('up').addEventListener('click',()=>{if(view)return;const stem=path.replace(/\/$/,'');path=stem.includes('/')?stem.slice(0,stem.lastIndexOf('/')+1):'';page=1;run()})
$('previous').addEventListener('click',()=>{if(page>1){page--;run()}})
$('next').addEventListener('click',()=>{if(page*Number($('page-size').value)<total){page++;run()}})
window.addEventListener('pagehide',()=>disconnect('Disconnected when leaving this page.'))

return {activate(){connected=true;$('session').hidden=false;$('status').textContent='Choose an exact scope. Current session authority is checked on every request.';$('workspace').focus()},clear:disconnect}
}
