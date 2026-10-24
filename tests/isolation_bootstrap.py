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
import hashlib
import json
import queue
import secrets
import subprocess
import time
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
_child_launch = threading.local()
_child_tickets = {}
_child_processes = {}
_child_start_lock = threading.RLock()
_child_root = None
child_role = None
child_identity = None
_CHILD_ROLES = ("authority", "client", "controller-host-crash-test")
_BOOTSTRAP = Path(__file__).resolve()
_INTERPRETER = Path(sys.executable).resolve()
_ROLE_OPERATIONS={
    "authority":frozenset(("open","elect","admit","consume","end","status","advance","configure","crash")),
    "client":frozenset(("open","prepare","worker","admin","account","status","advance","crash")),
    "controller-host-crash-test":frozenset(("open","elect_controller","enable_controller","controller_status","advance")),
}


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
        if event != "subprocess.Popen" or not _approved_child_spawn(args):
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
    ROOT = _child_root or Path(tempfile.mkdtemp(prefix="tpo-tests-isolated-")).resolve()
    (ROOT / "tmp").mkdir(exist_ok=True)
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


def _canonical_child_root(value):
    candidate = Path(value)
    if not candidate.is_absolute() or str(candidate) != str(candidate.resolve()) or ".." in candidate.parts:
        raise IsolationViolation("TEST ISOLATION: noncanonical child root")
    if any(parent.is_symlink() for parent in (candidate, *candidate.parents)):
        raise IsolationViolation("TEST ISOLATION: symlink child root")
    if not candidate.is_dir() or not candidate.name.startswith("tpo-tests-isolated-"):
        raise IsolationViolation("TEST ISOLATION: unowned child root")
    return candidate


def validate_child_launch(argv, *, executable, root, nonce, descriptors=(0,1,2)):
    """Pure validation; no process exception exists until start_test_child scopes it."""
    if type(nonce) is not str or len(nonce)!=64 or any(c not in "0123456789abcdef" for c in nonce):
        raise IsolationViolation("TEST ISOLATION: invalid child nonce")
    ticket=_child_tickets.get(nonce)
    if not ticket or ticket["used"] or ticket["expires"]<=time.monotonic():
        raise IsolationViolation("TEST ISOLATION: unissued child nonce")
    canonical=_canonical_child_root(root)
    if canonical!=ROOT or str(executable)!=str(_INTERPRETER) or descriptors!=(0,1,2):
        raise IsolationViolation("TEST ISOLATION: unapproved child executable/root/descriptors")
    expected=[str(_INTERPRETER),"-I","-B",str(_BOOTSTRAP),"--r2a-child",ticket["role"],nonce,str(ROOT),"--stdio"]
    if type(argv) is not list or argv!=expected:
        raise IsolationViolation("TEST ISOLATION: unapproved child role/argv")
    return expected


def _approved_child_spawn(args):
    pending=getattr(_child_launch,"pending",None)
    if pending is None:return False
    executable,argv,cwd,environment=args
    if executable!=pending["executable"] or argv!=pending["command"] or cwd!=pending["root"] or environment!=pending["env"]:
        return False
    try:
        validate_child_launch(pending["argv"],executable=executable,root=cwd,nonce=pending["nonce"])
    except IsolationViolation:return False
    _child_tickets[pending["nonce"]]["used"]=True
    _child_launch.pending=None
    return True


