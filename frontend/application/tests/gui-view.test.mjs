import test from 'node:test'
import assert from 'node:assert/strict'
import {mountGuiSmokePage} from '../../gui-smoke/view.mjs'
import {list,summary,scope,deferred} from '../../gui-smoke/tests/fixture.mjs'
const flush=()=>new Promise(r=>setImmediate(r))
function dom(){
 class Node{
  constructor(){this.children=[];this.hidden=false;this.disabled=false;this.value='';this.handlers={};this.dataset={};this._text=''}
  set textContent(value){this._text=String(value);this.children=[]}get textContent(){return this._text+this.children.map(x=>x.textContent??'').join('')}
  append(...nodes){this.children.push(...nodes);for(const n of nodes)if(typeof n==='object')n.parentElement=this}replaceChildren(){this.children=[];this._text=''}
  addEventListener(name,fn){this.handlers[name]=fn}setAttribute(){}focus(){}querySelector(){return null}reset(){}
 }
 const ids=['notice','scope-form','filter-form','scope-picker','scope-summary','summary','tasks','detail','workspace-view','detail-view','list-view','scope-title','snapshot-label','result-count','page-label','empty','previous','next','back','clear','workspace','connection','repository','load','status-filter','source-filter','branch-filter','page-size']
 const elements=new Map(ids.map(id=>[id,new Node()])),get=id=>elements.get(id)
 get('list-view').parentElement=get('workspace-view');get('filter-form').parentElement=get('list-view');for(const id of ['status-filter','source-filter','branch-filter','page-size'])get(id).parentElement=get('filter-form')
 const document={getElementById:get,createElement:()=>new Node(),createTextNode:text=>Object.assign(new Node(),{textContent:text}),createDocumentFragment:()=>new Node()}
 return {document,get,fill(){get('workspace').value='alpha';get('connection').value='panel';get('repository').value='repo';get('page-size').value='20'},fire(id,name){get(id).handlers[name]({preventDefault(){}})},visible(id){for(let node=get(id);node;node=node.parentElement)if(node.hidden)return false;return true}}
}
test('actual mounted filter edits clear data but preserve editable Apply surface',async()=>{
 const h=dom();globalThis.document=h.document;let calls=0
 const page=mountGuiSmokePage({client:{list:async(s,q)=>{calls++;return list(s,q,8)},summary:async s=>summary(s,8)}})
 page.activate();h.fill();h.fire('scope-form','submit');await flush();assert.equal(h.get('tasks').children.length,8)
 h.get('page-size').value='5';h.fire('page-size','input');assert.equal(h.get('tasks').children.length,0);assert.equal(h.get('summary').children.length,0);assert.equal(h.get('detail').children.length,0)
 assert.equal(h.visible('filter-form'),true);assert.equal(h.visible('page-size'),true);assert.equal(calls,1);assert.equal(h.get('previous').disabled,true);assert.equal(h.get('next').disabled,true)
 h.get('branch-filter').value='m';h.fire('branch-filter','input');h.get('branch-filter').value='main';h.fire('branch-filter','input');assert.equal(h.visible('branch-filter'),true);assert.equal(h.get('branch-filter').value,'main')
 h.fire('filter-form','submit');await flush();assert.equal(calls,2);assert.equal(h.get('tasks').children.length,5);assert.equal(h.visible('filter-form'),true)
 page.clear();assert.equal(h.visible('filter-form'),false);assert.equal(h.get('tasks').children.length,0);delete globalThis.document
})
test('actual mounted clear wins over an ignored-abort pending list',async()=>{
 const h=dom();globalThis.document=h.document;const pending=deferred();let summaries=0
 const page=mountGuiSmokePage({client:{list:()=>pending.promise,summary:async()=>{summaries++;return summary()}}})
 page.activate();h.fill();h.fire('scope-form','submit');page.clear();pending.resolve(list(scope));await flush();assert.equal(summaries,0);assert.equal(h.visible('filter-form'),false);assert.equal(h.get('tasks').children.length,0);assert.equal(h.get('load').disabled,true);delete globalThis.document
})
