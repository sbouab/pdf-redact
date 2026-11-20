"""Saves the app's icon (drawn by pdfredact/gui/style.py) as a macOS icon set: a folder of PNG files that
`iconutil -c icns` turns into the .icns file Finder and the Dock show. build.sh does both:

    python packaging/macos/make_icon.py "build/PDF Redact.iconset"
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")       # it only draws: no window, no Dock icon

from PySide6.QtGui import QGuiApplication  # noqa: E402

from pdfredact.gui.style import icon_pixmap  # noqa: E402

SIZES = (16, 32, 128, 256, 512)        # each also at twice the size, for Retina screens ("@2x")


def main(folder):
    app = QGuiApplication(sys.argv[:1])    # noqa: F841  (pictures need it)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for size in SIZES:
        for scale, suffix in ((1, ""), (2, "@2x")):
            path = folder / f"icon_{size}x{size}{suffix}.png"
            if not icon_pixmap(size * scale).save(str(path), "PNG"):
                raise SystemExit(f"can't write {path}")
    print(f"icon set: {folder}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
