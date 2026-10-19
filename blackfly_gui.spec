# PyInstaller spec for the standalone Windows build.
#   .venv\Scripts\pyinstaller blackfly_gui.spec --noconfirm
# or simply run tools\build_exe.ps1. Output: dist\BlackflyGUI\BlackflyGUI.exe
#
# PySpin is bundled automatically when it is installed in the build venv.
# Users still need the Spinnaker SDK (or its USB3 driver) installed.

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

datas = collect_data_files("pyqtgraph")
binaries = []
hiddenimports = collect_submodules(
    "pyqtgraph",
    filter=lambda name: not any(s in name for s in (".opengl", ".examples", ".jupyter", ".canvas")),
)

try:
    import PySpin  # noqa: F401

    pyspin_datas, pyspin_binaries, pyspin_hidden = collect_all("PySpin")
    datas += pyspin_datas
    binaries += pyspin_binaries
    hiddenimports += pyspin_hidden
    print("Bundling PySpin")
except ImportError:
    print("WARNING: PySpin not installed in the build environment; the build will only "
          "support the simulated camera.")

excludes = [
    "tkinter", "matplotlib", "IPython", "PyQt5", "PyQt6", "pytest",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtQuickWidgets", "PySide6.Qt3DCore",
    "PySide6.QtMultimedia", "PySide6.QtPdf", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtSql",
]

a = Analysis(
    ["packaging/launcher.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BlackflyGUI",
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="BlackflyGUI")
