import os
from pathlib import Path
from typing import IO, Optional


class AlreadyRunningError(RuntimeError):
    pass


class SingleInstanceLock:
    """OS-level file lock: two bots polling one Telegram token fight over updates."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._handle: Optional[IO[bytes]] = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")
        try:
            _lock(handle)
        except OSError as exc:
            handle.close()
            raise AlreadyRunningError(f"Another instance holds {self.path}") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()).encode())
        handle.flush()
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            _unlock(self._handle)
        finally:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> "SingleInstanceLock":
        self.acquire()
        return self

    def __exit__(self, *_exc) -> None:
        self.release()


if os.name == "nt":
    import msvcrt

    # Lock a byte past the PID text so the file stays readable.
    _LOCK_OFFSET = 4096

    def _lock(handle: IO[bytes]) -> None:
        handle.seek(_LOCK_OFFSET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(handle: IO[bytes]) -> None:
        handle.seek(_LOCK_OFFSET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock(handle: IO[bytes]) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(handle: IO[bytes]) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
