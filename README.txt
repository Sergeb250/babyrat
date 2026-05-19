================================================================================
  NEXUS RAT — Remote Administration Toolkit
  Version 3.0  |  Windows Agent + Web Dashboard + Python Builder
================================================================================

TABLE OF CONTENTS
  1. Overview
  2. Architecture
  3. Dashboard Features
  4. Agent Features (client.py)
     4.1  Persistence & Immortality Engine
     4.2  Evasion & Antivirus Bypass
     4.3  Screen & Camera Streaming
     4.4  Audio Capture & Injection
     4.5  Remote HID (Mouse & Keyboard)
     4.6  File Management
     4.7  Remote Shell (PowerShell)
     4.8  Credential Harvesting
     4.9  Cryptography & Ransomware
     4.10 System Lock
     4.11 Worm & Self-Replication
     4.12 Remote Payload Execution
  5. Server Infrastructure (server.py)
  6. Builder & Manager (remote_manager.py)
  7. Protocol Reference
  8. Build Instructions
  9. Configuration
 10. Trigger Strings & Obfuscation


================================================================================
1. OVERVIEW
================================================================================

NEXUS RAT is a remote monitoring and administration toolkit consisting of three
components:

  +----------+          +----------+          +-----------+
  |  Agent   | -------> |  Server  | <------- | Dashboard |
  |client.py |  WS/WSS  |server.py |   WS     |   HTML5   |
  |(PyInst.) |          |(FastAPI) |          | (built-in)|
  +----------+          +----------+          +-----------+
                              |
                        +-----------+
                        |  Manager  |
                        |remote_man.|
                        |(Builder)  |
                        +-----------+

  - Agent:  Python script compiled via PyInstaller into a single .exe.
            Runs on target Windows machines.
  - Server: FastAPI + uvicorn WebSocket server. Hosts the web dashboard.
  - Manager: CLI/TUI tool to configure, obfuscate, and build the agent.


================================================================================
2. ARCHITECTURE
================================================================================

2.1 Communication Channels
  - Control channel:  /ws/client      (agent registration + commands + shell)
  - Stream channel:   /ws/client      (same connection, multiplexed)
  - Camera channel:   /ws/client_cam  (separate WebSocket for webcam)
  - Viewer channel:   /ws/viewer/{id} (dashboard browser, max 10 per agent)
  - Cam viewer:       /ws/viewer_cam/{id} (dashboard browser, max 5 per agent)

2.2 Protocol
  All control messages are JSON. Stream data is binary:
    \x01 + JPEG bytes  (screen or camera frame)
    \x04 + samplerate(2B LE) + numsamples(2B LE) + s16le PCM  (audio)
    \x03 + PCM header + s16le payload  (admin-to-agent mic)

2.3 Key Design Patterns
  - All persistence/privacy/evasion functions are idempotent and self-healing
  - Stream uses adaptive quality (15-85) based on send latency
  - Frame differencing (MD5) skips encode/send for identical frames
  - HID events get priority: stream throttles to 15fps when input active
  - Mutual watchdog: 5 hidden copies monitor each other continuously


================================================================================
3. DASHBOARD FEATURES
================================================================================

The dashboard is a single-page HTML/JS/CSS application served at the root URL.

3.1 Node List (Sidebar)
  Displays all connected agents with:
    - Hostname, IP address, OS version
    - Admin status indicator
    - Encryption/decrypt toggle buttons per node

3.2 Screen Stream  ("Desktop Stream")
  Canvas-based MJPEG viewer with rAF rendering.
  - createImageBitmap for hardware-accelerated decode
  - Adaptive quality controlled by agent's feedback loop
  - Mouse input: rAF-coalesced at up to 30fps, proportional to canvas size

3.3 Webcam Stream  ("Webcam Stream")
  Dedicated camera tab with separate WebSocket channel.
  - Supports MediaRecorder capture from canvas (WebM export)
  - Toggle audio substream