class _OwnedProcessJob:
    """Windows venv launcher family, assigned before publishing the child grant."""
    def __init__(self):
        if not _installed or child_role is not None:raise IsolationViolation("TEST ISOLATION: parent supervision only")
        self.handle=None
        if os.name!="nt":return
        import ctypes
        from ctypes import wintypes
        self.api=ctypes.WinDLL("kernel32",use_last_error=True)
        self.api.CreateJobObjectW.argtypes=(wintypes.LPVOID,wintypes.LPCWSTR)
        self.api.CreateJobObjectW.restype=wintypes.HANDLE
        self.api.AssignProcessToJobObject.argtypes=(wintypes.HANDLE,wintypes.HANDLE)
        self.api.AssignProcessToJobObject.restype=wintypes.BOOL
        self.api.TerminateJobObject.argtypes=(wintypes.HANDLE,wintypes.UINT)
        self.api.TerminateJobObject.restype=wintypes.BOOL
        self.api.CloseHandle.argtypes=(wintypes.HANDLE,)
        self.api.CloseHandle.restype=wintypes.BOOL
        self.api.QueryInformationJobObject.argtypes=(wintypes.HANDLE,ctypes.c_int,wintypes.LPVOID,wintypes.DWORD,wintypes.LPVOID)
        self.api.QueryInformationJobObject.restype=wintypes.BOOL
        self.handle=self.api.CreateJobObjectW(None,None)
        if not self.handle:raise OSError("owned process job unavailable")

    def attach(self,process):
        if self.handle and not self.api.AssignProcessToJobObject(self.handle,int(process._handle)):
            raise OSError("owned launcher job assignment rejected")

    def close(self):
        if self.handle is None:return
        import ctypes
        from ctypes import wintypes
        class Accounting(ctypes.Structure):
            _fields_=[("times",ctypes.c_int64*4),("faults",wintypes.DWORD),("total",wintypes.DWORD),
                      ("active",wintypes.DWORD),("terminated",wintypes.DWORD)]
        if not self.api.TerminateJobObject(self.handle,1):raise OSError("owned job termination failed")
        deadline=time.monotonic()+3
        while True:
            count=Accounting()
            if not self.api.QueryInformationJobObject(self.handle,1,ctypes.byref(count),ctypes.sizeof(count),None):
                raise OSError("owned job exit evidence unavailable")
            if count.active==0:break
            if time.monotonic()>=deadline:raise TimeoutError("owned process family still active")
            time.sleep(0.01)
        self.api.CloseHandle(self.handle)
        self.handle=None


