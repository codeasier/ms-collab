import test from 'node:test'
import assert from 'node:assert/strict'
import {mountPipelinePage,durationPoints} from '../runs.mjs'
const scope={workspace_id:'alpha',connection_id:'ci',pipeline_id:'build'},source={kind:'stored_snapshot',revision:1,full_history_complete:false,live_provider_verified:false}
const summary={id:'run-1',status:'failure',observed_at:'2026-01-01T00:00:00Z',stage_count:1,job_count:1,details_available:true},job={id:'job-1',name:'Unit <img src=x>',status:'failure',started_at:null,finished_at:null}
const run={...scope,id:summary.id,status:summary.status,observed_at:summary.observed_at,stages:[{name:'Checks',jobs:[job]}]},counts={success:0,failure:1,cancelled:0,timed_out:0,skipped:0,queued:0,running:0,unknown:0}
const responses={runs:{scope,source,total:1,offset:0,limit:100,items:[summary]},detail:{scope,source,run},durations:{scope,source,coverage:{source:'supplied_snapshots',full_history_complete:false,limit:100,supplied_runs:1,nonterminal_or_unknown_runs:0,observed_terminal_runs:1,selected_runs:1,runs_with_details:1,runs_with_samples:0,missing_details:0,malformed_entries:0,excluded_jobs:1,invalid_duration_jobs:0,ambiguous_jobs:0,selection_truncated:false},groups:[]},tasks:{scope,source,run:{id:summary.id,status:summary.status,observed_at:summary.observed_at,details_available:true},coverage:{stage_count:1,task_count:1,status_counts:counts,duplicate_id_tasks:0,stage_status_supplied:false},stages:[{index:0,name:'Checks',task_count:1,status_counts:counts}],filter:{status:''},total:1,offset:0,limit:10,items:[{stage_index:0,job_index:0,...job,id_occurrences:1}]}}
const tick=()=>new Promise(r=>setImmediate(r))
async function harness(action){
 const old={document:globalThis.document,window:globalThis.window,location:globalThis.location},nodes=new Map(),events=new Map(),calls=[]
 class Node{
  constructor(tag='div'){this.tagName=tag;this.children=[];this.attrs=new Map();this.listeners=new Map();this.value='';this.hidden=false;this.disabled=false;this.open=false;this._text=''}
  set id(value){this._id=value;nodes.set(value,this)} get id(){return this._id}
  set textContent(value){this._text=String(value);this.children=[]} get textContent(){return this._text+this.children.map(n=>n.textContent).join('')}
  append(...children){this.children.push(...children)} replaceChildren(...children){this._text='';this.children=[...children]}
  setAttribute(key,value){this.attrs.set(key,String(value))} getAttribute(key){return this.attrs.get(key)??null}
  addEventListener(name,fn){if(!this.listeners.has(name))this.listeners.set(name,[]);this.listeners.get(name).push(fn)}
  fire(name){return Promise.all((this.listeners.get(name)||[]).map(fn=>fn({preventDefault(){}})))}
  showModal(){this.open=true} close(){this.open=false}
 }
 const ids=['detail','detail-title','detail-status','detail-body','panel','scope-title','revision','summary','coverage','runs','durations','session','workspace','connection','pipeline','query','state','status','previous','next','disconnect','scope-form','reload','filters','detail-close']
 for(const id of ids){const n=new Node();n.id=id}nodes.get('panel').hidden=true
 globalThis.document={getElementById:id=>nodes.get(id),createElement:tag=>new Node(tag),createElementNS:(_,tag)=>new Node(tag)};globalThis.window={addEventListener:(name,fn)=>events.set(name,fn)};globalThis.location={protocol:'https:',hostname:'app.example.invalid'}
 let hook=async kind=>structuredClone(responses[kind])
 const client={clear(){calls.push('clear')},request:async(_scope,kind,options)=>{calls.push(kind);return hook(kind,options,_scope)}}
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

// Controller-level transport fixture only. Run IDs intentionally reappear across scopes and
// revisions; the successful job in run-a even has the *other run's ID*. No job can be a target.
function sampledSnapshot(tuple=scope,revision=1){
 const proof={...source,revision},names=['run-a','run-b'],stage='Checks',taskName='Measured',
   stamp='2026-01-02T00:00:01Z',start='2026-01-01T23:59:59Z',end='2026-01-02T00:00:00Z',
   marker=[tuple.workspace_id,tuple.connection_id,tuple.pipeline_id,revision].join('/'),
   sampledJob={id:'run-b',name:taskName,status:'success',started_at:start,finished_at:end},
   failedJob={id:'other',name:'Unmeasured '+marker,status:'failure',started_at:null,finished_at:null}
 const list=names.map((id,i)=>({id,status:i?'success':'failure',observed_at:stamp,stage_count:1,job_count:i?1:2,details_available:true}))
 const samples=list.map(row=>({run_id:row.id,run_status:row.status,observed_at:stamp,job_id:sampledJob.id,started_at:start,finished_at:end,duration_seconds:1}))
 const coverage={...responses.durations.coverage,supplied_runs:2,observed_terminal_runs:2,selected_runs:2,runs_with_details:2,runs_with_samples:2,excluded_jobs:1}
 const group={key:JSON.stringify([stage,taskName]),stage_name:stage,job_name:taskName,count:2,average_seconds:1,min_seconds:1,max_seconds:1,latest_seconds:1,samples}
 return {marker,get(kind,options){
  const envelope={scope:{...tuple},source:{...proof}}
  if(kind==='runs')return structuredClone({...envelope,total:list.length,offset:0,limit:100,items:list})
  if(kind==='durations')return structuredClone({...envelope,coverage,groups:[group]})
  const row=list.find(r=>r.id===options.runID);assert.ok(row,'details and tasks must target a loaded run')
  const jobs=row.id==='run-a'?[sampledJob,failedJob]:[sampledJob]
  if(kind==='detail')return structuredClone({...envelope,run:{...tuple,id:row.id,status:row.status,observed_at:stamp,stages:[{name:stage,jobs}]}})
  assert.equal(kind,'tasks','no extra or job-targeted operation')
  const stateCounts={...counts,success:1,failure:jobs.length-1}
  return structuredClone({...envelope,run:{id:row.id,status:row.status,observed_at:stamp,details_available:true},
   coverage:{stage_count:1,task_count:jobs.length,status_counts:stateCounts,duplicate_id_tasks:0,stage_status_supplied:false},
   stages:[{index:0,name:stage,task_count:jobs.length,status_counts:stateCounts}],filter:{status:''},total:jobs.length,offset:0,limit:10,
   items:jobs.map((j,job_index)=>({stage_index:0,job_index,...j,id_occurrences:1}))})
 }}
}
function sampleDriver(h){
 const requests=[]
 let select=sampledSnapshot(),intercept
 h.setHook(async(kind,options,tuple)=>{
  const entry={kind,tuple:{...tuple},options:{...options}};requests.push(entry)
  const reply=select.get(kind,options)
  return intercept?intercept(entry,reply):reply
 })
 const all=(root,predicate)=>[...(predicate(root)?[root]:[]),...root.children.flatMap(node=>all(node,predicate))]
 const sampleButtons=()=>all(h.nodes.get('durations'),node=>node.tagName==='button'&&node.textContent==='View run')
 const sample=id=>{
  const choices=sampleButtons().filter(b=>b.getAttribute('aria-label')==='View run '+id)
  assert.equal(choices.length,1,'one explicitly named button in the sample list for '+id)
  const b=choices[0];assert.equal(b.type,'button');assert.equal(b.textContent,'View run');return b
 }
 return {requests,sample,sampleButtons,all,setData:(tuple,revision)=>{select=sampledSnapshot(tuple,revision);return select},
  intercept:fn=>{intercept=fn},click:async(button)=>{await button.fire('click');await tick()}}
}

test('each duration sample opens its exact loaded run and unfiltered task page even when the local run list hides it',()=>harness(async h=>{
 const s=sampleDriver(h);await h.open()
 assert.deepEqual(s.sampleButtons().map(b=>b.getAttribute('aria-label')).sort(),['View run run-a','View run run-b'])
 const durations=h.nodes.get('durations')
 assert.match(durations.textContent,/Checks \/ Measured/);assert.match(durations.textContent,/Show 2 samples/)
 for(const [name,status] of [['run-a','failure'],['run-b','success']]){
  const button=s.sample(name)
  const [row]=s.all(durations,node=>node.tagName==='li'&&s.all(node,child=>child===button).length===1)
  assert.ok(row);assert.ok(row.textContent.includes(name+' ('+status+')'));assert.ok(row.textContent.includes('1 seconds'));assert.ok(row.textContent.includes('2026-01-01T23:59:59Z'))
 }
 const sample=s.sample('run-a')
 const [disclosure]=s.all(durations,node=>node.tagName==='details')
 disclosure.open=true;await disclosure.fire('toggle');assert.deepEqual(s.requests.map(x=>x.kind),['runs','durations'])
 h.nodes.get('query').value='run-b';await h.nodes.get('filters').fire('submit')
 assert.equal(h.nodes.get('runs').children.length,1);assert.match(h.nodes.get('runs').textContent,/run-b/);assert.doesNotMatch(h.nodes.get('runs').textContent,/run-a/)
 assert.deepEqual(s.requests.map(x=>x.kind),['runs','durations'])
 await s.click(sample)
 assert.deepEqual(s.requests.slice(2).map(x=>[x.tuple,x.kind,x.options.runID]),[[scope,'detail','run-a'],[scope,'tasks','run-a']])
 const {signal,...opts}=s.requests[3].options;assert.ok(signal instanceof AbortSignal);assert.deepEqual(opts,{runID:'run-a',revision:1,offset:0,limit:10,status:''})
 assert.equal(h.nodes.get('detail-title').textContent,'run-a');assert.equal(h.nodes.get('detail').open,true)
 assert.match(h.nodes.get('tasks').textContent,/Unmeasured alpha\/ci\/build\/1/);assert.match(h.nodes.get('task-summary').textContent,/2 matching of 2 recorded/)
 await h.nodes.get('detail-close').fire('click')
 const [ordinary]=s.all(h.nodes.get('runs'),node=>node.tagName==='button')
 assert.equal(ordinary.getAttribute('aria-label'),'View run-b');await s.click(ordinary)
 assert.deepEqual(s.requests.slice(4).map(x=>[x.kind,x.options.runID]),[['detail','run-b'],['tasks','run-b']])
 assert.equal(h.nodes.get('detail-title').textContent,'run-b');assert.match(h.nodes.get('task-summary').textContent,/1 matching of 1 recorded/)
}))

test('duplicate duration clicks share one pending read and late detail or task results cannot replace a newer run',async()=>{
 for(const [kind,status] of [['detail',200],['detail',401],['tasks',200],['tasks',412]])await harness(async h=>{
  const s=sampleDriver(h);await h.open();let release
  const held=new Promise(r=>{release=r})
  s.intercept(async(req,value)=>{
   if(req.kind===kind&&req.options.runID==='run-a'){
    await held // Non-cooperating transport deliberately delivers after abort.
    if(status!==200)throw Object.assign(Error('Older owner'),{status})
   }
   return value
  })
  const a=s.sample('run-a'),b=s.sample('run-b')
  const first=a.fire('click');await tick()
  const same=a.fire('click');const card=h.nodes.get('runs').children[0].children[1].fire('click');await tick()
  assert.deepEqual(s.requests.slice(2).map(x=>x.kind),kind==='detail'?['detail']:['detail','tasks'])
  const delayed=s.requests.at(-1);assert.equal(delayed.kind,kind);assert.equal(delayed.options.signal.aborted,false)
  await s.click(b);assert.equal(delayed.options.signal.aborted,true);assert.equal(h.nodes.get('detail-title').textContent,'run-b')
  const n=s.requests.length,shown=h.nodes.get('detail-body').textContent;assert.match(shown,/1 matching of 1 recorded/)
  release();await Promise.all([first,same,card]);await tick()
  assert.equal(s.requests.length,n);assert.deepEqual(s.requests.slice(-2).map(x=>[x.kind,x.options.runID]),[['detail','run-b'],['tasks','run-b']])
  assert.equal(h.nodes.get('panel').hidden,false);assert.equal(h.nodes.get('detail').open,true)
  assert.equal(h.nodes.get('detail-body').textContent,shown);assert.equal(h.nodes.get('detail-title').textContent,'run-b')
  assert.equal(h.calls.includes('clear'),false)
 })
})

test('current sample detail 401, task 412 and inconsistent detail revision clear evidence without fallback or retry',async()=>{
 for(const failure of ['detail401','tasks412','revision'])await harness(async h=>{
  const s=sampleDriver(h);await h.open()
  await s.click(s.sample('run-b'));assert.match(h.nodes.get('task-summary').textContent,/1 matching of 1 recorded/);await h.nodes.get('detail-close').fire('click')
  const button=s.sample('run-a'),before=s.requests.length
  s.intercept((req,value)=>{
   if(failure===req.kind+'401')throw Object.assign(Error('Synthetic current 401'),{status:401})
   if(failure===req.kind+'412')throw Object.assign(Error('Synthetic current 412'),{status:412})
   if(failure==='revision'&&req.kind==='detail')value.source.revision++
   return value
  })
  await s.click(button)
  assert.deepEqual(s.requests.slice(before).map(x=>x.kind),failure==='tasks412'?['detail','tasks']:['detail'])
  assert.equal(h.nodes.get('panel').hidden,true);assert.equal(h.nodes.get('detail').open,false);assert.equal(h.nodes.get('detail-body').textContent,'')
  assert.equal(h.nodes.get('durations').textContent,'');assert.equal(h.nodes.get('runs').textContent,'')
  assert.equal(h.calls.includes('clear'),failure==='detail401');if(failure==='detail401')assert.equal(h.nodes.get('session').hidden,true)
  const n=s.requests.length;await s.click(button);assert.equal(s.requests.length,n)
 })
})

test('a detached sample cannot retarget a reused run ID across workspace, connection, pipeline or snapshot replacement',async()=>{
 for(const changed of ['workspace_id','connection_id','pipeline_id','revision','same revision reload'])await harness(async h=>{
  const s=sampleDriver(h);await h.open();const previous=s.sample('run-a')
  await s.click(previous);const retiredFilter=h.nodes.get('task-status-filter')
  assert.equal(retiredFilter.disabled,false);await h.nodes.get('detail-close').fire('click')
  const tuple={...scope},revision=changed==='revision'?2:1
  if(Object.hasOwn(tuple,changed)){
   tuple[changed]='second';const field={workspace_id:'workspace',connection_id:'connection',pipeline_id:'pipeline'}[changed]
   h.nodes.get(field).value=tuple[changed];await h.nodes.get(field).fire('input')
   const n=s.requests.length;await s.click(previous);assert.equal(s.requests.length,n)
   s.setData(tuple,revision);await h.nodes.get('scope-form').fire('submit');await tick()
  }else{s.setData(tuple,revision);await h.nodes.get('reload').fire('click');await tick()}
  assert.equal(h.nodes.get('panel').hidden,false);const current=s.sample('run-a');assert.notEqual(previous,current)
  const baseline=s.requests.length;await s.click(previous)
  assert.equal(s.requests.length,baseline);assert.equal(h.nodes.get('detail').open,false)
  await s.click(current);const reads=s.requests.slice(baseline)
  assert.deepEqual(reads.map(x=>[x.kind,x.tuple,x.options.runID]),[['detail',tuple,'run-a'],['tasks',tuple,'run-a']]);assert.equal(reads[1].options.revision,revision)
  assert.ok(h.nodes.get('tasks').textContent.includes('Unmeasured '+[tuple.workspace_id,tuple.connection_id,tuple.pipeline_id,revision].join('/')))
  const body=h.nodes.get('detail-body').textContent
  assert.notEqual(h.nodes.get('task-status-filter'),retiredFilter)
  retiredFilter.value='failure';await retiredFilter.fire('change');await tick()
  await s.click(previous);assert.equal(s.requests.length,baseline+2);assert.equal(h.nodes.get('detail').open,true);assert.equal(h.nodes.get('detail-body').textContent,body)
 })
})

test('mounted task cards keep position-specific elapsed, status and original timestamps without promoting duplicate jobs to trends',()=>harness(async h=>{
 const observed='2026-01-01T00:00:02.000005Z', start='2026-01-01T00:00:00Z'
 const stages=[
  {name:'First',jobs:[
   {id:'repeat',name:'Collect',status:'failure',started_at:start,finished_at:'2026-01-01T00:00:01.2Z'},
   {id:'repeat',name:'Collect',status:'running',started_at:start,finished_at:observed}]},
  {name:'Second',jobs:[
   {id:'repeat',name:'Collect',status:'timed_out',started_at:'2026-01-01T00:00:02.000004Z',finished_at:observed},
   {id:'later',name:'Later',status:'cancelled',started_at:start,finished_at:'2026-01-01T00:00:02.000006Z'},
   // The existing JS detail reader can display extra fractional digits; the
   // derived metric cannot silently round them, or discard the whole page.
   {id:'precise',name:'Extra precision',status:'skipped',started_at:'2026-01-01T00:00:00.1234567Z',finished_at:observed}]}]
 const totals=(failure,running,timed_out,cancelled,skipped=0)=>({...counts,failure,running,timed_out,cancelled,skipped})
 const row={...summary,status:'running',observed_at:observed,stage_count:2,job_count:5}
 const items=stages.flatMap((stage,stage_index)=>stage.jobs.map((j,job_index)=>({...j,stage_index,job_index,id_occurrences:j.id==='repeat'?3:1})))
 h.setHook(async(kind,opt)=>{
  if(kind==='runs')return {...responses.runs,items:[row]}
  if(kind==='durations')return {...responses.durations,coverage:{...responses.durations.coverage,
   nonterminal_or_unknown_runs:1,observed_terminal_runs:0,selected_runs:0,runs_with_details:0,excluded_jobs:0}}
  if(kind==='detail')return {scope,source,run:{...scope,id:row.id,status:row.status,observed_at:observed,stages}}
  assert.equal(kind,'tasks');assert.equal(opt.runID,row.id);assert.equal(opt.revision,source.revision)
  const matching=items.filter(j=>!opt.status||j.status===opt.status)
  return {scope,source,run:{id:row.id,status:row.status,observed_at:observed,details_available:true},
   coverage:{stage_count:2,task_count:5,status_counts:totals(1,1,1,1,1),duplicate_id_tasks:3,stage_status_supplied:false},
   stages:stages.map((stage,index)=>({index,name:stage.name,task_count:stage.jobs.length,status_counts:index?totals(0,0,1,1,1):totals(1,1,0,0)})),
   filter:{status:opt.status},total:matching.length,offset:0,limit:10,items:matching}
 })
 await h.open();const before=h.nodes.get('durations').textContent;assert.match(before,/No valid successful-job duration samples/)
 await h.detail()
 assert.equal(h.nodes.get('detail').open,true)
 const cards=h.nodes.get('tasks').children;assert.equal(cards.length,5)
 const expected=['1.2s','unavailable','0.000001s','unavailable','unavailable']
 const check=(card,j,value)=>{
  assert.equal(card.tagName,'article');assert.equal(card.className,'job')
  const lines=card.children.filter(n=>n.tagName==='p').map(n=>n.textContent)
  assert.ok(lines.includes(`Stage ${j.stage_index+1}, task ${j.job_index+1} · ${j.id} · ${j.status}`))
  assert.ok(lines.includes('Started: '+j.started_at));assert.ok(lines.includes('Finished: '+j.finished_at))
  assert.equal(lines.some(line=>line.includes('Ambiguous provider ID: 3 occurrences')),j.id==='repeat')
  assert.deepEqual(card.children.filter(n=>n.className==='task-elapsed').map(n=>n.textContent),['Recorded elapsed: '+value])
 }
 cards.forEach((card,index)=>check(card,items[index],expected[index]))
 assert.equal(h.nodes.get('durations').textContent,before)
 const filter=h.nodes.get('task-status-filter');filter.value='timed_out';await filter.fire('change')
 const filtered=h.nodes.get('tasks').children;assert.equal(filtered.length,1);check(filtered[0],items[2],expected[2])
 assert.equal(h.nodes.get('durations').textContent,before)
 assert.deepEqual(h.calls,['runs','durations','detail','tasks','tasks'])
}))

test('duration coordinates use recorded instants, retain ties/fractions/zero, and never synthesize missing samples',()=>{
 const sample=(id,start,seconds)=>({run_id:id,started_at:start,duration_seconds:seconds})
 const base='2026-01-01T00:00:00.'
 const a=sample('a',base+'000000Z',0),b=sample('b',base+'000001Z',0.1),c=sample('c',base+'000004Z',0.05)
 const input=[c,a,b],points=durationPoints(input)
 assert.deepEqual(points.map(p=>p.sample),[a,b,c]);assert.deepEqual(input,[c,a,b])
 assert.deepEqual(points.map(p=>[p.x,p.y]),[[18,106],[134,18],[482,62]])
 const tied=sample('z','2026-01-01T01:00:00+01:00',0)
 assert.deepEqual(durationPoints([tied,a]).map(p=>[p.sample.run_id,p.x,p.y]),[['a',250,106],['z',250,106]])
 for(const rows of [[],[a],[a,sample('bad','2026-02-30T12:00:00Z',3)]])assert.deepEqual(durationPoints(rows),[])
})

test('plot selection is local and stale controls cannot target repeating run IDs after reload or sign-out',()=>harness(async h=>{
 const s=sampleDriver(h);await h.open()
 function controls(){
  const [chart]=s.all(h.nodes.get('durations'),n=>n.tagName==='figure')
  return ['input','button'].map(tag=>s.all(chart,n=>n.tagName===tag)[0])
 }
 const [oldRange,oldView]=controls()
 assert.equal(oldView.getAttribute('aria-label'),'View selected run run-b')
 oldRange.value='0';await oldRange.fire('input');assert.equal(oldView.getAttribute('aria-label'),'View selected run run-a')
 assert.deepEqual(s.requests.map(r=>r.kind),['runs','durations'])
 s.setData(scope,2);await h.nodes.get('reload').fire('click')
 oldRange.value='1';await oldRange.fire('input');await s.click(oldView)
 const [range,view]=controls();assert.equal(s.requests.length,4);assert.equal(view.getAttribute('aria-label'),'View selected run run-b')
 range.value='0';await range.fire('input');await s.click(view)
 assert.deepEqual(s.requests.slice(-2).map(r=>[r.kind,r.options.runID]),[['detail','run-a'],['tasks','run-a']]);assert.equal(s.requests.at(-1).options.revision,2)
 h.page.clear();await s.click(view);assert.equal(s.requests.length,6);assert.equal(h.nodes.get('durations').textContent,'')
}))

test('a denied chart drilldown clears both trend and fallback, without retry',()=>harness(async h=>{
 const s=sampleDriver(h);await h.open()
 const [view]=s.all(h.nodes.get('durations'),n=>n.tagName==='button'&&n.textContent==='View selected run')
 s.intercept((req,data)=>{if(req.kind==='detail')throw Object.assign(Error('Denied'),{status:403});return data})
 await s.click(view);assert.deepEqual(s.requests.map(r=>r.kind),['runs','durations','detail'])
 assert.equal(h.nodes.get('durations').textContent,'');assert.equal(h.nodes.get('panel').hidden,true);assert.equal(h.nodes.get('detail').open,false)
}))
