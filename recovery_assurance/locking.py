"""Opt-in, nonblocking coordination for drills sharing a dedicated local lock file."""

import errno
import os
from contextlib import contextmanager
from pathlib import Path


class DrillLockBusy(Exception):
    """Another cooperating process already holds the requested drill lock."""


@contextmanager
def drill_lock(path: Path | None, *, database: Path | None = None):
    if path is None:
        yield
        return
    path = Path(path).absolute()
    if path.suffix.lower() != ".lock":
        raise ValueError("Lock file must end in .lock")
    if path.is_symlink():
        raise ValueError("Lock file must not be a symbolic link")
    if database is not None:
        database = Path(database).resolve()
        protected = [database] + [
            database.with_name(database.name + suffix) for suffix in ("-journal", "-wal", "-shm")
        ]
        for target in protected:
            if path.resolve() == target.resolve() or (
                path.is_file() and target.is_file() and path.samefile(target)
            ):
                raise ValueError("Lock file must be separate from history and SQLite sidecars")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock:
        if os.fstat(lock.fileno()).st_size:
            raise ValueError("Lock file must be empty")
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN):
                raise DrillLockBusy("Another drill holds the requested lock") from None
            raise
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
