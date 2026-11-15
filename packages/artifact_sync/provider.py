# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""OBS ListObjects only. No environment credentials, object reads or downloads."""
from base64 import b64encode
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import hashlib
import hmac
import http.client
import ipaddress
import re
import socket
import ssl
from urllib.parse import urlencode, unquote_to_bytes
import xml.etree.ElementTree as ET

from packages.artifacts import Artifact, Scope
from packages.artifacts.catalog import instant, key

MAX_OBJECTS = 500
_BUCKET = re.compile(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]\Z")
_REGION = re.compile(r"[a-z]{2}-[a-z]+-[1-9][0-9]{0,2}\Z")


class ProviderError(RuntimeError):
    """Only stable, value-free reasons escape the provider boundary."""
    def __init__(self, code):
        allowed = {'credential_scope_mismatch', 'invalid_clock', 'invalid_marker',
                   'provider_unavailable', 'invalid_response', 'response_too_large',
                   'moving_or_invalid_listing', 'scan_limit_reached', 'credential_unavailable',
                   'stale_observation', 'index_limit_or_invalid_result'}
        super().__init__(code if type(code) is str and code in allowed else 'provider_unavailable')


def object_key(value, *, empty=False):
    key(value, root=empty)
    if len(value.encode('utf-8')) > 1024 or len(value.split('/')) > 32:
        raise ValueError('invalid key')
    return value


@dataclass(frozen=True, slots=True)
class Connection:
    scope: Scope
    bucket: str
    region: str
    prefix: str = ''
    page_size: int = 50
    max_pages: int = 10
    window_hours: int = 24
    offset_minutes: int = 0
    timeout: int = 10
    max_response_bytes: int = 262144

    def __post_init__(self):
        if (type(self.scope) is not Scope or type(self.bucket) is not str or not _BUCKET.fullmatch(self.bucket)
                or type(self.region) is not str or not _REGION.fullmatch(self.region)):
            raise ValueError('invalid OBS connection')
        object_key(self.prefix, empty=True)
        if self.prefix and not self.prefix.endswith('/'):
            raise ValueError('prefix must be a directory boundary')
        for value, low, high in ((self.page_size,1,100),(self.max_pages,1,10),(self.window_hours,1,720),
                                 (self.offset_minutes,-840,840),(self.timeout,1,15),(self.max_response_bytes,1024,524288)):
            if type(value) is not int or not low <= value <= high:
                raise ValueError('invalid bound')
        if self.page_size*self.max_pages > MAX_OBJECTS:
            raise ValueError('scan exceeds object bound')

    @property
    def host(self):
        return f'{self.bucket}.obs.{self.region}.myhuaweicloud.com'

    @property
    def origin(self):
        return 'https://' + self.host


@dataclass(frozen=True, slots=True)
class BoundCredential:
    scope: Scope
    origin: str
    access_key: str = field(repr=False, compare=False)
    secret_key: str = field(repr=False, compare=False)
    security_token: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if type(self.scope) is not Scope or type(self.origin) is not str:
            raise ValueError('invalid credential binding')
        if type(self.access_key) is not str or not re.fullmatch(r'[A-Za-z0-9]{16,128}',self.access_key):
            raise ValueError('invalid credential')
        for value, maximum in ((self.secret_key,256),(self.security_token,8192)):
            if value is None and maximum==8192:
                continue
            if type(value) is not str or not 16 <= len(value) <= maximum or any(not 33 <= ord(c) <=126 for c in value):
                raise ValueError('invalid credential')


def _bound(connection, credential):
    if (type(connection) is not Connection or type(credential) is not BoundCredential
            or credential.scope!=connection.scope or credential.origin!=connection.origin):
        raise ProviderError('credential_scope_mismatch')


def _now(clock):
    value=clock()
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ProviderError('invalid_clock')
    return value.astimezone(timezone.utc)


def list_headers(connection, credential, now):
    """OBS-defined HMAC-SHA1 header authentication; never a signed URL."""
    _bound(connection,credential)
    date=format_datetime(_now(lambda:now),usegmt=True)
    extra='' if credential.security_token is None else f'x-obs-security-token:{credential.security_token}\n'
    canonical=f'GET\n\n\n{date}\n{extra}/{connection.bucket}/'
    signature=b64encode(hmac.new(credential.secret_key.encode('ascii'),canonical.encode('utf-8'),hashlib.sha1).digest()).decode('ascii')
    headers={'Date':date,'Authorization':f'OBS {credential.access_key}:{signature}',
             'Accept':'application/xml','Accept-Encoding':'identity'}
    if credential.security_token is not None:
        headers['x-obs-security-token']=credential.security_token
    return headers


class _PinnedHTTPS(http.client.HTTPSConnection):
    """Public DNS only; pin one answer for the socket and preserve TLS hostname."""
    def connect(self):
        addresses=socket.getaddrinfo(self.host,443,type=socket.SOCK_STREAM)
        if not addresses or len(addresses)>32:
            raise OSError('invalid DNS')
        for family,socktype,proto,canon,address in addresses:
            ip=ipaddress.ip_address(address[0])
            if family not in (socket.AF_INET,socket.AF_INET6) or not ip.is_global or ip.is_reserved or ip.is_multicast:
                raise OSError('nonpublic destination')
        family,socktype,proto,canon,address=addresses[0]
        raw=socket.socket(family,socktype,proto)
        try:
            raw.settimeout(self.timeout);raw.connect(address)
            self.sock=self._context.wrap_socket(raw,server_hostname=self.host)
        except BaseException:
            raw.close();raise


