// Copyright (c) 2026 codeasier; SPDX-License-Identifier: MIT
import test from 'node:test';
import assert from 'node:assert/strict';
import {Client,ViewController,ViewError,scopeOf,queryOptions,validateList,validateSummary,validateDetail,messageFor} from '../client.mjs';
import {scope,options,list,summary,detail,response,deferred} from './fixture.mjs';
const clone = structuredClone;
const rejectMutations = (factory,validate,mutations) => {
  for (const mutate of mutations) { const v=factory(); mutate(v); assert.throws(()=>validate(v),ViewError,mutate.toString()); }
};
const controller = (api = {}) => {
  const events=[];
  const client={list:async(s,q)=>list(s,q),summary:async(s,r)=>summary(s,2,r),detail:async(s,id,r)=>detail(s,id,r),...api};
  return {view:new ViewController(client,state=>events.push(state)),events};
};

test('scope and query building reject path injection and unbounded/unstable pagination',()=>{
  assert.deepEqual(scopeOf(scope),scope); assert.notEqual(scopeOf(scope),scope);
  for(const value of ['../alpha','alpha/x','a?b','a#b','a%2fb','','a'.repeat(129)]) assert.throws(()=>scopeOf({...scope,workspace_id:value}),ViewError);
  assert.throws(()=>scopeOf({...scope,token:'never'}),ViewError);
  for(const q of [{limit:0},{limit:101},{limit:1.2},{status:'completed'},{source:'external'},{branch:'bad\nbranch'}]) assert.throws(()=>queryOptions(q),ViewError);
  for(const offset of [-1,501,1.1]) assert.throws(()=>queryOptions({},offset,1),ViewError);
  assert.throws(()=>queryOptions({},20),ViewError);
  assert.deepEqual(queryOptions({limit:'5',status:'success'},5,4),{offset:5,limit:5,status:'success',revision:4});
});

test('list accepts explicit saved snapshot and literal branch text, not added execution capabilities',()=>{
  const v=list(); v.items[0].branch='<img src=x onerror=alert(1)>';
  assert.equal(validateList(v,scope,options),v);
  rejectMutations(list,v=>validateList(v,scope,options),[
    v=>v.scope.workspace_id='beta',v=>v.scope.extra='hidden',v=>v.private_log='hidden',
    v=>v.available='true',v=>v.source.kind='live',v=>v.source.live_provider_verified=true,
    v=>v.source.full_history_complete=true,v=>v.source.execution_enabled=true,v=>v.source.revision=0,
    v=>v.source.observed_at='2026-01-02T12:00:00',v=>v.source.observed_at='not-a-dateZ',
    v=>v.capabilities.read=false,v=>v.capabilities.retry=true,v=>v.capabilities.download=true,
    v=>v.capabilities.refresh=true,v=>v.capabilities.token='hidden',v=>v.total=501,
    v=>v.offset=1,v=>v.limit=19,v=>v.items.pop(),v=>v.next_offset=2,
    v=>v.items[1].id=v.items[0].id,v=>v.items[0].status='complete',v=>v.items[0].source='network',
    v=>v.items[0].details_available=false,v=>v.items[0].stages_available=1,
    v=>v.items[0].summary_available=null,v=>v.items[0].branch='é'.repeat(201),
    v=>v.items[0].created_at='invalidZ',v=>v.items[0].report_url='https://invalid.example/'
  ]);
});

test('list binds revision, exact filters and stable page cardinality',()=>{
  assert.throws(()=>validateList(list(),scope,{...options,revision:2}),ViewError);
  for(const filter of [{status:'failed'},{source:'web'},{branch:'other'}]) assert.throws(()=>validateList(list(),scope,{...options,...filter}),ViewError);
  const q={offset:20,limit:20,revision:3}, page=list(scope,q,23,3);
  assert.equal(validateList(page,scope,q).items.length,3);
  assert.equal(page.next_offset,null);
  assert.throws(()=>validateList({...page,next_offset:40},scope,q),ViewError);
});

test('unpublished and published-empty snapshots are distinct',()=>{
  const unpublished=list(scope,options,0,0), empty=list(scope,options,0,1);
  assert.equal(validateList(unpublished,scope,options).available,false);
  assert.equal(validateList(empty,scope,options).available,true);
  assert.equal(validateSummary(summary(scope,0,0),scope,0).total,0);
  unpublished.source.observed_at='2026-01-02T12:00:00Z';
  assert.throws(()=>validateList(unpublished,scope,options),ViewError);
  assert.throws(()=>validateDetail(detail(scope,'task-1',0),scope,'task-1',0),ViewError);
});

