from PyInstaller.utils.hooks import collect_submodules

# Build V90 : reprend telle quelle la configuration GStreamer validée du
# KaronlineBox_GST_V12.spec (aucune DLL/plugin GStreamer embarqué, pour
# éviter le conflit de double chargement des DLL cœur GStreamer avec
# l'installation système utilisée par core/gstreamer_player.py).

binaries = []
datas = []
hiddenimports = collect_submodules("core") + collect_submodules("ui") + [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
]

a = Analysis(
    ["app.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="KaronlineBox_V90",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon="ui\\kb_logo_luxury.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="KaronlineBox_V90",
)