class GuardedChild:
    """Fixed-role child; only its stdio pipes are inherited. All waits are bounded."""
    def __init__(self,process,nonce,role,grant_path,job):
        self.process,self.nonce,self.role,self.grant_path=process,nonce,role,grant_path
        self.sequence=0
        self.frames=queue.Queue(maxsize=32)
        self.closed=False
        self.native_handle=None
        self.job=job
        self._request_lock=threading.RLock()
        def read_frames():
            try:
                while True:
                    line=process.stdout.readline(131073)
                    if not line:break
                    if len(line.encode("utf-8"))>131072:break
                    self.frames.put(json.loads(line),timeout=1)
            except (ValueError,queue.Full):pass
            finally:
                try:self.frames.put(None,timeout=1)
                except queue.Full:pass
        self.reader=threading.Thread(target=read_frames,daemon=True)
        self.reader.start()
        self.ready=self._receive(5)
        if self.ready.get("nonce_hash")!=hashlib.sha256(nonce.encode()).hexdigest() or self.ready.get("role")!=role or self.ready.get("guard_installed") is not True:
            self.close()
            raise IsolationViolation("TEST ISOLATION: child guard handshake failed")
        if os.name=="nt":
            # The Windows venv redirector is a different process from Python.
            # Supervise the authenticated Python PID, not just the redirector.
            import _winapi
            self.native_handle=_winapi.OpenProcess(0x100000|0x1000|0x0001,False,self.ready["pid"])
        elif self.ready["pid"]!=process.pid:
            self.close()
            raise IsolationViolation("TEST ISOLATION: child process identity mismatch")

    def _receive(self,timeout):
        try:frame=self.frames.get(timeout=timeout)
        except queue.Empty:raise TimeoutError("guarded child response timeout") from None
        if frame is None:
            self.process.wait(timeout=2)
            diagnostic=self.process.stderr.read(16384).splitlines()
            safe=next((line for line in reversed(diagnostic) if line.startswith("IsolationViolation: TEST ISOLATION:")),"child entry failed")
            raise EOFError("guarded child exited without response: "+safe)
        return frame

    def _write(self,frame):
        data=json.dumps(frame,separators=(",",":"))
        if len(data.encode())>131072:raise ValueError("bounded child frame required")
        self.process.stdin.write(data+"\n")
        self.process.stdin.flush()

    def request(self,operation,payload=None,*,relay=None,timeout=5):
        with self._request_lock:
            return self._request(operation,payload,relay=relay,timeout=timeout)

    def _request(self,operation,payload=None,*,relay=None,timeout=5):
        if self.closed or type(timeout) not in (int,float) or not 0<timeout<=10:
            raise ValueError("live child and finite timeout required")
        self.sequence+=1
        self._write(dict(nonce=self.nonce,sequence=self.sequence,operation=operation,payload=payload or {}))
        deadline=time.monotonic()+timeout
        while True:
            frame=self._receive(max(0.001,deadline-time.monotonic()))
            if frame.get("rpc"):
                if relay is None:raise ValueError("declared authority relay required")
                if frame.get("sequence")!=self.sequence or frame.get("nonce_hash")!=hashlib.sha256(self.nonce.encode()).hexdigest():
                    raise ValueError("child relay identity mismatch")
                try:
                    result=relay(frame["operation"],frame["payload"])
                    response=dict(rpc_result=result)
                except (EOFError,TimeoutError,OSError,RuntimeError):response=dict(rpc_error="UNAVAILABLE")
                self._write(dict(nonce=self.nonce,sequence=self.sequence,**response))
                continue
            if frame.get("sequence")!=self.sequence:raise ValueError("child response identity mismatch")
            if "error" in frame:raise RuntimeError("guarded child operation rejected: "+frame["error"])
            return frame["result"]

    def close(self):
        if self.closed:return
        try:
            if self.process.poll() is None:self.request("shutdown",timeout=2)
        except (EOFError,TimeoutError,BrokenPipeError,OSError,ValueError,RuntimeError):pass
        self.closed=True
        try:self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.terminate()
            self.process.wait(timeout=3)
        if self.native_handle is not None:
            import _winapi
            if _winapi.WaitForSingleObject(self.native_handle,0)!=0:
                _winapi.TerminateProcess(self.native_handle,1)
                if _winapi.WaitForSingleObject(self.native_handle,3000)!=0:raise TimeoutError("owned child termination timeout")
            _winapi.CloseHandle(self.native_handle)
            self.native_handle=None
        for stream in (self.process.stdin,self.process.stdout,self.process.stderr):
            try:stream.close()
            except OSError:pass # Already proven exited; a broken pipe may fail flush.
        self.reader.join(timeout=2)
        self.job.close()
        self.grant_path.unlink(missing_ok=True)
        _child_processes.pop(self.nonce,None)

    def terminate(self):
        if self.native_handle is not None:
            import _winapi
            if _winapi.WaitForSingleObject(self.native_handle,0)!=0:_winapi.TerminateProcess(self.native_handle,1)
            if _winapi.WaitForSingleObject(self.native_handle,3000)!=0:raise TimeoutError("owned child termination timeout")
        elif self.process.poll() is None:self.process.kill()
        self.process.wait(timeout=3)

    def exit_evidence(self):
        if self.process.poll() is None:raise ValueError("positive owned process exit required")
        if self.native_handle is not None:
            import _winapi
            if _winapi.WaitForSingleObject(self.native_handle,0)!=0:raise ValueError("Python caller still alive")
        return dict(pid=self.ready["pid"],identity=self.ready["identity"],role=self.role,
                    nonce_hash=hashlib.sha256(self.nonce.encode()).hexdigest(),exit_code=self.process.returncode,
                    scope="THIS_PROCESS_AND_ITS_THREADS_NO_DELEGATED_PHYSICAL_CALLER")

    def __enter__(self):return self
    def __exit__(self,*args):self.close()


def start_test_child(role):
    with _child_start_lock:
        return _start_test_child(role)


def terminate_test_controller(child):
    """No PID API: only the exact still-registered guarded host object is owned."""
    if (not _installed or child_role is not None or not isinstance(child,GuardedChild)
        or child.role!='controller-host-crash-test' or child.closed
        or _child_processes.get(child.nonce) is not child
        or child.ready.get('role')!=child.role
        or child.ready.get('nonce_hash')!=hashlib.sha256(child.nonce.encode()).hexdigest()):
        raise IsolationViolation('TEST ISOLATION: owned controller-host required')
    child.terminate()
    return child.exit_evidence()