test('summary is exact, bounded, consistent and explicitly non-live',()=>{
  assert.equal(validateSummary(summary(),scope,1).total,2);
  rejectMutations(summary,v=>validateSummary(v,scope,1),[
    v=>v.scope.repository_id='other',v=>v.source.revision=2,v=>v.total=3,
    v=>v.states.success=-1,v=>v.states.pending=0.5,v=>v.states.success=501,
    v=>v.states.other=0,v=>delete v.states.unknown,v=>v.interpretation='live_queue'
  ]);
});

test('detail preserves actual/requested commits and absent evidence without inference',()=>{
  const v=detail(); assert.equal(validateDetail(v,scope,'task-1',1),v);
  assert.notEqual(v.task.commit,v.task.requested_commit);
  Object.assign(v.task,{status:'pending',started_at:null,finished_at:null,duration_seconds:null,commit:null,summary:null,stages:null});
  assert.equal(validateDetail(v,scope,'task-1',1).task.summary,null);
  v.task.stages=[]; assert.deepEqual(validateDetail(v,scope,'task-1',1).task.stages,[]);
});

test('detail rejects identity, timestamp, state, stage and summary contradictions',()=>{
  rejectMutations(detail,v=>validateDetail(v,scope,'task-1',1),[
    v=>v.task.id='task-2',v=>v.source.revision=2,v=>v.task.report='private',
    v=>v.task.created_at='2026-01-03T11:00:00Z',v=>v.task.started_at='2026-01-02T10:00:00Z',
    v=>v.task.finished_at='2026-01-03T11:00:00Z',v=>v.task.finished_at='2026-01-02T11:00:30Z',
    v=>v.task.duration_seconds=61,v=>v.task.duration_seconds=Infinity,
    v=>v.task.started_at=null,v=>v.task.status='pending',v=>v.task.status='running',
    v=>v.task.commit='abc',v=>v.task.requested_commit='A'.repeat(40),
    v=>v.task.summary.total=4,v=>v.task.summary.passed=-1,v=>v.task.summary.extra=0,
    v=>{v.task.summary.failed=1;v.task.summary.total=4;},
    v=>v.task.stages.push(clone(v.task.stages[0])),v=>v.task.stages[0].status='completed',
    v=>v.task.stages[0].name='bad\nname',v=>v.task.stages[0].duration_seconds=-1,
    v=>v.task.stages=Array.from({length:21},(_,i)=>({...v.task.stages[0],id:`stage-${i}`}))
  ]);
});

test('cookie transport sends only fixed same-origin routes and no bearer identity',async()=>{
  const calls=[];
  const client=new Client(async(path,init)=>{calls.push({path,init});
    if(path==='/auth/session') return response({principal_id:'synthetic:alice'});
    if(path.includes('/summary')) return response(summary());
    if(path.includes('/tasks/task-1')) return response(detail());
    return response(list());
  });
  const signal=new AbortController().signal;
  assert.equal(await client.session(signal),'synthetic:alice');
  await client.list(scope,options,signal); await client.summary(scope,1,signal); await client.detail(scope,'task-1',1,signal);
  assert.equal(calls[1].path,'/api/workspaces/alpha/gui-smoke/panel/repo/tasks?offset=0&limit=20');
  assert.equal(calls[2].path,'/api/workspaces/alpha/gui-smoke/panel/repo/summary?revision=1');
  assert.equal(calls[3].path,'/api/workspaces/alpha/gui-smoke/panel/repo/tasks/task-1?revision=1');
  for(const {path,init} of calls) {
    assert.ok(path.startsWith('/')); assert.equal(init.method,'GET'); assert.equal(init.credentials,'same-origin');
    assert.equal(init.mode,'same-origin'); assert.equal(init.redirect,'error'); assert.equal(init.cache,'no-store');
    assert.equal(init.signal,signal); assert.equal(init.headers,undefined); assert.equal(init.body,undefined);
  }
  await assert.rejects(()=>client.detail(scope,'../other',1),ViewError);
});

