"""Exercise guards through intercepted calls, never actual dangerous operations."""
import asyncio
import os
from pathlib import Path
import smtplib
import socket
import sqlite3
import _sqlite3
import subprocess
import stat as stat_module
import sys
import urllib.request
from unittest.mock import Mock

import httpx
import pytest
import isolation_bootstrap as isolation


# These checks run during collection, before ordinary fixtures. No application
# modules are imported by this regression module's top level.
assert isolation.disposable_path(os.environ["DB_PATH"])
assert isolation.disposable_path(os.environ["STATE_FILE"])


@pytest.mark.parametrize("transport", [smtplib.SMTP, smtplib.SMTP_SSL])
def test_smtp_is_denied_without_network(transport):
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        transport("smtp.invalid", 587)


@pytest.mark.parametrize("url", ["http://external.invalid/", "https://external.invalid/"])
def test_unmocked_httpx_transport_is_denied(url):
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        httpx.get(url)


@pytest.mark.asyncio
async def test_unmocked_async_http_transport_is_denied():
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        async with httpx.AsyncClient() as client:
            await client.get("https://external.invalid/")


def test_unmocked_urllib_is_denied_before_dns():
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        urllib.request.urlopen("https://external.invalid/")


@pytest.mark.parametrize("address", [("192.0.2.1", 443), ("127.0.0.1", 8000)])
def test_external_and_arbitrary_localhost_socket_denied(address):
    native = Mock()
    guarded = isolation._guard_socket_call(native, "connect")
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        guarded(object(), address)
    native.assert_not_called()


def test_external_dns_is_denied():
    native = Mock()
    guarded = isolation._guard_resolver(native, "getaddrinfo")
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        guarded("external.invalid", 443)
    native.assert_not_called()


@pytest.mark.parametrize("database", [
    "watcher.sqlite",
    str(isolation.REPOSITORY / "watcher.sqlite"),
    str(isolation.REPOSITORY / "never-open-other.db"),
    "file:" + (isolation.REPOSITORY / "never-open.sqlite").as_posix() + "?mode=ro",
])
def test_persistent_database_is_denied_before_open(database):
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        sqlite3.connect(database)


def test_sqlite_attach_cannot_escape_temp_root(tmp_path):
    with sqlite3.connect(str(tmp_path / "synthetic.sqlite")) as conn:
        with isolation.expected_violation(), pytest.raises(sqlite3.DatabaseError):
            conn.execute("ATTACH DATABASE ? AS forbidden", (":memory:",))


@pytest.mark.parametrize("relative", [".env", ".env.production", "playwright_state.json", ".auth/never-read.json"])
def test_sensitive_file_reads_are_denied(relative):
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        (isolation.REPOSITORY / relative).read_bytes()


def test_persistent_file_mutations_are_denied():
    candidate = isolation.REPOSITORY / "must-not-create-or-remove.guard"
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        candidate.write_text("blocked")
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        candidate.unlink()


def test_cli_or_browser_process_is_denied():
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        subprocess.run(["must-not-execute"], check=True)


def test_healthcheck_swallowed_transport_failure_still_records_violation():
    from app.monitoring.checkin import send_healthcheck_ping
    with isolation.expected_violation():
        assert send_healthcheck_ping("https://external.invalid/", success=True) is False
        with pytest.raises(isolation.IsolationViolation, match="intercepted unsafe operation"):
            isolation.assert_no_violations()


@pytest.mark.asyncio
async def test_real_playwright_launch_is_denied_before_browser_start():
    from playwright.async_api import async_playwright
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        await async_playwright().start()


def test_synthetic_environment_and_bound_default_paths():
    from app.config import settings
    from app.database.repository import DB_PATH, DatabaseRepository
    from app.auth.manager import STATE_FILE
    assert settings.TPO_USERNAME == ""
    assert settings.TPO_PASSWORD == ""
    assert settings.SMTP_PASSWORD == ""
    assert settings.HEALTHCHECK_WATCHER_URL is None
    assert settings.SECRET_KEY == "synthetic-test-key-not-for-deployment"
    assert isolation.disposable_path(DB_PATH)
    assert isolation.disposable_path(STATE_FILE)
    assert isolation.disposable_path(DatabaseRepository.__init__.__defaults__[0])


