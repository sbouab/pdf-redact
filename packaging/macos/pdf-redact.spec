# PyInstaller recipe for "PDF Redact.app", the desktop app for Apple Silicon Macs. build.sh runs it from the
# repository folder, after making the icon (build/PDF Redact.icns).
import tomllib
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parents[1]
NAME = "PDF Redact"
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]

a = Analysis(
    [str(ROOT / "packaging" / "macos" / "launch.py")],
    pathex=[str(ROOT)],
    excludes=["onnxruntime", "tkinter"],      # never used (pdfredact/__init__.py keeps onnxruntime out on purpose)
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    console=False,
    argv_emulation=False,                      # Qt receives the PDFs itself (pdfredact/gui/fileopen.py)
    target_arch="arm64",
)
coll = COLLECT(exe, a.binaries, a.datas, name=NAME)
app = BUNDLE(
    coll,
    name=f"{NAME}.app",
    icon=str(ROOT / "build" / f"{NAME}.icns"),
    bundle_identifier="io.github.sbouab.pdf-redact",
    version=VERSION,
    info_plist={
        "CFBundleDisplayName": NAME,
        "CFBundleShortVersionString": VERSION,
        "NSHighResolutionCapable": True,
        "NSHumanReadableCopyright": "Uses PyMuPDF (AGPL-3.0) and Qt for Python (LGPL-3.0).",
        # offered for PDFs ("Open With", a PDF dropped on the app's icon), never made the default PDF app
        "CFBundleDocumentTypes": [{
            "CFBundleTypeName": "PDF document",
            "CFBundleTypeRole": "Viewer",
            "LSHandlerRank": "Alternate",
            "LSItemContentTypes": ["com.adobe.pdf"],
        }],
    },
)
