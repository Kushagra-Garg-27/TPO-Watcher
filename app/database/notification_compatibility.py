"""Guarded consent compatibility; no schema installation or runtime activation."""
from contextlib import contextmanager, closing
from pathlib import Path
from time import time_ns
import sqlite3


class ConsentBinding:
    def consent_repository(self, connection):
        from app.database.event_repository import EventRepository
        return EventRepository(connection,clock=lambda:time_ns()//1_000_000,record_mode=True)


def consent_binding(connection):
    """Recognize an existing fictional runtime, including OFF with old obligations."""
    from app.database.protected_payloads import require_binding
    require_binding(connection)
    markers={r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('notification_runtime','notification_schema_migrations','subscriber_versions')")}
    if not markers:return None
    from app.database.notification_migrator import require_connection, SchemaError, VERSION, SCHEMA_SHA256
    require_connection(connection)
    if len(markers)!=3:raise SchemaError("incomplete notification consent runtime")
    rows=connection.execute("SELECT singleton,config_version,policy_version,writer_mode,fanout_mode,send_mode FROM notification_runtime").fetchall()
    if len(rows)!=1 or tuple(rows[0][:3])!=(1,"F-S0","F-S0") or any(v not in ("OFF","V2") for v in rows[0][3:]):
        raise SchemaError("unsupported notification consent runtime")
    metadata=connection.execute("SELECT version,checksum,phase FROM notification_schema_migrations").fetchall()
    if len(metadata)!=1 or tuple(metadata[0])!=(VERSION,SCHEMA_SHA256,"FICTIONAL_ONLY"):
        raise SchemaError("notification consent schema identity mismatch")
    required={"subscriber_create","subscriber_mutation_guard","subscriber_epochs",
        "prefs_insert_guard","prefs_insert_epoch","prefs_update_guard","prefs_update_epoch",
        "prefs_delete_guard","prefs_delete_epoch"}
    triggers={r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    if not required<=triggers:raise SchemaError("missing consent epoch trigger")
    return ConsentBinding()


def repository_factory(path):
    from app.database.sqlite_repository import SQLiteUserRepository, SQLiteTokenRepository
    users,tokens=SQLiteUserRepository(path),SQLiteTokenRepository(path)
    # Validate the configured runtime at actual factory construction as well as
    # at each later connection. No mode or epoch is changed here.
    with users._get_conn():pass
    return users,tokens


def account_transport_required(path):
    """Read existing metadata only; a mail factory cannot create/migrate a DB."""
    target=Path(path)
    if not target.is_file():return False
    with closing(sqlite3.connect(target.resolve().as_uri()+"?mode=ro",uri=True)) as connection:
        connection.row_factory=sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return consent_binding(connection) is not None


@contextmanager
def repository_unit(repositories):
    """Compose existing helpers without independent commits; no open connection escapes."""
    from app.database.sqlite_repository import SQLiteBaseRepository
    participants=tuple(repositories)
    if not participants or not all(isinstance(r,SQLiteBaseRepository) for r in participants):
        # Preserve protocol-based legacy dependency overrides.
        yield
        return
    if len({Path(r.db_path).resolve() for r in participants})!=1:
        raise ValueError("account unit requires one database")
    if any(r._unit_connection is not None for r in participants):
        raise RuntimeError("nested account unit forbidden")
    manager=participants[0]._get_conn()
    connection=None
    try:
        with manager as connection:
            if not connection.in_transaction:connection.execute("BEGIN IMMEDIATE")
            for repository in participants:repository._unit_connection=connection
            try:yield
            except BaseException:
                connection.rollback()
                raise
            else:connection.commit()
            finally:
                for repository in participants:repository._unit_connection=None
    finally:
        if connection is not None:connection.close()


def account_request_response(repositories, legacy_message):
    """Compatible fixtures expose neither existence nor internal transport status."""
    from app.database.sqlite_repository import SQLiteBaseRepository
    user=repositories[0]
    compatible=False
    if isinstance(user,SQLiteBaseRepository):
        with user._get_conn() as connection:compatible=user._consent_binding(connection) is not None
    return {"status":"success","message":(
        "Request received. If eligible, an account link will be attempted. Please check your inbox."
        if compatible else legacy_message)}
