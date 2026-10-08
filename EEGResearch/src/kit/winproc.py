"""Windows process plumbing for the kit: one copy per machine, a stop signal, children that die with it, and what
the SYSTEM updater needs: who is signed in, and starting the sensors as them.

Importable anywhere (CI imports the kit on Linux); calling these off Windows raises OSError.
"""

from __future__ import annotations

import ctypes
import os
import socket
import sys
from ctypes import wintypes
from typing import NamedTuple

ERROR_FILE_NOT_FOUND = 2
ERROR_ACCESS_DENIED = 5
ERROR_INSUFFICIENT_BUFFER = 122
ERROR_ALREADY_EXISTS = 183
AF_INET = 2
TCP_TABLE_OWNER_PID_CONNECTIONS = 4
SYNCHRONIZE = 0x00100000
EVENT_MODIFY_STATE = 0x0002
WAIT_OBJECT_0, WAIT_ABANDONED, WAIT_TIMEOUT = 0x0, 0x80, 0x102
PROCESS_QUERY_INFORMATION, PROCESS_VM_READ = 0x0400, 0x0010
SEM_FAILCRITICALERRORS, SEM_NOGPFAULTERRORBOX = 0x0001, 0x0002
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
TOKEN_QUERY, TOKEN_USER, TOKEN_ELEVATION = 0x0008, 1, 20
WIN_LOCAL_SYSTEM_SID = 22
WTS_ACTIVE, WTS_DISCONNECTED, WTS_SESSION_INFO = 0, 4, 24
CREATE_UNICODE_ENVIRONMENT, CREATE_BREAKAWAY_FROM_JOB = 0x00000400, 0x01000000
WINHTTP_ACCESS_TYPE_NAMED_PROXY = 3
_FILETIME_UNIX_OFFSET_S = 11_644_473_600  # 1601-01-01 to 1970-01-01

if sys.platform == "win32":
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _psapi = ctypes.WinDLL("psapi", use_last_error=True)
    _k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    _k32.CreateMutexW.restype = wintypes.HANDLE
    _k32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    _k32.OpenMutexW.restype = wintypes.HANDLE
    _k32.ReleaseMutex.argtypes = [wintypes.HANDLE]
    _k32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    _k32.CreateEventW.restype = wintypes.HANDLE
    _k32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    _k32.OpenEventW.restype = wintypes.HANDLE
    _k32.SetEvent.argtypes = _k32.ResetEvent.argtypes = [wintypes.HANDLE]
    _k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _k32.WaitForSingleObject.restype = wintypes.DWORD
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    _k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _k32.GetCurrentProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.SetErrorMode.argtypes = [wintypes.UINT]
    _psapi.EnumProcessModulesEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.HMODULE), wintypes.DWORD,
                                            ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
    _psapi.GetModuleFileNameExW.argtypes = [wintypes.HANDLE, wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
    _psapi.GetModuleFileNameExW.restype = wintypes.DWORD
    _k32.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    _iphlpapi = ctypes.WinDLL("iphlpapi")
    _iphlpapi.GetExtendedTcpTable.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
                                              wintypes.ULONG, ctypes.c_int, wintypes.ULONG]
    _iphlpapi.GetExtendedTcpTable.restype = wintypes.DWORD
    _advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    _advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    _advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
                                            ctypes.POINTER(wintypes.DWORD)]
    _advapi.IsWellKnownSid.argtypes = [wintypes.LPVOID, ctypes.c_int]
    _advapi.CreateProcessAsUserW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.LPVOID,
                                             wintypes.LPVOID, wintypes.BOOL, wintypes.DWORD, wintypes.LPVOID,
                                             wintypes.LPCWSTR, wintypes.LPVOID, wintypes.LPVOID]
    _wts = ctypes.WinDLL("wtsapi32", use_last_error=True)
    _wts.WTSEnumerateSessionsW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                           ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.DWORD)]
    _wts.WTSQuerySessionInformationW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_int,
                                                 ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.DWORD)]
    _wts.WTSQueryUserToken.argtypes = [wintypes.ULONG, ctypes.POINTER(wintypes.HANDLE)]
    _wts.WTSFreeMemory.argtypes = [wintypes.LPVOID]
    _userenv = ctypes.WinDLL("userenv", use_last_error=True)
    _userenv.CreateEnvironmentBlock.argtypes = [ctypes.POINTER(wintypes.LPVOID), wintypes.HANDLE, wintypes.BOOL]
    _userenv.DestroyEnvironmentBlock.argtypes = [wintypes.LPVOID]
    _winhttp = ctypes.WinDLL("winhttp", use_last_error=True)
    _k32.GlobalFree.argtypes = [wintypes.LPVOID]