3.4 Remote Shell  ("Sidebar Terminal" + "Pop-out Terminal")
  Two shell instances per agent:
  - Side terminal: persistent, shows in the right panel
  - Pop terminal: modal, opens with current working directory from file explorer
  Both support:
    - PowerShell command execution with real-time 'running...' indicator
    - 120s timeout (90s for pop)
    - Font size A+/A−  (7-32px, persisted in localStorage)
    - Scroll lock toggle to freeze view for large outputs
    - 500-line history limit

3.5 File Explorer
  Remote directory browser with:
    - Drive enumeration (C:\, D:\, etc.)
    - Directory listing with async scan
    - File download (base64 transfer)
    - Open file on target
    - "PS here" button to open pop-out terminal at current directory

3.6 Download & Execute
  - Download from URL with User-Agent spoofing
  - Run silently or with terminal output capture
  - Random UUID-named file in TEMP, auto-deleted after terminal output

3.7 Run Local Program
  - Execute a file already present on the target
  - Optional terminal output capture

3.8 Sound Inject
  - Upload WAV/audio file
  - Sent chunked via base64 over WebSocket
  - Played on target speaker (supports WAV and raw PCM)

3.9 Keylogger Modal
  - Start/stop remote key capture
  - Fetch accumulated keystroke buffer

3.10 Credential Vault
    - Harvest passwords from Chrome, Edge, Brave browsers
    - Harvest cookies from Chrome, Edge

3.11 URL Injector
    - Force target browser to open a URL

3.12 Kill Windows Defender
    - One-click multi-layer Defender disable (confirms first)

3.13 Lock Device
    - Request PIN-lock with password (user provides)

3.14 File Encryption / Decryption
    - Crypt the target (encrypt files on all/specified drives)
    - Decrypt .locked files with matching password

3.15 Audio Stream (Admin-to-Agent)
    - Browser mic captured via MediaStream
    - Sent as real-time PCM chunks over WebSocket
    - Played on target speakers


================================================================================
4. AGENT FEATURES (client.py)
================================================================================

--------------------------------------------------------------------------------
4.1 PERSISTENCE & IMMORTALITY ENGINE
--------------------------------------------------------------------------------

All features in this section are activated by `enhanced_install_persistence()`
(called at startup) which calls `_immortality_init()`.

4.1.1 Registry Persistence  (_registry_persistence)
  - HKCU\Software\Microsoft\Windows\CurrentVersion\Run  "WinSvcUpdate"
  - HKLM\...\Run  (if admin) multiple keys
  - RunOnce for recovery after failed termination
  - Userinit fallback (HKLM\...\Userinit)

4.1.2 Windows Service  (_install_service)
  - Creates "WinSvcUpdate" service: auto-start, LocalSystem
  - 3 recovery actions: restart service, restart computer (after 2 failures)
  - Hidden desktop session (no console window)

4.1.3 WMI Event Subscription  (_wmi_persistence)
  - __EventFilter: watches for __InstanceModificationEvent on System process
  - CommandLineEventConsumer: launches agent when event fires
  - Root namespace: \root\subscription
  - Completely fileless persistence

4.1.4 Scheduled Tasks  (_scheduled_tasks_persistence)
  - 4 tasks with different triggers:
    - OnLogon: runs at every user logon
    - OnStart: runs at system boot (SYSTEM context)
    - OnIdle: runs when system idle 10 minutes
    - Every 5 minutes: periodic re-execution

4.1.5 Dead Man's Switch  (_dead_mans_switch + _dead_mans_switch_updater)
  - Writes heartbeat timestamp to registry key
  - Creates scheduled task that checks heartbeat every 5 minutes
  - If heartbeat is >10 minutes stale (agent killed), task re-launches agent
  - Background thread updates heartbeat every 5 minutes

4.1.6 NTFS ADS Hiding  (_ads_hide)
  - Copies executable into alternate data stream of trusted file:
    C:\Windows\System32\calc.exe:WinSvcUpdate.exe
  - Creates scheduled task to execute the ADS copy

4.1.7 Hidden Copies  (_hidden_copies_deploy + _hidden_copies_register)
  Deploys 5 copies to:
    (1) %APPDATA%\WinSvcUpdate\WinSvcCopy0.exe
    (2) %LOCALAPPDATA%\WinSvcCopy1.exe
    (3) %TEMP%\WinSvcCopy2.exe
    (4) %USERPROFILE%\WinSvcCopy3.exe
    (5) %APPDATA%\SysCache\WinSvcCopy4.exe
  Each copy registered in HKCU\Run as "WinSvcCopy0" through "WinSvcCopy4".