test('response decoding refuses redirects, HTML, malformed envelopes and oversized bytes',async()=>{
  const responses=[
    ()=>new Response('<html>secret</html>',{headers:{'Content-Type':'text/html'}}),
    ()=>new Response('{bad',{headers:{'Content-Type':'application/json'}}),
    ()=>new Response(new Uint8Array([0xff]),{headers:{'Content-Type':'application/json'}}),
    ()=>new Response(JSON.stringify({data:{principal_id:'alice'},token:'secret'}),{headers:{'Content-Type':'application/json'}}),
    ()=>response({principal_id:'alice',token:'secret'}),
    ()=>response({principal_id:'../alice'}),
    ()=>new Response(' '.repeat(1024*1024+1),{headers:{'Content-Type':'application/json'}}),
    ()=>{const r=response({principal_id:'alice'});Object.defineProperty(r,'redirected',{value:true});return r;},
    ()=>({ok:true,redirected:false,headers:new Headers({'Content-Type':'application/json'}),body:null})
  ];
  for(const make of responses) await assert.rejects(()=>new Client(async()=>make()).session(),ViewError);
});

test('errors preserve only status, never server message or hidden contents',async()=>{
  const client=new Client(async()=>new Response('private provider credential',{status:503}));
  await assert.rejects(()=>client.session(),error=>error instanceof ViewError && error.status===503 && !error.message.includes('credential'));
  assert.match(messageFor(new ViewError(412)),/snapshot changed/);
  assert.match(messageFor(new ViewError(401)),/Sign in/);
  assert.doesNotMatch(messageFor(new Error('private')),/private/);
});

test('login and logout use explicit cookie CSRF protocol without exposing a token',async()=>{
  const calls=[],csrf='x'.repeat(32),url='https://provider.example.invalid/authorize?response_type=code&code_challenge_method=S256&state=synthetic';
  const client=new Client(async(path,init)=>{calls.push({path,init});return response(path==='/auth/start'?{authorization_url:url}:{logged_out:true});});
  assert.equal(await client.signIn(),url); assert.equal(await client.signIn(csrf),url); await client.signOut(csrf);
  assert.deepEqual(calls[0].init.headers,{'Content-Type':'application/json','X-MS-Collab-Login':'1'});
  assert.deepEqual(calls[1].init.headers,{'Content-Type':'application/json','X-MS-Collab-Login':'1','X-CSRF-Token':csrf});
  assert.deepEqual(calls[2].init.headers,{'Content-Type':'application/json','X-CSRF-Token':csrf});
  for(const {init} of calls) {assert.equal(init.method,'POST');assert.equal(init.body,'{}');assert.equal(init.credentials,'same-origin');assert.equal(init.headers.Authorization,undefined);}
  for(const bad of [null,'short','bad\r\nvalue','a'.repeat(513)]) await assert.rejects(()=>client.signOut(bad),ViewError);
  assert.equal(calls.length,3);
});

test('authentication replies reject insecure or credential-bearing redirects and false logout',async()=>{
  const suffix='?response_type=code&code_challenge_method=S256';
  for(const authorization_url of ['http://provider.invalid/'+suffix,'https://user:pass@provider.invalid/'+suffix,'https://provider.invalid/'+suffix+'#secret','javascript:alert(1)','https://provider.invalid/?response_type=token','https://provider.invalid/?response_type=code&code_challenge_method=plain']) {
    await assert.rejects(()=>new Client(async()=>response({authorization_url})).signIn(),ViewError);
  }
  await assert.rejects(()=>new Client(async()=>response({authorization_url:'https://provider.invalid/'+suffix,access_token:'secret'})).signIn(),ViewError);
  for(const v of [{logged_out:false},{logged_out:true,token:'secret'},{}]) await assert.rejects(()=>new Client(async()=>response(v)).signOut('x'.repeat(32)),ViewError);
});

test('controller publishes nothing until list and summary agree at one revision',async()=>{
  const wait=deferred(),seen=[];
  const {view}=controller({summary:async(s,r)=>{seen.push(r);return wait.promise;}});
  const work=view.load(scope); await Promise.resolve();
  assert.equal(view.state.phase,'loading');assert.equal(view.view,null);assert.deepEqual(seen,[1]);
  wait.resolve(summary());assert.equal(await work,true);assert.equal(view.state.phase,'list');
});

