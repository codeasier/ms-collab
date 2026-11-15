// Controller state-machine checks. Real layout and browser behavior remain in CJS acceptance.
import test from 'node:test'
import assert from 'node:assert/strict'
import {mountResponsibilityPage} from '../browser.mjs'
class Element{
 constructor(){this.children=[];this.events={};this.elements=[];this.hidden=false;this.open=false;this.value='';this.text='';this.dataset={}}
 set textContent(value){this.text=value;this.children=[]}get textContent(){return this.text+this.children.map(c=>c.textContent).join('')}
 append(...nodes){this.children.push(...nodes)}replaceChildren(...nodes){this.text='';this.children=nodes}
 addEventListener(name,fn){(this.events[name]??=[]).push(fn)}
 async fire(name){for(const fn of this.events[name]||[])await fn({preventDefault(){}})}
 close(){if(this.open){this.open=false;void this.fire('close')}}showModal(){this.open=true}
}
const scope={workspace_id:'alpha',connection_id:'source',repository:'sample/project'}
const fields=[{id:'ui',name:'<img src=x onerror=alert(1)>',kind:'developer',paths:['ui'],owner_ids:['reader'],backup:null}]
const common={scope,revision:2,config_version:2,identity_namespace:'ms-collab.principal.id',workflow_enabled:false}
const stale={...common,fields,references:{state:'reconfirmation_required',stale_ids:['reader'],confirmation_token:'a'.repeat(64)}}
const current={...common,revision:3,config_version:3,fields,references:{state:'current',stale_ids:[],confirmation_token:'b'.repeat(64)}}
const candidates={...current,mode:'readonly_foundation',review_status:'unverified',approval_available:false,transport_executable:false,external_effect:'not_observed',items:[],total:0,offset:0,limit:20,view:'all',incomplete_pull_count:0,directory_status:'not_implemented',coverage:'approved_observed_subset',community_revision:1,snapshot_available:false,snapshot_observed_at:null,evidence_current:false,evidence_observed_at:null};delete candidates.fields;delete candidates.references
const tick=()=>new Promise(r=>setImmediate(r))
function fixture(overrides={}){
 const elements=new Map(),el=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id)},events={};let config=structuredClone(stale),cleared=0
 globalThis.document={getElementById:el,createElement:()=>new Element()};globalThis.location={protocol:'https:',hostname:'example.invalid'};globalThis.window={addEventListener:(name,fn)=>events[name]=fn}
 const calls=[],client={clear(){cleared++},async request(selected,action,options){calls.push({selected,action,options});if(overrides[action])return overrides[action](selected,options);if(action==='capabilities'){const {references,fields,...data}=config;return {...data,capabilities:{read:true,edit:true}}}if(action==='config')return structuredClone(config);if(action==='reconfirm'){config=structuredClone(current);return structuredClone(config)}if(action==='candidates')return structuredClone(candidates);throw Error('unexpected action')}}
 const controller=mountResponsibilityPage({client,sessionMode:true})
 async function load(){for(const [key,value] of Object.entries({workspace:'alpha',connection:'source',owner:'sample',repo:'project'}))el(key).value=value;await el('scope-form').fire('submit');await tick()}
 return {el,calls,load,events,controller,cleared:()=>cleared}
}
test('retained configuration blocks candidate reads but explicit confirm is single-shot',async()=>{
 const {el,calls,load}=fixture();await load();assert.deepEqual(calls.map(x=>x.action),['capabilities','config']);assert.equal(el('panel').hidden,false);assert.equal(el('reconfirm').hidden,false);assert.match(el('reference-status').textContent,/Historical, unconfirmed/);assert.match(el('configuration').textContent,/reader \(historical, unconfirmed\)/)
 for(const dismissal of ['cancel','escape','close']){
  await el('reconfirm').fire('click');assert.equal(el('confirmation').open,true);assert.match(el('confirm-summary').textContent,/CURRENT local identity and membership: reader/);assert.ok(!el('confirm-summary').textContent.includes('a'.repeat(64)))
  if(dismissal==='cancel')await el('cancel').fire('click');else if(dismissal==='escape')await el('confirmation').fire('cancel');else el('confirmation').close()
  assert.equal(el('confirmation').open,false);assert.equal(el('confirm-summary').textContent,'');await el('confirm-form').fire('submit');assert.equal(calls.length,2)
 }
 await el('reconfirm').fire('click');await Promise.all([el('confirm-form').fire('submit'),el('confirm-form').fire('submit')]);await tick();assert.equal(calls.filter(x=>x.action==='reconfirm').length,1);assert.deepEqual(calls[2].options.fields,fields);assert.equal(calls[2].options.confirmation_token,'a'.repeat(64));assert.equal(calls[2].options.revision,2);assert.equal(el('reconfirm').hidden,true);assert.match(el('status').textContent,/explicitly reconfirmed/)
})
test('late accepted confirmation cannot survive scope or identity cleanup',async()=>{
 for(const reason of ['scope','identity','pagehide']){
  let release;const pending=new Promise(r=>release=r),f=fixture({reconfirm:()=>pending});await f.load();await f.el('reconfirm').fire('click');const result=f.el('confirm-form').fire('submit');await tick()
  if(reason==='scope'){f.el('workspace').value='beta';await f.el('workspace').fire('input')}else if(reason==='identity')await f.el('disconnect').fire('click');else f.events.pagehide()
  release(structuredClone(current));await result;await tick();assert.equal(f.el('panel').hidden,true);assert.equal(f.el('configuration').children.length,0);assert.equal(f.el('confirm-summary').textContent,'');assert.deepEqual(f.calls.map(x=>x.action),['capabilities','config','reconfirm'])
 }
})
test('proposal failure clears configuration and never retries or silently reloads a token',async()=>{
 for(const status of [401,409,412,undefined]){
  const f=fixture({reconfirm:()=>{throw Object.assign(Error('Synthetic failure'),{status})}});await f.load();await f.el('reconfirm').fire('click');await f.el('confirm-form').fire('submit');await tick();assert.equal(f.el('panel').hidden,true);assert.equal(f.el('reconfirm').hidden,true);assert.equal(f.el('confirm-summary').textContent,'');assert.deepEqual(f.calls.map(x=>x.action),['capabilities','config','reconfirm']);if(status===401)assert.equal(f.cleared(),1)
 }
})