4.1.8 Mutual Watchdog  (_mutual_watchdog)
  - Every 60 seconds, each running copy checks all 5 locations
  - If any copy is missing, it is immediately redeployed from an existing copy
  - Self-healing: kills any single copy that is tampered

4.1.9 Process Masquerading  (_masquerade_process)
  - Re-launches agent via wscript.exe (Microsoft-signed binary)
  - Uses COM ScriptControl to execute agent from within trusted process

4.1.10 Mutex Singleton  (_create_mutex)
    - Creates named mutex "Global\WinSvcUpdateXXX" (XXX = current PID hash)
    - Prevents multiple concurrent instances of the agent

4.1.11 Handle Revocation  (_handle_revocation_loop)
    - Background thread polls system handle table via NtQuerySystemInformation
    - Finds handles held by other processes that reference our PID
    - Duplicates and closes those handles (prevents external termination)
    - Polls every 100ms in batches with 10ms sleep between iterations

--------------------------------------------------------------------------------
4.2 EVASION & ANTIVIRUS BYPASS
--------------------------------------------------------------------------------

4.2.1 AMSI Patching  (_patch_amsi)
  - Locates AmsiScanBuffer in amsi.dll
  - Changes memory protection to RWX
  - Overwrites first 3 bytes: XOR EAX,EAX (return 0 = clean) then RET
  - This prevents all PowerShell/script scanning by AMSI

4.2.2 ETW Patching  (_patch_etw)
  - Locates EtwEventWrite in ntdll.dll
  - Patches to immediate RET (0xC3)
  - Prevents Event Tracing for Windows from logging agent activity

4.2.3 String Obfuscation  (_obf, _obf_b, _s)
  - At build time, 70+ suspicious strings are replaced with:
    _s("<base64>")
  - Runtime decodes via XOR with _OBF_KEY + shuffled XOR
  - Function name _s is randomly renamed per build (e.g. _x7)
  - Key is randomly generated per build, injected into binary

4.2.4 Windows Defender Killer  (_disable_defender)
  8-layer attack:
    Layer 0: Patch AMSI/ETW first (so PS commands are not scanned)
    Layer 1: taskkill /f on MsMpEng.exe, NisSrv.exe, SecurityHealthService.exe
    Layer 2: Add-MpPreference exclusions for TEMP, APPDATA, .exe, .dll, .ps1
    Layer 3: Set-MpPreference disable all monitoring features
    Layer 4: Registry policies (HKLM\Software\Policies\Microsoft\Windows Defender)
    Layer 5: sc stop + sc config disabled on all Defender services
    Layer 6: WMI preference removal
    Layer 7: Disable cloud protection, PUA, signature checks

4.2.5 Watchdog Process (PowerShell)
  - Fileless PowerShell script launched with -WindowStyle Hidden
  - Runs in loop: checks if agent is alive, re-launches if dead
  - Script is encoded as base64 and never touches disk

--------------------------------------------------------------------------------
4.3 SCREEN & CAMERA STREAMING
--------------------------------------------------------------------------------

4.3.1 Screen Capture & MJPEG Stream  (stream_loop)
  Uses MSS (Multiple Screen Shots) to capture the primary monitor.
  Pipeline:
    1. mss.grab() → BGRA raw bytes
    2. PIL.Image.frombytes() → convert to RGB
    3. JPEG encode with quality 15-85
    4. Prepend \x01 marker byte
    5. Send over WebSocket control channel

  Adaptive quality control:
    - Measures send-to-send elapsed time (last 10 frames)
    - If avg > 250ms, quality -= 8 (reduce bandwidth)
    - If avg < 60ms, quality += 4 (increase quality)
    - Frame interval = avg * 1.5 (clamped 20-250ms)

  Frame differencing:
    - Computes MD5 hash of raw BGRA pixels
    - If hash matches previous frame, skips encode+send entirely
    - Saves CPU and bandwidth for static screens

  HID priority throttle:
    - If HID activity detected within last 500ms, forces quality=15,
      min interval 120ms (frees bandwidth for input commands)
    - When HID idle, restores quiet quality

