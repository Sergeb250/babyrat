import subprocess
import sys
import platform

cmd = [
    "pyinstaller",
    "--onefile",          # single .exe file
    "--noconsole",        # no terminal window (Windows)
    "--name", "vnc-client",
    "--hidden-import", "pynput.keyboard._win32",   # Windows pynput fix
    "--hidden-import", "pynput.mouse._win32",
    "--hidden-import", "pystray._win32",
    "client.py"
]

if platform.system() == "Darwin":
    # macOS: replace win32 hidden imports with darwin ones
    cmd = [c.replace("_win32", "_darwin") for c in cmd]
elif platform.system() == "Linux":
    cmd = [c.replace("_win32", "_xorg") for c in cmd]

subprocess.run(cmd, check=True)
print("Build complete: dist/vnc-client" + (".exe" if sys.platform=="win32" else ""))
