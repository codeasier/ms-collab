"""Synthetic same-origin wrapper, provider integration and isolation checks."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from packages.pipeline_ui import PipelineUI
from packages.pipeline_ui.tests.browser_server import make_api


class Tests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.api=make_api(Path(temp.name)/"test.sqlite3")
        self.ui=PipelineUI(api=self.api,origin="https://collab.example.invalid")

    def call(self,path="/pipelines/",**extra):
        environ={"HTTP_HOST":"collab.example.invalid","wsgi.url_scheme":"https","PATH_INFO":path,"QUERY_STRING":"","REQUEST_METHOD":"GET","wsgi.input":io.BytesIO(b"")}
        environ.update(extra);result={}
        def start(status,headers):result.update(status=int(status.split()[0]),headers=dict(headers))
        result["body"]=b"".join(self.ui(environ,start));return result

    def test_static_allowlist_and_security_headers(self):
        for path in ("/pipelines/","/pipelines/runs.mjs","/pipelines/client.mjs","/pipelines/style.css"):
            r=self.call(path);self.assertEqual(r["status"],200)
            self.assertEqual(r["headers"]["Cache-Control"],"no-store")
            self.assertIn("frame-ancestors 'none'",r["headers"]["Content-Security-Policy"])
            self.assertIn("connect-src 'self'",r["headers"]["Content-Security-Policy"])
            self.assertNotIn(b"synthetic-reader",r["body"])
            self.assertEqual(self.call(path,REQUEST_METHOD="HEAD")["body"],b"")
        for path in ("/pipelines/../README.md","/pipelines/tests/browser.cjs","/","/manage/"):
            self.assertEqual(self.call(path)["status"],404)
        self.assertEqual(self.call(QUERY_STRING="token=x")["status"],404)

    def test_same_origin_for_static_and_api(self):
        for extra in ({"HTTP_HOST":"evil.invalid"},{"HTTP_ORIGIN":"https://evil.invalid"},{"HTTP_SEC_FETCH_SITE":"cross-site"},{"wsgi.url_scheme":"http"}):
            for path in ("/pipelines/","/api/workspaces/alpha/pipelines/example-ci/build/runs"):
                self.assertEqual(self.call(path,**extra)["status"],403)

    def test_provider_publication_is_readable_but_no_anonymous_or_cross_space_access(self):
        path="/api/workspaces/alpha/pipelines/example-ci/build/runs"
        self.assertEqual(self.call(path)["status"],401)
        response=self.call(path,HTTP_AUTHORIZATION="Bearer synthetic-reader",QUERY_STRING="limit=100")
        self.assertEqual(response["status"],200)
        body=json.loads(response["body"])["data"]
        self.assertEqual((body["total"],body["source"]["revision"]),(20,2))
        self.assertEqual(self.call(path.replace("alpha","beta"),HTTP_AUTHORIZATION="Bearer synthetic-reader")["status"],404)
        duration=self.call(path.replace("/runs","/durations"),HTTP_AUTHORIZATION="Bearer synthetic-reader",QUERY_STRING="limit=100")
        report=json.loads(duration["body"])["data"]
        self.assertEqual(report["groups"][0]["average_seconds"],12)
        self.assertEqual(report["coverage"]["runs_with_samples"],2)

    def test_recorded_trend_and_slow_run_in_same_authorized_snapshot(self):
        base="/api/workspaces/commons/pipelines/example-ci/build"
        def read(suffix,query=""):
            response=self.call(base+suffix,HTTP_AUTHORIZATION="Bearer synthetic-reader",QUERY_STRING=query)
            self.assertEqual(response["status"],200)
            return json.loads(response["body"])["data"]
        report=read("/durations","limit=100")
        group,=report["groups"]
        self.assertEqual([(s["run_id"],s["duration_seconds"]) for s in group["samples"]],
                         [("run-17",4),("run-18",19),("run-20",7)])
        slow=group["samples"][1]
        detail=read("/runs/"+slow["run_id"])
        self.assertEqual((detail["scope"],detail["source"]),(report["scope"],report["source"]))
        self.assertEqual(detail["run"]["status"],"failure")
        tasks=read("/runs/"+slow["run_id"]+"/tasks",f"revision={report['source']['revision']}&limit=10")
        task,=tasks["items"]
        self.assertEqual((task["id"],task["status"],task["started_at"],task["finished_at"]),
                         (slow["job_id"],"success",slow["started_at"],slow["finished_at"]))
        self.assertFalse(report["source"]["live_provider_verified"])

    def test_no_execution_log_or_provider_refresh_routes(self):
        base="/api/workspaces/alpha/pipelines/example-ci/build"
        for method,path in (("POST",base+"/runs"),("POST",base+"/refresh"),("GET",base+"/runs/run-20/logs"),("POST",base+"/runs/run-20/retry")):
            self.assertEqual(self.call(path,REQUEST_METHOD=method,HTTP_AUTHORIZATION="Bearer synthetic-reader")["status"],404)

    def test_explicit_origin_and_real_application_required(self):
        for origin in ("http://example.invalid","http://localhost:8000","https://x.invalid/","https://x.invalid?x=y","https://u:p@x.invalid","https://x.invalid\n"):
            with self.assertRaises(ValueError):PipelineUI(api=self.api,origin=origin)
        with self.assertRaises(ValueError):PipelineUI(api=None,origin="https://x.invalid")


if __name__=="__main__":unittest.main()
