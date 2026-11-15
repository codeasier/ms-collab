"""Synthetic approved index behind the actual read-only browser application."""
from datetime import timedelta
from pathlib import Path
import tempfile
from wsgiref.simple_server import make_server, WSGIRequestHandler

from packages.artifact_sync import ArtifactSynchronizer, IndexedArtifactApplication, initialize_indexes
from packages.artifacts_ui import ArtifactBrowserUI
from .fixtures import NOW, POLICY, SCOPE, Transport, Verifier, credential, initialize, page, provider, user


class Quiet(WSGIRequestHandler):
    def log_message(self, *args):
        pass


def main():
    with tempfile.TemporaryDirectory() as folder:
        store = initialize(Path(folder) / 'synthetic.sqlite3')
        initialize_indexes(store, (POLICY,))
        now = [NOW]
        args = dict(store=store, providers=(provider(Verifier()),), provider_key='test', clock=lambda: now[0])
        transport = Transport([page((('builds/<svg onload=alert(1)>.txt', '2026-01-01T23:00:00Z', 1),
                                    ('builds/approved.txt', '2026-01-01T23:00:00Z', 2)))])
        service = ArtifactSynchronizer(**args, credential_loader=lambda ref, scope, origin: credential(scope, origin), transport=transport)
        result = service.refresh(user(), SCOPE, expected_revision=1)
        preview = service.review(user('approver'), SCOPE, expected_revision=2)
        assert preview['candidate_digest'] == result['candidate_digest']
        service.approve(user('approver'), SCOPE, expected_revision=2, candidate_digest=preview['candidate_digest'])
        now[0] += timedelta(minutes=1)
        transport.calls = []
        transport.pages = [page((('builds/PENDING-PRIVATE.txt', '2026-01-02T00:00:00Z', 3),))]
        service.refresh(user(), SCOPE, expected_revision=3)
        with make_server('127.0.0.1', 0, None, handler_class=Quiet) as server:
            origin = f'http://127.0.0.1:{server.server_port}'
            server.set_app(ArtifactBrowserUI(api=IndexedArtifactApplication(**args), origin=origin))
            print(origin, flush=True)
            server.serve_forever()


if __name__ == '__main__':
    main()
