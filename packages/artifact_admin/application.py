# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Narrow management HTTP surface over the reviewed artifact sync service."""
from dataclasses import asdict
import json
import re

from packages.artifacts import Scope
from packages.artifact_sync import ArtifactSynchronizer, ProviderError
from packages.artifact_sync.records import authorized, publication_current
from packages.artifact_sync.discovery import directory, directory_request, grant_fence
from packages.directory_cursors.directory import configured, serve
from packages.artifact_refresh import ConfiguredArtifactRefresh
from packages.workspace_api import WorkspaceApplication
from packages.workspace_api.application import _object
from packages.workspaces import PolicyError

_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z')
_CONFIRM = 'approve_complete_candidate_to_reviewed_readers'


class ArtifactAdministrationAPI(WorkspaceApplication):
    """Trusted factory only; reuse the SAME verifier, store and sync dependencies."""
    def __init__(self, *, service, allow_refresh=True, refresher=None, directory_cursors=None):
        if type(service) is not ArtifactSynchronizer:
            raise ValueError('configured artifact synchronizer required')
        if type(allow_refresh) is not bool:
            raise ValueError('explicit refresh availability required')
        if refresher is not None and (type(refresher) is not ConfiguredArtifactRefresh or refresher.service is not service or not allow_refresh):
            raise ValueError('same-authority refresh configuration required')
        self._service, self._allow_refresh, self._refresher = service, allow_refresh, refresher
        self._directory_cursors = configured(directory_cursors)

    def _dispatch(self, environ):
        assertion = self._service._verify_request(environ)
        listing = directory_request(environ, 'artifact-admin')
        if listing is not None:
            workspace, query = listing
            return serve(self._service, assertion, workspace=workspace, query=query,
                         action='artifact.scopes.admin', store=self._directory_cursors,
                         allow_refresh=self._allow_refresh,
                         projection=lambda policy, actor, db: directory(policy, actor, db, workspace, status=self._evaluate_status))
        scope, action = self._route(environ)
        if action == 'status':
            return self._service._authenticated_transaction(assertion,
                lambda policy, actor, connection: self._status(policy, actor, connection, scope), with_connection=True)
        expected = self._revision(environ)
        if action == 'review':
            result = self._service._review_verified(assertion, scope, expected_revision=expected)
            snapshot = result.pop('snapshot')
            result.update(scope=scope.as_dict(), snapshot={'schema': 1, **asdict(snapshot)},
                          readers=list(result['readers']), complete=True, count=len(snapshot.artifacts))
            return result
        body = self._body(environ)
        if action == 'refresh':
            if body != {}:
                raise PolicyError(400, 'invalid_request')
            if not self._allow_refresh:
                def unavailable(policy, actor, connection):
                    record = authorized(policy, actor, connection, scope, 'refresh')
                    policy._match(expected, record.revision)
                    raise PolicyError(503, 'provider_unavailable')
                return self._service._authenticated_transaction(assertion, unavailable, with_connection=True)
            try:
                refresh = self._service._refresh_verified if self._refresher is None else self._refresher.refresh
                result = refresh(assertion, scope, expected_revision=expected)
            except ProviderError:
                raise PolicyError(502, 'provider_unavailable') from None
        else:
            if set(body) != {'candidate_digest', 'readers', 'confirmation'} or body['confirmation'] != _CONFIRM or type(body['readers']) is not list:
                raise PolicyError(400, 'invalid_request')
            # Read/preview capability is mandatory on this user-facing approval
            # route, and rechecked in the same final transaction (see guard below).
            result = self._service._approve_verified(assertion, scope, expected_revision=expected,
                candidate_digest=body['candidate_digest'], expected_readers=body['readers'], require_review=True)
        return {'scope': scope.as_dict(), **result}

    def _status(self, policy, actor, connection, scope, *, budget=None):
        """Public exact status: no authority fields are added to its response."""
        return self._evaluate_status(policy, actor, connection, scope, budget=budget)[0]

    def _evaluate_status(self, policy, actor, connection, scope, *, budget=None):
        """The directory and exact-scope status share the SAME capabilities."""
        grants, current = {}, None
        for name in ('refresh', 'review', 'approve'):
            try:
                record = authorized(policy, actor, connection, scope, name, budget=budget)
                grants[name] = True
                current = record
            except PolicyError as error:
                if error.status not in (403, 404):
                    raise
                grants[name] = False
        if current is None or not any(grants.values()):
            raise PolicyError(404, 'not_found')
        # A user-facing approval needs preview; refresh needs trusted host opt-in.
        grants['approve'] = grants['approve'] and grants['review']
        grants['refresh'] = grants['refresh'] and self._allow_refresh
        if not any(grants.values()):
            raise PolicyError(404, 'not_found')
        public = {'scope': scope.as_dict(), 'revision': current.revision,
                'published_revision': current.published_revision if publication_current(policy, current) else None,
                'pending': current.candidate is not None, 'capabilities': grants}
        return public, [grant_fence(current, actor), grants]

    @staticmethod
    def _route(environ):
        path, verb = environ.get('PATH_INFO', ''), environ.get('REQUEST_METHOD', '')
        if type(path) is not str or len(path) > 1000 or environ.get('QUERY_STRING', ''):
            raise PolicyError(404, 'not_found')
        parts = path.split('/')
        if (len(parts) != 8 or parts[:3] != ['', 'api', 'workspaces'] or parts[4] != 'artifact-admin'
                or any(not _ID.fullmatch(p) for p in parts[3:])):
            raise PolicyError(404, 'not_found')
        action = parts[7]
        if (verb, action) not in {('GET', 'status'), ('GET', 'review'), ('POST', 'refresh'), ('POST', 'approve')}:
            raise PolicyError(404, 'not_found')
        return Scope(parts[3], parts[5], parts[6]), action

    @staticmethod
    def _body(environ):
        if environ.get('CONTENT_TYPE', '').lower() not in {'application/json', 'application/json; charset=utf-8'}:
            raise PolicyError(415, 'json_required')
        length = environ.get('CONTENT_LENGTH', '')
        if type(length) is not str or not re.fullmatch(r'[0-9]{1,6}', length):
            raise PolicyError(411, 'length_required')
        if int(length) > 16384:
            raise PolicyError(413, 'body_too_large')
        try:
            raw = environ['wsgi.input'].read(int(length))
            if type(raw) is not bytes or len(raw) != int(length):
                raise ValueError()
            body = json.loads(raw.decode('utf-8'), object_pairs_hook=_object,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if type(body) is not dict:
                raise ValueError()
            return body
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            raise PolicyError(400, 'invalid_request') from None