else:
    _k32 = _psapi = _iphlpapi = _advapi = _wts = _userenv = _winhttp = None


def _api():
    if _k32 is None:
        raise OSError("Windows only")
    return _k32


class _IoCounters(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in ("r", "w", "o", "rb", "wb", "ob")]


class _BasicLimits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD)]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimits), ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class SingleInstance:
    """Owns the named mutex for this process's life; `acquired` is False when another copy holds it."""

    def __init__(self, name: str) -> None:
        k32 = _api()
        ctypes.set_last_error(0)  # a new mutex need not set it, and ctypes keeps the last call's value
        self.handle = k32.CreateMutexW(None, True, name)
        error = ctypes.get_last_error()
        # Access denied: another user's copy owns the Global\ name, which counts as running too.
        self.acquired = bool(self.handle) and error != ERROR_ALREADY_EXISTS


class StopSignal:
    """The named event `--stop` sets: the copy holding the mutex resets it, and its sidecar opens it as it is."""

    def __init__(self, name: str, reset: bool = True) -> None:
        k32 = _api()
        self.handle = k32.CreateEventW(None, True, False, name)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if reset:
            k32.ResetEvent(self.handle)

    def wait(self, seconds: float) -> bool:
        return _api().WaitForSingleObject(self.handle, int(seconds * 1000)) == WAIT_OBJECT_0


def signal_stop(name: str) -> bool:
    """Sets the named event; False when no copy created it."""
    k32 = _api()
    handle = k32.OpenEventW(EVENT_MODIFY_STATE, False, name)
    if not handle:
        return False
    try:
        return bool(k32.SetEvent(handle))
    finally:
        k32.CloseHandle(handle)


def wait_until_released(name: str, seconds: float) -> bool:
    """True once no process holds the named mutex (or none exists); False if one still does after seconds."""
    k32 = _api()
    handle = k32.OpenMutexW(SYNCHRONIZE, False, name)
    if not handle:
        return ctypes.get_last_error() == ERROR_FILE_NOT_FOUND
    try:
        result = k32.WaitForSingleObject(handle, int(seconds * 1000))
        if result in (WAIT_OBJECT_0, WAIT_ABANDONED):
            k32.ReleaseMutex(handle)
            return True
        return False
    finally:
        k32.CloseHandle(handle)


def set_error_mode() -> None:
    """A missing DLL in this process or a child becomes an exit code, never a dialog nobody can see."""
    _api().SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX)


_job = None  # held for the process's life: closing the last handle is what kills the children


def kill_children_with_me() -> None:
    """Puts this process in a kill-on-close job, so every child dies with it however it ends."""
    global _job
    k32 = _api()
    job = k32.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    info = _ExtendedLimits()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if (not k32.SetInformationJobObject(job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info),
                                        ctypes.sizeof(info))
            or not k32.AssignProcessToJobObject(job, k32.GetCurrentProcess())):
        error = ctypes.get_last_error()
        k32.CloseHandle(job)
        raise ctypes.WinError(error)
    _job = job


class _TcpRow(ctypes.Structure):
    """MIB_TCPROW_OWNER_PID: addresses and ports in network byte order, each port in the low 16 bits."""
    _fields_ = [(n, wintypes.DWORD) for n in ("state", "local_addr", "local_port", "remote_addr", "remote_port", "pid")]


def tcp_owner_pid(local: tuple[str, int], remote_port: int) -> int | None:
    """The PID owning the IPv4 endpoint local of a TCP connection to remote_port, or None if there is none."""
    _api()
    size = wintypes.DWORD(0)
    for _ in range(4):  # the table can grow between sizing it and reading it
        table = ctypes.create_string_buffer(max(size.value, ctypes.sizeof(wintypes.DWORD)))
        result = _iphlpapi.GetExtendedTcpTable(table, ctypes.byref(size), False, AF_INET,
                                               TCP_TABLE_OWNER_PID_CONNECTIONS, 0)
        if result == 0:
            break
        if result != ERROR_INSUFFICIENT_BUFFER:
            return None
    else:
        return None
    count = wintypes.DWORD.from_buffer(table).value
    addr = int.from_bytes(socket.inet_aton(local[0]), "little")
    for row in (_TcpRow * count).from_buffer(table, ctypes.sizeof(wintypes.DWORD)):
        if (row.local_addr == addr and socket.ntohs(row.local_port & 0xFFFF) == local[1]
                and socket.ntohs(row.remote_port & 0xFFFF) == remote_port):
            return row.pid
    return None


