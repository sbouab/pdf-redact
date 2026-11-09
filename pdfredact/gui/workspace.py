"""The app's private folder for temporary files, and the clean-up of the folders an app left behind.

The redacted copy is written here by the helper process before the app reads it into memory. The folder is
deleted when the app closes. Each folder holds a locked file while its app runs: the operating system releases
that lock when the app ends, even in a crash, so the next start can tell the leftovers from the folders of an
app still running, and delete them.
"""
import contextlib
import os
import shutil
import tempfile
import time
from pathlib import Path

PREFIX = "pdf-redact-app-"
LOCK = "owner.lock"
UNLOCKED_GRACE = 600      # seconds: a folder without a lock file may be one an app is creating right now

if os.name == "nt":
    import msvcrt

    def _lock(fh):
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(fh):
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock(fh):
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fh):
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _take_lock(path):
    """The lock file at `path` (created if needed), opened and locked; None if another app holds it, or if it
    can't be opened (then it isn't ours to touch)."""
    try:
        fh = open(path, "a+b")
    except OSError:
        return None
    try:
        _lock(fh)
    except OSError:
        fh.close()
        return None
    return fh


def _release(fh):
    try:
        _unlock(fh)
    finally:
        fh.close()


def _delete(folder, attempts=5):
    """Delete a folder and everything in it. On Windows a file can stay busy for a moment (a helper process
    that was just stopped, an antivirus scan), so try a few times. True if the folder is gone."""
    for i in range(attempts):
        try:
            shutil.rmtree(folder)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            if i == attempts - 1:
                return False
            time.sleep(0.1 * (i + 1))
    return False


class Workspace:
    """A private folder for the app's temporary files, deleted by close()."""

    def __init__(self, parent=None):
        self.path = Path(tempfile.mkdtemp(prefix=PREFIX, dir=parent))     # owner-only on macOS and Linux
        self._closed = False
        self._lock = _take_lock(self.path / LOCK)    # None only if the system refuses: then it may be cleaned early

    def file(self, name):
        """A path inside the folder (nothing is created)."""
        return self.path / name

    def remove(self, path):
        """Delete a file of the folder if possible. A file already gone is fine; one still busy (on Windows, an
        antivirus scan) is left for close(), which deletes the folder with everything in it."""
        with contextlib.suppress(OSError):
            os.remove(path)

    def close(self):
        """Release the folder and delete it with everything in it. Calling it again does nothing."""
        if self._closed:
            return
        self._closed = True
        if self._lock is not None:
            _release(self._lock)
            self._lock = None
        _delete(self.path)

    @classmethod
    def remove_leftovers(cls, parent=None):
        """Delete the folders left by apps that are no longer running. Returns how many were deleted."""
        removed = 0
        for folder in Path(parent or tempfile.gettempdir()).glob(PREFIX + "*"):
            try:
                if _left_behind(folder):
                    removed += _delete(folder)
            except OSError:
                continue                         # deleted meanwhile (another app starting), or not ours to read
        return removed


def _left_behind(folder):
    """True for the folder of an app that no longer runs, when this user may delete it."""
    if not folder.is_dir() or folder.is_symlink():
        return False
    if hasattr(os, "getuid") and folder.stat().st_uid != os.getuid():
        return False                             # another user's, in a shared temporary folder
    lock_path = folder / LOCK
    if not lock_path.exists():                   # its app may be creating it right now
        return time.time() - folder.stat().st_mtime >= UNLOCKED_GRACE
    fh = _take_lock(lock_path)
    if fh is None:
        return False                             # its app is still running
    _release(fh)
    return True
