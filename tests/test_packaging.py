"""The Mac app's parts that can be checked on any computer: its starting script, its self-check, its icon and its
PyInstaller recipe. Building the app itself takes a Mac (packaging/macos/build.sh). Run:  uv run pytest"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MACOS = ROOT / "packaging" / "macos"


def python(*args):
    """Run Python on `args` from the repository folder, Qt without a screen."""
    return subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True, cwd=ROOT, timeout=300,
                          env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})


def test_the_app_blocks_the_network_before_anything_is_loaded():
    """The app's helper processes run its starting script again (as __mp_main__), before the job's code: the
    network is blocked there first too."""
    launch = (MACOS / "launch.py").as_posix()
    done = python("-c", "import runpy, socket, sys; real = socket.socket.connect; "
                        f"runpy.run_path('{launch}', run_name='__mp_main__'); "
                        "print(socket.socket.connect is not real, 'pymupdf' in sys.modules, 'PySide6' in sys.modules)")
    assert done.stdout.split() == ["True", "False", "False"], done.stdout + done.stderr


def test_the_self_check_passes():
    """build.sh runs it on the built app: the window opens unseen, and a made-up PDF is redacted in a helper
    process, as the app does it."""
    done = python(MACOS / "launch.py", "--self-check")
    assert done.returncode == 0 and "self-check passed" in done.stdout, done.stdout + done.stderr


def test_the_icon_is_drawn_at_every_size_macos_asks_for(qapp, tmp_path):
    from PySide6.QtGui import QImage
    folder = tmp_path / "PDF Redact.iconset"
    done = python(MACOS / "make_icon.py", folder)
    assert done.returncode == 0, done.stderr
    sizes = {p.name: (QImage(str(p)).width(), QImage(str(p)).height()) for p in folder.glob("*.png")}
    expected = {}
    for size in (16, 32, 128, 256, 512):
        expected[f"icon_{size}x{size}.png"] = (size, size)
        expected[f"icon_{size}x{size}@2x.png"] = (2 * size, 2 * size)
    assert sizes == expected


def test_the_app_opens_pdfs_without_becoming_the_default_pdf_app():
    """The recipe is valid Python; the app is offered for PDFs ("Open With", a drop on its icon), Preview stays
    the default."""
    spec = (MACOS / "pdf-redact.spec").read_text(encoding="utf-8")
    compile(spec, "pdf-redact.spec", "exec")
    for needed in ('"com.adobe.pdf"', '"LSHandlerRank": "Alternate"', '"CFBundleTypeRole": "Viewer"',
                   'target_arch="arm64"', "launch.py"):
        assert needed in spec, needed
