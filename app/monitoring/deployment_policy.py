"""Inert public-export contract. No settings, repository, custody or transport imports."""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import time


class PolicyError(ValueError):
    """Non-secret operational failure; never include supplied values or paths."""


def _json(raw):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise PolicyError('Duplicate JSON field')
            result[key]=value
        return result
    def constant(value):raise PolicyError('Non-finite JSON value')
    try:return json.loads(raw,object_pairs_hook=pairs,parse_constant=constant)
    except (ValueError,TypeError,UnicodeError,RecursionError):raise PolicyError('Invalid JSON document') from None


def _canonical(path):
    try:
        value=Path(path)
        if not value.is_absolute() or '..' in value.parts or value.resolve()!=value.absolute():
            raise PolicyError('Canonical absolute path required')
        return value
    except (TypeError,ValueError,OSError):raise PolicyError('Invalid operational path') from None


def _test_root():
    guard=sys.modules.get('isolation_bootstrap')
    return Path(guard.ROOT).resolve() if guard and guard._installed and guard.ROOT is not None else None


def _read(path,limit):
    path=_canonical(path)
    descriptor=None
    try:
        descriptor=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_BINARY',0))
        info=os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 0<info.st_size<=limit:
            raise PolicyError('Bounded regular file required')
        result=b''
        while len(result)<=limit:
            block=os.read(descriptor,min(65536,limit+1-len(result)))
            if not block:break
            result+=block
        if len(result)!=info.st_size or len(result)>limit:raise PolicyError('Operational file changed')
        return result
    except (OSError,ValueError):raise PolicyError('Required read-only file unavailable') from None
    finally:
        if descriptor is not None:os.close(descriptor)


@dataclass(frozen=True)
class InertPolicy:
    version: int
    profile: str
    snapshot_root: str
    snapshot_sha256: str

    def __post_init__(self):
        if type(self.version) is not int or self.version!=1 or self.profile!='INERT_REHEARSAL':
            raise PolicyError('Only version 1 inert rehearsal is supported')
        if not isinstance(self.snapshot_root,str):raise PolicyError('Invalid snapshot root')
        root=_canonical(self.snapshot_root)
        test=_test_root()
        if str(root)!='/run/tpo/rehearsal' and not (test and root!=test and root.is_relative_to(test)):
            raise PolicyError('Snapshot root not approved')
        if not isinstance(self.snapshot_sha256,str) or re.fullmatch('[0-9a-f]{64}',self.snapshot_sha256) is None:
            raise PolicyError('Snapshot SHA256 required')


def load_policy(path):
    path=_canonical(path)
    test=_test_root()
    if path.name!='runtime-policy.json' or not (str(path)=='/run/tpo/runtime-policy.json' or test and path.is_relative_to(test)):
        raise PolicyError('Policy path not approved')
    value=_json(_read(path,16384))
    if type(value) is not dict or set(value)!={'version','profile','snapshot_root','snapshot_sha256'}:
        raise PolicyError('Exact non-secret inert policy fields required')
    return InertPolicy(**value)


