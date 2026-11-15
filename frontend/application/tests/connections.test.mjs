import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import {pathToFileURL} from 'node:url'
const source=fs.readFileSync(new URL('../connections.mjs',import.meta.url),'utf8').replace("'/manage/client.mjs'",JSON.stringify(new URL('../../workspace-admin/client.mjs',import.meta.url).href))
const {validateConnections,mountConnectionsPage,mountConnectionPicker}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'))
const clone=x=>JSON.parse(JSON.stringify(x)),flush=()=>new Promise(r=>setImmediate(r)),deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return{promise,resolve}}
const keys=['reader_ids','refresher_ids','approver_ids'],scope={workspace_id:'alpha',connection_id:'source',repository:'sample/project'}
const view={id:'source-policy',scope,revision:1,grants:{reader_ids:['admin','reader'],refresher_ids:['admin'],approver_ids:['admin']},options:['admin','reader'].map(p=>({principal_id:p,eligible:{reader_ids:true,refresher_ids:p==='admin',approver_ids:p==='admin'}})),snapshot_available:true,approved_count:2}
const managed={workspace_id:'alpha',module:'community',items:[view]},directory={workspace_id:'alpha',module:'community',items:[{id:view.id,scope,revision:1}]}
const spaces=[{id:'alpha',name:'Synthetic <img src=x>',kind:'product',state:'active',revision:1}]
function dom(){
 class Node{
  constructor(tag){this.tag=tag;this.children=[];this.hidden=false;this.disabled=false;this.value='';this.handlers={};this.dataset={};this._text='';this.checked=false}
  set textContent(value){this._text=String(value);this.children=[]}get textContent(){return this._text+this.children.map(x=>x.textContent??'').join('')}
  append(...nodes){this.children.push(...nodes);for(const n of nodes)if(typeof n==='object')n.parentElement=this;if(this.tag==='select'&&this.children.length&&!this.value)this.value=this.children[0].value}
  replaceChildren(){this.children=[];this._text='';this.value=''}addEventListener(n,f){this.handlers[n]=f}close(){this.open=false}showModal(){this.open=true}dispatchEvent(e){this.handlers[e.type]?.(e)}requestSubmit(){this.handlers.submit?.({preventDefault(){}})}
 }
 const ids=['connection-status','connection-results','connection-directory','connection-managed','connection-dialog','connection-warning','connection-workspace','connection-controls','connection-scope','connection-save','connection-cancel','connection-confirm','connection-picker','picker-connection','picker-use','picker-workspace','picker-status','picker-load','workspace','connection','repository','scope-form']
 const nodes=new Map(ids.map(id=>[id,new Node(['connection-workspace','picker-workspace','picker-connection'].includes(id)?'select':'div')]));const get=id=>nodes.get(id)
 const doc={getElementById:get,createElement:tag=>new Node(tag)}
 return{doc,get,fire:(id,event)=>get(id).handlers[event]({preventDefault(){}}),all(root,predicate){const found=[];const walk=n=>{if(predicate(n))found.push(n);n.children.forEach(walk)};walk(root);return found}}
}
function setup(request){const h=dom(),calls=[];const session={request:async(p,o)=>{calls.push([p,o]);return request?request(p,o):p==='/api/workspaces'?clone(spaces):p.endsWith('/manage')?clone(managed):clone(directory)}};return {...h,calls,session,page:mountConnectionsPage({session,doc:h.doc})}}
async function load(h){await h.page.activate();await h.fire('connection-scope','submit')}
function choose(h){const form=h.get('connection-managed').children[0];const checkbox=h.all(form,n=>n.tag==='input'&&n.dataset.principal==='reader'&&n.dataset.grant==='reader_ids')[0];checkbox.checked=false;form.handlers.change();form.handlers.submit({preventDefault(){}});return form}
test('strict response scope, bounded shape and semantics fail closed',()=>{
 assert.equal(validateConnections(managed,'alpha',true).length,1)
 for(const change of [v=>v.workspace_id='beta',v=>v.items[0].scope.repository='a/b/c',v=>v.items[0].id+='\n',v=>v.items[0].revision=2**53,v=>v.items.push(clone(view)),v=>v.items[0].grants.reader_ids.push('outsider'),v=>v.items[0].options[0].eligible.reader_ids=1,v=>v.items[0].snapshot_available=false,v=>v.items[0].credential_ref='secret']){const v=clone(managed);change(v);assert.throws(()=>validateConnections(v,'alpha',true))}
})
test('actual mounted current options, text-only rendering and explicit replacement confirmation',async()=>{
 const h=setup();await load(h);assert.equal(h.get('connection-results').hidden,false);assert.match(h.get('connection-directory').textContent,/sample\/project/);assert.match(h.get('connection-workspace').textContent,/<img src=x>/)
 const form=choose(h);assert.equal(h.get('connection-dialog').open,true);assert.equal(h.calls.some(([,o])=>o?.method==='PATCH'),false);await h.fire('connection-cancel','click');assert.equal(h.get('connection-dialog').open,false)
 form.handlers.submit({preventDefault(){}});h.session.request=async(p,o)=>{assert.equal(o.method,'PATCH');assert.equal(o.revision,1);assert.deepEqual(o.body.reader_ids,['admin']);return{...clone(view),revision:2,grants:o.body,snapshot_available:false,approved_count:0}}
 await h.fire('connection-confirm','submit');assert.equal(h.get('connection-results').hidden,true);assert.equal(h.get('connection-managed').children.length,0);assert.match(h.get('connection-status').textContent,/Grants replaced/)
})
test('conflict clears private form and never automatically retries',async()=>{
 const h=setup();await load(h);choose(h);let requests=0;h.session.request=async()=>{requests++;throw Object.assign(Error('Revision changed'),{status:412})};await h.fire('connection-confirm','submit');assert.equal(requests,1);assert.equal(h.get('connection-managed').children.length,0);assert.equal(h.get('connection-dialog').open,false);assert.match(h.get('connection-status').textContent,/Reload/)
})
test('clear wins over ignored abort between directory and management',async()=>{
 const pending=deferred(),h=setup(p=>p==='/api/workspaces'?clone(spaces):pending.promise);await h.page.activate();const request=h.fire('connection-scope','submit');h.page.clear();pending.resolve(clone(directory));await request;assert.equal(h.calls.length,2);assert.equal(h.get('connection-controls').hidden,true);assert.equal(h.get('connection-managed').children.length,0)
})
test('old pending PATCH cannot unlock, clear or overwrite a newer operation',async()=>{
 const h=setup();await load(h);choose(h);const old=deferred();h.session.request=()=>old.promise;const previous=h.fire('connection-confirm','submit');h.page.clear();h.session.request=async p=>p==='/api/workspaces'?clone(spaces):p.endsWith('/manage')?clone(managed):clone(directory);await load(h);choose(h);const current=deferred();h.session.request=()=>current.promise;const newest=h.fire('connection-confirm','submit');old.resolve({...clone(view),revision:2,grants:{...view.grants,reader_ids:['admin']},snapshot_available:false,approved_count:0});await previous;assert.equal(h.get('connection-dialog').open,false);assert.equal(h.get('connection-save').disabled,true);assert.match(h.get('connection-status').textContent,/Replacing grants/);current.resolve({...clone(view),revision:2,grants:{...view.grants,reader_ids:['admin']},snapshot_available:false,approved_count:0});await newest;assert.equal(h.get('connection-dialog').open,false)
})
test('picker rechecks access, fills existing form without URLs/storage and clears lifecycle',async()=>{
 const h=dom(),calls=[];let submitted=0;h.get('scope-form').handlers.submit=()=>submitted++
 const picker=mountConnectionPicker({doc:h.doc,session:{request:async p=>{calls.push(p);return p==='/api/workspaces'?clone(spaces):clone(directory)}}});await picker.activate();await h.fire('picker-load','click');await h.fire('picker-use','click');assert.equal(submitted,1);assert.equal(h.get('repository').value,'sample/project');assert.equal(calls.length,2);picker.clear();assert.equal(h.get('connection-picker').hidden,true);assert.equal(h.get('picker-connection').children.length,0)
})
test('picker ignores stale discovery and cannot submit unverified options',async()=>{
 const h=dom(),pending=deferred();let submits=0;h.get('scope-form').handlers.submit=()=>submits++;const picker=mountConnectionPicker({doc:h.doc,session:{request:p=>p==='/api/workspaces'?Promise.resolve(clone(spaces)):pending.promise}});await picker.activate();const request=h.fire('picker-load','click');picker.clear();pending.resolve(clone(directory));await request;await h.fire('picker-use','click');assert.equal(submits,0);assert.equal(h.get('picker-connection').children.length,0)
})

test('pending replacement dismisses modal before await so actual logout/recheck remain reachable',async()=>{const h=setup();await load(h);choose(h);const pending=deferred();h.session.request=()=>pending.promise;const operation=h.fire('connection-confirm','submit');assert.equal(h.get('connection-dialog').open,false);assert.equal(h.get('connection-warning').textContent,'');assert.equal(h.get('connection-managed').children.length,0);h.page.clear();pending.resolve({...clone(view),revision:2,grants:{...view.grants,reader_ids:['admin']},snapshot_available:false,approved_count:0});await operation;assert.equal(h.get('connection-controls').hidden,true);assert.equal(h.get('connection-managed').children.length,0)})