4.3.2 Webcam Stream  (camera_loop)
  OpenCV VideoCapture → JPEG at 28fps target.
    - Opens camera on demand, auto-closes after 6 idle ticks (~0.5s)
    - Separate async lock for thread safety
    - Sends over either the main stream channel or dedicated cam channel

4.3.3 Audio Stream  (_ensure_audio, _audio_put, _audio_try_pop)
  Two backend options (auto-detected):
    sounddevice: callback-based float32 capture → s16le PCM
    pyaudio: callback-based int16 capture (fallback)
  Target format: 44100Hz, mono, s16le
  Prefers Stereo Mix loopback device if available

  Packet format:
    \x04 + samplerate(uint16 LE) + framecount(uint16 LE) + s16le PCM

--------------------------------------------------------------------------------
4.4 AUDIO CAPTURE & INJECTION
--------------------------------------------------------------------------------

4.4.1 Target Audio Capture
  Streams microphone/loopback audio to dashboard in real-time.
  Thread-safe queue (deque, max 80 frames).
  Toggle via "audio" field in CMD_VIEW command.

4.4.2 Sound Injection  (_play_sound_inject_async)
  Plays received audio on target speakers.
  Supports:
    - WAV files (played via winsound.PlaySound)
    - Raw PCM (played via sounddevice or pyaudio with correct parameters)
  Chunked assembly: receives base64 chunks, assembles into complete file.

4.4.3 Admin-to-Agent Mic  (_feed_remote_mic_pcm, _remote_mic_worker)
  Browser captures admin's microphone, sends PCM chunks over WebSocket.
  Agent parses PCM header, writes to output stream via sounddevice or pyaudio.
  Real-time playback without intermediate files.

--------------------------------------------------------------------------------
4.5 REMOTE HID (MOUSE & KEYBOARD)
--------------------------------------------------------------------------------

All HID operations use pynput's Controller classes and are processed through
a background thread queue for low-latency, non-blocking execution.

4.5.1 Mouse Move  (do_move)
  Parameter: x, y (absolute coordinates, scaled to target resolution)
  Thread-safe queue → HID worker → pynput MouseController.position

4.5.2 Mouse Click  (do_click)
  Parameter: btn ("left"|"right"), down (true|false)
  Uses pynput Button.left/Button.right, press()/release()

4.5.3 Keyboard Key  (do_key)
  Parameter: key (string name, e.g. "a", "ctrl", "esc", "enter")
  Maps to pynput Key enum attributes; if attribute not found, uses string directly

4.5.4 HID Worker Thread  (_hid_worker)
  - Dedicated daemon thread with a queue.Queue (maxsize 128)
  - FIFO processing, suppresses exceptions silently
  - Started on first HID action, persists for agent lifetime

--------------------------------------------------------------------------------
4.6 FILE MANAGEMENT
--------------------------------------------------------------------------------

4.6.1 Directory Listing  (CMD_FILE_LS)
  Path "DRIVES" enumerates all drive letters.
  Regular paths use os.scandir with async offload.
  Returns: name, is_dir, size for each entry.

4.6.2 File Download  (CMD_FILE_DL)
  Reads file from target, base64-encodes, sends to dashboard.
  Async offloaded for non-blocking operation.

4.6.3 Run File  (CMD_FILE_RUN)
  Calls os.startfile(path) on target.
  Uses Windows' default association (no output capture).

--------------------------------------------------------------------------------
4.7 REMOTE SHELL (POWER​SHELL)
--------------------------------------------------------------------------------

4.7.1 Shell Execution  (CMD_SHELL)
  Two shell profiles:
    "side" - sidebar terminal, 120s timeout
    "pop"  - pop-out terminal, 90s timeout

  Execution flow:
    1. Receives command + optional cwd
    2. Validates cwd exists
    3. Sends immediate status "running"
    4. Executes via cmd.exe or PowerShell (auto-detects PS commands)
    5. Sends output text

