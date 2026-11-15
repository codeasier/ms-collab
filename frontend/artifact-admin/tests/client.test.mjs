import test from 'node:test'
import assert from 'node:assert/strict'
import {createHash} from 'node:crypto'
import {candidateDigest,createClient,LatestOperation,scopeFrom,validateStatus,validateReview,validateMutation} from '../client.mjs'
const scope=scopeFrom('alpha','source','bucket')
const clone=x=>structuredClone(x)
const snapshot={schema:1,scope,observed_at:'2026-01-02T00:00:00+00:00',offset_minutes:0,artifacts:[{key:'builds/<svg onload=alert(1)>空间😀.txt',modified_at:'2026-01-01T23:00:00+00:00',size:12,is_dir:false}]}
async function review(){return {scope,revision:2,candidate_digest:await candidateDigest(snapshot),snapshot:clone(snapshot),readers:['admin','reader'],complete:true,count:1}}
const status={scope,revision:2,published_revision:null,pending:true,capabilities:{refresh:false,review:true,approve:true}}

test('full preview validates scope, complete rows and exact ASCII canonical digest',async()=>{
 const value=await review();assert.equal(await validateReview(value,scope,2),value)
 // Independent expected Python-compatible canonical representation, including surrogate pairs.
 const raw='{"artifacts":[{"is_dir":false,"key":"builds/<svg onload=alert(1)>\\u7a7a\\u95f4\\ud83d\\ude00.txt","modified_at":"2026-01-01T23:00:00+00:00","size":12}],"observed_at":"2026-01-02T00:00:00+00:00","offset_minutes":0,"schema":1,"scope":{"bucket_id":"bucket","connection_id":"source","workspace_id":"alpha"}}'
 assert.equal(value.candidate_digest,createHash('sha256').update(raw).digest('hex'))
})
test('missing row even with adjusted count cannot approve an unseen full digest',async()=>{
 const value=await review();value.snapshot.artifacts=[];value.count=0
 await assert.rejects(validateReview(value,scope,2))
})
test('missing, duplicate, cross-scope, malformed or partial preview fails closed',async()=>{
 const changes=[v=>v.complete=false,v=>v.count=2,v=>v.revision=3,v=>v.snapshot.scope.workspace_id='beta',v=>v.scope.bucket_id='other',v=>v.readers=[],v=>v.readers=['reader','admin'],v=>v.readers=['admin','admin'],v=>v.readers=['admin\n'],v=>v.snapshot.artifacts.push(clone(v.snapshot.artifacts[0])),v=>v.snapshot.artifacts[0].size=-1,v=>v.snapshot.artifacts[0].key='/escape',v=>v.snapshot.artifacts[0].key='builds/../x',v=>v.snapshot.artifacts[0].is_dir=true,v=>v.snapshot.artifacts[0].modified_at='2027-01-01T00:00:00Z',v=>v.snapshot.artifacts[0].key='bad\ud800',v=>v.snapshot.secret='DO_NOT_LEAK',v=>delete v.readers]
 for(const change of changes){const value=clone(await review());change(value);await assert.rejects(validateReview(value,scope,2))}
})
test('capabilities and revisions cannot be inferred or coerced',()=>{
 assert.equal(validateStatus(status,scope),status)
 for(const value of [{...status,revision:true},{...status,published_revision:3},{...status,capabilities:{refresh:false,review:false,approve:true}},{...status,capabilities:{refresh:false,review:false,approve:false}},{...status,pending:'yes'},{...status,scope:{...scope,bucket_id:'other'}}])assert.throws(()=>validateStatus(value,scope))
 for(const bad of ['alpha\n','../x','','space name'])assert.throws(()=>scopeFrom(bad,'source','bucket'))
})
test('mutations require exact next revision and compatible bounded result',()=>{
 validateMutation({scope,revision:3,publication:'approved'},scope,2,'approve')
 validateMutation({scope,revision:2,publication:'pending',candidate_digest:'a'.repeat(64),scanned:500,pages:10,retained:500},scope,1,'refresh')
 for(const value of [{scope,revision:4,publication:'approved'},{scope,revision:3,publication:'pending'},{scope:{...scope,workspace_id:'other'},revision:3,publication:'approved'}])assert.throws(()=>validateMutation(value,scope,2,'approve'))
})
test('credentials stay in opaque headers; mutations include exact If-Match and never auto retry',async()=>{
 const calls=[],client=createClient({fetcher:async(...args)=>{calls.push(args);return {ok:true,text:async()=>JSON.stringify({data:status})}}})
 client.connect('synthetic-admin')
 await client.request(scope,'status')
 await client.request(scope,'refresh',{revision:2,body:{}})
 await client.request(scope,'review',{revision:2})
 assert.equal(calls.length,3)
 for(const [url,opts] of calls){assert.ok(!url.includes('synthetic-admin'));assert.equal(opts.headers.Authorization,'Bearer synthetic-admin');assert.equal(opts.credentials,'omit');assert.equal(opts.redirect,'error');assert.equal(opts.cache,'no-store')}
 assert.equal(calls[1][1].headers['If-Match'],'"2"');assert.equal(calls[1][1].body,'{}');assert.equal(calls[2][1].headers['If-Match'],'"2"')
 client.clear();await assert.rejects(client.request(scope,'status'));assert.equal(calls.length,3)
 for(const v of ['x\n','x\u0000','x y',''])assert.throws(()=>client.connect(v))
})
test('HTTP failures are value-free and never retried; invalid envelopes rejected',async()=>{
 let calls=0
 const client=createClient({fetcher:async()=>{calls++;return {ok:false,status:412,text:async()=>'PRIVATE'}}});client.connect('synthetic-admin')
 await assert.rejects(client.request(scope,'approve',{revision:2,body:{}}),e=>e.status===412&&!e.message.includes('PRIVATE'));assert.equal(calls,1)
 const malformed=createClient({fetcher:async()=>({ok:true,text:async()=>'{}'})});malformed.connect('synthetic-admin');await assert.rejects(malformed.request(scope,'status'))
})
test('generation fencing still rejects delayed work when fetch ignores abort',async()=>{
 const gate=new LatestOperation(),first=gate.begin(),second=gate.begin()
 assert.equal(first.signal.aborted,true);assert.equal(gate.current(first),false);assert.equal(gate.current(second),true)
 gate.clear();assert.equal(gate.current(second),false)
 const third=gate.begin();assert.equal(gate.current(first),false);assert.equal(gate.current(third),true)
})