def test_import_time_database_path_is_disposable():
    # A compiled fictional module simulates collection-time DB initialization.
    namespace = {}
    exec(compile("import os, sqlite3\nconnection = sqlite3.connect(os.environ['DB_PATH'])\nconnection.close()", "synthetic_collection.py", "exec"), namespace)


def test_synthetic_sqlite_repository_and_fixtures_are_allowed(tmp_path):
    from app.database.repository import DatabaseRepository
    repo = DatabaseRepository(str(tmp_path / "synthetic.sqlite"))
    user_id = repo.users.create_user("fiction.student24@vit.edu", 2028, "VIT_CE", pref_ppo=False)
    repo.users.set_verified(user_id)
    assert repo.users.get_by_id(user_id)["is_verified"] == 1
    assert repo.users.get_preferences(user_id)["pref_ppo"] == 0


def test_in_memory_sqlite_is_allowed():
    with sqlite3.connect(":memory:") as conn:
        assert conn.execute("SELECT 42").fetchone()[0] == 42


def test_in_process_fastapi_testclient_is_allowed():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    application = FastAPI()
    @application.get("/synthetic")
    def synthetic():
        return {"synthetic": True}
    with TestClient(application) as client:
        assert client.get("/synthetic").json() == {"synthetic": True}


def test_explicit_synthetic_email_transport_is_allowed():
    from app.api.email_service import EmailService
    class SyntheticEmailTransport:
        def __init__(self):
            self.messages = []
        def _send_email_to(self, destination, subject, html):
            self.messages.append((destination, subject))
            return True
    transport = SyntheticEmailTransport()
    assert EmailService(transport).send_verification_email("fiction.student24@vit.edu", "synthetic-capability-not-live") is True
    assert transport.messages[0][0] == "fiction.student24@vit.edu"


def test_explicit_httpx_mock_transport_is_allowed():
    def synthetic(request):
        return httpx.Response(200, json={"synthetic": True})
    with httpx.Client(transport=httpx.MockTransport(synthetic)) as client:
        assert client.get("https://external.invalid/").json() == {"synthetic": True}


def test_dotenv_settings_source_does_not_read_files(tmp_path):
    from pydantic_settings import BaseSettings, SettingsConfigDict
    candidate = tmp_path / ".env"
    candidate.write_text("SYNTHETIC_VALUE=should-not-be-loaded\n")
    class SyntheticSettings(BaseSettings):
        SYNTHETIC_VALUE: str = "default"
        model_config = SettingsConfigDict(env_file=candidate)
    assert SyntheticSettings().SYNTHETIC_VALUE == "default"


def test_temp_files_can_be_changed_and_deleted(tmp_path):
    source = tmp_path / "synthetic.txt"
    destination = tmp_path / "renamed.txt"
    source.write_text("fictional")
    source.rename(destination)
    assert destination.read_text() == "fictional"
    destination.unlink()
    assert not destination.exists()


# These initial regressions are safe against the old implementation too:
# native SQLite can touch only memory or the fixture cwd, and audit calls below
# are pure Python calls with no downstream OS operation.
@pytest.mark.parametrize("constructor", [sqlite3.connect, sqlite3.dbapi2.connect])
def test_sqlite_alias_attach_relative_is_denied(constructor):
    conn = constructor(":memory:")
    try:
        with isolation.expected_violation(), pytest.raises(sqlite3.DatabaseError):
            conn.execute("ATTACH DATABASE 'synthetic-relative.sqlite' AS forbidden")
    finally:
        conn.close()


@pytest.mark.parametrize("constructor", [_sqlite3.connect, sqlite3.Connection, sqlite3.dbapi2.Connection])
def test_raw_sqlite_constructors_are_denied_before_open(constructor):
    conn = None
    try:
        with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
            conn = constructor(":memory:")
    finally:
        if conn is not None:
            conn.close()


@pytest.mark.parametrize("event,args", [
    ("socket.gethostbyname", ("synthetic.invalid",)),
    ("socket.gethostbyaddr", ("192.0.2.1",)),
    ("socket.getnameinfo", (("192.0.2.1", 443), 0)),
    ("socket.sendmsg", (object(), ("192.0.2.1", 443))),
])
def test_resolver_and_sendmsg_audit_events_fail_closed(event, args):
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        isolation._audit(event, args)


