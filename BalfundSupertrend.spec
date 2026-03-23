# BalfundSupertrend.spec
# Run locally:  pyinstaller BalfundSupertrend.spec

import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

a = Analysis(
    ['app.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        *collect_data_files('customtkinter'),
    ],
    hiddenimports=[
        'customtkinter',
        'pyotp',
        'schedule',
        'websocket',
        'websocket._app',
        'websocket._core',
        'dotenv',
        'strategy_logger',
        *collect_submodules('customtkinter'),
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib','numpy','pandas','scipy','PIL'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='BalfundSupertrend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # no black terminal window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,              # add icon.ico here if you have one
)
