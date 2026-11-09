"""The app's private folder for temporary files: private, deleted on close, and deleted at the next start
when the app that made it is gone. Run from the repository folder:  uv run pytest"""
import os
import subprocess
import sys
import time
from pathlib import Path

from pdfredact.gui.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]

CHILD = """
import sys
sys.path.insert(0, {root!r})
from pdfredact.gui.workspace import Workspace
ws = Workspace({parent!r})
print(ws.path, flush=True)
if {wait}:
    sys.stdin.readline()
    ws.close()
"""


def child(parent, wait):
    code = CHILD.format(root=str(ROOT), parent=str(parent), wait=wait)
    return subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)


def test_workspace_is_private_and_removed_on_close(tmp_path):
    ws = Workspace(tmp_path)
    assert ws.path.is_dir() and ws.path.parent == tmp_path and ws.path.name.startswith("pdf-redact-app-")
    if os.name == "posix":
        assert ws.path.stat().st_mode & 0o777 == 0o700
    out = ws.file("result.pdf")
    assert out.parent == ws.path and not out.exists()
    out.write_bytes(b"%PDF-1.7")
    ws.remove(out)
    ws.remove(out)                                # already gone: fine
    assert not out.exists()
    out.write_bytes(b"%PDF-1.7")
    ws.close()
    assert not ws.path.exists()
    ws.close()                                    # twice: fine


def test_leftover_of_a_dead_app_is_removed(tmp_path):
    p = child(tmp_path, wait=False)
    left = Path(p.stdout.readline().strip())
    p.wait(timeout=30)
    assert left.is_dir()
    assert Workspace.remove_leftovers(tmp_path) == 1
    assert not left.exists()


def test_folder_of_a_running_app_is_kept(tmp_path):
    p = child(tmp_path, wait=True)
    try:
        running = Path(p.stdout.readline().strip())
        assert Workspace.remove_leftovers(tmp_path) == 0
        assert running.is_dir()
    finally:
        p.communicate("\n", timeout=30)
    assert not running.exists()


def test_own_folder_is_kept(tmp_path):
    ws = Workspace(tmp_path)
    assert Workspace.remove_leftovers(tmp_path) == 0 and ws.path.is_dir()
    ws.close()


def test_unrelated_and_fresh_folders_are_kept(tmp_path):
    other = tmp_path / "pdf-redact-notes"
    other.mkdir()
    named_like_ours = tmp_path / "pdf-redact-app-file.txt"
    named_like_ours.write_text("not a folder", encoding="utf-8")
    fresh = tmp_path / "pdf-redact-app-fresh"          # being created by an app starting right now: no lock yet
    fresh.mkdir()
    old = tmp_path / "pdf-redact-app-old"              # its app crashed before taking the lock
    old.mkdir()
    an_hour_ago = time.time() - 3600
    os.utime(old, (an_hour_ago, an_hour_ago))
    assert Workspace.remove_leftovers(tmp_path) == 1
    assert other.is_dir() and named_like_ours.is_file() and fresh.is_dir() and not old.exists()


def test_a_folder_whose_lock_cannot_be_opened_is_kept(tmp_path):
    odd = tmp_path / "pdf-redact-app-odd"             # e.g. another user's folder in a shared /tmp
    (odd / "owner.lock").mkdir(parents=True)          # opening it as a file fails
    assert Workspace.remove_leftovers(tmp_path) == 0
    assert odd.is_dir()


def test_a_busy_file_is_left_for_the_folder_clean_up(tmp_path, monkeypatch):
    """On Windows a file can stay busy for a moment (an antivirus scan): removing it then must not fail. It goes
    with the folder when the app closes."""
    ws = Workspace(tmp_path)
    busy = ws.file("redacted-1.pdf")
    busy.write_bytes(b"%PDF-1.7")

    def refuse(path):
        raise PermissionError(13, "The process cannot access the file", str(path))
    monkeypatch.setattr(os, "remove", refuse)
    ws.remove(busy)
    monkeypatch.undo()
    ws.close()
    assert not ws.path.exists()


def test_a_folder_deleted_meanwhile_is_skipped(tmp_path, monkeypatch):
    """Another app starting at the same moment may delete a leftover while this one looks at it."""
    import shutil
    gone = tmp_path / "pdf-redact-app-gone"
    gone.mkdir()
    real_is_dir = Path.is_dir

    def vanishing(self):
        found = real_is_dir(self)
        if self == gone:
            shutil.rmtree(gone)
        return found
    monkeypatch.setattr(Path, "is_dir", vanishing)
    assert Workspace.remove_leftovers(tmp_path) == 0