def session_id(pid: int) -> int | None:
    """The Windows session pid runs in, or None if Windows will not say (another user's process, say)."""
    session = wintypes.DWORD()
    return session.value if _api().ProcessIdToSessionId(pid, ctypes.byref(session)) else None


def own_session() -> int | None:
    return session_id(os.getpid())


def peer_in_this_session(peer: tuple[str, int], local_port: int) -> bool:
    """Whether the process at the other end of a loopback connection to local_port runs in this session."""
    pid = tcp_owner_pid(peer, local_port)
    mine = own_session()
    return pid is not None and mine is not None and session_id(pid) == mine


def loaded_modules(pid: int | None = None) -> list[str]:
    """Full paths of every DLL in a process: this one, or pid's."""
    k32 = _api()
    if pid is None:
        handle, close = k32.GetCurrentProcess(), False
    else:
        handle, close = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid), True
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = 512
        while True:
            modules = (wintypes.HMODULE * size)()
            needed = wintypes.DWORD()
            if not _psapi.EnumProcessModulesEx(handle, modules, ctypes.sizeof(modules), ctypes.byref(needed), 0x03):
                raise ctypes.WinError(ctypes.get_last_error())
            count = needed.value // ctypes.sizeof(wintypes.HMODULE)
            if count <= size:
                break
            size = count + 32
        name = ctypes.create_unicode_buffer(32768)
        paths = []
        for i in range(count):
            if _psapi.GetModuleFileNameExW(handle, modules[i], name, len(name)):
                paths.append(name.value)
        return paths
    finally:
        if close:
            k32.CloseHandle(handle)


# --- for the self-updater, which runs as SYSTEM -----------------------------------------------------------------

def _token_information(kind: int) -> ctypes.Array:
    process = _api().GetCurrentProcess()
    token = wintypes.HANDLE()
    if not _advapi.OpenProcessToken(process, TOKEN_QUERY, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        needed = wintypes.DWORD()
        _advapi.GetTokenInformation(token, kind, None, 0, ctypes.byref(needed))
        buffer = ctypes.create_string_buffer(needed.value)
        if not _advapi.GetTokenInformation(token, kind, buffer, needed, ctypes.byref(needed)):
            raise ctypes.WinError(ctypes.get_last_error())
        return buffer
    finally:
        _k32.CloseHandle(token)


def running_as_system() -> bool:
    user = _token_information(TOKEN_USER)  # TOKEN_USER: the SID pointer comes first
    return bool(_advapi.IsWellKnownSid(ctypes.c_void_p.from_buffer(user).value, WIN_LOCAL_SYSTEM_SID))


def running_elevated() -> bool:
    return bool(wintypes.DWORD.from_buffer(_token_information(TOKEN_ELEVATION)).value)


class Session(NamedTuple):
    id: int
    active: bool  # at the keyboard, as against switched away from
    logon: float | None  # Unix time; None when Windows would not say


class _SessionInfo(ctypes.Structure):
    _fields_ = [("SessionId", wintypes.DWORD), ("pWinStationName", wintypes.LPWSTR), ("State", ctypes.c_int)]


class _WtsInfo(ctypes.Structure):
    """WTSINFOW."""
    _fields_ = [("State", ctypes.c_int), *((n, wintypes.DWORD) for n in ("SessionId", "a", "b", "c", "d", "e", "f")),
                ("WinStationName", wintypes.WCHAR * 32), ("Domain", wintypes.WCHAR * 17),
                ("UserName", wintypes.WCHAR * 21), ("ConnectTime", ctypes.c_longlong),
                ("DisconnectTime", ctypes.c_longlong), ("LastInputTime", ctypes.c_longlong),
                ("LogonTime", ctypes.c_longlong), ("CurrentTime", ctypes.c_longlong)]


def _session_info(session: int) -> _WtsInfo | None:
    buffer, size = wintypes.LPVOID(), wintypes.DWORD()
    if not _wts.WTSQuerySessionInformationW(None, session, WTS_SESSION_INFO, ctypes.byref(buffer), ctypes.byref(size)):
        return None
    try:
        return _WtsInfo.from_buffer_copy(ctypes.string_at(buffer, ctypes.sizeof(_WtsInfo)))
    finally:
        _wts.WTSFreeMemory(buffer)


def signed_in_sessions() -> list[Session]:
    """Every session someone is signed in to, at the keyboard or switched away. One Windows will not describe counts
    too, with no sign-in time, so the updater takes it for a lesson under way."""
    _api()
    listing, count = wintypes.LPVOID(), wintypes.DWORD()
    if not _wts.WTSEnumerateSessionsW(None, 0, 1, ctypes.byref(listing), ctypes.byref(count)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        rows = [(r.SessionId, r.State) for r in (_SessionInfo * count.value).from_address(listing.value)]
    finally:
        _wts.WTSFreeMemory(listing)
    found = []
    for session, state in rows:
        if state not in (WTS_ACTIVE, WTS_DISCONNECTED):
            continue
        info = _session_info(session)
        if info is not None and not info.UserName:
            continue  # session 0, or a sign-in screen
        logon = info.LogonTime / 1e7 - _FILETIME_UNIX_OFFSET_S if info and info.LogonTime else None
        found.append(Session(session, state == WTS_ACTIVE, logon))
    return found


class _StartupInfo(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
                ("lpTitle", wintypes.LPWSTR), *((n, wintypes.DWORD) for n in "xyXYcCaf"),
                ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD), ("lpReserved2", wintypes.LPVOID),
                ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE), ("dwProcessId", wintypes.DWORD),
                ("dwThreadId", wintypes.DWORD)]