def _start_test_child(role):
    """The sole audited subprocess exception; clients cannot launch descendants."""
    if not _installed or child_role is not None or role not in _CHILD_ROLES:
        raise IsolationViolation("TEST ISOLATION: undeclared child role/parent")
    if len(_child_processes)>=6 or len(_child_tickets)>=256:
        raise IsolationViolation("TEST ISOLATION: bounded child budget exhausted")
    root=_canonical_child_root(str(ROOT))
    nonce=secrets.token_hex(32)
    ticket=dict(role=role,used=False,expires=time.monotonic()+30)
    _child_tickets[nonce]=ticket
    argv=[str(_INTERPRETER),"-I","-B",str(_BOOTSTRAP),"--r2a-child",role,nonce,str(root),"--stdio"]
    validate_child_launch(argv,executable=str(_INTERPRETER),root=str(root),nonce=nonce)
    grant_path=root/("r2a-child-"+nonce+".json")
    grant=dict(role=role,root=str(root),nonce_hash=hashlib.sha256(nonce.encode()).hexdigest(),
        parent_pid=os.getpid(),launcher_pid=None,expires=time.time()+30,interpreter=str(_INTERPRETER),bootstrap=str(_BOOTSTRAP),descriptors=[0,1,2])
    grant_path.write_text(json.dumps(grant),encoding="utf-8")
    # Do not pass the parent environment, credentials or application settings.
    environment={"SYSTEMROOT":os.environ.get("SYSTEMROOT",r"C:\Windows"),"TEMP":str(root.parent),
                 "TMP":str(root.parent),"PATH":str(_INTERPRETER.parent),"PYTEST_DISABLE_PLUGIN_AUTOLOAD":"1"}
    command=subprocess.list2cmdline(argv) if os.name=="nt" else argv
    _child_launch.pending=dict(executable=str(_INTERPRETER),command=command,root=str(root),env=environment,nonce=nonce,argv=argv)
    job=_OwnedProcessJob()
    try:
        process=subprocess.Popen(argv,executable=str(_INTERPRETER),cwd=str(root),env=environment,
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding="utf-8",
            shell=False,close_fds=True)
        job.attach(process)
        grant["launcher_pid"]=process.pid
        published=grant_path.with_suffix(".tmp")
        published.write_text(json.dumps(grant),encoding="utf-8")
        published.replace(grant_path)
        child=GuardedChild(process,nonce,role,grant_path,job)
        _child_processes[nonce]=child
        return child
    except BaseException:
        job.close()
        if "process" in locals():
            if process.poll() is None:process.kill()
            process.wait(timeout=3)
            for stream in (process.stdin,process.stdout,process.stderr):
                try:stream.close()
                except OSError:pass
        grant_path.unlink(missing_ok=True)
        raise
    finally:_child_launch.pending=None


