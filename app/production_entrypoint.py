"""Explicit production startup. No work occurs on import.

Usage: python -m app.production_entrypoint --config /run/tpo/runtime.json
The configuration grants source observation only when the operator supplied its
authorization evidence. Physical mail requires a separate bounded pilot object.
"""
import argparse
import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import signal
import time
from types import SimpleNamespace
from urllib.parse import urlsplit


def _unique(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('duplicate runtime configuration field')
        result[key]=value
    return result


@dataclass(frozen=True)
class RuntimeFileConfig:
    public_origin:str
    database:str
    database_sha256:str
    vault:str
    checkpoints:str
    wrapping_keys:dict
    active_generation:int
    source_authorization:dict
    listing_contract:dict
    legacy_shutdown:dict
    auth_username_file:str
    auth_password_file:str
    session_state:str
    session_secret_file:str
    pilot:dict|None=None
    smtp:dict|None=None
    runtime:dict|None=None
    poll_interval_ms:int=60000
    public_host:str='0.0.0.0'
    public_port:int=8000

    @classmethod
    def from_mapping(cls,data):
        if type(data) is not dict or data.get('version')!=1:raise ValueError('runtime configuration version 1 required')
        values=dict(data);values.pop('version')
        if set(values)-set(cls.__dataclass_fields__):raise ValueError('unknown runtime configuration field')
        try:result=cls(**values)
        except TypeError:raise ValueError('complete explicit runtime configuration required') from None
        origin=urlsplit(result.public_origin)
        if (origin.scheme!='https' or not origin.hostname or origin.username or origin.password
                or origin.path or origin.query or origin.fragment
                or result.public_origin!='https://'+origin.netloc
                or any(c.isspace() for c in result.public_origin)):
            raise ValueError('explicit canonical approved HTTPS public origin required')
        if (result.pilot is None)!=(result.smtp is None):raise ValueError('SMTP requires separate pilot authorization')
        if (not isinstance(result.database_sha256,str) or len(result.database_sha256)!=64
                or any(c not in '0123456789abcdefABCDEF' for c in result.database_sha256)
                or type(result.active_generation) is not int or result.active_generation<1
                or not isinstance(result.wrapping_keys,dict) or str(result.active_generation) not in result.wrapping_keys
                or type(result.poll_interval_ms) is not int or not 1000<=result.poll_interval_ms<=3600000
                or type(result.public_port) is not int or not 1<=result.public_port<=65535
                or not isinstance(result.public_host,str) or not result.public_host):
            raise ValueError('bounded explicit runtime configuration required')
        for name in ('database','vault','checkpoints','auth_username_file','auth_password_file','session_state','session_secret_file'):
            if not isinstance(getattr(result,name),str) or not Path(getattr(result,name)).is_absolute():
                raise ValueError('absolute external runtime paths required')
        from app.tpo.production_source import SourceAuthorization
        from app.tpo.listing import ListingContract
        from app.monitoring.production_runtime import LegacyShutdownEvidence,ProductionConfig,PilotAuthorization
        try:
            SourceAuthorization(**result.source_authorization)
            contract=ListingContract(**result.listing_contract)
            if contract.allow_complete:raise ValueError('VIERP response is not a complete census')
            LegacyShutdownEvidence(**result.legacy_shutdown).validate()
            runtime=dict(result.runtime or {})
            if runtime.get('action_base',result.public_origin)!=result.public_origin:
                raise ValueError('runtime action links must use the approved public origin')
            runtime['action_base']=result.public_origin
            ProductionConfig(**runtime)
            if result.pilot is not None:
                pilot=dict(result.pilot)
                pilot['recipients']=tuple(pilot['recipients'])
                if 'message_classes' in pilot:pilot['message_classes']=tuple(pilot['message_classes'])
                PilotAuthorization(**pilot)
                if set(result.smtp)!={'host','port','username_file','password_file','sender'}:
                    raise ValueError('SMTP settings must reference injected credential mounts')
        except (TypeError,KeyError):raise ValueError('invalid runtime capability configuration') from None
        return result


def load_config(path):
    path=Path(path)
    if not path.is_absolute() or path.is_symlink() or path.stat().st_size>65536:
        raise ValueError('bounded explicit runtime configuration file required')
    return RuntimeFileConfig.from_mapping(json.loads(path.read_text(encoding='utf-8'),object_pairs_hook=_unique))


def _external(path,*,exists=True):
    candidate=Path(path)
    repository=Path(__file__).resolve().parent.parent
    if (not candidate.is_absolute() or candidate.is_symlink()
            or candidate.resolve().is_relative_to(repository) or exists and not candidate.is_file()):
        raise ValueError('external operator-managed mount required')
    return candidate


def _secret(path,*,binary=False):
    candidate=_external(path)
    if candidate.stat().st_size>8192 or os.name!='nt' and candidate.stat().st_mode & 0o077:
        raise ValueError('bounded owner-only credential mount required')
    value=candidate.read_bytes()
    if binary:
        if len(value)!=32:raise ValueError('32-byte injected wrapping key required')
        return value
    text=value.decode('utf-8').strip()
    if not text:raise ValueError('empty credential mount rejected')
    return text


@contextmanager
def configured_runtime(config):
    """Open an already migrated/enrolled store; never provision keys or schema."""
    from app.database.notification_migrator import ProductionDatabaseAuthority
    from app.database.production_key_provider import ProductionKeyProvider,SQLiteCheckpointStore
    from app.database.protected_payloads import ProtectedPayloads,protected_scope
    from app.database.sqlite_repository import SQLiteUserRepository,SQLiteTokenRepository,SQLiteDeliveryRepository
    from app.auth.manager import AuthManager
    from app.config import settings
    from app.tpo.production_source import AuthenticatedVIERPSource,SourceAuthorization
    from app.tpo.listing import ListingContract
    from app.monitoring.production_runtime import ProductionRuntime,ProductionConfig,LegacyShutdownEvidence,PilotAuthorization
    from app.notifications.production_smtp import SMTPSettings,SMTPTransport
    if not isinstance(config,RuntimeFileConfig):raise ValueError('typed runtime file configuration required')
    authority=ProductionDatabaseAuthority.bind(_external(config.database),expected_sha256=config.database_sha256)
    _external(config.vault);_external(config.checkpoints)
    generation_keys={int(k):_secret(v,binary=True) for k,v in config.wrapping_keys.items()}
    provider=ProductionKeyProvider(config.vault,wrapping_keys=generation_keys,active_generation=config.active_generation,
        checkpoints=SQLiteCheckpointStore(config.checkpoints))
    settings.TPO_USERNAME=_secret(config.auth_username_file)
    settings.TPO_PASSWORD=_secret(config.auth_password_file)
    settings.SECRET_KEY=_secret(config.session_secret_file)
    if len(settings.SECRET_KEY)<32:raise ValueError('independent strong web session secret required')
    runtime_options=dict(config.runtime or {});runtime_options['action_base']=config.public_origin
    runtime_config=ProductionConfig(**runtime_options)
    settings.BASE_URL=runtime_config.action_base
    settings.PUBLIC_HOST,settings.PUBLIC_PORT=config.public_host,config.public_port
    settings.COOKIE_SECURE=True
    auth=AuthManager();auth.state_file=str(_external(config.session_state,exists=False))
    if not Path(auth.state_file).parent.is_dir():raise ValueError('external writable browser session directory required')
    clock=lambda:int(time.time()*1000)
    source=AuthenticatedVIERPSource(authorization=SourceAuthorization(**config.source_authorization),
        contract=ListingContract(**config.listing_contract),clock=clock,auth=auth)
    pilot=transport=None
    if config.pilot is not None:
        approved=dict(config.pilot);approved['recipients']=tuple(approved['recipients'])
        if 'message_classes' in approved:approved['message_classes']=tuple(approved['message_classes'])
        pilot=PilotAuthorization(**approved)
        smtp=config.smtp
        transport=SMTPTransport(SMTPSettings(smtp['host'],smtp['port'],_secret(smtp['username_file']),
            _secret(smtp['password_file']),smtp['sender']))
    with authority.scope():
        codec=ProtectedPayloads(provider,str(authority.path))
        with protected_scope(authority.path,codec),authority.connect() as connection:
            database=SimpleNamespace(db_path=str(authority.path),
                users=SQLiteUserRepository.open_existing(str(authority.path),connection=connection),
                tokens=SQLiteTokenRepository.open_existing(str(authority.path),connection=connection),
                deliveries=SQLiteDeliveryRepository.open_existing(str(authority.path),connection=connection))
            runtime=ProductionRuntime(authority=authority,connection=connection,database=database,codec=codec,
                source=source,clock=clock,exclusion=LegacyShutdownEvidence(**config.legacy_shutdown),
                config=runtime_config,pilot=pilot,transport=transport)
            try:yield runtime
            finally:runtime.stop()


def application_for(runtime):
    from app.main import create_application
    from app.api import routes_auth,routes_preferences
    from app.api.email_service import EmailService,get_email_service
    from app.database.notification_compatibility import repository_factory
    application=create_application(runtime=runtime)
    application.state.account_public_origin=runtime.action_base
    def repositories():return repository_factory(str(runtime.authority.path))
    def email():return EmailService(transport=runtime.account_gateway(),account_transport_required=True)
    application.dependency_overrides[routes_auth.get_db_repos]=repositories
    application.dependency_overrides[routes_preferences.get_db_repos]=repositories
    application.dependency_overrides[get_email_service]=email
    return application


async def serve(config):
    import uvicorn
    from app.logging_config import setup_logging
    from app.main import get_uvicorn_config
    setup_logging()
    stop=asyncio.Event()
    loop=asyncio.get_running_loop()
    for kind in (signal.SIGINT,signal.SIGTERM):
        try:loop.add_signal_handler(kind,stop.set)
        except (NotImplementedError,RuntimeError):pass
    with configured_runtime(config) as runtime:
        runtime.start()
        server=uvicorn.Server(get_uvicorn_config(application_for(runtime)))
        server.capture_signals=lambda:__import__('contextlib').nullcontext()
        tasks=[asyncio.create_task(runtime.run(stop,poll_interval_ms=config.poll_interval_ms)),
               asyncio.create_task(server.serve())]
        try:
            done,_=await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
            for task in done:task.result()
        finally:
            stop.set();server.should_exit=True
            try:await asyncio.wait_for(asyncio.gather(*tasks,return_exceptions=True),timeout=40)
            except TimeoutError:
                for task in tasks:task.cancel()
                await asyncio.gather(*tasks,return_exceptions=True)
            runtime.stop()


def main():
    parser=argparse.ArgumentParser(description='Explicit TPO production source and notification runtime')
    parser.add_argument('--config',required=True)
    args=parser.parse_args()
    asyncio.run(serve(load_config(args.config)))


if __name__=='__main__':main()