@pytest.mark.parametrize("event,args", [
    ("os.chmod", ("synthetic.txt", 0o600, 123)),
    ("os.utime", ("synthetic.txt", None, None, 123)),
    ("os.symlink", ("source.txt", "destination.txt", 123)),
    ("shutil.rmtree", ("synthetic-directory", 123)),
])
def test_descriptor_audit_arguments_are_not_ignored(event, args, monkeypatch):
    def reject_descriptor(path, dir_fd=None):
        if dir_fd == 123:
            isolation._deny("synthetic descriptor rejected")
        return path
    monkeypatch.setattr(isolation, "_mutation_path", reject_descriptor)
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        isolation._audit(event, args)


@pytest.mark.parametrize("constructor", [sqlite3.connect, sqlite3.dbapi2.connect])
@pytest.mark.parametrize("file_backed", [False, True])
def test_guarded_sqlite_aliases_allow_synthetic_sql_and_literal_attach(constructor, file_backed, tmp_path):
    database = str(tmp_path / "owned.sqlite") if file_backed else ":memory:"
    target = str(tmp_path / "attached.sqlite").replace("'", "''")
    conn = constructor(database)
    try:
        conn.execute("CREATE TABLE main.synthetic (id INTEGER)")
        conn.execute("ATTACH DATABASE '" + target + "' AS owned")
        conn.execute("CREATE TABLE owned.synthetic (id INTEGER)")
        conn.execute("DETACH DATABASE owned")
        assert conn.execute("SELECT 42").fetchone() == (42,)
    finally:
        conn.close()


@pytest.mark.parametrize("mode", ["ro", "rw", "rwc"])
def test_guarded_sqlite_preserves_supported_owned_uris(mode, tmp_path):
    target = tmp_path / "owned-uri.sqlite"
    sqlite3.connect(str(target)).close()
    sqlite3.connect("file:" + target.as_posix() + "?mode=" + mode, uri=True).close()


def test_sqlite_literal_unsupported_uri_attach_is_denied(tmp_path):
    # Even absent an authorizer this URI refers only to this synthetic fixture.
    target = ("file:" + (tmp_path / "synthetic.sqlite").as_posix() + "?cache=private").replace("'", "''")
    conn = sqlite3.connect(":memory:")
    try:
        with isolation.expected_violation("unsupported SQLite URI"), pytest.raises(sqlite3.DatabaseError):
            conn.execute("ATTACH DATABASE '" + target + "' AS forbidden")
        assert isolation._violations == []
    finally:
        conn.close()


def test_sqlite_parameter_attach_records_unknown_target_denial():
    conn = sqlite3.connect(":memory:")
    try:
        with isolation.expected_violation("unresolved SQLite ATTACH target disabled"):
            with pytest.raises(sqlite3.DatabaseError):
                conn.execute("ATTACH DATABASE ? AS forbidden", (":memory:",))
            assert isolation._violations[-1] == "unresolved SQLite ATTACH target disabled"
    finally:
        conn.close()


def test_sqlite_reinitialization_is_denied_and_original_state_survives(tmp_path):
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE synthetic (id INTEGER)")
        with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
            sqlite3.Connection.__init__(conn, str(tmp_path / "synthetic-reinitialized.sqlite"))
        assert conn.execute("SELECT count(*) FROM synthetic").fetchone() == (0,)
        assert not (tmp_path / "synthetic-reinitialized.sqlite").exists()
    finally:
        conn.close()


@pytest.mark.parametrize("positional", [False, True])
def test_sqlite_custom_factory_denied_before_inert_factory(positional):
    factory = Mock()
    args = (5.0, 0, "", True, factory) if positional else ()
    kwargs = {} if positional else {"factory": factory}
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        sqlite3.connect(":memory:", *args, **kwargs)
    factory.assert_not_called()


@pytest.mark.parametrize("failure", ["construction", "authorizer"])
def test_sqlite_permit_reset_and_unpublished_connection_closed(failure):
    conn = Mock()
    native = Mock(return_value=conn)
    if failure == "construction":
        native.side_effect = TypeError("synthetic construction failure")
    else:
        conn.set_authorizer.side_effect = TypeError("synthetic authorizer failure")
    with pytest.raises(TypeError):
        isolation._guard_sqlite_connect(native)(":memory:")
    assert isolation._sqlite_construction.pending is False
    assert conn.close.call_count == (failure == "authorizer")