4.7.2 PowerShell Execution  (_run_powershell_sync)
  Uses -EncodedCommand with base64-encoded UTF-16LE for:
    - Fast startup (no profile loading)
    - Bypass execution policy
    - No window creation
  Handles CLIXML output conversion (_strip_clixml)

4.7.3 cmd.exe Execution  (_run_shell_sync)
  Used for simple commands and non-PS environments.
  Detects cmd built-ins via _SIMPLE_CMD_PREFIXES.

--------------------------------------------------------------------------------
4.8 CREDENTIAL HARVESTING
--------------------------------------------------------------------------------

4.8.1 Browser Password Extraction  (harvest_passwords)
  Targets: Chrome, Edge, Brave
  Process:
    1. Read Local State JSON for encrypted master key
    2. Decrypt master key via DPAPI (CryptUnprotectData)
    3. Copy Login Data SQLite database to temp
    4. Query logins table: origin_url, username_value, password_value
    5. Decrypt each password (AES-GCM with iv+payload or DPAPI fallback)
    6. Return formatted results

4.8.2 Browser Cookie Extraction  (harvest_cookies)
  Same browser targets as passwords.
  Queries cookies table, returns host_key, name, encrypted_value.

4.8.3 Chromium Decryption  (_decrypt_chromium)
  Attempts AES-GCM decryption first (Chrome v80+).
  Falls back to DPAPI (legacy Chrome versions).

--------------------------------------------------------------------------------
4.9 CRYPTOGRAPHY & RANSOMWARE
--------------------------------------------------------------------------------

4.9.1 File Encryption  (do_encrypt)
  - AES-256 in CBC mode
  - Key derived via SHA-256(password)
  - Random 16-byte IV per file
  - PKCS7 padding
  - Output: .locked files (IV + ciphertext)
  - Targets: all drives by default, or comma-separated paths
  - Recursive walk through all subdirectories

4.9.2 File Decryption  (do_decrypt)
  - Matches .locked files
  - Extracts IV from first 16 bytes
  - Decrypts and removes PKCS7 padding (validates pad bytes)
  - Restores original filename (removes .locked suffix)

--------------------------------------------------------------------------------
4.10 SYSTEM LOCK
--------------------------------------------------------------------------------

4.10.1 Lock Screen  (do_lock)
  Fullscreen Tkinter overlay with:
    - Black background, red "SYSTEM LOCKED" header
    - PIN entry mask (shows asterisks)
    - Suppresses all non-digit keyboard input (backspace and enter allowed)
    - Rate limiting: 3 fails → 2s lockout, 5 fails → 10s, 10+ fails → 60s
    - On correct PIN: re-enables inputs, clears lock state, exits

4.10.2 USB / HID Disable  (_disable_external_inputs)
  PowerShell: Get-PnpDevice → Disable-PnpDevice for classes:
    USB, HIDClass, Keyboard, Mouse, Pointing

4.10.3 USB / HID Enable  (_enable_external_inputs)
  PowerShell: Get-PnpDevice → Enable-PnpDevice for same classes.
  Called on successful unlock.

4.10.4 Persistent Lock State  (set_lock_state / clear_lock_state)
  Stores password in:
    1. Registry: HKCU\Software\WinSvcUpdater\SysLckDwn (REG_SZ)
    2. File: %TEMP%\.syslck (hidden + system attributes)
  Both cleared on successful unlock.

4.10.5 Boot-time Lock Re-application  (check_lock_state)
  Called at agent startup (before everything else including persistence).
  Checks registry + file for lock password. If found, starts do_lock() and
  blocks main thread up to 10 seconds until lock screen is active.
  This prevents user activity between boot and agent activation.

--------------------------------------------------------------------------------
4.11 WORM & SELF-REPLICATION
--------------------------------------------------------------------------------

4.11.1 USB Spreader  (_usb_spreader_loop)
  - Background thread polling every 5 seconds
  - Detects new removable drives (DRIVE_REMOVABLE)
  - Copies agent executable with hidden+system attributes
  - Creates autorun.inf pointing to the hidden copy
  - Creates LNK shortcut masquerading as a folder:
    - Folder icon, name like "Documents & Settings"
    - Double-click: opens real files in hidden Documents folder, executes agent