class OBSReadTransport:
    """Only a computed bucket-root GET. No proxy, redirect, retry or URL input."""
    def list_page(self, connection, credential, marker, now):
        _bound(connection,credential)
        if marker:
            try:object_key(marker)
            except (ValueError,TypeError):raise ProviderError('invalid_marker') from None
            if not marker.startswith(connection.prefix):raise ProviderError('invalid_marker')
        query={'prefix':connection.prefix,'max-keys':str(connection.page_size),'encoding-type':'url'}
        if marker:query['marker']=marker
        client=None
        try:
            client=_PinnedHTTPS(connection.host,timeout=connection.timeout,context=ssl.create_default_context())
            client.request('GET','/?'+urlencode(query),headers=list_headers(connection,credential,now))
            response=client.getresponse()
            if response.status!=200:raise ProviderError('provider_unavailable')
            if response.getheader('Content-Encoding','identity').lower()!='identity':raise ProviderError('invalid_response')
            if response.getheader('Content-Type','').split(';',1)[0].strip().lower() not in ('application/xml','text/xml'):
                raise ProviderError('invalid_response')
            raw=response.read(connection.max_response_bytes+1)
            if len(raw)>connection.max_response_bytes:raise ProviderError('response_too_large')
            return raw
        except ProviderError:raise
        except Exception:raise ProviderError('provider_unavailable') from None
        finally:
            if client is not None:client.close()


def _decode(value):
    if type(value) is not str or len(value)>4096 or re.search(r'%(?![0-9a-fA-F]{2})',value):
        raise ValueError('invalid URL encoding')
    return unquote_to_bytes(value).decode('utf-8','strict')


def parse_page(raw,connection,marker):
    """Normalize four metadata fields only; owner/ETag/URLs never leave parsing."""
    try:
        if type(raw) is not bytes or len(raw)>connection.max_response_bytes or re.search(br'<!\s*(?:DOCTYPE|ENTITY)',raw,re.I):
            raise ValueError()
        root=ET.fromstring(raw.decode('utf-8','strict'))
        namespaces=('http://obs.myhuaweicloud.com/doc/2015-06-30/',
                    f'http://obs.{connection.region}.myhuaweicloud.com/doc/2015-06-30/')
        namespace=next((n for n in namespaces if root.tag=='{'+n+'}ListBucketResult'),None)
        if namespace is None or len(list(root.iter()))>5000:raise ValueError()
        def children(parent,name):return parent.findall('{'+namespace+'}'+name)
        def one(parent,name,optional=False):
            values=children(parent,name)
            if not values and optional:return ''
            if len(values)!=1 or len(values[0]) or values[0].attrib:raise ValueError()
            return values[0].text or ''
        if (one(root,'Name')!=connection.bucket or one(root,'EncodingType')!='url'
                or _decode(one(root,'Prefix'))!=connection.prefix or _decode(one(root,'Marker'))!=marker
                or one(root,'MaxKeys')!=str(connection.page_size) or children(root,'CommonPrefixes')):
            raise ValueError()
        truncated=one(root,'IsTruncated')
        if truncated not in ('true','false'):raise ValueError()
        entries=children(root,'Contents')
        if len(entries)>connection.page_size:raise ValueError()
        rows=[];previous=marker
        for item in entries:
            name=object_key(_decode(one(item,'Key')))
            if not name.startswith(connection.prefix) or name<=previous:raise ValueError()
            size=one(item,'Size')
            if not re.fullmatch(r'[0-9]{1,16}',size) or int(size)>9007199254740991:raise ValueError()
            rows.append(Artifact(name,instant(one(item,'LastModified')).isoformat(),int(size),name.endswith('/')))
            previous=name
        next_marker=_decode(one(root,'NextMarker',optional=True))
        if truncated=='true' and (not rows or next_marker!=rows[-1].key or next_marker<=marker):raise ValueError()
        if truncated=='false' and next_marker:raise ValueError()
        return tuple(rows),next_marker
    except (ValueError,TypeError,UnicodeError,ET.ParseError,OverflowError,RecursionError):
        raise ProviderError('invalid_response') from None


@dataclass(frozen=True,slots=True)
class Observation:
    observed_at: str
    objects: tuple[Artifact,...]
    pages: int
    scanned: int


def collect(connection,credential,*,before_read,clock=lambda:datetime.now(timezone.utc),transport=None):
    """Bounded recent-window subset; this low-level library is NOT authorization."""
    _bound(connection,credential)
    if not callable(before_read):raise ValueError('current authorization callback required')
    cutoff=_now(clock);since=cutoff-timedelta(hours=connection.window_hours)
    transport=transport or OBSReadTransport();marker='';rows=[];seen=set();scanned=0
    for page in range(1,connection.max_pages+1):
        before_read()
        try:raw=transport.list_page(connection,credential,marker,_now(clock))
        except ProviderError:raise
        except Exception:raise ProviderError('provider_unavailable') from None
        values,next_marker=parse_page(raw,connection,marker)
        for value in values:
            if value.key in seen or instant(value.modified_at)>cutoff:raise ProviderError('moving_or_invalid_listing')
            seen.add(value.key);scanned+=1
            if instant(value.modified_at)>=since:rows.append(value)
        if not next_marker:
            return Observation(cutoff.isoformat(),tuple(rows),page,scanned)
        marker=next_marker
    raise ProviderError('scan_limit_reached')
