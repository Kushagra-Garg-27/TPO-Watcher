"""Tests-only, process-wide guards installed before importing application modules.

Preferred entry point: python -I -B tests/isolation_bootstrap.py --self-test
Then: python -I -B tests/isolation_bootstrap.py --pytest -q
Plain pytest also installs guards from the first lines of tests/conftest.py.
This is an accidental-side-effect guard, not a sandbox for hostile Python code.
"""

import ast
from contextlib import contextmanager
import os
from pathlib import Path
import socket
import _socket
import sqlite3
import sys
import tempfile
import threading
from urllib.parse import unquote, urlsplit


class IsolationViolation(RuntimeError):
    """An operation was denied before touching a transport or forbidden path."""


REPOSITORY = Path(__file__).resolve().parents[1]
ROOT = None
_installed = False
_violations = []
_loopback_test = False
_listeners = {}
_socketpair = threading.local()
_sqlite_construction = threading.local()
_SQLITE_CONNECTION = sqlite3.Connection


def _deny(reason, prefix="TEST ISOLATION: "):
    # Do not include paths, addresses, tokens, or credential values in diagnostics.
    _violations.append(reason)
    raise IsolationViolation(prefix + reason)


@contextmanager
def expected_violation(reason=None):
    """A regression test may deliberately exercise a denied, intercepted call."""
    start = len(_violations)
    try:
        yield
    finally:
        unexpected = [item for item in _violations[start:] if reason is not None and item != reason]
        del _violations[start:]
        if unexpected:
            raise IsolationViolation("TEST ISOLATION: unexpected denial in negative test")


def assert_no_violations():
    if _violations:
        reasons = ", ".join(sorted(set(_violations)))
        _violations.clear()
        raise IsolationViolation("TEST ISOLATION: intercepted unsafe operation: " + reasons)


def disposable_path(path):
    try:
        resolved = Path(os.fsdecode(path)).resolve()
        return ROOT is not None and resolved.is_relative_to(ROOT)
    except (TypeError, ValueError, OSError):
        return False


def _mutation_path(path, dir_fd=None, *, readlink=None, platform=None):
    if dir_fd not in (-1, None):
        # Linux shutil.rmtree uses relative names and directory descriptors.
        # Resolve the directory itself, never trust the relative string alone.
        if (sys.platform if platform is None else platform) != "linux":
            _deny("unsupported descriptor-relative filesystem mutation")
        if type(dir_fd) is not int or dir_fd < 0:
            _deny("invalid filesystem directory descriptor")
        try:
            value = (os.readlink if readlink is None else readlink)("/proc/self/fd/" + str(dir_fd))
            directory = Path(os.fsdecode(value))
            if not directory.is_absolute():
                _deny("unresolved filesystem directory descriptor")
            directory = directory.resolve()
        except (OSError, ValueError, TypeError):
            _deny("unresolved filesystem directory descriptor")
        if not disposable_path(directory) or not directory.is_dir():
            _deny("filesystem descriptor outside disposable test root")
        return directory / os.fsdecode(path)
    return path


def _guard_os_open(native, *, readlink=None, platform=None):
    def guarded_open(path, flags, mode=0o777, *, dir_fd=None):
        if dir_fd is not None:
            if type(dir_fd) is not int or dir_fd < 0:
                _deny("invalid filesystem directory descriptor")
            path = _mutation_path(path, dir_fd, readlink=readlink, platform=platform)
            if not disposable_path(path):
                _deny("descriptor-relative open outside disposable test root")
        # Absolute lexical target preserves leaf symlink/O_NOFOLLOW semantics.
        # Removing dir_fd also lets the ordinary open audit see the true target.
        return native(path, flags, mode)
    guarded_open.__isolation_guard__ = "os.open"
    return guarded_open


def _database_path(database):
    value = os.fsdecode(database)
    if value == ":memory:":
        return None
    if value.startswith("file:"):
        parsed = urlsplit(value)
        if parsed.netloc or parsed.query not in ("", "mode=ro", "mode=rw", "mode=rwc"):
            _deny("unsupported SQLite URI")
        value = unquote(parsed.path)
        if os.name == "nt" and value.startswith("/") and len(value) > 2 and value[2] == ":":
            value = value[1:]
    if not value or not Path(value).is_absolute():
        _deny("relative/default SQLite path disabled")
    if not disposable_path(value):
        _deny("database outside disposable test root")
    return value