def test_sqlite_construction_permit_is_consumed_once_before_open():
    conn = Mock()
    def native(database):
        isolation._audit("sqlite3.connect", (database,))
        assert isolation._sqlite_construction.pending is False
        with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
            isolation._audit("sqlite3.connect", (":memory:",))
        return conn
    assert isolation._guard_sqlite_connect(native)(":memory:") is conn
    conn.set_authorizer.assert_called_once()
    assert isolation._sqlite_construction.pending is False


@pytest.mark.parametrize("operation", ["connect", "connect_ex", "sendto"])
@pytest.mark.parametrize("address", [
    ("synthetic.invalid", 443), ("192.0.2.1", 443), ("127.0.0.1", 8000),
    (b"127.0.0.1", 8000), ("127.0.0.1", "http"), "synthetic-path",
])
def test_destinations_rejected_before_inert_native_call(operation, address):
    native = Mock()
    args = (b"synthetic", address) if operation == "sendto" else (address,)
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        isolation._guard_socket_call(native, operation)(object(), *args)
    native.assert_not_called()


@pytest.mark.parametrize("flags", [False, True])
def test_both_sendto_forms_rejected_before_inert_delegate(flags):
    native = Mock()
    args = (b"synthetic", 0, ("synthetic.invalid", 443)) if flags else (b"synthetic", ("synthetic.invalid", 443))
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        isolation._guard_socket_call(native, "sendto")(object(), *args)
    native.assert_not_called()


@pytest.mark.parametrize("operation", ["getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr", "getnameinfo"])
def test_all_resolvers_denied_before_inert_delegate_and_installed(operation):
    native = Mock()
    args = ("synthetic.invalid", 443) if operation == "getaddrinfo" else ("synthetic.invalid",)
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        isolation._guard_resolver(native, operation)(*args)
    native.assert_not_called()
    assert getattr(socket, operation).__isolation_guard__ == operation


@pytest.mark.parametrize("address,live", [
    (("::1", 12345), True), (("127.0.0.1", 12345), False),
    (("127.0.0.1", 12345, 1, 0), True), (("127.0.0.1", True), True),
])
def test_owned_endpoint_requires_exact_live_literal_address(address, live, monkeypatch):
    listener = Mock()
    listener.fileno.return_value = 42 if live else -1
    monkeypatch.setattr(isolation, "_loopback_test", True)
    monkeypatch.setattr(isolation, "_listeners", {listener: ("127.0.0.1", 12345)})
    native = Mock()
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        isolation._guard_socket_call(native, "connect")(object(), address)
    native.assert_not_called()


def test_owned_numeric_resolver_and_connection_forward_to_inert_delegates(monkeypatch):
    listener = Mock()
    listener.fileno.return_value = 42
    monkeypatch.setattr(isolation, "_loopback_test", True)
    monkeypatch.setattr(isolation, "_listeners", {listener: ("127.0.0.1", 12345)})
    resolver = Mock(return_value=["synthetic"])
    assert isolation._guard_resolver(resolver, "getaddrinfo")("127.0.0.1", 12345) == ["synthetic"]
    resolver.assert_called_once_with("127.0.0.1", 12345, 0, 0, 0, socket.AI_NUMERICHOST | socket.AI_NUMERICSERV)
    native = Mock(return_value=0)
    sock = object()
    assert isolation._guard_socket_call(native, "connect_ex")(sock, ("127.0.0.1", 12345)) == 0
    native.assert_called_once_with(sock, ("127.0.0.1", 12345))
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        isolation._guard_resolver(resolver, "getaddrinfo")("127.0.0.1", 12345, flags=socket.AI_CANONNAME)
    assert resolver.call_count == 1


@pytest.mark.parametrize("args", [((),), ((), (), 0, None), ((), (), 0, ("192.0.2.1", 443))])
def test_sendmsg_unconditionally_denied_before_inert_native(args):
    native = Mock()
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        isolation._guard_socket_call(native, "sendmsg")(object(), *args)
    native.assert_not_called()


@pytest.mark.skipif(not hasattr(socket.socket, "sendmsg"), reason="Windows has no native sendmsg; Linux runtime outstanding")
def test_native_sendmsg_method_has_installed_guard():
    assert socket.socket.sendmsg.__isolation_guard__ == "sendmsg"
    # A non-socket receiver cannot transmit even if the wrapper is removed:
    # the native descriptor rejects its type before inspecting buffers/address.
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        socket.socket.sendmsg(object(), (), (), 0, ("192.0.2.1", 443))


