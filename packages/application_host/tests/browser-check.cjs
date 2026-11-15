// Explicit synthetic HTTPS/cookie acceptance. No real provider or credentials.
const { chromium } = require('playwright');
const { spawn, execFileSync } = require('node:child_process');
const { mkdtempSync, rmSync } = require('node:fs');
const { tmpdir } = require('node:os');
const { join } = require('node:path');
const assert = require('node:assert/strict');
const readline = require('node:readline');

(async () => {
  const temp = mkdtempSync(join(tmpdir(), 'ms-collab-host-cert-'));
  let server, browser;
  try {
    // Temporary test key/certificate, never committed, exported or trusted by OS.
    execFileSync('openssl', ['req','-x509','-newkey','rsa:2048','-nodes','-days','1',
      '-keyout',join(temp,'key.pem'),'-out',join(temp,'cert.pem'),'-subj','/CN=localhost',
      '-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'], {stdio:'ignore'});
    server = spawn('python3',['-m','packages.application_host.tests.browser_server',join(temp,'cert.pem'),join(temp,'key.pem')],
      {env:{...process.env,MS_COLLAB_SYNTHETIC_HOST_TEST:'1'},stdio:['ignore','pipe','pipe']});
    const lines = readline.createInterface({input:server.stdout});
    const startup = await Promise.race([
      new Promise((resolve,reject)=>{lines.once('line',line=>{try{resolve(JSON.parse(line));}catch(e){reject(e);}});server.once('exit',code=>reject(Error('Fixture exited '+code)));}),
      new Promise((_,reject)=>setTimeout(()=>reject(Error('Fixture startup timeout')),10000))]);
    browser = await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_EXECUTABLE || undefined,args:['--no-sandbox']});
    // Exception is confined to this disposable synthetic browser context.
    const context = await browser.newContext({ignoreHTTPSErrors:true});
    const page = await context.newPage();
    await page.goto(startup.origin);
    const begin = await page.evaluate(async()=>{
      const r=await fetch('/auth/start',{method:'POST',headers:{'Content-Type':'application/json','X-MS-Collab-Login':'1'},body:'{}'});
      return {status:r.status,body:await r.json()};
    });
    assert.equal(begin.status,200);
    assert.equal(new URL(begin.body.data.authorization_url).hostname,'localhost');
    const pending = (await context.cookies()).find(c=>c.name==='__Host-ms-collab-login');
    assert.ok(pending.secure && pending.httpOnly && pending.sameSite==='Lax' && pending.path==='/');
    await page.goto(begin.body.data.authorization_url);
    await page.waitForURL(startup.origin+'/');
    const cookies = await context.cookies();
    const session = cookies.find(c=>c.name==='__Host-ms-collab-session');
    const csrf = cookies.find(c=>c.name==='__Host-ms-collab-csrf');
    assert.ok(session.secure && session.httpOnly && session.sameSite==='Lax' && session.path==='/');
    assert.ok(csrf.secure && !csrf.httpOnly && csrf.sameSite==='Strict' && csrf.path==='/');
    assert.equal(cookies.some(c=>c.name==='__Host-ms-collab-login'),false);
    const scriptCookies = await page.evaluate(()=>document.cookie);
    assert.equal(scriptCookies.includes('__Host-ms-collab-session'),false);
    assert.ok(scriptCookies.includes('__Host-ms-collab-csrf'));
    const statuses=await page.evaluate(async()=>{
      const csrf=document.cookie.split('; ').find(s=>s.startsWith('__Host-ms-collab-csrf=')).split('=')[1];
      const paths=[
        '/api/workspaces/alpha/community/source/sample/project/items',
        '/api/workspaces/alpha/pipelines/example-ci/build/runs',
        '/api/workspaces/alpha/artifacts/source/bucket/quarters',
        '/api/workspaces/alpha/research-assets/catalog/types',
        '/api/workspaces/alpha/responsibility/source/sample/project/config',
        '/api/workspaces/alpha/assistant/conversations/one/events'];
      const reads=await Promise.all(paths.map(async p=>{const r=await fetch(p);return r.status;}));
      const approved=await (await fetch(paths[0])).json();
      const shared=await fetch(paths[1].replace('/alpha/','/commons/'));
      const raw=await fetch('/api/workspaces/alpha/modules/pipelines/resources/raw');
      const path='/api/workspaces/alpha/assistant/conversations/one';
      const noCsrf=await fetch(path,{method:'PATCH',headers:{'Content-Type':'application/json','If-Match':'"2"'},body:JSON.stringify({state:'archived'})});
      const good=await fetch(path,{method:'PATCH',headers:{'Content-Type':'application/json','If-Match':'"2"','X-CSRF-Token':csrf},body:JSON.stringify({state:'archived'})});
      const revoked=await fetch('/synthetic-revoke',{method:'POST'});
      const revokedReads=await Promise.all(paths.map(async p=>{const r=await fetch(p);return r.status;}));
      document.cookie='__Host-ms-collab-csrf=; Path=/; Secure; Max-Age=0; SameSite=Strict';
      const stale=await fetch('/auth/start',{method:'POST',headers:{'Content-Type':'application/json','X-MS-Collab-Login':'1'},body:'{}'});
      return {reads,approved:approved.data.items.map(x=>x.number),shared:shared.status,raw:raw.status,
        writes:[noCsrf.status,good.status],revoked:revoked.status,revokedReads,stale:stale.status};
    });
    assert.deepEqual(statuses,{reads:[200,200,200,200,200,200],approved:[1],shared:404,raw:404,
      writes:[401,200],revoked:200,revokedReads:[401,401,401,401,401,401],stale:401});
    assert.equal((await context.cookies()).filter(c=>c.name.startsWith('__Host-ms-collab-')).length,0);
    const retry = await page.evaluate(async()=>{
      const r=await fetch('/auth/start',{method:'POST',headers:{'Content-Type':'application/json','X-MS-Collab-Login':'1'},body:'{}'});
      return {status:r.status,body:await r.json()};
    });
    assert.equal(retry.status,200);
    await page.goto(retry.body.data.authorization_url);
    await page.waitForURL(startup.origin+'/');
    assert.notEqual((await context.cookies()).find(c=>c.name==='__Host-ms-collab-session').value,session.value);
    const logout = await page.evaluate(async()=>{
      const csrf=document.cookie.split('; ').find(s=>s.startsWith('__Host-ms-collab-csrf=')).split('=')[1];
      const out=await fetch('/auth/logout',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:'{}'});
      const after=await fetch('/api/workspaces');
      return [out.status,after.status];
    });
    assert.deepEqual(logout,[200,401]);
    assert.equal((await context.cookies()).filter(c=>c.name.startsWith('__Host-ms-collab-')).length,0);
    console.log('Synthetic unified host HTTPS login, six module reads, approval and workspace fences, CSRF write, revocation, recovery and logout passed. Not live GitCode or production acceptance.');
  } finally {
    if(browser) await browser.close();
    if(server && server.exitCode===null){server.kill('SIGTERM');await new Promise(resolve=>{server.once('exit',resolve);setTimeout(()=>{if(server.exitCode===null)server.kill('SIGKILL');resolve();},3000);});}
    rmSync(temp,{recursive:true,force:true});
  }
})().catch(error=>{console.error(error.message);process.exitCode=1;});