@contextmanager
def read_snapshot(policy):
    if type(policy) is not InertPolicy:raise PolicyError('Validated inert policy required')
    policy.__post_init__()
    raw=_read(Path(policy.snapshot_root)/'opportunities.sqlite',8*1024*1024)
    if hashlib.sha256(raw).hexdigest()!=policy.snapshot_sha256:raise PolicyError('Public snapshot digest mismatch')
    if raw[:16]!=b'SQLite format 3\0' or raw[18:20]!=b'\x01\x01':
        raise PolicyError('Standalone rollback-format snapshot required')
    path=Path(policy.snapshot_root)/'opportunities.sqlite'
    if not (_test_root() and path.is_relative_to(_test_root())) and (
        os.access(path.parent,os.W_OK) or os.access(path,os.W_OK)):
        raise PolicyError('Read-only snapshot mount and file permissions required')
    if any(Path(str(path)+suffix).exists() for suffix in ('-wal','-shm','-journal')):
        raise PolicyError('Standalone snapshot without sidecars required')
    c=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    try:
        c.execute('PRAGMA query_only=ON')
        c.execute('BEGIN')
        def bootstrap_authorize(action,arg1,arg2,database,trigger):
            if action==sqlite3.SQLITE_SELECT:return sqlite3.SQLITE_OK
            if action==sqlite3.SQLITE_READ and arg1 in ('sqlite_master','public_listing'):return sqlite3.SQLITE_OK
            if action==sqlite3.SQLITE_PRAGMA and arg1 in ('page_count','page_size','integrity_check','journal_mode') and arg2 is None:return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        c.set_authorizer(bootstrap_authorize)
        pages=c.execute('PRAGMA page_count').fetchone()[0];size=c.execute('PRAGMA page_size').fetchone()[0]
        if not 0<pages*size<=8*1024*1024:raise PolicyError('Bounded public snapshot required')
        if hashlib.sha256(c.serialize()).hexdigest()!=policy.snapshot_sha256:
            raise PolicyError('Connected public snapshot digest mismatch')
        if c.execute('PRAGMA journal_mode').fetchone()[0]!='delete':raise PolicyError('Rollback-format snapshot required')
        tables=c.execute('SELECT name,type,sql FROM sqlite_master').fetchall()
        expected='createtablepublic_listing(singletonintegerprimarykey,payloadtextnotnull)'
        if len(tables)!=1 or tables[0][:2]!=('public_listing','table') or re.sub(r'\s+','',tables[0][2]).casefold()!=expected:
            raise PolicyError('Exact public-only snapshot schema required')
        if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise PolicyError('Invalid public snapshot')
        def authorize(action,arg1,arg2,database,trigger):
            if action==sqlite3.SQLITE_SELECT:return sqlite3.SQLITE_OK
            if action==sqlite3.SQLITE_READ and arg1=='public_listing':return sqlite3.SQLITE_OK
            if action==sqlite3.SQLITE_FUNCTION and arg2=='count':return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        c.set_authorizer(authorize)
        yield c
    except sqlite3.Error:raise PolicyError('Public snapshot unavailable') from None
    finally:c.close()


NULLABLE=frozenset(('max_package','min_package','placement_type','registration_end','eligible_programs',
    'company_type','first_seen_at','registration_start','registration_start_time','registration_end_time',
    'registration_start_alternate','registration_end_alternate','deadline_timezone','compensation_unit',
    'organizations','job_title','job_description','academic_year'))
ITEM_FIELDS=NULLABLE|{'id','company','is_active','listing_status','eligibility_status','observed_ms','valid_until_ms'}


def public_listing(policy,*,now_ms=None):
    with read_snapshot(policy) as c:rows=c.execute('SELECT singleton,payload FROM public_listing').fetchall()
    if len(rows)!=1 or rows[0][0]!=1:raise PolicyError('One public projection required')
    value=_json(rows[0][1])
    integer=lambda n:type(n) is int and 0<=n<=9007199254740991
    if (type(value) is not dict or set(value)!={'items','total','shown','status','observed_ms'}
        or type(value['items']) is not list or len(value['items'])>5
        or not integer(value['total']) or value['total']>100000 or value['shown']!=len(value['items'])
        or type(value['shown']) is not int or value['total']<value['shown']
        or value['status'] not in ('VERIFIED','STALE','UNVERIFIED')
        or not (value['observed_ms'] is None or integer(value['observed_ms']))):raise PolicyError('Invalid public projection')
    if value['status']!='UNVERIFIED' and value['observed_ms'] is None:raise PolicyError('Missing observation time')
    if value['items'] and value['status']!='VERIFIED':raise PolicyError('Invalid current projection')
    ids=set()
    for item in value['items']:
        if (type(item) is not dict or set(item)!=ITEM_FIELDS
            or any(item[k] is not None and (not isinstance(item[k],str) or len(item[k])>20000) for k in NULLABLE)
            or any(not isinstance(item[k],str) or not item[k].strip() or len(item[k])>512 for k in ('id','company'))
            or item['id'] in ids or item['is_active']!='True' or item['listing_status']!='CURRENT'
            or item['eligibility_status']!='PROGRAMME_MATCH_ONLY'
            or not integer(item['observed_ms']) or not integer(item['valid_until_ms'])
            or item['valid_until_ms']<item['observed_ms'] or value['observed_ms']<item['observed_ms']
            or item['compensation_unit'] not in (None,'LPA','monthly stipend','annual salary')):
            raise PolicyError('Invalid public opportunity')
        ids.add(item['id'])
    now=int(time.time()*1000) if now_ms is None else now_ms
    if not integer(now):raise PolicyError('Invalid projection clock')
    if any(not item['observed_ms']<=now<=item['valid_until_ms'] for item in value['items']):
        return dict(items=[],total=0,shown=0,status='STALE',observed_ms=value['observed_ms'])
    return value
