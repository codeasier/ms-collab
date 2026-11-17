import test from 'node:test'
import assert from 'node:assert/strict'
import {mountPipelinePage} from '../runs.mjs'
const scope={workspace_id:'alpha',connection_id:'ci',pipeline_id:'build'},source={kind:'stored_snapshot',revision:1,full_history_complete:false,live_provider_verified:false}
const summary={id:'run-1',status:'failure',observed_at:'2026-01-01T00:00:00Z',stage_count:1,job_count:1,details_available:true},job={id:'job-1',name:'Unit <img src=x>',status:'failure',started_at:null,finished_at:null}
const run={...scope,id:summary.id,status:summary.status,observed_at:summary.observed_at,stages:[{name:'Checks',jobs:[job]}]},counts={success:0,failure:1,cancelled:0,timed_out:0,skipped:0,queued:0,running:0,unknown:0}
const responses={runs:{scope,source,total:1,offset:0,limit:100,items:[summary]},detail:{scope,source,run},durations:{scope,source,coverage:{source:'supplied_snapshots',full_history_complete:false,limit:100,supplied_runs:1,nonterminal_or_unknown_runs:0,observed_terminal_runs:1,selected_runs:1,runs_with_details:1,runs_with_samples:0,missing_details:0,malformed_entries:0,excluded_jobs:1,invalid_duration_jobs:0,ambiguous_jobs:0,selection_truncated:false},groups:[]},tasks:{scope,source,run:{id:summary.id,status:summary.status,observed_at:summary.observed_at,details_available:true},coverage:{stage_count:1,task_count:1,status_counts:counts,duplicate_id_tasks:0,stage_status_supplied:false},stages:[{index:0,name:'Checks',task_count:1,status_counts:counts}],filter:{status:''},total:1,offset:0,limit:10,items:[{stage_index:0,job_index:0,...job,id_occurrences:1}]}}
const tick=()=>new Promise(r=>setImmediate(r))
async function harness(action){
 const old={document:globalThis.document,window:globalThis.window,location:globalThis.location},nodes=new Map(),events=new Map(),calls=[]
 class Node{
  constructor(tag='div'){this.tagName=tag;this.children=[];this.listeners=new Map();this.value='';this.hidden=false;this.disabled=false;this.open=false;this._text=''}
  set id(value){this._id=value;nodes.set(value,this)} get id(){return this._id}
  set textContent(value){this._text=String(value);this.children=[]} get textContent(){return this._text+this.children.map(n=>n.textContent).join('')}
  append(...children){this.children.push(...children)} replaceChildren(...children){this._text='';this.children=[...children]}
  setAttribute(){} addEventListener(name,fn){if(!this.listeners.has(name))this.listeners.set(name,[]);this.listeners.get(name).push(fn)}
  fire(name){return Promise.all((this.listeners.get(name)||[]).map(fn=>fn({preventDefault(){}})))}
  showModal(){this.open=true} close(){this.open=false}
 }
 const ids=['detail','detail-title','detail-status','detail-body','panel','scope-title','revision','summary','coverage','runs','durations','session','workspace','connection','pipeline','query','state','status','previous','next','disconnect','scope-form','reload','filters','detail-close']
 for(const id of ids){const n=new Node();n.id=id}nodes.get('panel').hidden=true
 globalThis.document={getElementById:id=>nodes.get(id),createElement:tag=>new Node(tag)};globalThis.window={addEventListener:(name,fn)=>events.set(name,fn)};globalThis.location={protocol:'https:',hostname:'app.example.invalid'}
 let hook=async kind=>structuredClone(responses[kind])
 const client={clear(){calls.push('clear')},request:async(_scope,kind,options)=>{calls.push(kind);return hook(kind,options)}}
 const page=mountPipelinePage({client,sessionMode:true})
 const open=async()=>{page.activate();for(const [id,value]of [['workspace','alpha'],['connection','ci'],['pipeline','build']])nodes.get(id).value=value;await nodes.get('scope-form').fire('submit');await tick();assert.equal(nodes.get('panel').hidden,false)}
 const detail=()=>nodes.get('runs').children[0].children[1].fire('click')
 try{await action({nodes,calls,page,open,detail,setHook:fn=>hook=fn,events})}finally{Object.assign(globalThis,old)}
}
test('real controller opens selected detail and reaches a revision-pinned task request',()=>harness(async({nodes,calls,open,detail})=>{
 await open();await detail();assert.deepEqual(calls,['runs','durations','detail','tasks']);assert.equal(nodes.get('detail').open,true);assert.match(nodes.get('task-summary').textContent,/1 matching of 1 recorded/);assert.match(nodes.get('tasks').textContent,/Unit <img src=x>/);assert.equal(nodes.get('task-status-filter').disabled,false)
}))
test('closing a pending detail stops the later task read despite ineffective abort',()=>harness(async({nodes,calls,open,detail,setHook})=>{
 await open();let release,seen;const ready=new Promise(r=>seen=r),gate=new Promise(r=>release=r);setHook(async kind=>{if(kind==='detail'){seen();await gate}return structuredClone(responses[kind])});const pending=detail();await ready;await nodes.get('detail-close').fire('click');release();await pending;assert.deepEqual(calls,['runs','durations','detail']);assert.equal(nodes.get('detail').open,false);assert.equal(nodes.get('detail-body').textContent,'')
}))
test('session clear drops a late task response and cannot repopulate the dialog',()=>harness(async({nodes,calls,page,open,detail,setHook})=>{
 await open();let release,seen;const ready=new Promise(r=>seen=r),gate=new Promise(r=>release=r);setHook(async kind=>{if(kind==='tasks'){seen();await gate}return structuredClone(responses[kind])});const pending=detail();await ready;page.clear();release();await pending;assert.equal(nodes.get('detail-body').textContent,'');assert.equal(nodes.get('detail').open,false);assert.equal(nodes.get('workspace').value,'');assert.equal(nodes.get('panel').hidden,true);assert.equal(calls.at(-1),'clear')
}))
test('a rejected task projection clears old authorized evidence without fallback',()=>harness(async({nodes,open,detail,setHook})=>{
 await open();setHook(async kind=>{const result=structuredClone(responses[kind]);if(kind==='tasks')result.coverage.stage_status_supplied=true;return result});await detail();assert.equal(nodes.get('panel').hidden,true);assert.equal(nodes.get('detail-body').textContent,'');assert.equal(nodes.get('detail').open,false);assert.match(nodes.get('status').textContent,/could not be validated/)
}))
test('repeated reload/manual-submit and same pending detail do not create parallel reads',()=>harness(async({nodes,calls,page,open,detail,setHook})=>{
 await open();let release;const gate=new Promise(r=>release=r);setHook(async kind=>{if(kind==='runs')await gate;return structuredClone(responses[kind])})
 const pending=nodes.get('reload').fire('click');await nodes.get('reload').fire('click');await nodes.get('scope-form').fire('submit');assert.equal(page.loading,true);assert.equal(calls.filter(k=>k==='runs').length,2)
 release();await pending;assert.equal(page.loading,false)
 let finish;const slow=new Promise(r=>finish=r);setHook(async kind=>{if(kind==='detail')await slow;return structuredClone(responses[kind])})
 const first=detail();await detail();assert.equal(calls.filter(k=>k==='detail').length,1);finish();await first;assert.equal(calls.filter(k=>k==='tasks').length,1)
}))
test('old ignored-abort completion cannot unlock newer pending scope loading',()=>harness(async({nodes,calls,page,open,setHook})=>{
 await open();let releaseOld,releaseNew,count=0
 const old=new Promise(r=>releaseOld=r),fresh=new Promise(r=>releaseNew=r)
 setHook(async kind=>{if(kind==='runs')return ++count===1?old:fresh;return structuredClone(responses[kind])})
 const pending=nodes.get('reload').fire('click');await nodes.get('workspace').fire('input');await nodes.get('scope-form').fire('submit');assert.equal(page.loading,true);assert.equal(count,2)
 releaseOld(structuredClone(responses.runs));await pending;assert.equal(page.loading,true);assert.equal(nodes.get('panel').hidden,true);assert.equal(calls.filter(k=>k==='durations').length,1)
 await nodes.get('reload').fire('click');await nodes.get('scope-form').fire('submit');assert.equal(count,2)
 releaseNew(structuredClone(responses.runs));await tick();assert.equal(page.loading,false);assert.equal(nodes.get('panel').hidden,false);assert.equal(calls.filter(k=>k==='durations').length,2)
}))
