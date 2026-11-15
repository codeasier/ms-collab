// Copyright (c) 2026 codeasier; SPDX-License-Identifier: MIT
// Shared-session presentation only. No authentication client or provider flow.
import {ViewController,messageFor,scopeOf} from './client.mjs'
export function mountGuiSmokePage({client}) {
const $=id=>document.getElementById(id)
let enabled=false
const node = (tag, cls, value) => { const el=document.createElement(tag); if(cls) el.className=cls; if(value != null) el.textContent=String(value); return el; };
const notice = (value,error=false) => { $('notice').textContent=value; $('notice').className='notice'+(error?' error':''); };
const when = value => value === null ? 'Not reported' : new Date(value).toISOString().replace('T',' ').replace('Z',' UTC');
const duration = value => value === null ? 'Not reported' : `${Number(value.toFixed(3))} s`;
const badge = value => node('span','badge '+value,value);
function clearDOM() {
  for(const id of ['summary','tasks','detail']) $(id).replaceChildren();
  $('workspace-view').hidden=true; $('detail-view').hidden=true; $('list-view').hidden=true;
  $('scope-summary').textContent='Choose an authorized scope'; $('empty').textContent='';
  $('scope-title').textContent='Task observations'; $('snapshot-label').textContent=''; $('result-count').textContent=''; $('page-label').textContent='';
}
function datum(label,value,code=false) { const frag=document.createDocumentFragment(); frag.append(node('dt','',label)); const dd=node('dd'); dd.append(node(code?'code':'span','',value)); frag.append(dd); return frag; }
function detailCard(title, entries) { const card=node('section','detail-card'); card.append(node('h4','',title)); const dl=node('dl'); for(const e of entries) dl.append(datum(...e)); card.append(dl); return card; }
function renderDetail(response) {
  const row=response.task, target=$('detail');
  const header=node('div','detail-header'); header.append(node('h3','',row.id),badge(row.status)); target.append(header);
  const grid=node('div','detail-grid');
  grid.append(detailCard('Reported task',[['Branch',row.branch],['Source',row.source],['Created',when(row.created_at)],['Started',when(row.started_at)],['Finished',when(row.finished_at)],['Duration',duration(row.duration_seconds)]]));
  const evidence=detailCard('Revision evidence',[['Actual commit',row.commit ?? 'Not reported',true],['Requested commit',row.requested_commit ?? 'Not requested',true]]);
  evidence.append(node('p','note','Requested commit is not proof of the revision executed. These are saved reports, not independently verified execution evidence.')); grid.append(evidence);
  const summary=node('section','detail-card'); summary.append(node('h4','','Reported case summary'));
  if(row.summary === null) summary.append(node('p','note','Summary not reported. Missing counts are not zero.'));
  else { const dl=node('dl'); for(const key of ['passed','failed','skipped','total']) dl.append(datum(key[0].toUpperCase()+key.slice(1),row.summary[key])); summary.append(dl); }
  grid.append(summary);
  const stages=node('section','detail-card'); stages.append(node('h4','','Reported stages'));
  if(row.stages === null) stages.append(node('p','note','Stage details not reported.'));
  else if(!row.stages.length) stages.append(node('p','note','No stages in this published observation.'));
  else for(const stage of row.stages) { const item=node('div','stage'); item.append(node('strong','',stage.name),badge(stage.status),node('span','',duration(stage.duration_seconds))); stages.append(item); }
  stages.append(node('p','note','Stage durations are not guaranteed to sum to task duration.')); grid.append(stages); target.append(grid);
}
const controller = new ViewController(client, state => {
  clearDOM();
  if(state.phase === 'error') {
    if(state.error?.status === 401) { clear(); }
    $('scope-picker').open=true; notice(messageFor(state.error),true); return;
  }
  if(state.phase === 'idle') { notice(enabled ? 'Choose a scope or load its published tasks.' : 'Sign in to inspect an authorized scope.'); return; }
  if(state.phase === 'loading') { notice('Checking current access and loading a consistent snapshot…'); return; }
  const view=state.view, {list,summary,scope}=view;
  $('workspace-view').hidden=false;
  $('scope-title').textContent=`${scope.workspace_id} / ${scope.connection_id} / ${scope.repository_id}`;
  $('scope-summary').textContent=`Scope · ${scope.workspace_id} / ${scope.connection_id} / ${scope.repository_id}`; $('scope-picker').open=false;
  $('snapshot-label').textContent=list.available ? `Revision ${list.source.revision} · Observed ${when(list.source.observed_at)} · Not live` : 'No published snapshot. No live provider was contacted.';
  $('summary').hidden=!list.available;
  if(list.available) for(const key of ['success','failed','running','pending','cancelled','unknown']) { const card=node('div','stat'); card.dataset.state=key; card.append(node('p','','Reported '+key),node('strong','',summary.states[key])); $('summary').append(card); }
  if(state.phase === 'loading-detail') { $('detail-view').hidden=false; notice('Loading task details at the displayed revision…'); return; }
  if(state.phase === 'detail') { $('detail-view').hidden=false; renderDetail(state.detail); notice('Task details from the same published snapshot.'); $('back').focus(); return; }
  $('list-view').hidden=false; notice(list.available ? 'Current access verified for this read. Results are saved observations only.' : 'This authorized scope has no published snapshot yet.');
  $('result-count').textContent=`${list.total} matching · ${summary.total} in snapshot`;
  $('tasks').hidden=list.items.length === 0;
  $('empty').hidden=list.items.length !== 0;
  $('empty').textContent=!list.available ? 'No snapshot has been published for this scope.' : list.total === 0 ? 'No published tasks match these filters.' : 'No tasks on this page. Load the first page again.';
  for(const row of list.items) {
    const item=node('article','task');
    const field=(cls,label,value) => { const p=node('p',cls); p.append(node('small','',label),typeof value === 'string'?document.createTextNode(value):value); return p; };
    item.append(field('task-id','Task',row.id),field('branch','Branch',row.branch),field('state','Reported state',badge(row.status)),field('origin','Source',row.source),field('created','Created',when(row.created_at)));
    const button=node('button','quiet','Details'); button.type='button'; button.setAttribute('aria-label','Inspect task '+row.id); button.addEventListener('click',()=>controller.detail(row.id)); item.append(button); $('tasks').append(item);
  }
  $('previous').disabled=list.offset === 0; $('next').disabled=list.next_offset === null;
  $('page-label').textContent=`Page ${Math.floor(list.offset/list.limit)+1} of ${Math.max(1,Math.ceil(list.total/list.limit))}`;
});
function filters() { return {status:$('status-filter').value,source:$('source-filter').value,branch:$('branch-filter').value,limit:Number($('page-size').value)}; }
function inputScope() { return scopeOf({workspace_id:$('workspace').value,connection_id:$('connection').value,repository_id:$('repository').value}); }

function availability(){for(const id of ['workspace','connection','repository','load'])$(id).disabled=!enabled}
function resetFields(){$('scope-form').reset();$('filter-form').reset();$('scope-picker').open=true}
function clear(message='Verify the current session before loading a scope.'){
 enabled=false;controller.clear();resetFields();availability();notice(message)
}
$('scope-form').addEventListener('submit',event=>{event.preventDefault();if(!enabled)return;try{controller.load(inputScope(),filters())}catch(error){controller.clear();notice(messageFor(error),true)}})
$('filter-form').addEventListener('submit',event=>{event.preventDefault();if(!enabled)return;try{controller.load(inputScope(),filters())}catch(error){controller.clear();notice(messageFor(error),true)}})
for(const id of ['workspace','connection','repository'])$(id).addEventListener('input',()=>{controller.clear();$('filter-form').reset();$('scope-picker').open=true})
for(const id of ['status-filter','source-filter','branch-filter','page-size'])$(id).addEventListener('input',()=>{
 // Clear the saved snapshot and request epoch, not the user's editing surface.
 controller.clear();$('workspace-view').hidden=false;$('list-view').hidden=false
 $('tasks').hidden=true;$('empty').hidden=false;$('empty').textContent='Filters changed. Apply filters to load a new snapshot.'
 $('previous').disabled=true;$('next').disabled=true;notice('Filters changed. No previous snapshot is shown.')
})
$('clear').addEventListener('click',()=>{controller.clear();$('scope-picker').open=true})
$('back').addEventListener('click',()=>{controller.back();$('tasks').querySelector('button')?.focus()})
$('previous').addEventListener('click',()=>{const view=controller.view;if(enabled&&view)controller.page(Math.max(0,view.list.offset-view.list.limit))})
$('next').addEventListener('click',()=>{const view=controller.view;if(enabled&&view?.list.next_offset!=null)controller.page(view.list.next_offset)})
clear()
return {clear,activate(){clear();enabled=true;availability();notice('Current session verified. Choose an exact authorized scope.')}}
}
