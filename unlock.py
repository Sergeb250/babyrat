import os
import sys
import hashlib
try:
    from Crypto.Cipher import AES
except ImportError:
    print("Error: pycryptodome is not installed. Please run `pip install pycryptodome`")
    sys.exit(1)

def decrypt_file(fp, key):
    try:
        with open(fp, "rb") as f:
            data = f.read()

        if len(data) <= 16:
            print(f"[-] {fp} is too small to be a valid encrypted file.")
            return False

        iv = data[:16]
        ciphertext = data[16:]
        
        cipher = AES.new(key, AES.MODE_CBC, iv)
        decrypted_padded = cipher.decrypt(ciphertext)
        
        # Unpad (PKCS#7)
        pad_len = decrypted_padded[-1]
        
        # Verify padding integrity
        if pad_len > 16 or pad_len < 1:
            return False
            
        decrypted = decrypted_padded[:-pad_len]
        
        # Remove '.locked' extension
        orig_fp = fp[:-7] if fp.endswith(".locked") else fp + ".unlocked"
        
        with open(orig_fp, "wb") as f:
            f.write(decrypted)
            
        os.remove(fp)
        return True
    except Exception as e:
        return False

def unlock_system():
    print("====================================")
    print("      C2 Premium - Decryptor        ")
    print("====================================")
    print("\nNote: The C2 agent encrypts files using AES-256-CBC based on the password")
    print("you entered in the dashboard prompt, NOT with the RSA private key.")
    
    password = input("\nEnter the password used to lock the files: ")
    key = hashlib.sha256(password.encode()).digest()
    
    count = 0
    failed = 0
    
    directories_to_scan = [
        os.path.join(os.path.expanduser("~"), "Desktop"),
        os.path.join(os.path.expanduser("~"), "Documents")
    ]
    
    for d in directories_to_scan:
        if not os.path.exists(d):
            continue
            
        print(f"\nScanning: {d}")
        for root, _, files in os.walk(d):
            for fn in files:
                if fn.endswith(".locked"):
                    fp = os.path.join(root, fn)
                    print(f"[*] Found: {fn} -> Decrypting...", end=" ")
                    if decrypt_file(fp, key):
                        print("SUCCESS")
                        count += 1
                    else:
                        print("FAILED (Wrong Password?)")
                        failed += 1

    print("\n====================================")
    print(f"Summary: {count} files successfully unlocked.")
    if failed > 0:
        print(f"         {failed} files failed to unlock.")
    print("====================================")

if __name__ == "__main__":
    unlock_system()