def _audit(event, args):
    if event == "open":
        path, mode, flags = args
        if isinstance(path, int):
            return  # Existing stdio/descriptors; documented boundary.
        candidate = Path(os.fsdecode(path))
        inside = disposable_path(candidate)
        name = candidate.name.lower()
        sensitive = (
            name == ".env" or name.startswith(".env.") and name != ".env.example"
            or name.endswith((".sqlite", ".sqlite3", ".db", ".sqlite-wal", ".sqlite-shm"))
            or "playwright_state" in name or "storagestate" in name
            or any(part.lower() in (".auth", "secrets") for part in candidate.parts)
        )
        if sensitive and not inside:
            _deny("sensitive file outside disposable test root")
        write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
        writing = bool(flags & write_flags) or bool(mode and any(c in mode for c in "wax+"))
        if writing and not inside and os.fsdecode(path) != os.devnull:
            _deny("file write outside disposable test root")
    elif event == "sqlite3.connect":
        if not getattr(_sqlite_construction, "pending", False):
            _deny("unguarded SQLite constructor disabled")
        _sqlite_construction.pending = False  # One creation, consumed before open.
        _database_path(args[0])
    elif event in ("sqlite3.load_extension", "sqlite3.enable_load_extension"):
        _deny("SQLite extensions disabled")
    elif event in ("os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.truncate", "os.utime", "os.chown"):
        index = {"os.remove": 1, "os.rmdir": 1, "os.mkdir": 2,
                 "os.chmod": 2, "os.utime": 3, "os.chown": 3}.get(event)
        descriptor = args[index] if index is not None else None
        if not disposable_path(_mutation_path(args[0], descriptor)):
            _deny("filesystem mutation outside disposable test root")
    elif event in ("os.rename", "os.link", "os.symlink"):
        descriptors = args[2:3] if event == "os.symlink" else args[2:]
        if any(value not in (-1, None) for value in descriptors):
            _deny("descriptor-relative rename disabled")
        source = args[0]
        if event == "os.symlink" and not Path(source).is_absolute():
            source = Path(args[1]).parent / source
        if not disposable_path(source) or not disposable_path(args[1]):
            _deny("rename outside disposable test root")
    elif event == "shutil.rmtree":
        if not disposable_path(_mutation_path(args[0], args[1])):
            _deny("recursive delete outside disposable test root")
    elif event in ("subprocess.Popen", "os.system", "os.posix_spawn", "os.posix_spawnp"):
        _deny("subprocess/browser/CLI execution disabled")
    elif event in ("socket.connect", "socket.sendto"):
        _check_connection(args[-1])
    elif event == "socket.getaddrinfo":
        _check_connection((args[0], args[1]))
    elif event in ("socket.gethostbyname", "socket.gethostbyaddr", "socket.getnameinfo", "socket.sendmsg"):
        _deny("unsupported native resolver/sendmsg disabled")
    elif event == "socket.__new__":
        if not isinstance(args[0], socket.socket):
            _deny("unguarded socket constructor disabled")
    elif event == "socket.bind":
        _check_bind(args[1])


def _check_connection(address):
    if not isinstance(address, tuple) or len(address) not in (2, 4):
        _deny("network connection disabled")
    host, port = address[:2]
    if type(host) is not str or type(port) is not int or (len(address) == 4 and address[2:] != (0, 0)):
        _deny("unsupported network address disabled")
    # No DNS lookup, arbitrary localhost service, proxy, or external network.
    live_endpoints = {value[:2] for sock, value in list(_listeners.items()) if sock.fileno() >= 0}
    if not ((_loopback_test or getattr(_socketpair, "active", False)) and host in ("127.0.0.1", "::1") and (host, port) in live_endpoints):
        _deny("unmocked network/DNS connection disabled")


def _check_bind(address):
    if not ((_loopback_test or getattr(_socketpair, "active", False))
            and isinstance(address, tuple) and len(address) == 2
            and address[0] in ("127.0.0.1", "::1") and type(address[1]) is int and address[1] == 0):
        _deny("network listener disabled")