def test_socket_factory_bindings_and_raw_constructor_audit_denial():
    assert socket.SocketType is socket.socket
    for operation in ("connect", "connect_ex", "sendto"):
        assert getattr(socket.socket, operation).__isolation_guard__ == operation
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        isolation._audit("socket.__new__", (object(), socket.AF_INET, socket.SOCK_STREAM, 0))
    native = Mock()
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        isolation._guard_native_socketpair(native)(socket.AF_INET, socket.SOCK_STREAM, 0)
    native.assert_not_called()


@pytest.mark.parametrize("event,args", [
    ("os.remove", ("synthetic.txt", 123)), ("os.rmdir", ("synthetic-dir", 123)),
    ("os.mkdir", ("synthetic-dir", 0o700, 123)), ("os.chown", ("synthetic.txt", 1, 2, 123)),
    ("os.rename", ("source", "dest", 123, -1)), ("os.link", ("source", "dest", -1, 123)),
])
def test_related_descriptor_audit_tuples_fail_closed(event, args, monkeypatch):
    def reject_descriptor(path, dir_fd=None):
        if dir_fd == 123:
            isolation._deny("synthetic descriptor rejected")
        return path
    monkeypatch.setattr(isolation, "_mutation_path", reject_descriptor)
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        isolation._audit(event, args)


def test_descriptor_open_resolves_owned_target_and_strips_descriptor(tmp_path):
    native = Mock(return_value=17)
    reader = Mock(return_value=str(tmp_path))
    guarded = isolation._guard_os_open(native, readlink=reader, platform="linux")
    assert guarded("synthetic.txt", os.O_WRONLY, 0o600, dir_fd=123) == 17
    reader.assert_called_once_with("/proc/self/fd/123")
    native.assert_called_once_with(tmp_path / "synthetic.txt", os.O_WRONLY, 0o600)
    assert os.open.__isolation_guard__ == "os.open"


@pytest.mark.parametrize("case", ["outside", "traversal", "missing", "relative", "invalid", "negative", "unsupported", "not-directory"])
def test_descriptor_open_denial_has_inert_downstream_and_synthetic_descriptor(case, tmp_path):
    native = Mock()
    reader = Mock(return_value=str(tmp_path))
    path, descriptor, platform = "synthetic.txt", 123, "linux"
    if case == "outside":
        reader.return_value = str(isolation.ROOT.parent / "synthetic-outside-directory")
    elif case == "traversal":
        path = os.path.relpath(isolation.ROOT.parent, tmp_path) + "/synthetic.txt"
    elif case == "missing":
        reader.side_effect = OSError("synthetic unavailable descriptor")
    elif case == "relative":
        reader.return_value = "pipe:[synthetic]"
    elif case == "invalid":
        descriptor = "synthetic"
    elif case == "negative":
        descriptor = -1
    elif case == "unsupported":
        platform = "win32"
    elif case == "not-directory":
        reader.return_value = str(tmp_path / "synthetic-missing-directory")
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        isolation._guard_os_open(native, readlink=reader, platform=platform)(path, os.O_WRONLY, dir_fd=descriptor)
    native.assert_not_called()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux owned directory-descriptor runtime outstanding")
def test_linux_owned_descriptor_operations_and_safe_cleanup(tmp_path):
    import shutil
    assert os.open in os.supports_dir_fd
    assert shutil.rmtree.avoids_symlink_attacks
    directory = tmp_path / "owned-directory"
    directory.mkdir()
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        fd = os.open("synthetic.txt", os.O_CREAT | os.O_WRONLY, 0o600, dir_fd=descriptor)
        os.close(fd)
        os.chmod("synthetic.txt", 0o600, dir_fd=descriptor)
        os.utime("synthetic.txt", dir_fd=descriptor)
        os.unlink("synthetic.txt", dir_fd=descriptor)
    finally:
        os.close(descriptor)
    nested = directory / "nested"
    nested.mkdir()
    (nested / "synthetic.txt").write_text("synthetic")
    shutil.rmtree(directory)


