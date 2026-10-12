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


def _mutation_path(path, dir_fd=None):
    if dir_fd not in (-1, None):
        # Linux shutil.rmtree uses relative names and directory descriptors.
        # Resolve the directory itself, never trust the relative string alone.
        if sys.platform != "linux":
            _deny("unsupported descriptor-relative filesystem mutation")
        directory = Path(os.readlink("/proc/self/fd/" + str(dir_fd))).resolve()
        if not disposable_path(directory):
            _deny("filesystem descriptor outside disposable test root")
        return directory / os.fsdecode(path)
    return path


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
        _database_path(args[0])
    elif event in ("sqlite3.load_extension", "sqlite3.enable_load_extension"):
        _deny("SQLite extensions disabled")
    elif event in ("os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.truncate", "os.utime"):
        descriptor = args[-1] if event in ("os.remove", "os.rmdir", "os.mkdir") else None
        if not disposable_path(_mutation_path(args[0], descriptor)):
            _deny("filesystem mutation outside disposable test root")
    elif event in ("os.rename", "os.link", "os.symlink"):
        source = args[0]
        if event == "os.symlink" and not Path(source).is_absolute():
            source = Path(args[1]).parent / source
        if not disposable_path(source) or not disposable_path(args[1]):
            _deny("rename outside disposable test root")
        descriptors = args[3:] if event == "os.symlink" else args[2:]
        if any(value not in (-1, None) for value in descriptors):
            _deny("descriptor-relative rename disabled")
    elif event == "shutil.rmtree":
        if not disposable_path(args[0]):
            _deny("recursive delete outside disposable test root")
    elif event in ("subprocess.Popen", "os.system", "os.posix_spawn", "os.posix_spawnp"):
        _deny("subprocess/browser/CLI execution disabled")
    elif event in ("socket.connect", "socket.sendto"):
        _check_connection(args[-1])
    elif event == "socket.getaddrinfo":
        _check_connection((args[0], args[1]))


def _check_connection(address):
    if not isinstance(address, tuple) or len(address) < 2:
        _deny("network connection disabled")
    host, port = address[:2]
    # No DNS lookup, arbitrary localhost service, proxy, or external network.
    live_ports = {value[1] for sock, value in list(_listeners.items()) if _loopback_test or sock.fileno() >= 0}
    if not ((_loopback_test or getattr(_socketpair, "active", False)) and host in ("127.0.0.1", "::1") and port in live_ports):
        _deny("unmocked network/DNS connection disabled")


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

    original_connect = sqlite3.connect
    def guarded_connect(database, *args, **kwargs):
        _database_path(database)
        conn = original_connect(database, *args, **kwargs)
        def authorizer(action, arg1, arg2, db_name, trigger):
            if action == sqlite3.SQLITE_ATTACH:
                _database_path(arg1)
            return sqlite3.SQLITE_OK
        conn.set_authorizer(authorizer)
        return conn
    sqlite3.connect = guarded_connect

    original_bind = socket.socket.bind
    def guarded_bind(sock, address):
        permitted_pair = getattr(_socketpair, "active", False) and isinstance(address, tuple) and address[1] == 0
        if not ((_loopback_test or permitted_pair) and isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1")):
            _deny("network listener disabled")
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
        lambda: socket.getaddrinfo("external.invalid", 443),
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
                conn.execute("ATTACH DATABASE ? AS forbidden", (str(REPOSITORY / "never-open.sqlite"),))
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
