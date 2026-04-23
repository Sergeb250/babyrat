# C2 Premium "Total Control" - Usage Guide

This guide covers the deployment and operational lifecycle of the C2 Premium Suite.

## 📋 Prerequisites
- **Python 3.10+** (Recommended)
- **Pip Dependencies**:
  ```bash
  pip install mss websockets opencv-python pynput pywin32 fastapi uvicorn pycryptodome pillow
  ```
- **Builder**: `pyinstaller` must be in your PATH for stub generation.

---

## 🚀 Step 1: Launching the Control Hub
1. Open a terminal and run the all-in-one manager:
   ```bash
   python vnc_all_in_one.py
   ```
2. In the **📡 C2 Console** tab:
   - Select your **Bind Address** (e.g., `0.0.0.0` to listen on all interfaces).
   - Enter your **Server Port** (Default: `8080`).
3. Click **"START SERVER"**. The terminal log at the bottom will confirm if the relay is active.

---

## 🛠️ Step 2: Generating a Stealth Stub
1. Navigate to the **🛠️ Premium Builder** tab.
2. **Relay Host**: Select the IP that the target agent should connect back to.
3. **Advanced Options**:
   - **Force Admin (UAC)**: Ensures the agent re-launches with elevation for SAM dumping/system control.
   - **Stealth Persistence**: Automatically installs the agent and configures it to run on startup.
   - **PDF Decoy Binder**: 
     - Check **"Bundle with Decoy PDF"**.
     - Click **"Select PDF"** and pick a legitimate document (e.g., `invoice.pdf`).
4. Click **"GENERATE PREMIUM STUB (.EXE)"**.
5. Your final payload will be located in the `dist/Network_Support_Premium.exe` directory.

---

## 🕹️ Step 3: Command & Control (C2)
Once a target is infected, they will appear in the **📡 Global Nodes** list.

### 🖥️ Remote View & HID Injection
- **Surveillance**: Toggle between **Desktop View** and **Webcam Stream**.
- **HID Control**: Click the screen canvas to focus. You can now use your mouse and keyboard to control the remote machine directly.
- **Keys Support**: Full support for `Enter`, `Shift`, `Ctrl`, `Esc`, etc.

### 🔑 Credential Harvesting
- **Browser Vault**: Extracts saved logins from Chrome, Edge, Brave, and Firefox.
- **Cookies**: Use the **"Cookies"** tool to grab active sessions for 2FA bypass.
- **SAM Dump**: Request a dump stored in the remote `%TEMP%` for administrative hash collection.

### 📁 Advanced System Ops
- **PowerShell Terminal**: Use the right-hand console for real-time script execution.
- **Explorer**: Navigate the remote filesystem and run or download files.
- **System Locker**: Trigger **"Vault Encryption"** to lock local user folders with AES-256 (requires a password).
- **Maintenance Lock**: Lock the target out of their OS with a persistent, startup-aware overlay.

---

## 🛡️ Best Practices for Stealth
- **Icon Spacing**: When using the PDF Binder, ensure your decoy PDF looks professional. The agent will inherit a similar icon.
- **Relay Rotation**: If a relay host becomes flagged, generate a new stub with a different interface IP via the builder.
- **UAC Elevation**: Always check if the node has an **"ADMIN"** tag. If it doesn't, many system features (like Hive dumping) will be restricted.

---
> [!IMPORTANT]
> This framework is designed for professional administrative use. Always ensure you have explicit permission before deploying stubs to any machine.