4.11.2 Network Share Spreader  (_network_share_spreader)
  - Enumerates network shares via "net view"
  - Tests each share for writable access
  - Copies agent via UNC path
  - Creates scheduled tasks on remote machines to execute the copy
  - Continues in a loop with 60-second intervals

4.11.3 LAN Worm  (_lan_worm_scan)
  - Scans local /24 subnet for port 445 (SMB)
  - For each open SMB port:
    - Attempts ADMIN$ share copy
    - Uses PowerShell to create remote scheduled task
    - Authenticates as current user context
  - Independent thread, non-blocking

4.11.4 LNK Masquerading  (_create_lnk_on_drive)
  - Creates LNK file with:
    - Custom icon: shell32.dll folder icon
    - Custom name: "Documents & Settings" (or random folder-like)
    - Target: cmd.exe that opens real Files folder + launches agent
    - Real files hidden in \Documents\ (created if needed)

--------------------------------------------------------------------------------
4.12 REMOTE PAYLOAD EXECUTION
--------------------------------------------------------------------------------

4.12.1 Download & Execute  (CMD_DL_EXE)
  - URL as input, optional filename
  - Downloaded to %TEMP%\.{uuid}_{filename} (random prefix prevents collision)
  - User-Agent spoofed as Chrome browser
  - Two modes:
    terminal=true: Executes via PowerShell, captures stdout/stderr, returns
                   output, auto-deletes exe after completion
    terminal=false: Fire-and-forget via subprocess.Popen(shell=True)

4.12.2 Run Local Executable  (CMD_RUN_EXE)
  - Same terminal=true/false modes as download
  - No download step, runs the specified local path directly


================================================================================
5. SERVER INFRASTRUCTURE (server.py)
================================================================================

5.1 ClientSession
  Each connected agent is wrapped in a ClientSession object:
    - ws: WebSocket connection
    - info: dict (id, hostname, os, is_admin, resolution)
    - viewers: set of viewer WebSockets tracking this agent
    - cam_viewers: set of camera viewer WebSockets
    - last_seen: float timestamp
    - agent_send_lock: AsyncLock for serialized agent messages
  Methods: agent_send_bytes, agent_send_text, broadcast_bytes,
           broadcast_text, broadcast_cam_bytes, is_alive

5.2 ServerState
  - clients: dict[device_id → ClientSession]
  - Lock-protected read/write/remove
  - cleanup_stale() removes clients inactive >120s

5.3 WebSocket Endpoints
  /ws/client (agent control)
    - Receives agent registration
    - Relays binary stream data to all viewers of this agent
    - Relays text messages (shell output, status) to viewers

  /ws/client_cam (agent camera)
    - Separate channel for camera frames
    - Relays to cam_viewers only

  /ws/viewer/{device_id} (dashboard main)
    - Sends agent stream frames to the browser
    - Receives command messages and forwards to agent
    - Max 10 viewers per agent

  /ws/viewer_cam/{device_id} (dashboard camera)
    - Dedicated camera viewer channel
    - Max 5 viewers per agent
    - Enables/disables camera substream on agent

5.4 HTTP Endpoints
  GET  /           Serves the dashboard HTML SPA
  GET  /clients    JSON list of all connected agents
  GET  /stats      Detailed JSON stats (active clients, viewers, uptime)
  GET  /server_logs?lines=N  Returns last N lines of server.log
  POST /build_client  Triggers PyInstaller to build agent exe
  GET  /network_config  Returns current network configuration
  POST /network_config  Saves updated network configuration

5.5 Dashboard HTML/JS SPA
  ~4000-line embedded single-page application with all UI features listed
  in Section 3. Built with vanilla JavaScript, no frameworks.

5.6 Logging
  - File: server.log (same directory)
  - Console: stderr
  - Format: timestamp [LEVEL] message
  - Python logging module with RotatingFileHandler


================================================================================
6. BUILDER & MANAGER (remote_manager.py)
================================================================================

6.1 Configuration Management  (Manager class)
  Reads/writes manager_config.json:
    - host, ws_port, stealth, onefile, icon, version, upx, name
    - download_dir, admin, pdf_decoy, pdf_path

