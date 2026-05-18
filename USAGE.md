# C2 Premium "Total Control" — Usage Guide

Comprehensive guide for all features including immortality, worm propagation, and server ops.

---

## 1. Launch the Control Hub

```bash
python server.py
```
Open `http://127.0.0.1:8080/` in a browser. Set `HOST` / `PORT` via env or `network_config.json`.

For the launcher GUI: `python dashboard_host.py` or `python server.py --gui`

Server endpoints:
- `/` — Main dashboard
- `/clients` — Connected agents JSON
- `/stats` — Server statistics
- `/server_logs` — Log tail

---

## 2. Generate Agent Stub

The agent (`client.py`) can be run directly with Python or compiled:

**Direct (Python):**
```bash
python client.py
```

**Via builder endpoint:** POST `/build_client` (requires PyInstaller in PATH)

---

## 3. Dashboard Controls

### 3.1 Remote Desktop & HID
| Control | Action |
|---------|--------|
| **Desktop / Webcam** | Toggle via pill buttons top-right |
| **Mouse** | Click/drag on the canvas |
| **Keyboard** | Type while canvas is focused |
| **Audio** | Toggle audio streaming |
| **Mic** | Broadcast microphone to agent |

### 3.2 Credential Harvesting
| Button | Function |
|--------|----------|
| **Browser Passwords** | Extracts Chrome/Edge/Brave/Firefox saved logins |
| **Session Cookies** | Grabs active session cookies for session hijack |

### 3.3 File Operations
- **File Explorer** — Browse remote filesystem, download/run files
- **PS here** — Open PowerShell terminal in the selected folder
- **Silent Downloader** — Download + execute remote files on target

### 3.4 System Control
| Feature | Description |
|---------|-------------|
| **Lock Device** | Fullscreen lock overlay with PIN |
| **Vault Encryption** | AES-256 encrypt user files (password-based) |
| **Vault Decryption** | Recover encrypted files |
| **URL Injector** | Open URL in target's default browser |
| **Inject Sound** | Play audio on target machine |

### 3.5 PowerShell Terminal
Two terminals available:
- **Side terminal** (right panel) — Quick commands
- **Folder terminal** (via "PS here" in Explorer) — Commands run in selected directory

Commands execute via `cmd.exe` for simple operations (instant) and PowerShell for complex ones. A `⏳ running...` indicator shows during execution.

---

## 4. Immortality Features (Agent-Side)

All activate automatically at startup via `_immortality_init()`.

| # | Feature | What It Does | Kill Difficulty |
|---|---------|--------------|----------------|
| — | **AMSI Patch** | Patches `AmsiScanBuffer` to bypass PowerShell/Macro detection | — |
| — | **ETW Patch** | Patches `EtwEventWrite` to block telemetry to EDR/SIEM | — |
| 1 | **Watchdog Process** | PowerShell process monitors agent PID; respawns if killed | ⭐⭐⭐⭐⭐ |
| 2 | **Registry Persistence** | 6 autostart entries: HKCU/HKLM Run, RunOnce, Userinit | ⭐⭐⭐⭐ |
| 3 | **Windows Service** | Installed as service with 3-stage OS-managed recovery (30s/60s/90s) | ⭐⭐⭐⭐ |
| 4 | **WMI Subscription** | Fileless persistence via WMI event filter + consumer | ⭐⭐⭐⭐⭐ |
| 5 | **Scheduled Tasks** | 4 overlapping tasks: logon, every 5min, startup, idle | ⭐⭐⭐⭐ |
| 6 | **Masquerade** | Launches via `wscript.exe` (trusted Windows binary) | ⭐⭐⭐⭐⭐ |
| 7 | **Handle Revocation** | Background thread closes foreign handles to agent process | ⭐⭐⭐⭐⭐ |
| 8 | **Dead Man's Switch** | Registry heartbeat updated every 5min; 10min check task respawns if stale | ⭐⭐⭐⭐⭐ |
| 9 | **ADS Hiding** | Executable hidden in NTFS stream of calc.exe/notepad.exe/explorer.exe | ⭐⭐⭐⭐ |
| 10 | **Mutex** | Named mutex prevents duplicate instances | ⭐⭐⭐ |

---

## 5. Worm / Self-Replication

Three background threads activate from `_spreader_init()`.

### 5.1 USB Spread
- Polls `GetLogicalDrives()` every 5 seconds
- On detecting a `DRIVE_REMOVABLE`, copies executable + creates:
  - `autorun.inf` for legacy auto-run
  - `ReadMe.lnk` shortcut (masquerades as folder)
  - `Documents\` hidden folder — existing files moved inside
- Files marked HIDDEN | SYSTEM

### 5.2 Network Share Spread
- Runs `net view /all` periodically (every 5min)
- Parses `\\SERVER\Share` entries
- Copies exe via PowerShell `Copy-Item`
- Creates remote scheduled tasks via `schtasks /s`

### 5.3 LAN Worm (SMB)
- Scans local subnet `*.0/24` for port 445 (0.5s timeout)
- For each open SMB host: copies to `\\IP\ADMIN$`
- Installs remote scheduled tasks for logon + periodic execution

---

## 6. Server Stability Features

| Feature | Detail |
|---------|--------|
| Concurrent broadcasting | Agent→viewer sends run in parallel (slow viewers don't block others) |
| Stale cleanup | Removes clients with no data for 120s (every 30s) |
| Connection limits | Max 10 viewers, 5 cam viewers per agent |
| Thread-safe state | Async lock on client operations |
| Health stats | `/stats` endpoint shows active clients, viewers, uptime |

---

## 7. Performance Optimizations

| Area | Improvement |
|------|-------------|
| **Shell commands** | Simple commands (dir, cd, echo) run via `cmd.exe` (~10ms startup); complex ones via PowerShell `-EncodedCommand` |
| **Stream vs Control** | Separate locks for streaming data and control messages — shell output isn't blocked by screen capture |
| **Timeout** | Folder terminal: 90s, Side terminal: 120s |
| **Terminal UI** | Line-capped at 500 entries, running indicator, auto-scroll |

---

## 8. Configuration

**`network_config.json:**
```json
{
  "server_port": 8080,
  "client_target_host": "127.0.0.1",
  "client_target_port": 8080,
  "server_bind_host": "127.0.0.1"
}
```

Environment variables: `SERVER_IP`, `SERVER_PORT`, `HOST`, `PORT`

---

> [!IMPORTANT]
> This framework is designed for authorized security research and professional administrative use only. Ensure you have explicit permission before deploying agents.