def _run_test_child(arguments):
    global _child_root,child_role,child_identity
    if len(arguments)!=5 or arguments[0]!="--r2a-child" or arguments[1] not in _CHILD_ROLES or arguments[4]!="--stdio":
        raise IsolationViolation("TEST ISOLATION: invalid child entry")
    role,nonce,root_value=arguments[1:4]
    if len(nonce)!=64 or any(c not in "0123456789abcdef" for c in nonce):
        raise IsolationViolation("TEST ISOLATION: invalid child nonce")
    root=_canonical_child_root(root_value)
    if root.parent!=Path(tempfile.gettempdir()).resolve():raise IsolationViolation("TEST ISOLATION: invalid child root parent")
    grant_path=root/("r2a-child-"+nonce+".json")
    if grant_path.is_symlink():raise IsolationViolation("TEST ISOLATION: linked child grant")
    deadline=time.monotonic()+3
    while True:
        grant=json.loads(grant_path.read_text(encoding="utf-8"))
        if type(grant.get("launcher_pid")) is int:break
        if time.monotonic()>=deadline:raise IsolationViolation("TEST ISOLATION: unpublished child grant")
        time.sleep(0.01)
    expected=dict(role=role,root=str(root),nonce_hash=hashlib.sha256(nonce.encode()).hexdigest(),
                  interpreter=str(_INTERPRETER),bootstrap=str(_BOOTSTRAP),descriptors=[0,1,2])
    mismatches=[k for k,v in expected.items() if grant.get(k)!=v]
    if os.getppid() not in (grant.get("parent_pid"),grant.get("launcher_pid")):mismatches.append("parent_pid")
    if mismatches or type(grant.get("expires")) not in (int,float) or grant["expires"]<=time.time():
        raise IsolationViolation("TEST ISOLATION: child grant rejected: "+",".join(mismatches))
    _child_root,child_role,child_identity=root,role,secrets.token_hex(16)
    install()
    if role=='controller-host-crash-test':
        watchdog=threading.Timer(90,lambda:os._exit(71))
        watchdog.daemon=True
        watchdog.start()
    grant_path.unlink()
    sys.path.insert(0,str(REPOSITORY))
    sys.stdout.reconfigure(encoding="utf-8")
    def emit(frame):
        sys.stdout.write(json.dumps(frame,separators=(",",":"))+"\n")
        sys.stdout.flush()
    emit(dict(pid=os.getpid(),role=role,identity=child_identity,nonce_hash=expected["nonce_hash"],
              guard_installed=_installed,app_loaded=any(k.startswith("app.") for k in sys.modules)))
    sequence=0
    handler=None
    while True:
        line=sys.stdin.readline(131073)
        if not line:break
        if len(line.encode())>131072:raise IsolationViolation("TEST ISOLATION: oversized child frame")
        frame=json.loads(line)
        if type(frame) is not dict or set(frame)!={"nonce","sequence","operation","payload"}:
            raise IsolationViolation("TEST ISOLATION: exact child frame required")
        if frame.get("nonce")!=nonce or type(frame.get("sequence")) is not int or frame["sequence"]!=sequence+1:
            raise IsolationViolation("TEST ISOLATION: child frame identity")
        sequence=frame["sequence"]
        operation,payload=frame.get("operation"),frame.get("payload")
        if type(payload) is not dict:raise IsolationViolation("TEST ISOLATION: child payload")
        try:
            if operation=="shutdown":
                if handler:
                    handler.sequence=sequence
                    handler.close()
                emit(dict(sequence=sequence,result={"closed":True}))
                break
            if operation=="probe":
                if payload=={"check":"guard"}:
                    result=dict(guard_installed=_installed,pid=os.getpid())
                elif payload=={"check":"denials"}:
                    import smtplib
                    checks=(lambda:subprocess.run(["must-not-execute"]),lambda:os.system("must-not-execute"),
                        lambda:socket.socket().connect(("127.0.0.1",9)),lambda:smtplib.SMTP("smtp.invalid",587),
                        lambda:sqlite3.connect(str(REPOSITORY/"watcher.sqlite")),lambda:(REPOSITORY/".env").read_text())
                    denied=0
                    for check in checks:
                        with expected_violation():
                            try:check()
                            except IsolationViolation:denied+=1
                    result=dict(denied=denied,app_loaded=any(k.startswith("app.") for k in sys.modules))
                else:raise IsolationViolation("TEST ISOLATION: unsupported probe")
            else:
                if not _installed or operation not in _ROLE_OPERATIONS[role]:
                    raise IsolationViolation("TEST ISOLATION: undeclared child operation")
                if handler is None:
                    from app.notifications.transport_arbiter import ChildRole
                    handler=ChildRole(role,emit,nonce)
                handler.sequence=sequence
                result=handler.handle(operation,payload)
            assert_no_violations()
            emit(dict(sequence=sequence,result=result))
        except Exception as exc:
            emit(dict(sequence=sequence,error=type(exc).__name__))


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
    elif sys.argv[1:2] == ["--r2a-child"]:
        _run_test_child(sys.argv[1:])
    else:
        raise SystemExit("Use --self-test or --pytest [pytest arguments]")