def test_descriptor_chmod_permits_valid_linux_disposable_file(tmp_path):
    target = tmp_path / "valid_chmod.tmp"
    target.write_text("synthetic")
    stat_val = target.stat()
    fstat_mock = Mock(return_value=stat_val)
    stat_mock = Mock(return_value=stat_val)
    readlink_mock = Mock(return_value=str(target))
    isolation._verify_chmod_descriptor(
        10, (10, 0o600, None),
        readlink=readlink_mock,
        fstat=fstat_mock,
        stat=stat_mock,
        platform="linux",
    )
    readlink_mock.assert_called_once_with("/proc/self/fd/10")
    fstat_mock.assert_called_once_with(10)
    stat_mock.assert_called_once_with(str(target.resolve()))


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native fchmod requires POSIX runtime")
def test_linux_native_fchmod_on_disposable_file_succeeds(tmp_path):
    assert hasattr(os, "fchmod")
    target = tmp_path / "native_fchmod.tmp"
    target.write_text("native fchmod content")
    fd = os.open(str(target), os.O_RDWR)
    try:
        os.fchmod(fd, 0o600)
        mode = stat_module.S_IMODE(os.stat(str(target)).st_mode)
        assert mode == 0o600
    finally:
        os.close(fd)


@pytest.mark.parametrize("case", [
    "negative_fd",
    "bool_fd",
    "non_int_fd",
    "bad_sig_dir_fd",
    "bad_sig_len",
    "bad_sig_mode",
    "non_linux_platform",
    "closed_descriptor",
    "unsupported_type_fifo",
    "unsupported_type_sock",
    "deleted_target",
    "pipe_target",
    "socket_target",
    "relative_target",
    "outside_root",
    "symlink_escaping",
    "inode_mismatch",
    "dev_mismatch",
    "unstatable_target",
])
def test_descriptor_chmod_adversarial_rejections(case, tmp_path):
    target = tmp_path / "safe.tmp"
    target.write_text("safe")
    real_stat = target.stat()

    fd = 10
    args = (10, 0o600, None)
    platform = "linux"
    readlink = Mock(return_value=str(target))
    fstat = Mock(return_value=real_stat)
    stat_func = Mock(return_value=real_stat)

    if case == "negative_fd":
        fd = -1
        args = (-1, 0o600, None)
    elif case == "bool_fd":
        fd = True
        args = (True, 0o600, None)
    elif case == "non_int_fd":
        fd = "10"
        args = ("10", 0o600, None)
    elif case == "bad_sig_dir_fd":
        args = (10, 0o600, 5)
    elif case == "bad_sig_len":
        args = (10, 0o600)
    elif case == "bad_sig_mode":
        args = (10, "0600", None)
    elif case == "non_linux_platform":
        platform = "win32"
    elif case == "closed_descriptor":
        fstat.side_effect = OSError(9, "Bad file descriptor")
    elif case == "unsupported_type_fifo":
        m = Mock()
        m.st_mode = stat_module.S_IFIFO | 0o600
        m.st_dev, m.st_ino = real_stat.st_dev, real_stat.st_ino
        fstat.return_value = m
    elif case == "unsupported_type_sock":
        m = Mock()
        m.st_mode = stat_module.S_IFSOCK | 0o600
        m.st_dev, m.st_ino = real_stat.st_dev, real_stat.st_ino
        fstat.return_value = m
    elif case == "deleted_target":
        readlink.return_value = str(target) + " (deleted)"
    elif case == "pipe_target":
        readlink.return_value = "pipe:[12345]"
    elif case == "socket_target":
        readlink.return_value = "socket:[12345]"
    elif case == "relative_target":
        readlink.return_value = "relative_name.tmp"
    elif case == "outside_root":
        readlink.return_value = str(isolation.REPOSITORY.parent / "escape.tmp")
    elif case == "symlink_escaping":
        outside_path = isolation.REPOSITORY.parent / "escape.tmp"
        readlink.return_value = str(outside_path)
    elif case == "inode_mismatch":
        m = Mock()
        m.st_mode = real_stat.st_mode
        m.st_dev = real_stat.st_dev
        m.st_ino = real_stat.st_ino + 1000
        stat_func.return_value = m
    elif case == "dev_mismatch":
        m = Mock()
        m.st_mode = real_stat.st_mode
        m.st_dev = real_stat.st_dev + 1
        m.st_ino = real_stat.st_ino
        stat_func.return_value = m
    elif case == "unstatable_target":
        stat_func.side_effect = OSError("stat failed")

    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        isolation._verify_chmod_descriptor(
            fd, args,
            readlink=readlink,
            fstat=fstat,
            stat=stat_func,
            platform=platform,
        )


