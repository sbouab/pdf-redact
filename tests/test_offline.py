"""Regular use never touches the network, from the first line of the tool on. Run:  uv run pytest"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def python(code):
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT)
    assert done.returncode == 0, done.stderr
    return done.stdout


def test_the_network_is_blocked_before_pymupdf_is_loaded():
    """redact.py blocks it first, both when run and when a helper process of the app imports it again (as
    __mp_main__, before the job's code is loaded)."""
    out = python(
        "import runpy, socket, sys\n"
        "real, seen = socket.socket.connect, []\n"
        "class Watch:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name in ('pymupdf', 'PySide6'):\n"
        "            seen.append((name, socket.socket.connect is not real))\n"
        "sys.meta_path.insert(0, Watch())\n"
        "runpy.run_path('redact.py', run_name='__mp_main__')\n"
        "print(sorted(set(seen)))\n")
    assert out.strip() == "[('pymupdf', True)]", out


def test_blocking_twice_changes_nothing():
    """The route is asked once, before the block: asking it again would only record a blocked attempt."""
    out = python(
        "import socket\n"
        "from pdfredact import offline\n"
        "first = offline.block_network()\n"
        "connect = socket.socket.connect\n"
        "print(offline.block_network() == first, socket.socket.connect is connect, offline.BLOCKED)\n"
        "try:\n"
        "    socket.create_connection(('127.0.0.1', 9), timeout=2)\n"
        "except OSError:\n"
        "    print(offline.BLOCKED)\n")
    assert out.split("\n")[:2] == ["True True []", "['127.0.0.1']"], out


def test_uv_works_offline_for_this_project():
    """`uv run` never looks for packages online; the first install says so on purpose (uv sync --no-offline)."""
    tomllib = pytest.importorskip("tomllib")           # Python 3.11 and later
    with open(ROOT / "pyproject.toml", "rb") as f:
        assert tomllib.load(f)["tool"]["uv"]["offline"] is True
