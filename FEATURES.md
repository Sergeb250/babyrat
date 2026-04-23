# C2 Premium "Total Control" - Feature List

## 🕹️ Interactive Remote Control (AnyDesk-style)
- **HID Injection Engine**: Full remote control of mouse and keyboard via browser.
- **Pixel-Perfect Mapping**: Automatic resolution scaling ensures clicks hit the exact target pixels.
- **Real-Time Mouse Relay**: Support for cursor movement, left clicks, right clicks, and double clicks.
- **Full Keyboard Capture**: Inject standard typing and system keys (Enter, Esc, Shift, Ctrl) directly into the OS.

## 🛡️ Stealth & Evasion (FUD Strategy)
- **AES-256 String Encryption**: All sensitive strings (IPs, Command IDs) are encrypted with a unique 32-byte key generated per build.
- **AMSI & ETW Patching**: Hex-encoded memory patches to bypass Windows Antimalware Scan Interface and Event Tracing.
- **Anti-Analysis Suite**: Detection for VMWare, VirtualBox, QEMU, and low-resource sandboxes.
- **UAC Self-Elevation**: Automatically requests Administrator privileges (`runas`) on launch to ensure high-integrity access.
- **Junk Code Injection**: Random polyglot logic added to the stub to disrupt signature-based detection.

## 📡 Advanced Surveillance
- **Real-Time Remote Desktop**: Low-latency screen streaming with adjustable quality and frame rate.
- **Live Webcam Stream**: Professional-grade frame capture from the target's primary camera.
- **Interactive Keylogger**: Live, buffered keystroke recording including special system keys.

## 🔑 Credential & Identity Harvesting
- **Universal Browser Vault**: Decrypts and extracts saved passwords from **Chrome, Edge, Brave, and Firefox**.
- **Session & Cookie Stripper**: Captures active browser cookies to facilitate session hijacking and 2MF bypass.
- **SAM & SYSTEM Dump**: Privileged routine to export registry hives for offline credential cracking.

## 📁 Remote Administration & File Ops
- **Breadcrumb File Explorer**: Modern, interactive navigator for directory traversal and file management.
- **Remote Execution**: Launch any binary or script on the target directly from the "Remote Explorer."
- **Payload Downloader**: Remotely download and execute `.exe`, `.ps1`, or `.bat` files from any URL.

## 🔐 System Locking & Control
- **Persistent Maintenance Lock**: A fullscreen overlay that survives reboots and launches instantly on startup.
- **AES-256 File Locker**: Military-grade encryption for Documents/Desktop, requiring a C2 key to restore.
- **Integrated PowerShell Console**: Professional Blue terminal for multi-line script execution.

## 🛠️ Deployment & Builder
- **Premium Builder Manager**: All-in-one GUI for server management and stub generation.
- **PDF Decoy Binder**: Bundle your stub with a legitimate PDF to distract the user during infection.
