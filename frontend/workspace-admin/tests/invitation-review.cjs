// Invitation-specific real-page journeys, invoked by both existing browser entry
// scripts. Accepted review/create/replay and stale-proof 412 use the real API.
const assert=require('node:assert/strict'),path=require('node:path')
const within=async promise=>{let timer;try{return await Promise.race([promise,new Promise((_,reject)=>timer=setTimeout(()=>reject(Error('Invitation review barrier timeout')),15000))])}finally{clearTimeout(timer)}}
const settle=page=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))))
async function exerciseInvitationReview({page,router,ready,reconnect,folder,readable,inspectState,invalidations=[]}){
 const viewport=page.viewportSize()
 const collection='/api/workspaces/alpha/invitations',selection=target=>collection+'/selections/'+target+'/viewer',long='p'.repeat(200),requests=[],releases=new Set(),routes=new Set()
 const record=request=>{const pathname=new URL(request.url()).pathname;if(pathname===collection||pathname.startsWith(collection+'/selections/'))requests.push({path:pathname,method:request.method(),headers:request.headers(),body:request.postData()})}
 const set=(pathname,handler)=>{routes.add(pathname);router.set(pathname,handler)},remove=pathname=>{router.remove(pathname);routes.delete(pathname)}
 const hold=(pathname,handler)=>{
  let seen,release,done;const started=new Promise(r=>seen=r),gate=new Promise(r=>release=r),finished=new Promise(r=>done=r)
  releases.add(release)
  set(pathname,async route=>{if(pathname===collection&&route.request().method()!=='POST')return route.continue();const response=await handler(route);seen();await gate;await route.fulfill(response);done()})
  return{started,finished,release:()=>{release();releases.delete(release)}}
 }
 const fill=async(target='admin')=>{await ready();await page.locator('#invite-open').click();await page.locator('#invite-principal').fill(target);await page.locator('#invite-role').selectOption('viewer')}
 const review=async()=>{await page.locator('#invite-review').click();await page.waitForFunction(()=>!document.querySelector('#invite-save').disabled)}
 const writes=()=>requests.filter(r=>r.method==='POST')
 const noReview=async()=>{assert.equal(await page.locator('#invite-selection').textContent(),'');assert.equal(await page.locator('#invite-save').isDisabled(),true)}
 const reset=async()=>{await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide')));await reconnect()}
 const synthetic=(status,data)=>({status,contentType:'application/json',body:JSON.stringify(status===200?{data}:{error:status===401?'authentication_required':'synthetic_rejection'})})
 page.on('request',record)
 try{
  await page.setViewportSize({width:1440,height:1000})
  await fill(long);const start=requests.length
  await noReview();await page.evaluate(()=>document.querySelector('#invite-form').dispatchEvent(new Event('submit',{cancelable:true})));await settle(page)
  assert.equal(requests.length,start,'no Review: neither GET nor POST')
  await review();assert.equal(writes().length,0);assert.equal(requests.length,start+1)
  const text=await page.locator('#invite-selection').textContent();assert.ok(text.includes(long));assert.match(text,/Workspace: alpha\. Role: viewer/);assert.ok(!text.includes('irs1-'))
  if(readable)await readable(page,['#invite-dialog h2','#invite-principal','#invite-role','#invite-review','#invite-selection','#invite-save','#invite-cancel','#invite-status'])
  await page.screenshot({path:path.join(folder,'invitation-review-desktop.png'),fullPage:true})
  await page.setViewportSize({width:390,height:844})
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth&&document.querySelector('#invite-dialog').scrollWidth<=document.querySelector('#invite-dialog').clientWidth))
  await page.locator('#invite-status').scrollIntoViewIfNeeded()
  assert.ok(await page.evaluate(()=>['#invite-save','#invite-cancel','#invite-status'].every(selector=>{const box=document.querySelector(selector).getBoundingClientRect(),clip=document.querySelector('#invite-dialog').getBoundingClientRect();return box.width>0&&box.height>0&&box.left>=0&&box.right<=innerWidth&&box.top>=0&&box.bottom<=innerHeight&&box.top>=clip.top&&box.bottom<=clip.bottom})))
  assert.equal(await page.locator('#invite-principal').inputValue(),long);assert.equal(await page.locator('#invite-save').isEnabled(),true)
  if(readable)await readable(page,['#invite-selection','#invite-save','#invite-cancel','#invite-status'])
  await page.screenshot({path:path.join(folder,'invitation-review-mobile.png'),fullPage:true});await page.setViewportSize(viewport)
  await page.locator('#invite-principal').fill('reader');await page.locator('#invite-principal').fill(long);await noReview()
  await review();await page.locator('#invite-role').selectOption('developer');await page.locator('#invite-role').selectOption('viewer');await noReview()
  await page.locator('#invite-cancel').click();assert.equal(writes().length,0)
  await fill();await review();await page.keyboard.press('Escape');await noReview();assert.equal(await page.locator('#invite-dialog').isVisible(),false)
  // Review freshness across reload, scope change and caller-supported navigation.
  for(const invalidate of [()=>page.evaluate(()=>document.querySelector('#reload').dispatchEvent(new Event('click'))),()=>page.evaluate(()=>document.querySelector('#workspace').dispatchEvent(new Event('change'))),...invalidations]){
   await fill();await review();await invalidate();await reconnect();await noReview()
  }
  // A cancelled delayed review is not restored into a reopened identical form.
  await fill();const cancelled=hold(selection('admin'),async route=>({response:await route.fetch()}))
  await page.locator('#invite-review').click();await within(cancelled.started);await page.locator('#invite-cancel').click();await fill()
  cancelled.release();await within(cancelled.finished);await settle(page);await noReview();assert.equal(await page.locator('#invite-review').isEnabled(),true)
  await page.locator('#invite-cancel').click();remove(selection('admin'))
  // A new explicit Review clears a completed review before any result. A failed
  // refresh must not quietly restore the old Send permission.
  await fill();await review();set(selection('admin'),route=>route.fulfill(synthetic(409)));await page.locator('#invite-review').click()
  await page.waitForFunction(()=>!document.querySelector('#invite-review').disabled);await noReview();await page.locator('#invite-cancel').click();await within(router.drain());remove(selection('admin'))
  // Wrong full response tuples/envelopes never unlock Send.
  for(const data of [{workspace_id:'other',principal_id:'admin',role:'viewer',precondition:'irs1-'+'a'.repeat(64)},{workspace_id:'alpha',principal_id:'reader',role:'viewer',precondition:'irs1-'+'a'.repeat(64)},{workspace_id:'alpha',principal_id:'admin',role:'developer',precondition:'irs1-'+'a'.repeat(64)},{workspace_id:'alpha',principal_id:'admin',role:'viewer',precondition:'irs1-'+'a'.repeat(64),extra:true}]){
   set(selection('admin'),route=>route.fulfill(synthetic(200,data)));await fill();await page.locator('#invite-review').click();await page.waitForFunction(()=>document.querySelector('#invite-status').textContent.includes('Unable to verify'));await noReview();await page.locator('#invite-cancel').click();await within(router.drain());remove(selection('admin'))
  }
  // Release an ignored-abort old GET while a newer GET is still held: neither
  // old success, error nor 401 may unlock Send or disconnect the current page.
  for(const status of [200,409,401]){
   await fill();const old=hold(selection('admin'),async route=>status===200?{response:await route.fetch()}:synthetic(status))
   await page.locator('#invite-review').click();await within(old.started)
   const oldCount=requests.length;await page.evaluate(()=>document.querySelector('#invite-review').dispatchEvent(new Event('click')));await settle(page);assert.equal(requests.length,oldCount)
   await page.locator('#invite-principal').fill('reader')
   const next=hold(selection('reader'),async route=>({response:await route.fetch()}));await page.locator('#invite-review').click();await within(next.started)
   old.release();await within(old.finished);await settle(page);await noReview();assert.equal(await page.locator('#invite-review').isDisabled(),true);assert.equal(await page.locator('#session').isVisible(),true)
   next.release();await within(next.finished);await page.waitForFunction(()=>!document.querySelector('#invite-save').disabled);assert.match(await page.locator('#invite-selection').textContent(),/Reviewed recipient: reader/)
   await page.locator('#invite-cancel').click();remove(selection('admin'));remove(selection('reader'))
  }
  // The actual backend rejects the stale, canonical-shaped proof. It does not
  // create an invitation. Only explicit Review then Send replaces the proof.
  const first=writes().length
  set(selection(long),route=>route.fulfill(synthetic(200,{workspace_id:'alpha',principal_id:long,role:'viewer',precondition:'irs1-'+'0'.repeat(64)})))
  await fill(long);await review();await page.locator('#invite-save').click();await page.waitForFunction(()=>document.querySelector('#invite-status').textContent.includes('Recipient selection changed'))
  await noReview();const rejected=requests.length;await settle(page);assert.equal(requests.length,rejected);assert.equal(writes().length,first+1)
  const rejectedState=await inspectState();assert.ok(!rejectedState.invitations.some(item=>item.principal_id===long));assert.ok(!rejectedState.members.some(item=>item.principal_id===long))
  await within(router.drain());remove(selection(long));await review()
  let lost=false,committed
  set(collection,async route=>{if(route.request().method()!=='POST'||lost)return route.continue();lost=true;const response=await route.fetch();assert.equal(response.status(),200);committed=(await response.json()).data;await route.abort('failed')})
  await page.evaluate(()=>{const form=document.querySelector('#invite-form');form.requestSubmit();form.requestSubmit()})
  await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('Result uncertain'));await within(router.drain());remove(collection)
  assert.equal(writes().length,first+2);assert.equal(committed.state,'pending')
  await fill(long);assert.equal(await page.locator('#invite-review').isDisabled(),true);assert.equal(await page.locator('#invite-save').textContent(),'Retry original invitation')
  assert.match(await page.locator('#invite-selection').textContent(),/result is still unconfirmed/)
  const gets=requests.filter(r=>r.method==='GET'&&r.path.includes('/selections/')).length
  await page.evaluate(()=>document.querySelector('#invite-review').dispatchEvent(new Event('click')));await settle(page)
  assert.equal(requests.filter(r=>r.method==='GET'&&r.path.includes('/selections/')).length,gets)
  // Cancel, edit away/back, same-session reload and scope roundtrip retain the
  // original request without making a new Review a prerequisite for retry.
  await page.locator('#invite-principal').fill('admin');await page.locator('#invite-principal').fill(long);assert.equal(await page.locator('#invite-save').textContent(),'Retry original invitation')
  await page.locator('#invite-cancel').click();await page.locator('#reload').click();await ready()
  await page.evaluate(()=>{const picker=document.querySelector('#workspace');picker.selectedIndex=-1;picker.dispatchEvent(new Event('change'))});await page.waitForFunction(()=>document.querySelector('#panel').hidden);await page.locator('#workspace').selectOption('alpha');await ready();await fill(long)
  // The server has already committed. Hold its real historical 200, interrupt
  // with in-page Reload, then hold a newer retry. Releasing the old success must
  // neither delete the retained original pair nor unlock that newer request.
  const oldRecovery=hold(collection,async route=>({response:await route.fetch()}))
  await page.locator('#invite-save').click();await within(oldRecovery.started)
  await page.evaluate(()=>document.querySelector('#reload').dispatchEvent(new Event('click')));await ready();await fill(long)
  assert.equal(await page.locator('#invite-review').isDisabled(),true);assert.equal(await page.locator('#invite-save').textContent(),'Retry original invitation')
  const newRecovery=hold(collection,async route=>({response:await route.fetch()}))
  await page.locator('#invite-save').click();await within(newRecovery.started)
  oldRecovery.release();await within(oldRecovery.finished);await settle(page)
  assert.equal(await page.locator('#invite-save').isDisabled(),true);assert.equal(await page.locator('#session').isVisible(),true)
  newRecovery.release();await within(newRecovery.finished);await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('Invitation recorded'));await ready();remove(collection)
  const attempts=writes().slice(first);assert.equal(attempts.length,4);assert.equal(new Set(attempts.map(r=>r.headers['idempotency-key'])).size,1);assert.equal(new Set(attempts.map(r=>r.body)).size,1)
  assert.notEqual(attempts[0].headers['if-invitation-recipient-match'],attempts[1].headers['if-invitation-recipient-match']);assert.equal(new Set(attempts.slice(1).map(r=>r.headers['if-invitation-recipient-match'])).size,1)
  assert.deepEqual(JSON.parse(attempts[3].body),{principal_id:long,role:'viewer'})
  assert.ok(!(await page.locator('#members').textContent()).includes(long),'invitation/replay never grants membership')
  assert.equal(await page.locator('#invitations li').filter({hasText:long}).count(),1)
  const recoveredState=await inspectState(),records=recoveredState.invitations.filter(item=>item.principal_id===long);assert.equal(records.length,1);assert.equal(records[0].id,committed.id);assert.equal(records[0].expires,committed.expires);assert.ok(!recoveredState.members.some(item=>item.principal_id===long))
  // None of these outcomes proves no write. They cannot enable a new Review or
  // replace an original proof, including malformed/mismatched 200 and unknown412.
  const unconfirmed=[{status:400},{status:409},{status:428},{status:412},{status:412,extra:true},{status:200,data:{}},{status:200,data:{...committed,principal_id:'reader'}}]
  for(const outcome of unconfirmed){
   await fill();await review();const before=writes().length
   set(collection,route=>route.request().method()!=='POST'?route.continue():route.fulfill(outcome.extra?{status:412,contentType:'application/json',body:'{"error":"invitation_recipient_selection_changed","extra":true}'}:synthetic(outcome.status,outcome.data)))
   await page.locator('#invite-save').click()
   if(outcome.status===200){await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('Result uncertain'));await fill()}
   else await page.waitForFunction(()=>!document.querySelector('#invite-save').disabled)
   assert.equal(await page.locator('#invite-review').isDisabled(),true);const count=requests.length
   await page.evaluate(()=>document.querySelector('#invite-review').dispatchEvent(new Event('click')));await settle(page);assert.equal(requests.length,count)
   await page.locator('#invite-save').click()
   if(outcome.status===200)await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('Result uncertain'))
   else await page.waitForFunction(()=>!document.querySelector('#invite-save').disabled)
   const pair=writes().slice(before);assert.equal(pair.length,2);assert.equal(pair[0].headers['idempotency-key'],pair[1].headers['idempotency-key']);assert.equal(pair[0].headers['if-invitation-recipient-match'],pair[1].headers['if-invitation-recipient-match']);assert.equal(pair[0].body,pair[1].body)
   await within(router.drain());remove(collection);await reset()
  }
  // Old POST outcomes cannot release a newer POST's busy ticket, mark its pair
  // rejected, or perform Cookie clear/onInvalid. Programmatic input interruption
  // exercises ownership even though ordinary close-while-saving stays blocked.
  for(const status of [200,409,401,412]){
   await fill();await review();const old=hold(collection,async()=>status===412?{status:412,contentType:'application/json',body:'{"error":"invitation_recipient_selection_changed"}'}:synthetic(status,{...committed,principal_id:'admin'}))
   await page.locator('#invite-save').click();await within(old.started);assert.equal(await page.locator('#invite-cancel').isDisabled(),true)
   await page.keyboard.press('Escape');assert.equal(await page.locator('#invite-dialog').isVisible(),true)
   await page.evaluate(()=>{const input=document.querySelector('#invite-principal');input.value='reader';input.dispatchEvent(new Event('input'))})
   await review();const next=hold(collection,async()=>synthetic(409));await page.locator('#invite-save').click();await within(next.started)
   old.release();await within(old.finished);await settle(page);assert.equal(await page.locator('#session').isVisible(),true);assert.equal(await page.locator('#invite-save').isDisabled(),true);assert.equal(await page.locator('#invite-review').isDisabled(),true)
   next.release();await within(next.finished);await page.waitForFunction(()=>!document.querySelector('#invite-save').disabled);assert.equal(await page.locator('#invite-review').isDisabled(),true);assert.equal(await page.locator('#invite-save').textContent(),'Retry original invitation')
   remove(collection);await reset()
  }
  // Actually current 401 still clears both dialog and session-owned state.
  set(selection('admin'),route=>route.fulfill(synthetic(401)));await fill();await page.locator('#invite-review').click();await page.waitForFunction(()=>document.querySelector('#session').hidden);await noReview();await within(router.drain());remove(selection('admin'));await reconnect()
  await fill();await review();await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide')))
  assert.equal(await page.locator('#invite-dialog').isVisible(),false);assert.equal(await page.locator('#invite-principal').inputValue(),'');await noReview();assert.equal(await page.locator('#invitations').textContent(),'')
  assert.deepEqual(await page.evaluate(()=>[localStorage.length,sessionStorage.length]),[0,0]);await reconnect()
 }finally{
  for(const release of releases)release()
  try{await within(router.drain());for(const pathname of routes)router.remove(pathname);page.off('request',record)}
  finally{if(viewport)await page.setViewportSize(viewport)}
 }
}
module.exports={exerciseInvitationReview}
