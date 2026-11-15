# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Strict persistent grant fences; never identity mutation or authentication.

Only trusted same-transaction code may capture current authority for an explicit
new grant. Loading old records must validate, never call capture as a fallback.
"""
import re

from .model import identifier

_MAX_GENERATION = 9007199254740991
_MAX_GRANTEES = 3000
_BINDING_KEYS = {'issuer', 'subject', 'generation'}


def _ids(values):
    if type(values) not in (tuple, list, set, frozenset) or len(values) > _MAX_GRANTEES:
        raise ValueError('invalid_authority_recipients')
    result = set()
    for value in values:
        identifier(value)
        result.add(value)
    return result


def principal_pin(policy, actor):
    """Current active principal, suitable only for an in-flight equality fence."""
    principal = policy._principal(actor)
    return policy._realm, principal.id, principal.issuer, principal.subject, principal.generation


def capture_authority(policy, recipients):
    """Explicit new grant capture, not a record decoder or legacy upgrader."""
    identities = _ids(recipients)
    if not identities <= policy._principals.keys():
        raise ValueError('unknown_authority_principal')
    result = {'version':1, 'realm':policy._realm, 'principals':{
        ident:{'issuer':policy._principals[ident].issuer,
               'subject':policy._principals[ident].subject,
               'generation':policy._principals[ident].generation}
        for ident in sorted(identities)}}
    validate_authority(result, identities)
    return result


def validate_authority(value, recipients):
    """Strict structure and exact configured recipient set; never fill defaults."""
    identities = _ids(recipients)
    if (type(value) is not dict or set(value) != {'version','realm','principals'}
            or type(value['version']) is not int or value['version'] != 1
            or type(value['realm']) is not str or re.fullmatch(r'[0-9a-f]{32}',value['realm']) is None
            or type(value['principals']) is not dict or set(value['principals']) != identities):
        raise ValueError('invalid_authority_fence')
    for ident, binding in value['principals'].items():
        identifier(ident)
        if type(binding) is not dict or set(binding) != _BINDING_KEYS:
            raise ValueError('invalid_authority_fence')
        identifier(binding['issuer']);identifier(binding['subject'])
        if type(binding['generation']) is not int or not 1 <= binding['generation'] <= _MAX_GENERATION:
            raise ValueError('invalid_authority_fence')


def authority_current(policy, actor, grant):
    """Use only a previously validated record; unknown/inactive identities deny."""
    # Still fail closed if a trusted caller accidentally supplies a partial value.
    if type(grant) is not dict or type(grant.get('principals')) is not dict:
        return False
    try:
        validate_authority(grant, tuple(grant['principals']))
    except (ValueError, TypeError):
        return False
    principal = policy._principals.get(actor)
    return (principal is not None and principal.active and grant['realm'] == policy._realm
            and grant['principals'].get(actor) == {'issuer':principal.issuer,
                'subject':principal.subject, 'generation':principal.generation})
