"""Start the daemon and its children without a console window.

On Windows, CREATE_BREAKAWAY_FROM_JOB does not leave a job that forbids breakaway:
the call succeeds, but the process still dies with the job. A native-messaging host
is in such a job. Reparenting the daemon to explorer.exe puts it outside that job.
If explorer cannot be opened, Win32_Process.Create is the fallback, because the WMI
service is also outside the host's job.
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import subprocess
from pathlib import Path

CREATE_NEW_PROCESS_GROUP = 0x00000200
DETACHED_PROCESS = 0x00000008
CREATE_NO_WINDOW = 0x08000000
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
PROCESS_CREATE_PROCESS = 0x0080
PROC_THREAD_ATTRIBUTE_PARENT_PROCESS = 0x00020000


def hidden_child_kwargs(platform: str | None = None) -> dict:
    """Subprocess kwargs that hide a console on Windows and detach on POSIX."""
    if (platform or os.name) == "nt":
        return {"creationflags": CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


async def create_hidden_subprocess(*argv: str, **kwargs):
    """Start a child with no console window. The daemon's job is applied separately."""
    kwargs.pop("start_new_session", None)
    kwargs.update(hidden_child_kwargs())
    return await asyncio.create_subprocess_exec(*argv, **kwargs)


def spawn_detached_daemon(argv: list[str], cwd: Path) -> int:
    """Start the resident daemon so it outlives the native-messaging host. Returns the pid."""
    if os.name != "nt":
        process = subprocess.Popen(
            argv,
            cwd=str(cwd),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return int(process.pid)
    pid = _spawn_reparented_to_explorer(argv, cwd)
    if pid is not None:
        return pid
    return _spawn_via_wmi(argv, cwd)


def _explorer_pid() -> int | None:
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq explorer.exe", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        check=False,
        creationflags=CREATE_NO_WINDOW,
    )
    for line in result.stdout.splitlines():
        parts = [part.strip().strip('"') for part in line.split(",")]
        if len(parts) >= 2 and parts[0].lower() == "explorer.exe" and parts[1].isdigit():
            return int(parts[1])
    return None


def _spawn_reparented_to_explorer(argv: list[str], cwd: Path) -> int | None:
    explorer = _explorer_pid()
    if explorer is None:
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    parent = kernel32.OpenProcess(PROCESS_CREATE_PROCESS, False, explorer)
    if not parent:
        return None
    try:
        return _create_process_with_parent(kernel32, argv, cwd, parent)
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(parent))


def _create_process_with_parent(kernel32, argv: list[str], cwd: Path, parent: int) -> int | None:
    class StartupInfoW(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("lpReserved", ctypes.c_wchar_p),
            ("lpDesktop", ctypes.c_wchar_p),
            ("lpTitle", ctypes.c_wchar_p),
            ("dwX", ctypes.c_ulong),
            ("dwY", ctypes.c_ulong),
            ("dwXSize", ctypes.c_ulong),
            ("dwYSize", ctypes.c_ulong),
            ("dwXCountChars", ctypes.c_ulong),
            ("dwYCountChars", ctypes.c_ulong),
            ("dwFillAttribute", ctypes.c_ulong),
            ("dwFlags", ctypes.c_ulong),
            ("wShowWindow", ctypes.c_ushort),
            ("cbReserved2", ctypes.c_ushort),
            ("lpReserved2", ctypes.c_void_p),
            ("hStdInput", ctypes.c_void_p),
            ("hStdOutput", ctypes.c_void_p),
            ("hStdError", ctypes.c_void_p),
        ]

    class StartupInfoExW(ctypes.Structure):
        _fields_ = [("StartupInfo", StartupInfoW), ("lpAttributeList", ctypes.c_void_p)]

    class ProcessInformation(ctypes.Structure):
        _fields_ = [
            ("hProcess", ctypes.c_void_p),
            ("hThread", ctypes.c_void_p),
            ("dwProcessId", ctypes.c_ulong),
            ("dwThreadId", ctypes.c_ulong),
        ]

    size = ctypes.c_size_t(0)
    kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
    attributes = ctypes.create_string_buffer(size.value)
    if not kernel32.InitializeProcThreadAttributeList(attributes, 1, 0, ctypes.byref(size)):
        return None
    parent_value = ctypes.c_void_p(parent)
    updated = kernel32.UpdateProcThreadAttribute(
        attributes,
        0,
        PROC_THREAD_ATTRIBUTE_PARENT_PROCESS,
        ctypes.byref(parent_value),
        ctypes.sizeof(parent_value),
        None,
        None,
    )
    if not updated:
        kernel32.DeleteProcThreadAttributeList(attributes)
        return None
    startup = StartupInfoExW()
    startup.StartupInfo.cb = ctypes.sizeof(StartupInfoExW)
    startup.lpAttributeList = ctypes.addressof(attributes)
    process_info = ProcessInformation()
    command = subprocess.list2cmdline(argv)
    flags = EXTENDED_STARTUPINFO_PRESENT | CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS
    created = kernel32.CreateProcessW(
        None,
        command,
        None,
        None,
        False,
        flags,
        None,
        str(cwd),
        ctypes.byref(startup),
        ctypes.byref(process_info),
    )
    kernel32.DeleteProcThreadAttributeList(attributes)
    if not created:
        return None
    pid = int(process_info.dwProcessId)
    kernel32.CloseHandle(ctypes.c_void_p(process_info.hThread))
    kernel32.CloseHandle(ctypes.c_void_p(process_info.hProcess))
    return pid


def _spawn_via_wmi(argv: list[str], cwd: Path) -> int:
    command = subprocess.list2cmdline(argv).replace("'", "''")
    directory = str(cwd).replace("'", "''")
    flags = CREATE_NO_WINDOW | DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    script = (
        "$startup = New-Object System.Management.ManagementClass('Win32_ProcessStartup'); "
        "$info = $startup.CreateInstance(); "
        f"$info.CreateFlags = {flags}; "
        "$info.ShowWindow = 0; "
        f"$result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{{CommandLine='{command}'; CurrentDirectory='{directory}'; ProcessStartupInformation=$info}}; "
        "if ($result.ReturnValue -ne 0) { throw $result.ReturnValue }; "
        "$result.ProcessId"
    )
    output = subprocess.check_output(
        ["powershell", "-NoProfile", "-Command", script],
        text=True,
        creationflags=CREATE_NO_WINDOW,
    )
    return int(output.strip())