def test_audit_chmod_routes_integer_descriptor_and_preserves_path_checks(tmp_path):
    safe_path = str(tmp_path / "safe_audit.tmp")
    Path(safe_path).write_text("safe")
    isolation._audit("os.chmod", (safe_path, 0o600, None))

    forbidden_path = str(isolation.REPOSITORY / "secret.key")
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        isolation._audit("os.chmod", (forbidden_path, 0o600, None))

    if sys.platform != "linux":
        with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
            isolation._audit("os.chmod", (10, 0o600, None))

def test_guarded_child_installs_before_app_and_has_distinct_pid():
    with isolation.start_test_child("client") as child:
        assert child.ready["guard_installed"] is True
        assert child.ready["app_loaded"] is False
        assert child.ready["pid"]!=os.getpid()
        assert child.request("probe",{"check":"guard"})["guard_installed"] is True


def test_guarded_child_denies_original_process_network_smtp_sqlite_and_secrets():
    with isolation.start_test_child("client") as child:
        result=child.request("probe",{"check":"denials"})
        assert result=={"denied":6,"app_loaded":False}
        with pytest.raises(RuntimeError,match="IsolationViolation"):
            child.request("app.main",{})


@pytest.mark.parametrize("change",["nonce","role","executable","module","arguments","descriptors","traversal","root"])
def test_child_launch_contract_rejects_unapproved_combinations(change,monkeypatch,tmp_path):
    import secrets
    import time
    nonce=secrets.token_hex(32)
    ticket={"role":"client","used":False,"expires":time.monotonic()+10}
    monkeypatch.setitem(isolation._child_tickets,nonce,ticket)
    argv=[str(isolation._INTERPRETER),"-I","-B",str(isolation._BOOTSTRAP),"--r2a-child","client",nonce,str(isolation.ROOT),"--stdio"]
    values=dict(executable=str(isolation._INTERPRETER),root=str(isolation.ROOT),nonce=nonce,descriptors=(0,1,2))
    if change=="nonce":values["nonce"]="0"*64
    elif change=="role":argv[5]="browser"
    elif change=="executable":values["executable"]="unapproved-executable"
    elif change=="module":argv[3]="app/main.py"
    elif change=="arguments":argv.append("--self-test")
    elif change=="descriptors":values["descriptors"]=(0,1,2,99)
    elif change=="traversal":values["root"]=str(isolation.ROOT/"tmp"/"..")
    else:values["root"]=str(tmp_path)
    with pytest.raises(isolation.IsolationViolation):isolation.validate_child_launch(argv,**values)


def test_child_nonce_consumed_no_arbitrary_audit_exception_or_descendant(monkeypatch):
    with isolation.start_test_child("client") as child:
        assert isolation._child_tickets[child.nonce]["used"]
        assert not child.grant_path.exists()
        with pytest.raises(isolation.IsolationViolation):
            isolation.validate_child_launch([],executable=str(isolation._INTERPRETER),root=str(isolation.ROOT),nonce=child.nonce)
        with isolation.expected_violation():
            with pytest.raises(isolation.IsolationViolation):subprocess.run([str(isolation._INTERPRETER),"-c","pass"])


def test_child_exit_proof_requires_actual_python_process_end_and_cleanup():
    child=isolation.start_test_child("authority")
    with pytest.raises(ValueError):child.exit_evidence()
    child.terminate()
    proof=child.exit_evidence()
    assert proof["pid"]==child.ready["pid"] and proof["scope"].startswith("THIS_PROCESS")
    child.close()
    assert child.closed and child.nonce not in isolation._child_processes and not child.grant_path.exists()


def test_wrong_wire_nonce_kills_child_without_follow_on_import():
    child=isolation.start_test_child("client")
    try:
        child._write(dict(nonce="0"*64,sequence=1,operation="open",payload={}))
        with pytest.raises(EOFError):child._receive(3)
        assert child.exit_evidence()["pid"]==child.ready["pid"]
    finally:child.close()
    assert child.nonce not in isolation._child_processes


def test_symlink_root_is_rejected_before_spawn(monkeypatch):
    from pathlib import Path
    original=Path.is_symlink
    monkeypatch.setattr(Path,"is_symlink",lambda p:True if p==isolation.ROOT else original(p))
    with pytest.raises(isolation.IsolationViolation):isolation.start_test_child("client")


