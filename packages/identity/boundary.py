"""Fail-closed adapter seam. This is NOT an OAuth/OIDC or JWT implementation.

Verifier and directory objects are trusted server dependencies. No implementation
that accepts unsigned claims, request headers, or a demo identity ships here.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import Callable, Protocol
from urllib.parse import urlsplit

from packages.workspaces import Principal


class AuthenticationError(Exception):
    """Safe, deliberately non-enumerating response for invalid identity."""
    status = 401

    def __init__(self):
        super().__init__("authentication_required")


class AuthenticationUnavailable(Exception):
    """No provider response, exception or credential is included in this error."""
    status = 503

    def __init__(self):
        super().__init__("authentication_unavailable")


def _text(value, limit=200):
    if type(value) is not str or not value or len(value) > limit or any(
            c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("invalid identity field")


def _issuer(value):
    _text(value)
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment):
        raise ValueError("issuer must be an explicit HTTPS identifier")


def _instant(value):
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")


@dataclass(frozen=True)
class Credential:
    """Opaque verifier input, never a principal ID. Do not serialize or log it.

    repr suppression is accidental-log protection, not secure memory erasure.
    HTTP credential extraction, rate limiting and request limits are not here.
    """
    value: str = field(repr=False)

    def __post_init__(self):
        _text(self.value, 8192)


@dataclass(frozen=True)
class VerifiedIdentity:
    """Trusted verifier output only; constructing this object authenticates nobody.

    The verifier attests the selected issuer and audience and must perform the
    protocol's actual signature/exchange, token-type, expiry and replay checks.
    This short-lived assertion cannot extend upstream credential validity.
    """
    issuer: str
    subject: str
    audience: str
    issued_at: datetime
    expires_at: datetime

    def __post_init__(self):
        _issuer(self.issuer)
        _text(self.subject)
        _text(self.audience)
        _instant(self.issued_at)
        _instant(self.expires_at)
        if self.expires_at <= self.issued_at:
            raise ValueError("invalid assertion interval")


class Verifier(Protocol):
    def verify(self, credential: Credential) -> VerifiedIdentity:
        """Verify an opaque credential, or raise AuthenticationError to deny."""
        ...


class PrincipalDirectory(Protocol):
    def resolve(self, issuer: str, subject: str) -> Principal | None:
        """Read the CURRENT explicit binding. No automatic account provisioning."""
        ...


@dataclass(frozen=True)
class Provider:
    key: str
    issuer: str
    audience: str
    verifier: Verifier = field(repr=False)
    max_assertion_age: timedelta = timedelta(minutes=5)

    def __post_init__(self):
        _text(self.key)
        _issuer(self.issuer)
        _text(self.audience)
        if (type(self.max_assertion_age) is not timedelta
                or not timedelta(0) < self.max_assertion_age <= timedelta(hours=1)):
            raise ValueError("invalid assertion age limit")
        if not callable(getattr(self.verifier, "verify", None)):
            raise ValueError("verifier is required")


class StaticPrincipalDirectory:
    """Immutable trusted bootstrap directory for tests/offline integration.

    No client-facing enrollment, relinking, management or persistence API. A live
    deployment requires a current transactional directory instead of this class.
    """
    def __init__(self, principals: tuple[Principal, ...]):
        by_pair, ids = {}, set()
        for principal in principals:
            if type(principal) is not Principal:
                raise ValueError("invalid principal record")
            _text(principal.id)
            _issuer(principal.issuer)
            _text(principal.subject)
            pair = (principal.issuer, principal.subject)
            if principal.id in ids or pair in by_pair:
                raise ValueError("duplicate identity binding")
            by_pair[pair] = principal
            ids.add(principal.id)
        self._principals = MappingProxyType(by_pair)

    def resolve(self, issuer: str, subject: str) -> Principal | None:
        return self._principals.get((issuer, subject))


class Authenticator:
    """Trusted server entry point. No default provider and no fallback identity.

    Returned Principal is a current-request identity snapshot, NOT a session,
    authorization decision or portable capability. Never cache it as authority;
    workspace operations must still reauthorize against their latest policy.
    """
    def __init__(self, *, providers: tuple[Provider, ...], directory: PrincipalDirectory,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        registry = {}
        for provider in providers:
            if type(provider) is not Provider or provider.key in registry:
                raise ValueError("invalid or duplicate provider")
            registry[provider.key] = provider
        if not callable(getattr(directory, "resolve", None)) or not callable(clock):
            raise ValueError("directory and clock are required")
        self._providers = MappingProxyType(registry)
        self._directory = directory
        self._clock = clock

    def authenticate(self, provider_key: str, credential: Credential) -> Principal:
        # provider_key chooses only a preconfigured verifier, never an URL.
        if type(provider_key) is not str or type(credential) is not Credential:
            raise AuthenticationError()
        provider = self._providers.get(provider_key)
        if provider is None:
            raise AuthenticationError()
        try:
            assertion = provider.verifier.verify(credential)
        except AuthenticationError:
            raise AuthenticationError() from None
        except Exception:
            raise AuthenticationUnavailable() from None
        if (type(assertion) is not VerifiedIdentity or assertion.issuer != provider.issuer
                or assertion.audience != provider.audience):
            raise AuthenticationError()
        self._check_time(assertion, provider)
        try:
            principal = self._directory.resolve(assertion.issuer, assertion.subject)
        except Exception:
            raise AuthenticationUnavailable() from None
        # Validate the directory result, not merely the requested lookup key.
        if (type(principal) is not Principal or not principal.active
                or (principal.issuer, principal.subject) != (assertion.issuer, assertion.subject)):
            raise AuthenticationError()
        try:
            _text(principal.id)
        except ValueError:
            raise AuthenticationError() from None
        # A slow verifier or directory must not turn an expired assertion into a login.
        self._check_time(assertion, provider)
        return principal

    def _check_time(self, assertion, provider):
        try:
            now = self._clock()
            _instant(now)
        except Exception:
            raise AuthenticationUnavailable() from None
        if (not assertion.issued_at <= now < assertion.expires_at
                or assertion.expires_at - assertion.issued_at > provider.max_assertion_age
                or now - assertion.issued_at >= provider.max_assertion_age):
            raise AuthenticationError()
