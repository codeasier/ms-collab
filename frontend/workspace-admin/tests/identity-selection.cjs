// Shared real-browser selection checks. Only adverse responses are intercepted;
// accepted selections and stale-precondition/create requests use the real API.
const assert=require('node:assert/strict')
const within=async promise=>{let timer;try{return await Promise.race([promise,new Promise((_,reject)=>timer=setTimeout(()=>reject(Error('Identity selection barrier timeout')),15000))])}finally{clearTimeout(timer)}}
const settle=page=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))))
async function exerciseIdentitySelection({page,router,ready,reconnect,invalidations=[]}){
 const selectionPath='/api/control/initial-admin-selections/',createPath='/api/control/workspaces',requests=[]
 const record=request=>{const path=new URL(request.url()).pathname;if(path.startsWith(selectionPath)||path===createPath)requests.push({path,method:request.method(),headers:request.headers(),body:request.postData()})}
 page.on('request',record)
 const writes=()=>requests.filter(request=>request.method==='POST')
 const fill=async(name='Identity selection guards',target='admin')=>{await ready();await page.locator('#create-open').click();await page.locator('#space-name').fill(name);await page.locator('#initial-admin').fill(target)}
 const review=async()=>{await page.locator('#initial-admin-review').click();await page.waitForFunction(()=>!document.querySelector('#create-save').disabled)}
 const empty=async()=>{assert.equal(await page.locator('#initial-admin-selection').textContent(),'');assert.equal(await page.locator('#create-save').isDisabled(),true)}
 const delayed=async(action,after)=>{
  await fill();let seen,release;const started=new Promise(resolve=>seen=resolve),gate=new Promise(resolve=>release=resolve)
  router.set(selectionPath+'admin',async route=>{const response=await route.fetch();seen();await gate;await route.fulfill({response})})
  try{
   await page.locator('#initial-admin-review').click();await within(started)
   const count=requests.filter(request=>request.path===selectionPath+'admin').length
   await page.evaluate(()=>document.querySelector('#initial-admin-review').dispatchEvent(new Event('click')))
   await settle(page);assert.equal(requests.filter(request=>request.path===selectionPath+'admin').length,count)
   await action();release();await within(router.drain());await settle(page);await after()
  }finally{release();await within(router.drain());router.remove(selectionPath+'admin')}
 }
 try{
  await fill();const initial=requests.length
  assert.equal(await page.locator('#create-save').isDisabled(),true)
  await page.evaluate(()=>document.querySelector('#create-form').dispatchEvent(new Event('submit',{cancelable:true})));await settle(page)
  assert.equal(requests.length,initial,'submit must not fetch a selection or send a write')
  await review();assert.equal(writes().length,0);assert.equal(requests.length,initial+1)
  assert.match(await page.locator('#initial-admin-selection').textContent(),/Reviewed initial administrator: admin/)
  await page.locator('#initial-admin').fill('reader');await page.locator('#initial-admin').fill('admin');await empty()
  await page.locator('#create-cancel').click()
  // Editing away and back cannot revive a delayed response, even without abort.
  await delayed(async()=>{await page.locator('#initial-admin').fill('reader');await page.locator('#initial-admin').fill('admin')},empty)
  await page.locator('#create-cancel').click()
  // A slower old review must not overwrite a newer target's completed review.
  await delayed(async()=>{await page.locator('#initial-admin').fill('reader');await review()},async()=>{assert.match(await page.locator('#initial-admin-selection').textContent(),/Reviewed initial administrator: reader/);assert.equal(await page.locator('#create-save').isDisabled(),false)})
  await page.locator('#create-cancel').click()
  await delayed(async()=>{await page.locator('#create-cancel').click();await fill()},empty)
  await page.keyboard.press('Escape');await empty();assert.equal(await page.locator('#create-dialog').isVisible(),false)
  await delayed(async()=>{await page.evaluate(()=>document.querySelector('#reload').dispatchEvent(new Event('click')));await ready()},empty)
  for(const invalidate of invalidations)await delayed(invalidate,async()=>{await empty();await reconnect()})
  // A malicious/mismatched successful envelope cannot become a usable selection.
  for(const data of [{initial_admin:'reader',precondition:'ias1-'+'a'.repeat(64)},{initial_admin:'admin',precondition:'ias1-'+'A'.repeat(64)},{initial_admin:'admin',precondition:'ias1-'+'a'.repeat(64),extra:true}]){
   router.set(selectionPath+'admin',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({data})}))
   await fill();await page.locator('#initial-admin-review').click();await page.waitForFunction(()=>document.querySelector('#create-status').textContent.includes('Unable to verify'))
   await empty();await page.locator('#create-cancel').click();await within(router.drain());router.remove(selectionPath+'admin')
  }
  // Rejection during review clears the session and does not send a create.
  router.set(selectionPath+'admin',route=>route.fulfill({status:401,contentType:'application/json',body:'{"error":"authentication_required"}'}))
  await fill();await page.locator('#initial-admin-review').click();await page.waitForFunction(()=>document.querySelector('#session').hidden);await empty();await within(router.drain());router.remove(selectionPath+'admin');await reconnect()
  assert.equal(writes().length,0)
  // A correctly shaped but stale selection reaches the actual backend: no write
  // on 412, no automatic GET/retry. Explicit review replaces only that proof.
  router.set(selectionPath+'reader',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({data:{initial_admin:'reader',precondition:'ias1-'+'0'.repeat(64)}})}))
  await fill('Identity selection recovery','reader');await review();await page.locator('#create-save').click()
  await page.waitForFunction(()=>document.querySelector('#create-status').textContent.includes('Creation was rejected'));await empty()
  const rejected=requests.length;await settle(page);assert.equal(requests.length,rejected);assert.equal(writes().length,1)
  await within(router.drain());router.remove(selectionPath+'reader');await review()
  await page.evaluate(()=>{const form=document.querySelector('#create-form');form.requestSubmit();form.requestSubmit()})
  await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('Workspace created: Identity selection recovery'));await ready()
  const recovered=writes();assert.equal(recovered.length,2);assert.equal(recovered[0].headers['idempotency-key'],recovered[1].headers['idempotency-key']);assert.notEqual(recovered[0].headers['if-initial-admin-match'],recovered[1].headers['if-initial-admin-match'])
  assert.deepEqual(JSON.parse(recovered[1].body),{name:'Identity selection recovery',kind:'product',initial_admin:'reader'})
  assert.match(recovered[1].headers['if-initial-admin-match'],/^ias1-[a-f0-9]{64}$/)
  // A conflict never authorizes a new retry key, even after another review.
  router.set(createPath,route=>route.request().method()==='POST'?route.fulfill({status:409,contentType:'application/json',body:'{"error":"idempotency_conflict"}'}):route.continue())
  await fill('Identity selection conflict');await review();await page.locator('#create-save').click();await page.waitForFunction(()=>document.querySelector('#create-status').textContent.includes('conflict'))
  router.set(selectionPath+'admin',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({data:{initial_admin:'admin',precondition:'ias1-'+'f'.repeat(64)}})}))
  await review();await page.locator('#create-save').click();await page.waitForFunction(()=>document.querySelector('#create-status').textContent.includes('conflict'))
  assert.equal(writes().length,4)
  const conflicts=writes().slice(-2);assert.equal(conflicts[0].headers['idempotency-key'],conflicts[1].headers['idempotency-key']);assert.equal(conflicts[0].headers['if-initial-admin-match'],conflicts[1].headers['if-initial-admin-match']);assert.equal(conflicts[0].body,conflicts[1].body)
  await page.locator('#create-cancel').click();await within(router.drain());router.remove(createPath);router.remove(selectionPath+'admin')
  // Only 412 can release the original pair. Other rejections plus a new user
  // review still must not replace a potentially unresolved key/precondition.
  for(const status of [400,428]){
   const before=writes().length
   router.set(createPath,route=>route.request().method()==='POST'?route.fulfill({status,contentType:'application/json',body:'{"error":"synthetic_rejection"}'}):route.continue())
   await fill('Identity selection rejection '+status);await review();await page.locator('#create-save').click();await page.waitForFunction(()=>!document.querySelector('#create-save').disabled)
   router.set(selectionPath+'admin',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({data:{initial_admin:'admin',precondition:'ias1-'+(status===400?'d':'e').repeat(64)}})}))
   await review();await page.locator('#create-save').click();await page.waitForFunction(()=>!document.querySelector('#create-save').disabled)
   assert.equal(writes().length,before+2)
   const attempts=writes().slice(-2);assert.equal(attempts[0].headers['idempotency-key'],attempts[1].headers['idempotency-key']);assert.equal(attempts[0].headers['if-initial-admin-match'],attempts[1].headers['if-initial-admin-match']);assert.equal(attempts[0].body,attempts[1].body)
   await page.locator('#create-cancel').click();await within(router.drain());router.remove(createPath);router.remove(selectionPath+'admin')
  }
 }finally{await within(router.drain());for(const path of [selectionPath+'admin',selectionPath+'reader',createPath])router.remove(path);page.off('request',record)}
}
module.exports={exerciseIdentitySelection}