6.2 CLI Usage
  python remote_manager.py --build 192.168.1.100:8080
  python remote_manager.py           (interactive TUI menu)

6.3 Build Pipeline  (build_agent)
  1. Check dependencies (pyinstaller, websockets, etc.)
  2. Obfuscate client.py (string replacement)
  3. Patch SERVER_IP/SERVER_PORT into client.py
  4. Generate PyInstaller .spec file
  5. Run PyInstaller

6.4 String Obfuscation  (_obfuscate_client)
  - Replaces 70+ trigger strings with _s("<base64>") calls
  - Random function name per build (replaces _s with e.g. _x7)
  - Random XOR key per build (bytes.fromhex injected at build time)
  - Trigger categories:
    Defender products, AMSI/ETW, persistence names, browser creds,
    ransomware functions, PowerShell flags, service control, schtasks,
    WMI classes, kernel32/ntdll, lock screen strings, hidden copies,
    audio/screen capture, WebSocket endpoints, HID functions,
    frame/streaming variables, thread/process management

6.5 Server Management  (start_server, stop_server)
  - Kills any process on the configured port
  - Starts uvicorn as subprocess with auto-restart
  - Interactive IP address and port selection
  - Auto-install missing server dependencies

6.6 PDF Decoy
  - Optional: embed PDF inside the PyInstaller binary
  - loader template runs embedded PDF extraction + launch before agent
  - Creates client_loader.py as entry point when PDF decoy is enabled

6.7 Dependency Management  (_ensure_deps)
  Checks for required Python packages:
    Agent build: pyinstaller, websockets, mss, pillow, pynput, opencv-python,
                 sounddevice, pyaudio, pycryptodome
    Server: fastapi, uvicorn, websockets, jinja2
  Installs missing packages via pip with version specifications


================================================================================
7. PROTOCOL REFERENCE
================================================================================

7.1 Registration
  Agent → Server: { "cmd": 0x4B, "data": { "id": "<uuid>",
    "hostname": "<pcname>", "os": "Windows-10-...", "is_admin": true,
    "res": { "w": 1920, "h": 1080 } } }

7.2 Shell
  Dashboard → Agent: { "cmd": 0x0F, "args": { "cmd": "dir",
    "cwd": "C:\\Users", "shellId": "side" } }
  Agent → Dashboard: { "cmd": 0x0F, "data": { "out": "...",
    "shellId": "side", "status": "running" } }

7.3 File Listing
  Dashboard → Agent: { "cmd": 0x11, "args": { "path": "C:\\Windows" } }
  Agent → Dashboard: { "cmd": 0x11, "data": { "path": "C:\\Windows",
    "items": [{"name":"file.exe","is_dir":false,"size":1234}],
    "error": null } }

7.4 File Download
  Dashboard → Agent: { "cmd": 0x13, "args": { "path": "C:\\file.txt" } }
  Agent → Dashboard: { "cmd": 0x13, "data": { "name": "file.txt",
    "bytes": "<base64>" } }

7.5 Stream Binary Frames
  Agent → Server → Viewers:
    Type byte + payload:
    0x01 + JPEG data       (screen/camera frame)
    0x04 + samplerate(2B LE) + count(2B LE) + s16le PCM  (audio)
    0x03 + PCM header + s16le payload  (admin mic → agent)

7.6 View Control
  Dashboard → Agent: { "cmd": 0x15, "args": { "mode": "screen|cam",
    "audio": true|false } }
  Also: { "cmd": 0x15, "args": { "substream": "cam",
    "enabled": true|false, "audio": true|false } }

7.7 HID
  Dashboard → Agent: { "cmd": 0x50, "args": { "x": 1024, "y": 768 } }
  Dashboard → Agent: { "cmd": 0x51, "args": { "btn": "left|right",
    "down": true|false } }
  Dashboard → Agent: { "cmd": 0x52, "args": { "key": "a" } }

7.8 Payload Download
  Dashboard → Agent: { "cmd": 0x41, "args": { "url": "https://...",
    "args": "--flag", "terminal": true|false,
    "name": "payload.exe" (optional) } }