def _guard_socket_call(native, operation):
    def guarded(sock, *args, **kwargs):
        if operation == "sendmsg":
            _deny("unsupported native resolver/sendmsg disabled")
        if kwargs or not args:
            _deny("unsupported socket call form disabled")
        _check_connection(args[-1])  # Both positional sendto forms end in address.
        return native(sock, *args)
    guarded.__isolation_guard__ = operation
    return guarded


def _guard_resolver(native, operation):
    if operation == "getaddrinfo":
        def guarded(host, port, family=0, type=0, proto=0, flags=0):
            _check_connection((host, port))
            if flags & socket.AI_CANONNAME:
                _deny("resolver canonical-name lookup disabled")
            return native(host, port, family, type, proto,
                          flags | socket.AI_NUMERICHOST | socket.AI_NUMERICSERV)
    else:
        def guarded(*args, **kwargs):
            _deny("unsupported native resolver/sendmsg disabled")
    guarded.__isolation_guard__ = operation
    return guarded


def _guard_native_socketpair(native):
    def guarded(family, type, proto):
        if not (getattr(_socketpair, "active", False) and family == getattr(socket, "AF_UNIX", None)):
            _deny("unguarded native socketpair disabled")
        return native(family, type, proto)
    return guarded


def _guard_sqlite_connect(native):
    def guarded_connect(database, *args, **kwargs):
        database = os.fsdecode(database)
        _database_path(database)
        factory = args[4] if len(args) > 4 else kwargs.get("factory", _SQLITE_CONNECTION)
        if factory is not _SQLITE_CONNECTION or ("factory" in kwargs and kwargs["factory"] is not _SQLITE_CONNECTION):
            _deny("custom SQLite connection factory disabled")
        _sqlite_construction.pending = True
        try:
            conn = native(database, *args, **kwargs)
        finally:
            _sqlite_construction.pending = False
        def authorizer(action, arg1, arg2, db_name, trigger):
            if action == sqlite3.SQLITE_ATTACH:
                if arg1 is None:
                    _deny("unresolved SQLite ATTACH target disabled")
                _database_path(arg1)
            return sqlite3.SQLITE_OK
        try:
            conn.set_authorizer(authorizer)
        except BaseException:
            conn.close()
            raise
        return conn
    return guarded_connect


@contextmanager
def local_server_test():
    """Only the existing ephemeral localhost header regression uses real sockets."""
    global _loopback_test
    _loopback_test = True
    try:
        yield
    finally:
        _loopback_test = False
        _listeners.clear()


def blocked_smtp(*args, **kwargs):
    _deny("real SMTP transport disabled", "CRITICAL TEST GUARDRAIL TRIGGERED: TEST ISOLATION: ")