test('controller clears on cross-endpoint revision, observation, count or authority failures',async()=>{
  const cases=[v=>v.source.revision=2,v=>v.source.observed_at='2026-01-02T12:01:00Z',v=>v.total=3,v=>v.available=false];
  for(const mutate of cases) {
    const v=summary();mutate(v);const {view}=controller({summary:async()=>v});
    assert.equal(await view.load(scope),false);assert.equal(view.state.phase,'error');assert.equal(view.view,null);
  }
  for(const status of [401,404,412,503]) {
    const {view}=controller({summary:async()=>{throw new ViewError(status);}});
    assert.equal(await view.load(scope),false);assert.equal(view.state.error.status,status);assert.equal(view.view,null);
  }
});

test('controller pins later pages and rejects changed totals or filters',async()=>{
  const calls=[];let count=23;
  const {view}=controller({list:async(s,q)=>{calls.push(clone(q));return list(s,q,count);},summary:async(s,r)=>summary(s,count,r)});
  assert.equal(await view.load(scope),true);assert.equal(await view.page(20),true);
  assert.equal(calls[1].revision,1);assert.equal(view.view.list.items.length,3);
  count=24;assert.equal(await view.page(20),false);assert.equal(view.view,null);
  assert.equal(await view.load(scope),true);
  assert.equal(await view.load(scope,{limit:20,status:'failed'},20),false);assert.equal(view.view,null);
  const fresh=controller().view;assert.equal(await fresh.load(scope,{},20),false);
});

test('new scope supersedes uncooperative old responses without rendering old data',async()=>{
  const slow=deferred(),beta={...scope,workspace_id:'beta'},signals=[];
  const {view,events}=controller({list:async(s,q,signal)=>{signals.push(signal);return s.workspace_id==='alpha'?slow.promise:list(s,q);}});
  const old=view.load(scope),current=view.load(beta);
  assert.equal(signals[0].aborted,true);assert.equal(await current,true);
  slow.resolve(list());assert.equal(await old,false);
  assert.equal(view.view.scope.workspace_id,'beta');
  assert.equal(events.filter(e=>e.phase==='list').length,1);
});

test('clear cancels loading and late error cannot overwrite a new successful scope',async()=>{
  const slow=deferred();const {view}=controller({list:async(s,q)=>s.workspace_id==='alpha'?slow.promise:list(s,q)});
  const old=view.load(scope);view.clear();assert.equal(view.view,null);assert.equal(view.state.phase,'idle');
  await view.load({...scope,workspace_id:'beta'});slow.reject(new ViewError(401));
  assert.equal(await old,false);assert.equal(view.state.phase,'list');assert.equal(view.view.scope.workspace_id,'beta');
});

test('clear during summary and detail cannot resurrect in-memory content',async()=>{
  const waiting=deferred(),{view}=controller({summary:async()=>waiting.promise});
  const load=view.load(scope);await Promise.resolve();view.clear();waiting.resolve(summary());
  assert.equal(await load,false);assert.equal(view.state.phase,'idle');assert.equal(view.view,null);
  const details=deferred(),second=controller({detail:async()=>details.promise}).view;
  await second.load(scope);const work=second.detail('task-1');second.clear();details.resolve(detail());
  assert.equal(await work,false);assert.equal(second.state.phase,'idle');assert.equal(second.view,null);
});

test('detail is scoped to current page and fixed version; Back suppresses a late reply',async()=>{
  const wait=deferred(),calls=[];
  const {view}=controller({detail:async(...args)=>{calls.push(args);return wait.promise;}});
  await view.load(scope);assert.equal(await view.detail('not-on-this-page'),false);assert.equal(calls.length,0);
  const work=view.detail('task-1');assert.equal(view.state.phase,'loading-detail');
  assert.deepEqual(calls[0].slice(0,3),[scope,'task-1',1]);view.back();assert.equal(calls[0][3].aborted,true);
  wait.resolve(detail());assert.equal(await work,false);assert.equal(view.state.phase,'list');
});

test('detail changed snapshot fails closed; competing details retain only latest',async()=>{
  const bad=detail();bad.source.observed_at='2026-01-02T12:01:00Z';
  const first=controller({detail:async()=>bad}).view;await first.load(scope);
  assert.equal(await first.detail('task-1'),false);assert.equal(first.view,null);assert.equal(first.state.phase,'error');
  const slow=deferred(),{view}=controller({detail:async(s,id,r)=>id==='task-1'?slow.promise:detail(s,id,r)});
  await view.load(scope);const older=view.detail('task-1');assert.equal(await view.detail('task-2'),true);
  slow.reject(new ViewError(404));assert.equal(await older,false);
  assert.equal(view.state.phase,'detail');assert.equal(view.state.detail.task.id,'task-2');
});
