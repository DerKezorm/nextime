"""Pictures and PDFs are read in processes of their own, never in the server's (TIME-9, as nexnotes NOTES-10).

A file of a few hundred bytes can ask a decoder for gigabytes or minutes: a PDF whose form draws itself, a GIF of a
thousand full-size frames, a page of two million lines. Whatever reads such a file therefore runs in a worker process
with limits of its own:

* **memory**: the worker puts itself under ``MEMORY_MB`` (``app/worker_limits.py``: a job object on Windows,
  ``RLIMIT_DATA``/``RLIMIT_AS`` on Linux). Past
  the limit an allocation fails, the worker ends, and the server goes on;
* **time**: each task has its seconds (``run(..., seconds=)``); a worker that does not answer in time is stopped from
  outside. On Linux ``RLIMIT_CPU`` stops a worker that computes too long by itself, too;
* **how many**: at most ``worker_limits.WORKERS`` worker processes at once; more uploads wait for a free one
  instead of flooding the machine.

A worker serves one task after the other (starting Python and the decoders takes a second or two) and is replaced
after ``RECYCLE`` tasks, after any failure, and after ``IDLE_SECONDS`` without work. A task that fails because of the
limits raises ``MediaFailed``; the caller keeps the file without a preview. An exception the task itself raises (a file
that is not what it was sent as) comes back as that exception.

Imports nothing of the app but ``worker_limits``, so that a fresh worker starts quickly (Windows spawns, it does not
fork); the task's module is imported in the worker.
"""

from __future__ import annotations

import importlib
import logging
import multiprocessing
import os
import threading
import time
from dataclasses import dataclass
from multiprocessing.connection import Connection
from typing import Any

from .. import worker_limits

logger = logging.getLogger("nextime.attachments")

#: Memory of one worker with what it starts, in MB (``NEXTIME_MEDIA_MEMORY_MB``).
MEMORY_MB = max(256, int(os.environ.get("NEXTIME_MEDIA_MEMORY_MB") or 1024))
#: Tasks a worker serves before it is replaced.
RECYCLE = 50
#: A worker without work ends after this long; the server never hands it a task once it has waited ``STALE_SECONDS``.
IDLE_SECONDS = 300.0
STALE_SECONDS = IDLE_SECONDS - 60
#: How long a new worker may take to start.
START_SECONDS = 60.0
#: The tasks a worker runs, as ``module:function``. Nothing else is ever imported or called there.
TARGETS = {
    "app.services.media:prepare_any",
}
#: Runs the tasks in this process (only for a test that changes a function in this process).
INLINE = False


class MediaFailed(Exception):
    """A task did not come back: ``timeout``, ``ended`` (its memory ran out or it crashed), ``start`` or
    ``limits`` (the limits could not be set, so nothing was read)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# --- In the worker ------------------------------------------------------------------------------------------------

def limit_self(memory: int) -> None:
    """Puts this process (and whatever it starts) under ``memory`` bytes (``worker_limits``)."""
    worker_limits.limit_self(memory)


def _call(target: str, args: tuple[Any, ...]) -> Any:
    module, name = target.split(":", 1)
    return getattr(importlib.import_module(module), name)(*args)


def _child(conn: Connection, memory: int, idle: float) -> None:
    try:
        limit_self(memory)
    except BaseException as exc:  # noqa: BLE001 - without limits nothing is read
        conn.send(("limits", f"{type(exc).__name__}: {exc}"))
        conn.close()
        return
    conn.send(("ready", None))
    while conn.poll(idle):
        try:
            target, args, seconds = conn.recv()
        except EOFError:
            break
        worker_limits.cpu_for(seconds)
        try:
            answer: tuple[str, Any] = ("ok", _call(target, args))
        except MemoryError:
            # What is left of the memory is not to be trusted with the next file.
            conn.send(("ended", "memory"))
            break
        except Exception as exc:  # noqa: BLE001 - the task's own exception goes back to the caller
            answer = ("raised", exc)
        try:
            conn.send(answer)
        except Exception as exc:  # noqa: BLE001 - an answer that cannot be sent is still an answer
            conn.send(("ended", f"answer: {type(exc).__name__}"))
            break
    conn.close()


# --- In the server ------------------------------------------------------------------------------------------------


@dataclass
class _Worker:
    process: Any
    conn: Connection
    done: int = 0
    idle_since: float = 0.0


_lock = threading.Lock()
_idle: list[_Worker] = []


def _end(worker: _Worker) -> None:
    try:
        worker.conn.close()
    except OSError:
        pass
    if worker.process.is_alive():
        worker.process.kill()
    worker.process.join(5)


def _start() -> _Worker:
    context = multiprocessing.get_context("spawn")
    mine, theirs = context.Pipe(duplex=True)
    process = context.Process(target=_child, args=(theirs, MEMORY_MB * 1024 * 1024, IDLE_SECONDS), daemon=True,
                              name="nextime-media")
    process.start()
    theirs.close()
    worker = _Worker(process=process, conn=mine)
    try:
        if not mine.poll(START_SECONDS):
            raise MediaFailed("start")
        status, detail = mine.recv()
    except (EOFError, OSError) as exc:
        _end(worker)
        raise MediaFailed("start") from exc
    except MediaFailed:
        _end(worker)
        raise
    if status != "ready":
        _end(worker)
        logger.error("A media worker could not limit itself: %s", detail)
        raise MediaFailed("limits")
    return worker


def _take() -> _Worker:
    with _lock:
        while _idle:
            worker = _idle.pop()
            if worker.process.is_alive() and time.monotonic() - worker.idle_since < STALE_SECONDS:
                return worker
            _end(worker)
    return _start()


def _give_back(worker: _Worker) -> None:
    worker.done += 1
    if worker.done >= RECYCLE:
        _end(worker)
        return
    worker.idle_since = time.monotonic()
    with _lock:
        _idle.append(worker)


def run(target: str, *args: Any, seconds: float) -> Any:
    """``target`` (one of ``TARGETS``) with ``args`` in a worker; its answer, or the exception it raised, or
    ``MediaFailed`` when the limits stopped it."""
    if target not in TARGETS:
        raise ValueError(f"not a media task: {target}")
    if INLINE:
        return _call(target, args)
    with worker_limits.slot():
        worker = _take()
        try:
            worker.conn.send((target, args, seconds))
            if not worker.conn.poll(seconds):
                raise MediaFailed("timeout")
            status, value = worker.conn.recv()
        except (EOFError, OSError) as exc:
            _end(worker)
            raise MediaFailed("ended") from exc
        except MediaFailed:
            _end(worker)
            raise
        if status == "ok":
            _give_back(worker)
            return value
        if status == "raised":
            _give_back(worker)
            raise value
        _end(worker)
        raise MediaFailed("ended")


def shutdown() -> None:
    """Ends every idle worker (at the end of a test, or when the limits change)."""
    with _lock:
        idle = list(_idle)
        _idle.clear()
    for worker in idle:
        _end(worker)
