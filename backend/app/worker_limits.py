"""Hard limits for the processes that read files (previews of attachments, ``services/media_worker.py``;
later the text recognition), and how many of them run at once.

* ``limit_self(memory, cpu_seconds)`` puts the calling process and everything it starts (ffmpeg, Tesseract) under
  ``memory`` bytes and, if given, ``cpu_seconds`` of CPU time: a job object under Windows (one limit for the process
  and all it starts together; whatever is in it is killed with the process; a crash ends it at once instead of
  waiting for an error dialog nobody sees), ``RLIMIT_DATA``, ``RLIMIT_AS`` and ``RLIMIT_CPU`` elsewhere (a child
  inherits them, but each process counts on its own: a worker and its ffmpeg or Tesseract may take up to twice the
  memory together). It raises ``OSError`` when it cannot: a worker without limits reads nothing.
* ``cpu_for(seconds)`` gives a worker that serves task after task its CPU seconds for the next one (Linux; on
  Windows the server's wall clock stops it).
* ``slot(wait)`` in the server: at most ``WORKERS`` such processes at once, previews and recognition together.

Only the standard library, so that a fresh worker starts quickly.
"""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

#: Worker processes at once in the whole server, previews and recognition together.
WORKERS = 2

#: The job object of this process on Windows, held as long as the process lives (closing it ends the process).
_job: Any = None


def _limit_windows(memory: int, cpu_seconds: float | None) -> None:
    import ctypes
    from ctypes import wintypes as wt

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wt.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wt.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wt.DWORD), ("SchedulingClass", wt.DWORD)]

    class Counters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in ("ro", "wo", "oo", "rb", "wb", "ob")]

    class Extended(ctypes.Structure):
        _fields_ = [("Basic", Basic), ("Io", Counters), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.restype = wt.HANDLE
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wt.LPCWSTR]
    kernel.SetInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
    kernel.GetCurrentProcess.restype = wt.HANDLE
    kernel.SetErrorMode.argtypes = [wt.UINT]
    kernel.SetErrorMode(0x0001 | 0x0002 | 0x8000)  # FAILCRITICALERRORS | NOGPFAULTERRORBOX | NOOPENFILEERRORBOX
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObject")
    limits = Extended()
    # PROCESS_MEMORY | JOB_MEMORY | DIE_ON_UNHANDLED_EXCEPTION | KILL_ON_JOB_CLOSE
    limits.Basic.LimitFlags = 0x100 | 0x200 | 0x400 | 0x2000
    if cpu_seconds:
        limits.Basic.LimitFlags |= 0x0002  # PROCESS_TIME
        limits.Basic.PerProcessUserTimeLimit = int(cpu_seconds * 10_000_000)
    limits.ProcessMemoryLimit = limits.JobMemoryLimit = memory
    if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        raise OSError(ctypes.get_last_error(), "SetInformationJobObject")
    if not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
        raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject")
    global _job
    _job = job


def _limit_posix(memory: int, cpu_seconds: float | None) -> None:
    import resource

    # What is really written counts against RLIMIT_DATA; the address space has room for what libraries only reserve.
    wanted = [(resource.RLIMIT_DATA, memory), (resource.RLIMIT_AS, memory + 1024 * 1024 * 1024)]
    if cpu_seconds:
        wanted.append((resource.RLIMIT_CPU, int(cpu_seconds) + 1))
    for limit, value in wanted:
        _soft, hard = resource.getrlimit(limit)
        if hard != resource.RLIM_INFINITY:
            value = min(value, hard)
        resource.setrlimit(limit, (value, hard))
    # Fewer malloc arenas in what this process starts: each would reserve address space of its own.
    os.environ["MALLOC_ARENA_MAX"] = "2"


def limit_self(memory: int, cpu_seconds: float | None = None) -> None:
    """This process and whatever it starts under ``memory`` bytes and ``cpu_seconds`` of CPU each; ``OSError`` when
    the system does not let it."""
    if memory <= 0:
        raise OSError("no memory limit")
    if sys.platform == "win32":
        _limit_windows(memory, cpu_seconds)
    else:
        try:
            _limit_posix(memory, cpu_seconds)
        except (ImportError, ValueError) as exc:
            raise OSError(str(exc)) from exc


def cpu_for(seconds: float) -> None:
    """Linux: the next task of this process may compute ``seconds`` more, then the system ends it (``SIGXCPU``)."""
    if sys.platform == "win32":
        return
    import resource

    used = resource.getrusage(resource.RUSAGE_SELF)
    _soft, hard = resource.getrlimit(resource.RLIMIT_CPU)
    wanted = int(used.ru_utime + used.ru_stime + seconds) + 1
    if hard != resource.RLIM_INFINITY:
        wanted = min(wanted, hard)
    resource.setrlimit(resource.RLIMIT_CPU, (wanted, hard))


_slots = threading.Condition()
_busy = 0


@contextmanager
def slot(wait: float | None = None) -> Iterator[None]:
    """One of the ``WORKERS`` places for a worker process; ``TimeoutError`` when none was free within ``wait``
    seconds (``None``: as long as it takes)."""
    global _busy
    with _slots:
        if not _slots.wait_for(lambda: _busy < WORKERS, timeout=wait):
            raise TimeoutError("no worker free")
        _busy += 1
    try:
        yield
    finally:
        with _slots:
            _busy -= 1
            _slots.notify()
