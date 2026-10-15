"""Capture writes the server never received, replayed once it answers again.

The Hermes counterpart of openclaw's ``write-buffer.ts`` and the claude-code /
codex warm-up buffer. A session write (a turn or a tool trace) that cannot reach
the server is queued in ``~/.cognee-plugin/hermes/pending-entries.json`` and
replayed after the next successful write, when a session starts, and, for the
ending session, before its session-end improve. Without it every turn captured
while the local server is still booting, or briefly down, is lost.

Only failures where the request certainly never reached the server are queued:
the connection could not be opened (refused, unreachable, DNS failure) or the
server answered 503 while warming up. A timeout, a reset mid-request or another
error can happen after the server stored the entry, so those are not replayed:
a lost turn is better than a duplicated one. Replayed entries leave the file one
by one, so a crash mid-replay re-sends at most the entry in flight.

The file is shared by every Hermes process on the machine; each
read-modify-write holds an exclusive ``flock`` on a sidecar lock file where the
platform has one, and a process-wide lock otherwise.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from .config import SHARED_PLUGIN_STATE_DIR

try:  # POSIX only; Windows falls back to the in-process lock.
    import fcntl
except ImportError:  # pragma: no cover - exercised on Windows only
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

WRITE_BUFFER_PATH = SHARED_PLUGIN_STATE_DIR / "hermes" / "pending-entries.json"
MAX_BUFFERED_ENTRIES = 500
MAX_BUFFERED_AGE_SECS = 7 * 24 * 60 * 60

SendFn = Callable[[dict[str, Any]], None]


def is_bufferable_write_error(exc: BaseException) -> bool:
    """True when a failed write certainly never reached the server."""
    status = getattr(exc, "status", None)
    if isinstance(status, int):
        return status == 503
    return bool(getattr(exc, "not_connected", False))


def _key(item: dict[str, Any]) -> str:
    return json.dumps(
        [item.get("queued_at"), item.get("session_id"), item.get("kind"), item.get("entry")],
        sort_keys=True,
    )


class WriteBuffer:
    def __init__(self, path: Path = WRITE_BUFFER_PATH) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._drain_lock = threading.Lock()
        # False only once a drain left the file empty and nothing was enqueued
        # since. True at start: an earlier run may have left entries behind.
        self._maybe_non_empty = True

    @contextlib.contextmanager
    def _locked(self):
        with self._lock:
            handle = None
            if fcntl is not None:
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    handle = open(f"{self.path}.lock", "a")  # noqa: SIM115
                    fcntl.flock(handle, fcntl.LOCK_EX)
                except OSError:
                    if handle is not None:
                        handle.close()
                    handle = None
            try:
                yield
            finally:
                if handle is not None:
                    with contextlib.suppress(OSError):
                        fcntl.flock(handle, fcntl.LOCK_UN)
                    handle.close()

    def _load(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        entries = data.get("entries") if isinstance(data, dict) else None
        return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []

    def _save(self, entries: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"entries": entries}), encoding="utf-8")
        os.replace(tmp, self.path)
        with contextlib.suppress(OSError):
            os.chmod(self.path, 0o600)

    @staticmethod
    def _prune(entries: list[dict[str, Any]], now: Optional[float] = None) -> list[dict[str, Any]]:
        """Keep the newest ``MAX_BUFFERED_ENTRIES``, none older than a week."""
        now = time.time() if now is None else now
        fresh = [
            e for e in entries if now - float(e.get("queued_at") or 0) <= MAX_BUFFERED_AGE_SECS
        ]
        return fresh[-MAX_BUFFERED_ENTRIES:]

    @property
    def maybe_non_empty(self) -> bool:
        return self._maybe_non_empty

    def enqueue(self, *, dataset: str, session_id: str, kind: str, entry: dict[str, Any]) -> None:
        self._maybe_non_empty = True
        item = {
            "dataset": dataset,
            "session_id": session_id,
            "kind": kind,
            "entry": entry,
            "queued_at": time.time(),
        }
        with self._locked():
            entries = self._load()
            entries.append(item)
            self._save(self._prune(entries))

    def size(self, session_id: str = "") -> int:
        with self._locked():
            entries = self._load()
        if session_id:
            return sum(1 for e in entries if e.get("session_id") == session_id)
        return len(entries)

    def _remove(self, item: dict[str, Any]) -> None:
        with self._locked():
            current = self._load()
            wanted = _key(item)
            for index, entry in enumerate(current):
                if _key(entry) == wanted:
                    del current[index]
                    self._save(current)
                    return

    def drain(
        self,
        send: SendFn,
        *,
        budget_secs: float,
        session_id: str = "",
        on_drop: Optional[Callable[[dict[str, Any], BaseException], None]] = None,
    ) -> int:
        """Replay queued entries oldest-first. Returns how many were stored.

        With ``session_id`` only that session's entries are sent (session end);
        others stay queued. A send that fails with a bufferable error ends the
        drain and keeps the rest; any other failure drops that entry, since
        replaying it can never succeed (a 4xx) or may duplicate it (a timeout).
        Single-flight per process: a drain already running is not joined.
        """
        if not self._drain_lock.acquire(blocking=bool(session_id)):
            return 0
        try:
            deadline = time.monotonic() + budget_secs
            with self._locked():
                pending = self._prune(self._load())
            stored = 0
            for item in pending:
                if time.monotonic() >= deadline:
                    break
                if session_id and item.get("session_id") != session_id:
                    continue
                try:
                    send(item)
                    stored += 1
                except Exception as exc:
                    if is_bufferable_write_error(exc):
                        break
                    if on_drop is not None:
                        on_drop(item, exc)
                self._remove(item)
            with self._locked():
                if not self._load():
                    self._maybe_non_empty = False
            return stored
        finally:
            self._drain_lock.release()


_BUFFERS: dict[str, WriteBuffer] = {}
_BUFFERS_LOCK = threading.Lock()


def write_buffer_for(path: Path = WRITE_BUFFER_PATH) -> WriteBuffer:
    """The process-wide buffer for ``path``, shared by every provider instance."""
    with _BUFFERS_LOCK:
        key = str(path)
        if key not in _BUFFERS:
            _BUFFERS[key] = WriteBuffer(Path(path))
        return _BUFFERS[key]