def install():
    global ROOT, _installed
    if _installed:
        return ROOT
    if any(name == "app.config" or name.startswith("app.database") or name == "app.main" for name in sys.modules):
        raise IsolationViolation("TEST ISOLATION: application imported before guards")
    # Never accept a caller-supplied root or use pytest's broad default temp root.
    ROOT = Path(tempfile.mkdtemp(prefix="tpo-tests-isolated-")).resolve()
    (ROOT / "tmp").mkdir()
    tempfile.tempdir = str(ROOT / "tmp")
    sys.dont_write_bytecode = True
    sys.addaudithook(_audit)

    # Remove inherited app settings without reading or printing their values.
    source = ast.parse((REPOSITORY / "app" / "config.py").read_text(encoding="utf-8"))
    fields = {node.target.id for node in ast.walk(source) if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)}
    fields.update(("DB_PATH", "STATE_FILE"))
    for key in list(os.environ):
        if key.upper() in fields:
            del os.environ[key]
    os.environ.update({
        "DB_PATH": str(ROOT / "collection.sqlite"),
        "STATE_FILE": str(ROOT / "synthetic-playwright-state.json"),
        "SECRET_KEY": "synthetic-test-key-not-for-deployment",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    })

    import smtplib
    smtplib.SMTP = blocked_smtp
    smtplib.SMTP_SSL = blocked_smtp

    # TestClient bypasses HTTPTransport. Explicit MockTransport/ASGITransport
    # remain usable. HTTPTransport is denied even before socket resolution.
    import httpx
    def deny_http(*args, **kwargs):
        _deny("unmocked HTTP transport disabled")
    async def deny_async_http(*args, **kwargs):
        _deny("unmocked HTTP transport disabled")
    httpx.HTTPTransport.handle_request = deny_http
    httpx.AsyncHTTPTransport.handle_async_request = deny_async_http

    # Disable dotenv settings at the source, before app.config is imported.
    # Direct attempted reads remain blocked by the audit hook.
    from pydantic_settings.sources import DotEnvSettingsSource
    DotEnvSettingsSource._read_env_files = lambda self: {}

    guarded_connect = _guard_sqlite_connect(sqlite3.connect)
    sqlite3.connect = guarded_connect
    sqlite3.dbapi2.connect = guarded_connect

    original_open = os.open
    os.open = _guard_os_open(original_open)
    if original_open in os.supports_dir_fd:
        os.supports_dir_fd.add(os.open)
    def deny_special_file(*args, **kwargs):
        _deny("special filesystem node creation disabled")
    for name in ("mkfifo", "mknod"):
        if hasattr(os, name):
            setattr(os, name, deny_special_file)

    for name in ("connect", "connect_ex", "sendto", "sendmsg"):
        if hasattr(socket.socket, name):
            setattr(socket.socket, name, _guard_socket_call(getattr(socket.socket, name), name))
    for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr", "getnameinfo"):
        setattr(socket, name, _guard_resolver(getattr(socket, name), name))
    socket.SocketType = socket.socket
    if hasattr(_socket, "socketpair"):
        _socket.socketpair = _guard_native_socketpair(_socket.socketpair)

    original_bind = socket.socket.bind
    def guarded_bind(sock, address):
        _check_bind(address)
        result = original_bind(sock, address)
        _listeners[sock] = sock.getsockname()
        return result
    socket.socket.bind = guarded_bind
    original_socketpair = socket.socketpair
    def guarded_socketpair(*args, **kwargs):
        # Windows implements asyncio's private self-pipe with a fresh loopback
        # socket pair. It can only connect to its own freshly bound port.
        _socketpair.active = True
        try:
            return original_socketpair(*args, **kwargs)
        finally:
            _socketpair.active = False
            for sock in list(_listeners):
                if sock.fileno() < 0:
                    del _listeners[sock]
    socket.socketpair = guarded_socketpair
    _installed = True
    return ROOT


def self_test():
    """Only intercepted forbidden operations; no app imports or real connections."""
    root = install()
    import smtplib
    import subprocess
    checks = [
        lambda: smtplib.SMTP("smtp.invalid", 587),
        lambda: smtplib.SMTP_SSL("smtp.invalid", 465),
        lambda: _guard_resolver(lambda *args: None, "getaddrinfo")("external.invalid", 443),
        lambda: sqlite3.connect(str(REPOSITORY / "watcher.sqlite")),
        lambda: (REPOSITORY / ".env").read_text(),
        lambda: (REPOSITORY / "playwright_state.json").read_text(),
        lambda: (REPOSITORY / "guard-must-not-create.txt").write_text("blocked"),
        lambda: subprocess.run(["must-not-execute"]),
    ]
    for check in checks:
        with expected_violation():
            try:
                check()
            except IsolationViolation:
                pass
            else:
                raise AssertionError("Bootstrap guard did not reject operation")
    with sqlite3.connect(str(root / "self-test.sqlite")) as conn:
        conn.execute("CREATE TABLE synthetic (id INTEGER)")
        with expected_violation():
            try:
                conn.execute("ATTACH DATABASE ? AS forbidden", (":memory:",))
            except sqlite3.DatabaseError:
                pass
            else:
                raise AssertionError("ATTACH guard did not reject operation")
    assert not any(name.startswith("app.") for name in sys.modules)
    assert_no_violations()
    print("Bootstrap self-test passed: 9 denied operations; synthetic SQLite permitted; no application imports.")


if __name__ == "__main__":
    # Reuse the same module when conftest imports it later.
    sys.modules["isolation_bootstrap"] = sys.modules[__name__]
    if sys.argv[1:] == ["--self-test"]:
        self_test()
    elif sys.argv[1:2] == ["--pytest"]:
        install()
        sys.path.insert(0, str(REPOSITORY))
        import pytest
        args = ["-p", "pytest_asyncio.plugin", "-p", "pytest_mock", "-p", "no:cacheprovider",
                "--basetemp=" + str(ROOT / "pytest"), *sys.argv[2:]]
        raise SystemExit(pytest.main(args))
    else:
        raise SystemExit("Use --self-test or --pytest [pytest arguments]")