def test_guard_install_failure_cannot_import_role_application(monkeypatch):
    import secrets,time,json,hashlib
    nonce=secrets.token_hex(32)
    grant_path=isolation.ROOT/("r2a-child-"+nonce+".json")
    grant=dict(role="client",root=str(isolation.ROOT),nonce_hash=hashlib.sha256(nonce.encode()).hexdigest(),
        interpreter=str(isolation._INTERPRETER),bootstrap=str(isolation._BOOTSTRAP),descriptors=[0,1,2],
        parent_pid=os.getppid(),launcher_pid=os.getppid(),expires=time.time()+5)
    grant_path.write_text(json.dumps(grant))
    def fail():raise isolation.IsolationViolation("TEST ISOLATION: synthetic guard installation failure")
    import builtins
    original=builtins.__import__
    attempted=[]
    def importing(name,*args,**kwargs):
        if name.startswith("app."):attempted.append(name)
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,"__import__",importing)
    monkeypatch.setattr(isolation,"install",fail)
    monkeypatch.setattr(isolation,"_child_root",None)
    monkeypatch.setattr(isolation,"child_role",None)
    monkeypatch.setattr(isolation,"child_identity",None)
    try:
        with pytest.raises(isolation.IsolationViolation):isolation._run_test_child(["--r2a-child","client",nonce,str(isolation.ROOT),"--stdio"])
        assert attempted==[]
    finally:grant_path.unlink(missing_ok=True)


def test_failed_child_handshake_cleans_owned_job_and_no_follow_on(monkeypatch):
    original=isolation.GuardedChild._receive
    def fail(self,timeout):
        original(self,timeout) # The guard handshake exists, but the parent loses it.
        raise TimeoutError("fictional handshake reply lost")
    monkeypatch.setattr(isolation.GuardedChild,"_receive",fail)
    baseline=set(isolation._child_processes)
    with pytest.raises(TimeoutError):isolation.start_test_child("client")
    assert set(isolation._child_processes)==baseline


@pytest.mark.parametrize("budget",["active","total"])
def test_child_execution_budget_is_default_deny(monkeypatch,budget):
    if budget=="active":monkeypatch.setattr(isolation,"_child_processes",{str(i):None for i in range(6)})
    else:monkeypatch.setattr(isolation,"_child_tickets",{str(i):None for i in range(256)})
    with pytest.raises(isolation.IsolationViolation):isolation.start_test_child("client")


@pytest.mark.parametrize('change',['nonce','role','executable','module','arguments','descriptors','traversal','root'])
def test_controller_host_exact_launch_contract_rejects_unapproved_combinations(change,monkeypatch,tmp_path):
    import secrets,time
    nonce=secrets.token_hex(32)
    monkeypatch.setitem(isolation._child_tickets,nonce,dict(role='controller-host-crash-test',used=False,expires=time.monotonic()+10))
    argv=[str(isolation._INTERPRETER),'-I','-B',str(isolation._BOOTSTRAP),'--r2a-child','controller-host-crash-test',nonce,str(isolation.ROOT),'--stdio']
    values=dict(executable=str(isolation._INTERPRETER),root=str(isolation.ROOT),nonce=nonce,descriptors=(0,1,2))
    assert isolation.validate_child_launch(argv,**values)==argv
    if change=='nonce':values['nonce']='0'*64
    elif change=='role':argv[5]='authority'
    elif change=='executable':values['executable']='unapproved-executable'
    elif change=='module':argv[3]='app/main.py'
    elif change=='arguments':argv.append('--execute')
    elif change=='descriptors':values['descriptors']=(0,1,2,99)
    elif change=='traversal':values['root']=str(isolation.ROOT/'tmp'/'..')
    else:values['root']=str(tmp_path)
    with pytest.raises(isolation.IsolationViolation):isolation.validate_child_launch(argv,**values)


def test_controller_host_termination_has_no_pid_or_unrelated_child_authority():
    with isolation.start_test_child('authority') as authority:
        for value in (os.getpid(),authority,object(),None):
            with pytest.raises(isolation.IsolationViolation):isolation.terminate_test_controller(value)
        assert authority.request('probe',{'check':'guard'})['guard_installed']


def test_controller_host_wrong_wire_nonce_exits_and_cannot_launch_descendants():
    with isolation.start_test_child('controller-host-crash-test') as host:
        assert host.request('probe',{'check':'denials'})==dict(denied=6,app_loaded=False)
        host._write(dict(nonce='0'*64,sequence=host.sequence+1,operation='controller_status',payload={}))
        with pytest.raises(EOFError):host._receive(3)
        assert host.exit_evidence()['pid']==host.ready['pid']


