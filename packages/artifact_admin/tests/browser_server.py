"""Synthetic split-role management and approved-reader integration fixture."""
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import tempfile
from wsgiref.simple_server import make_server, WSGIRequestHandler

from packages.artifact_admin import ArtifactAdministrationAPI, ArtifactAdministrationUI
from packages.artifact_sync import ArtifactSynchronizer, IndexedArtifactApplication, initialize_indexes
from packages.artifacts_ui import ArtifactBrowserUI
from packages.artifact_sync.tests.fixtures import CONFIG, NOW, POLICY, Verifier, credential, initialize, page, provider


class Quiet(WSGIRequestHandler):
    def log_message(self, *args):
        pass


def main():
    with tempfile.TemporaryDirectory() as directory:
        store = initialize(Path(directory) / 'synthetic.sqlite3')
        config = replace(CONFIG, page_size=50, max_pages=2)
        initialize_indexes(store, (replace(POLICY, connection=config),))
        now = [NOW]
        class StableTransport:
            count = 0
            def list_page(self, selected, secret, marker, sent_at):
                self.count += 1
                names = ['builds/<svg onload=alert(1)>.txt', 'builds/空间😀.sha256']
                names += [f'builds/package-{i:02}.zip' for i in range(10)]
                names += [f'builds/new-candidate-{i}.txt' for i in range(1, self.count + 1)]
                now[0] += timedelta(seconds=1)
                return page(tuple((name, '2026-01-01T23:00:00Z', 12) for name in sorted(names)), config=selected)
        args = dict(store=store, providers=(provider(Verifier()),), provider_key='test', clock=lambda: now[0])
        service = ArtifactSynchronizer(**args, credential_loader=lambda ref, scope, origin: credential(scope, origin), transport=StableTransport())
        with make_server('127.0.0.1', 0, None, handler_class=Quiet) as server:
            origin = f'http://127.0.0.1:{server.server_port}'
            admin = ArtifactAdministrationUI(api=ArtifactAdministrationAPI(service=service), origin=origin)
            reader = ArtifactBrowserUI(api=IndexedArtifactApplication(**args), origin=origin)
            def application(environ, start_response):
                path = environ.get('PATH_INFO', '')
                return (admin if path.startswith('/artifact-admin/') or '/artifact-admin/' in path else reader)(environ, start_response)
            server.set_app(application)
            print(origin, flush=True)
            server.serve_forever()


if __name__ == '__main__':
    main()