def _start_as(token, exe: str, cwd: str, environment) -> bool:
    info = _StartupInfo(cb=ctypes.sizeof(_StartupInfo), lpDesktop="winsta0\\default")
    process = _ProcessInformation()
    # Out of the task's job where it allows that, so the task's end cannot take the sensors with it.
    for flags in (CREATE_UNICODE_ENVIRONMENT | CREATE_BREAKAWAY_FROM_JOB, CREATE_UNICODE_ENVIRONMENT):
        command = ctypes.create_unicode_buffer(f'"{exe}"')  # CreateProcess may write to its command line
        if _advapi.CreateProcessAsUserW(token, None, command, None, None, False, flags, environment, cwd,
                                        ctypes.byref(info), ctypes.byref(process)):
            _k32.CloseHandle(process.hThread)
            _k32.CloseHandle(process.hProcess)
            return True
        if ctypes.get_last_error() != ERROR_ACCESS_DENIED:
            return False
    return False


def start_in_sessions(exe: str, cwd: str) -> list[int]:
    """exe in each session at the keyboard, as that session's user with their environment; needs SYSTEM."""
    started = []
    for session in signed_in_sessions():
        if not session.active:
            continue
        token = wintypes.HANDLE()
        if not _wts.WTSQueryUserToken(session.id, ctypes.byref(token)):
            continue
        environment = wintypes.LPVOID()
        try:
            if not _userenv.CreateEnvironmentBlock(ctypes.byref(environment), token, False):
                continue
            try:
                if _start_as(token, exe, cwd, environment):
                    started.append(session.id)
            finally:
                _userenv.DestroyEnvironmentBlock(environment)
        finally:
            _k32.CloseHandle(token)
    return started


class _ProxyInfo(ctypes.Structure):
    _fields_ = [("dwAccessType", wintypes.DWORD), ("lpszProxy", wintypes.LPVOID), ("lpszProxyBypass", wintypes.LPVOID)]


def https_proxy(spec: str | None) -> str | None:
    """WinHTTP's proxy setting ("host:port", or "http=a:1;https=b:2") as the proxy URL for https traffic."""
    entries = [e.strip() for e in (spec or "").replace(" ", ";").split(";") if e.strip()]
    chosen = next((e.split("=", 1)[1] for e in entries if e.lower().startswith("https=")), None)
    chosen = chosen or next((e for e in entries if "=" not in e), None)
    if not chosen:
        return None
    return chosen if "://" in chosen else f"http://{chosen}"


def winhttp_proxy() -> str | None:
    """The machine's WinHTTP proxy (`netsh winhttp show proxy`), where IT sets one for SYSTEM's traffic."""
    _api()
    info = _ProxyInfo()
    if not _winhttp.WinHttpGetDefaultProxyConfiguration(ctypes.byref(info)):
        return None
    try:
        if info.dwAccessType != WINHTTP_ACCESS_TYPE_NAMED_PROXY or not info.lpszProxy:
            return None
        return https_proxy(ctypes.wstring_at(info.lpszProxy))
    finally:
        for pointer in (info.lpszProxy, info.lpszProxyBypass):
            if pointer:
                _k32.GlobalFree(pointer)


def machine_guid() -> str | None:
    """Windows' own id for this installation, or None if it cannot be read."""
    try:
        import winreg  # noqa: PLC0415 -- Windows only

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
    except (ImportError, OSError):
        return None
    return value if isinstance(value, str) and value else None


def system_path(environ=os.environ) -> str:
    """PATH holding Windows' own folders only."""
    root = environ.get("SystemRoot") or r"C:\Windows"
    return ";".join([rf"{root}\System32", root, rf"{root}\System32\Wbem"])
