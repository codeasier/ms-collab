import test from 'node:test'
import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
const {createScenarioRouter}=createRequire(import.meta.url)('./scenario-router.cjs')
const origin='https://synthetic.example.invalid'
function route(path){
 const calls=[];let handled=false
 const finish=kind=>{assert.equal(handled,false,'a request must have exactly one terminal owner');handled=true;calls.push(kind)}
 return {calls,request:()=>({url:()=>origin+path}),continue:async()=>finish('continue'),abort:async()=>finish('abort'),fulfill:async()=>finish('fulfill')}
}
test('single dispatcher handles same-origin default and blocks external before scenarios',async()=>{
 let external=0;const router=createScenarioRouter({origin,onExternal:()=>external++}),inside=route('/api/invitations'),outside=route('/api/invitations')
 outside.request=()=>({url:()=>'https://other.example.invalid/api/invitations'})
 let called=0;router.set('/api/invitations',async r=>{called++;await r.fulfill()})
 await router.handle(inside);await router.handle(outside);router.remove('/api/invitations')
 const normal=route('/api/invitations');await router.handle(normal);await router.drain()
 assert.equal(called,1);assert.equal(external,1);assert.deepEqual(inside.calls,['fulfill']);assert.deepEqual(outside.calls,['abort']);assert.deepEqual(normal.calls,['continue'])
})
test('remove and replace during a pending handler never falls through or handles twice',async()=>{
 const router=createScenarioRouter({origin});let release,entered
 const barrier=new Promise(r=>release=r),started=new Promise(r=>entered=r),old=route('/api/invitations')
 router.set('/api/invitations',async r=>{entered();await barrier;await r.fulfill()})
 const pending=router.handle(old);await started;router.remove('/api/invitations')
 router.set('/api/invitations',r=>r.abort());const newer=route('/api/invitations');await router.handle(newer)
 let drained=false;const draining=router.drain().then(()=>drained=true);await Promise.resolve();assert.equal(drained,false)
 release();await pending;await draining;assert.deepEqual(old.calls,['fulfill']);assert.deepEqual(newer.calls,['abort']);assert.equal(drained,true)
})
test('scenario failures remain failures, not ignored errors or fallback retries',async()=>{
 const router=createScenarioRouter({origin}),request=route('/api/invitations')
 router.set('/api/invitations',async()=>{throw new Error('synthetic failure')})
 await assert.rejects(router.handle(request),/synthetic failure/);assert.deepEqual(request.calls,[])
 await router.drain()
})