7.9 Encryption/Lock
  Dashboard → Agent: { "cmd": 0x21|0x22, "args": { "password": "...",
    "targets": "C:\\, D:\\" } }
  Dashboard → Agent: { "cmd": 0x20, "args": { "password": "1234" } }

7.10 Keylogger
  Dashboard → Agent: { "cmd": 0x0C, "args": { "action": "start|stop|fetch" } }
  Agent → Dashboard: { "cmd": 0x0C, "data": "keystroke text..." }


================================================================================
8. BUILD INSTRUCTIONS
================================================================================

8.1 Requirements
  - Python 3.8+ (tested on 3.11)
  - Windows (for PyInstaller build targeting Windows)
  - Server can run on Linux (but the agent .exe must be built on Windows)

8.2 Quick Start (Server)
  python remote_manager.py
  → Select option [4] Start Server
  → Choose network interface and port

8.3 Quick Start (Build Agent)
  python remote_manager.py --build 192.168.1.100:80
  OR interactively:
  python remote_manager.py
  → [1] Edit Configuration → Set host/port, toggle stealth/onefile/admin
  → [2] Build Agent
  → [3] Build Agent with PDF Decoy

8.4 Manual Steps
  1. python -m pip install -r requirements.txt
  2. Edit SERVER_IP/SERVER_PORT in client.py (or use the manager)
  3. pyinstaller babyrat.spec (or use the manager's build function)
  4. The output exe will be in dist/babyrat.exe
  5. Deploy the exe to target and run (admin recommended for full features)

8.5 Obfuscation Levels
  Stealth mode (enabled by default):
    - All trigger strings replaced with runtime-decoded equivalents
    - Random XOR key per build
    - Random function name per build
  Without stealth:
    - Strings remain in plain text
    - Higher detection rate, useful for testing only


================================================================================
9. CONFIGURATION
================================================================================

9.1 manager_config.json
  {
    "host": "0.0.0.0",
    "ws_port": 80,
    "stealth": true,
    "onefile": true,
    "icon": "",
    "version": false,
    "upx": false,
    "name": "babyrat",
    "download_dir": "dist",
    "admin": true,
    "pdf_decoy": false,
    "pdf_path": "decoy.pdf"
  }

9.2 Environment Variables
  SERVER_IP (default: 54.174.116.107)
  SERVER_PORT or PORT (default: 80)

9.3 Build-time Variables (injected by manager)
  _OBF_KEY: 16 random bytes for string XOR obfuscation
  _s function name: random 2-3 character name (e.g. _x7)
  SERVER_IP, SERVER_PORT: target C2 address


================================================================================
10. TRIGGER STRINGS & OBFUSCATION
================================================================================

The following string categories are automatically obfuscated at build time:

  CATEGORY                    EXAMPLES
  ─────────────────────────── ──────────────────────────────────────────
  Defender products           WinDefend, Sense, WdBoot, WdFilter, etc.
  AMSI/ETW patching           AmsiScanBuffer, EtwEventWrite, VirtualProtect
  Persistence names           WinSvcUpdate, Global\WinSvcUpdate, SysLckDwn
  Browser credential paths    Login Data, Local State, os_crypt
  Ransomware functions        do_encrypt, do_decrypt, harvest_passwords
  PowerShell flags            -NoProfile, -ExecutionPolicy Bypass, -Enc
  Service control             sc create, sc config, sc stop
  Scheduled tasks             schtasks /create, /sc onlogon
  WMI classes                 __EventFilter, __FilterToConsumerBinding
  Process/memory APIs         kernel32, ntdll, CreateMutexW
  Lock screen strings         SYSTEM LOCKED, ENTER PIN, fullscreen
  Hidden copies/advertising   WinSvcCopy, SysCache, attrib +h, .syslck
  Audio/video capture         Stereo Mix, DirectShow, sounddevice
  WebSocket paths             websockets, /ws/client, /ws/client_cam
  HID classes                 do_move, do_click, do_key, MouseController
  Streaming variables         frame_interval, quality_adj, stream_loop
  Thread/process              ThreadPoolExecutor, set_event_loop
  Network indicators          ws://, wss://, WebSocket


================================================================================
END OF DOCUMENT
================================================================================
