"""Windows process plumbing for the kit: one copy per machine, a stop signal, children that die with it.

Importable anywhere (CI imports the kit on Linux); calling these off Windows raises OSError.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

ERROR_FILE_NOT_FOUND = 2
ERROR_ACCESS_DENIED = 5
ERROR_ALREADY_EXISTS = 183
SYNCHRONIZE = 0x00100000
EVENT_MODIFY_STATE = 0x0002
WAIT_OBJECT_0, WAIT_ABANDONED, WAIT_TIMEOUT = 0x0, 0x80, 0x102
PROCESS_QUERY_INFORMATION, PROCESS_VM_READ = 0x0400, 0x0010
SEM_FAILCRITICALERRORS, SEM_NOGPFAULTERRORBOX = 0x0001, 0x0002
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

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
else:
    _k32 = _psapi = None


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
    """The named event `--stop` sets; created unset, since only the copy holding the mutex creates it."""

    def __init__(self, name: str) -> None:
        k32 = _api()
        self.handle = k32.CreateEventW(None, True, False, name)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
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
