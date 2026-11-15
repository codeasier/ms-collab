"""Actual disposable TLS + HTTP cookie flow, not browser-rendering acceptance."""
import http.client
from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
import selectors
import ssl
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import urlsplit


class ReferenceTransportTests(unittest.TestCase):
    def test_real_temporary_https_login_cookie_csrf_and_reconfirmation(self):
        with tempfile.TemporaryDirectory(prefix='ms-collab-reference-tls-test-') as directory:
            cert=Path(directory)/'cert.pem';key=Path(directory)/'key.pem'
            subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-keyout',str(key),'-out',str(cert),'-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            child=subprocess.Popen([sys.executable,'-m','packages.responsibility_ui.tests.cookie_browser_server',str(cert),str(key)],env={**os.environ,'MS_COLLAB_SYNTHETIC_RESPONSIBILITY_UI':'1'},stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                with selectors.DefaultSelector() as ready:
                    ready.register(child.stdout,selectors.EVENT_READ)
                    self.assertTrue(ready.select(10),'synthetic TLS fixture did not start')
                fixture=json.loads(child.stdout.readline());origin=fixture['origin'];cookies={}
                # Trust only this disposable certificate in this local probe, never
                # a process-global or browser/system certificate exception.
                context=ssl.create_default_context(cafile=str(cert))
                def call(url,method='GET',body=None,extra=None):
                    target=urlsplit(url if url.startswith('https:') else origin+url)
                    headers={'Cookie':'; '.join(k+'='+v for k,v in cookies.get(target.hostname,{}).items())}
                    if body is not None:headers['Content-Type']='application/json'
                    headers.update(extra or {})
                    connection=http.client.HTTPSConnection(target.hostname,target.port,context=context,timeout=10)
                    try:
                        connection.request(method,target.path+('?' +target.query if target.query else ''),None if body is None else json.dumps(body),headers)
                        result=connection.getresponse();raw=result.read()
                        for name,value in result.getheaders():
                            if name.lower()=='set-cookie':
                                parsed=SimpleCookie();parsed.load(value)
                                for name,morsel in parsed.items():cookies.setdefault(target.hostname,{})[name]=morsel.value
                        return result.status,dict(result.getheaders()),json.loads(raw) if raw and dict(result.getheaders()).get('Content-Type','').startswith('application/json') else raw
                    finally:connection.close()
                status,_,data=call('/auth/start','POST',{}, {'Origin':origin,'X-MS-Collab-Login':'1'});self.assertEqual(status,200)
                status,headers,_=call(data['data']['authorization_url']);self.assertEqual(status,303)
                status,_,_=call(headers['Location']);self.assertEqual(status,303)
                self.assertEqual(call('/auth/session')[2]['data']['principal_id'],'admin')
                host=urlsplit(origin).hostname
                def mutation(extra=None):return {'Origin':origin,'X-CSRF-Token':cookies[host]['__Host-ms-collab-csrf'],**(extra or {})}
                base='/api/workspaces/alpha/responsibility/source/sample/project'
                fields=[dict(id='ui',name='Synthetic TLS owner',kind='developer',paths=['ui'],owner_ids=['reader'],backup=None)]
                self.assertEqual(call(base+'/config','PUT',{'fields':fields},mutation({'If-Match':'"1"'}))[0],200)
                self.assertEqual(call('/synthetic/identity-aba','POST',{}, {'Origin':origin})[0],200)
                status,_,data=call(base+'/config');self.assertEqual(status,200);config=data['data'];self.assertEqual(config['references']['state'],'reconfirmation_required')
                self.assertEqual(call(base+'/candidates?view=all')[0],409)
                proposal={'fields':fields,'confirmation_token':config['references']['confirmation_token']}
                self.assertEqual(call(base+'/reconfirm','POST',proposal,{'Origin':origin,'If-Match':'"2"'})[0],401)
                status,_,data=call(base+'/reconfirm','POST',proposal,mutation({'If-Match':'"2"'}));self.assertEqual(status,200)
                self.assertEqual(data['data']['references']['state'],'current');self.assertEqual(data['data']['revision'],3)
                self.assertEqual(call(base+'/reconfirm','POST',proposal,mutation({'If-Match':'"2"'}))[0],412)
            finally:
                child.terminate()
                try:self.assertEqual(child.wait(5),0)
                except subprocess.TimeoutExpired:
                    child.kill();child.wait(5);self.fail('synthetic fixture did not stop cleanly')
                child.stdout.close();child.stderr.close()
