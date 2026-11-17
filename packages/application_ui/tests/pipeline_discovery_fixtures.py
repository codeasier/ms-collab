"""Synthetic saved snapshots only. Never imported by production factories."""
from packages.pipeline_api import encode_snapshots, resource_id
from packages.pipeline_api.tests.test_application import run
from packages.pipelines import PipelineScope
from packages.workspaces import Membership, Resource


def seed_directory(store):
    def provision(policy):
        for workspace in ('beta', 'commons'):
            policy._grant_membership(Membership(workspace, 'admin', 'admin'))
        for workspace in ('alpha', 'beta', 'commons'):
            scope = PipelineScope(workspace, 'example-ci', 'build')
            row = run(scope)
            row['stages'][0]['name'] = workspace + ' checks'
            row['stages'][0]['jobs'][0]['name'] = workspace + ' <img src=x onerror=alert(1)>'
            record = Resource(workspace, 'pipelines', resource_id(scope), encode_snapshots(scope, [row]))
            policy._resources[(workspace, 'pipelines', record.id)] = record
        scope = PipelineScope('alpha', 'example-ci', 'empty')
        record = Resource('alpha', 'pipelines', resource_id(scope), encode_snapshots(scope, []))
        policy._resources[('alpha', 'pipelines', record.id)] = record
    store._transaction(provision, write=True)


def seed_pages(store):
    def provision(policy):
        for workspace in ('alpha', 'beta'):
            for i in range(26):
                scope = PipelineScope(workspace, 'example-ci', f'p{i:03d}')
                record = Resource(workspace, 'pipelines', resource_id(scope), encode_snapshots(scope, []))
                policy._resources[(workspace, 'pipelines', record.id)] = record
    store._transaction(provision, write=True)
