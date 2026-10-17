# -*- mode: python ; coding: utf-8 -*-
# WE-Mender 3.0（GUI · 单文件 · windowed）
# 构建：pyinstaller --clean --noconfirm WE-Mender.spec
# 产物：dist/WE-Mender.exe（双击即用，报告/备份生成在 exe 同目录）

a = Analysis(
    ['we_mender_gui.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['wemender'],
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
    a.binaries,
    a.datas,
    [],
    name='WE-Mender',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
