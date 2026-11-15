// Synthetic HTTPS + real unified cookies only. No real provider, task or runner.
const {chromium}=require('playwright');
const {spawn,execFileSync}=require('node:child_process');
const {mkdtempSync,rmSync,mkdirSync}=require('node:fs');
const {tmpdir}=require('node:os');const {join}=require('node:path');
const assert=require('node:assert/strict');const readline=require('node:readline');
(async()=>{
  const temp=mkdtempSync(join(tmpdir(),'ms-collab-gui-cert-'));let server,browser,page,errors=[],requests=[];
  const shots=process.env.SCREENSHOT_DIR||'gui-smoke-screenshots';mkdirSync(shots,{recursive:true});
  try {
    execFileSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-days','1','-keyout',join(temp,'key.pem'),'-out',join(temp,'cert.pem'),'-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'],{stdio:'ignore'});
    server=spawn('python3',['-m','packages.gui_smoke_ui.tests.browser_server',join(temp,'cert.pem'),join(temp,'key.pem')],{env:{...process.env,MS_COLLAB_SYNTHETIC_GUI_UI_TEST:'1'},stdio:['ignore','pipe','pipe']});
    let stderr='';server.stderr.on('data',data=>stderr+=data.toString());
    const lines=readline.createInterface({input:server.stdout});
    const startup=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Fixture startup timeout: '+stderr)),10000);lines.once('line',line=>{clearTimeout(timer);try{resolve(JSON.parse(line));}catch(e){reject(e);}});server.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+': '+stderr));});});
    browser=await chromium.launch({headless:true,args:['--no-sandbox']});
    const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1440,height:1050}});page=await context.newPage();
    page.on('pageerror',e=>errors.push(e.message));
    page.on('request',r=>requests.push({url:r.url(),method:r.method(),headers:r.headers()}));
    const text=async id=>(await page.locator('#'+id).textContent()).trim();
    async function notice(value){await page.waitForFunction(v=>document.getElementById('notice').textContent.includes(v),value);}
    async function load(workspace='alpha'){
      await page.locator('#scope-picker').evaluate(el=>el.open=true);
      for(const [id,value] of Object.entries({workspace,connection:'panel',repository:'repo'})) await page.locator('#'+id).fill(value);
      await page.locator('#load').click();
      if(['alpha','beta','empty','unpublished'].includes(workspace)) await page.locator('#workspace-view').waitFor({state:'visible'});
      else await notice('not available');
    }
    async function login(){await page.locator('#sign-in').click();await page.waitForURL(startup.origin+'/');await page.waitForFunction(()=>document.getElementById('session-label').textContent==='Signed in · alice');}
    async function control(path){assert.equal(await page.evaluate(async p=>(await fetch(p,{method:'POST'})).status,path),200);}
    await page.goto(startup.origin+'/gui-smoke/');await notice('Sign in');assert.equal(await page.locator('#load').isDisabled(),true);
    assert.equal(await page.locator('input[type=password]').count(),0);
    await login();
    const cookies=await context.cookies(),session=cookies.find(c=>c.name==='__Host-ms-collab-session'),csrf=cookies.find(c=>c.name==='__Host-ms-collab-csrf');
    assert.ok(session.secure&&session.httpOnly&&session.sameSite==='Lax');assert.ok(csrf.secure&&!csrf.httpOnly&&csrf.sameSite==='Strict');
    assert.equal((await page.evaluate(()=>document.cookie)).includes('__Host-ms-collab-session'),false);
    await load();assert.equal(await page.locator('.task').count(),20);assert.equal(await text('result-count'),'33 matching · 33 in snapshot');
    assert.ok((await text('tasks')).includes('<img src=x onerror="globalThis.injected=1">'));
    assert.equal(await page.locator('#tasks img').count(),0);assert.equal(await page.evaluate(()=>globalThis.injected),undefined);
    await page.locator('#page-size').selectOption('5');await page.getByRole('button',{name:'Apply filters'}).click();await page.waitForFunction(()=>document.querySelectorAll('.task').length===5);
    await page.screenshot({path:join(shots,'desktop-list.png'),fullPage:true});
    await page.getByRole('button',{name:'Inspect task task-1',exact:true}).click();await page.locator('#detail h3').waitFor();
    assert.ok((await text('detail')).includes('a'.repeat(40)));assert.ok((await text('detail')).includes('b'.repeat(40)));
    await page.screenshot({path:join(shots,'desktop-detail.png'),fullPage:true});
    await page.locator('#back').click();await page.locator('#next').click();await page.waitForFunction(()=>document.getElementById('page-label').textContent==='Page 2 of 7');
    await control('/synthetic-republish');await page.locator('#next').click();await notice('snapshot changed');assert.equal(await page.locator('.task').count(),0);
    await load();await page.locator('#status-filter').selectOption('pending');await page.getByRole('button',{name:'Apply filters'}).click();await page.waitForFunction(()=>document.querySelectorAll('.task').length===6);
    await page.getByRole('button',{name:'Inspect task task-3',exact:true}).click();await page.locator('#detail h3').waitFor();assert.ok((await text('detail')).includes('Missing counts are not zero'));assert.ok((await text('detail')).includes('Not reported'));
    await load('empty');assert.ok((await text('empty')).includes('No published tasks'));assert.equal(await page.locator('.stat').count(),6);
    await load('unpublished');assert.ok((await text('snapshot-label')).includes('No published snapshot'));assert.equal(await page.locator('.stat').count(),0);
    await load('commons');assert.equal(await page.locator('.task').count(),0);
    // Deliberately disable cancellation in this probe: generation must reject
    // captured authorized bytes even when the old transport completes normally.
    await page.evaluate(()=>{globalThis.originalAbort=AbortController.prototype.abort;AbortController.prototype.abort=function(){};});
    let held,handled,reached;const gate=new Promise(r=>held=r),done=new Promise(r=>handled=r),seen=new Promise(r=>reached=r);
    const oldRoute='**/api/workspaces/alpha/gui-smoke/panel/repo/tasks?*';
    await page.route(oldRoute,async route=>{try {const response=await route.fetch();reached();await gate;await route.fulfill({response});}finally{handled();}});
    await page.locator('#scope-picker').evaluate(el=>el.open=true);await page.locator('#workspace').fill('alpha');await page.locator('#load').click();await seen;
    await load('beta');held();await done;await page.unroute(oldRoute);
    assert.equal(await text('scope-title'),'beta / panel / repo');assert.equal((await text('tasks')).includes('alpha-branch'),false);
    await page.evaluate(()=>{AbortController.prototype.abort=globalThis.originalAbort;delete globalThis.originalAbort;});
    // Real navigation Back/Forward: no saved UI/auth reconstruction from history.
    await page.goto(startup.origin+'/gui-smoke/');await page.waitForFunction(()=>document.getElementById('session-label').textContent==='Signed in · alice');
    await page.goBack();await page.waitForFunction(()=>document.getElementById('session-label').textContent==='Signed in · alice');assert.equal(await page.locator('.task').count(),0);
    await page.goForward();await page.waitForFunction(()=>document.getElementById('session-label').textContent==='Signed in · alice');assert.equal(await page.locator('.task').count(),0);
    await page.setViewportSize({width:390,height:844});await load();await page.locator('#page-size').selectOption('5');await page.getByRole('button',{name:'Apply filters'}).click();await page.waitForFunction(()=>document.querySelectorAll('.task').length===5);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await page.screenshot({path:join(shots,'mobile-list.png'),fullPage:true});
    await page.getByRole('button',{name:'Inspect task task-1',exact:true}).click();await page.locator('#detail h3').waitFor();
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);await page.screenshot({path:join(shots,'mobile-detail.png'),fullPage:true});
    await page.locator('#clear').click();assert.equal(await page.locator('#detail').textContent(),'');await load();
    await page.evaluate(()=>{globalThis.originalAbort=AbortController.prototype.abort;AbortController.prototype.abort=function(){};});
    let releaseOut,receivedOut,finishedOut;const outGate=new Promise(r=>releaseOut=r),outSeen=new Promise(r=>receivedOut=r),outDone=new Promise(r=>finishedOut=r);
    await page.route(oldRoute,async route=>{try{const response=await route.fetch();receivedOut();await outGate;await route.fulfill({response});}finally{finishedOut();}});
    await page.locator('#next').click();await outSeen;
    await page.locator('#sign-out').click();await notice('Signed out');releaseOut();await outDone;await page.unroute(oldRoute);
    await page.evaluate(()=>{AbortController.prototype.abort=globalThis.originalAbort;delete globalThis.originalAbort;});
    assert.equal(await page.locator('.task').count(),0);assert.equal(await page.locator('#workspace').inputValue(),'');
    assert.equal((await context.cookies()).filter(c=>c.name.startsWith('__Host-ms-collab-')).length,0);
    await login();await load();await control('/synthetic-revoke');await page.locator('#next').click();await notice('session is not active');assert.equal(await page.locator('.task').count(),0);
    // Stale-cookie recovery is explicit: first click clears an invalid cookie;
    // a later user click starts a fresh attempt. No automatic auth write replay.
    await page.locator('#sign-in').click();await notice('Sign-in could not be started');
    assert.equal((await context.cookies()).filter(c=>c.name.startsWith('__Host-ms-collab-')).length,0);
    await login();await load();await control('/synthetic-policy-revoke');await page.locator('#next').click();await notice('not available');assert.equal(await page.locator('.task').count(),0);
    assert.deepEqual(await page.evaluate(()=>[localStorage.length,sessionStorage.length]),[0,0]);
    assert.equal(requests.some(r=>r.headers.authorization),false);
    assert.equal(requests.some(r=>r.url.includes('/api/')&&r.method!=='GET'),false);
    assert.equal(requests.some(r=>!['127.0.0.1','localhost'].includes(new URL(r.url).hostname)),false);
    assert.deepEqual(errors,[]);
    console.log('PASS: synthetic HTTPS login/cookie policy, scope isolation, consistent paging, task evidence, XSS, empty/unpublished, late response, browser history, mobile, logout, session/member revoke. No production/provider/runner acceptance.');
  } catch(error) {
    // This fixture has synthetic data only; never log cookies or request headers.
    if(page&&!page.isClosed()){console.error('Synthetic failure state',JSON.stringify({path:new URL(page.url()).pathname,body:(await page.locator('body').innerText()).slice(0,2500),errors,paths:requests.map(r=>({path:new URL(r.url).pathname,method:r.method}))}));await page.screenshot({path:join(shots,'failure.png'),fullPage:true});}
    throw error;
  } finally {
    if(browser)await browser.close();
    if(server&&server.exitCode===null){server.kill('SIGTERM');await new Promise(resolve=>{server.once('exit',resolve);setTimeout(()=>{if(server.exitCode===null)server.kill('SIGKILL');resolve();},3000);});}
    rmSync(temp,{recursive:true,force:true});
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
