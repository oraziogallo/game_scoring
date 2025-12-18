# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['process_video.py'],
    pathex=[],
    binaries=[('/opt/homebrew/bin/ffmpeg', '.'), ('/opt/homebrew/bin/ffprobe', '.')], 
    datas=[],
    hiddenimports=[],
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
    a.binaries,
    a.datas,
    [],
    name='game_scoring',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=True,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

app = BUNDLE(
    exe,
    name='game_scoring.app',
    icon='icon.png',
    bundle_identifier='com.yourname.game_scoring',
    info_plist={
        'CFBundleDocumentTypes': [
            {
                'CFBundleTypeName': 'Files',
                'CFBundleTypeRole': 'Viewer',
                'LSHandlerRank': 'Owner',
                'LSItemContentTypes': ['public.data', 'public.content'], # Accepts almost anything
            }
        ],
        'NSHighResolutionCapable': 'True'
    },
)