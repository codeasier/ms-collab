// Explicit synthetic HTTPS/cookie acceptance. No real provider or credentials.
const { chromium } = require('playwright');
const { spawn, execFileSync } = require('node:child_process');
const { mkdtempSync, rmSync } = require('node:fs');
const { tmpdir } = require('node:os');
const { join } = require('node:path');
const assert = require('node:assert/strict');
const readline = require('node:readline');

(async () => {
  const temp = mkdtempSync(join(tmpdir(), 'ms-collab-cookie-cert-'));
  let server, browser;
  try {
    // Temporary test key/certificate, never committed, exported or trusted by OS.
    execFileSync('openssl', ['req','-x509','-newkey','rsa:2048','-nodes','-days','1',
      '-keyout',join(temp,'key.pem'),'-out',join(temp,'cert.pem'),'-subj','/CN=localhost',
      '-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'], {stdio:'ignore'});
    server = spawn('python3',['-m','packages.session_api.tests.browser_server',join(temp,'cert.pem'),join(temp,'key.pem')],
      {env:{...process.env,MS_COLLAB_SYNTHETIC_COOKIE_TEST:'1'},stdio:['ignore','pipe','pipe']});
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
      const path='/api/workspaces/alpha/modules/board/resources/same';
      const csrf=document.cookie.split('; ').find(s=>s.startsWith('__Host-ms-collab-csrf=')).split('=')[1];
      const noCsrf=await fetch(path,{method:'PUT',headers:{'Content-Type':'application/json','If-Match':'"1"'},body:JSON.stringify({text:'denied'})});
      const good=await fetch(path,{method:'PUT',headers:{'Content-Type':'application/json','If-Match':'"1"','X-CSRF-Token':csrf},body:JSON.stringify({text:'synthetic-cookie-write'})});
      const revoked=await fetch('/synthetic-revoke',{method:'POST'});
      document.cookie='__Host-ms-collab-csrf=; Path=/; Secure; Max-Age=0; SameSite=Strict';
      const stale=await fetch('/auth/start',{method:'POST',headers:{'Content-Type':'application/json','X-MS-Collab-Login':'1'},body:'{}'});
      return [noCsrf.status,good.status,revoked.status,stale.status];
    });
    assert.deepEqual(statuses,[401,200,200,401]);
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
      const after=await fetch('/api/workspaces/alpha/modules/board/resources/same');
      return [out.status,after.status];
    });
    assert.deepEqual(logout,[200,401]);
    assert.equal((await context.cookies()).filter(c=>c.name.startsWith('__Host-ms-collab-')).length,0);
    console.log('Synthetic HTTPS cross-site callback, host cookie flags, HttpOnly isolation, CSRF, stale-cookie recovery and logout checks passed. Not live GitCode/TLS trust acceptance.');
  } finally {
    if(browser) await browser.close();
    if(server && server.exitCode===null){server.kill('SIGTERM');await new Promise(resolve=>{server.once('exit',resolve);setTimeout(()=>{if(server.exitCode===null)server.kill('SIGKILL');resolve();},3000);});}
    rmSync(temp,{recursive:true,force:true});
  }
})().catch(error=>{console.error(error.message);process.exitCode=1;});
