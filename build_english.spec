# -*- mode: python ; coding: utf-8 -*-
import sys
import os

# Python 3.13 compatibility workaround
if sys.version_info >= (3, 13):
    import warnings
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    # Add missing distutils workaround
    try:
        from setuptools import distutils
    except ImportError:
        pass

a = Analysis(
    ['client_loader.py'],
    pathex=[],
    binaries=[],
    datas=[('C:\\Users\\bense\\Downloads\\Chapter 2 PP&E.pdf', '.')],
    hiddenimports=['pkg_resources', 'asyncio', 'websockets', 'cryptography', 'Crypto'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'tcl', 'tix', 'PIL.ImageTk', 'matplotlib', 'numpy', 'pandas',
              'scipy', 'sympy', 'notebook', 'jupyter', 'bokeh', 'plotly', 'dash',
              'unittest', 'pydoc', 'doctest', 'turtle', 'venv', 'ensurepip', 'email', 
              'mailcap', 'http.server', 'cgi', 'cgitb', 'wsgiref', 'xmlrpc',
              'py_compile', 'compileall', 'filecmp', 'difflib', 'json.tool', 
              'configparser', 'netrc', 'getpass', 'msilib', 'antigravity', 'this', 
              'code', 'codeop', 'inspect', 'trace', 'profile', 'pstats', 'cProfile', 
              'bdb', 'pdb', 'poplib', 'imaplib', 'nntplib', 'telnetlib', 'quopri', 
              'smtplib', 'ftplib', 'asynchat', 'asyncore', 'multiprocessing', 
              'audioop', 'aifc', 'audiodev', 'sunaudiodev'],
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
    name='english',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    onefile=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    
    icon=[r'C:\Users\bense\Desktop\babyrat\babyrat-main\pdflogo.ico'],
)
