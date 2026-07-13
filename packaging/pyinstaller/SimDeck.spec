# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all

# SPECPATH is the directory containing this spec file (packaging/pyinstaller/).
# Walk two levels up to get the repo root so all paths below are absolute.
_root = os.path.normpath(os.path.join(SPECPATH, '..', '..'))

datas = [(os.path.join(_root, 'assets'), 'assets')]
binaries = []
hiddenimports = []

# collect_all grabs everything (py modules, data files, binaries) from setuptools,
# which ensures vendored files like jaraco/text/Lorem ipsum.txt are bundled.
_bins, _datas, _hidden = collect_all('setuptools')
binaries += _bins
datas += _datas
hiddenimports += _hidden


a = Analysis(
    [os.path.join(_root, 'run.py')],
    pathex=[os.path.join(_root, 'src')],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + ['PySide6.QtCore', 'PySide6.QtWidgets', 'PySide6.QtGui', 'pystray._win32', 'psutil', 'lifxlan', 'bitstring.bitstore_bitarray', 'bitstring.bitstore_bitarray_helpers', 'bitstring.bitstore_common_helpers', 'bitstring.bitstore_tibs', 'bitstring.bitstore_tibs_helpers', 'bitstring.array_', 'bitstring.bitarray_', 'bitstring.luts', 'bitstring.mxfp'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SimDeck',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[os.path.join(_root, 'assets', 'simdeck.ico')],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SimDeck',
)
