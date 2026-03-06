
# engine.py
# Network engine running in a background asyncio loop thread.
# Supports TCP(client/server), UDP(bind+send), Redis(pub/sub, optional), Serial(optional).
from __future__ import annotations

import asyncio
import binascii
import queue
import random
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

# ---------- Queue / Buffer Limits ----------
# Per-connection send queue limit (TCP/UDP/Redis/Serial sender queues).
CONN_SEND_Q_MAX = 500

# Receive buffer guard to prevent unbounded memory growth.
RX_BUF_MAX = 4 * 1024 * 1024     # 4MB
RX_BUF_KEEP = 16 * 1024          # keep tail bytes after trimming

# Max bytes shown per frame in log preview (HEX/UTF8).
LOG_FRAME_PREVIEW_MAX = 512  # bytes

# Minimum interval for stats push to UI.
STATS_PUSH_MIN_INTERVAL_SEC = 0.2


# ---------- Config Models ----------
@dataclass
class KeepAliveCfg:
    enabled: bool = False
    idle_sec: int = 60
    interval_sec: int = 10
    count: int = 5  # mainly used on Unix-like systems


@dataclass
class FrameCfg:
    mode: str = "delimiter"  # "delimiter" | "fixed"
    # delimiter
    delim_kind: str = "LF"   # "LF" | "CRLF" | "CUSTOMHEX"
    custom_hex: str = "0D0A"  # example: "0D0A"
    append_delim_on_send: bool = True
    # fixed
    fixed_len: int = 16
    send_policy: str = "strict"  # "strict" | "pad" | "truncate"
    pad_byte_hex: str = "00"
    # display (show format can be configured separately for RX/TX)
    rx_log_view: str = "hex"        # "hex" | "utf8" | "hex_utf8"
    tx_log_view: str = "hex_utf8"   # "hex" | "utf8" | "hex_utf8"
    show_utf8: bool = False          # legacy fallback for old settings

@dataclass
class JobCfg:
    enabled: bool = False
    every_sec: float = 60.0
    payload_is_hex: bool = False
    payload: str = ""


@dataclass
class TcpCfg:
    role: str = "client"  # "client" | "server"
    host: str = "127.0.0.1"
    port: int = 7000
    connect_timeout_sec: float = 5.0
    max_clients: int = 50

    # Client auto reconnect (backoff)
    auto_reconnect: bool = False
    backoff_min_sec: float = 1.0
    backoff_max_sec: float = 30.0
    max_retry: int = 0  # 0 means unlimited

    keepalive: KeepAliveCfg = field(default_factory=KeepAliveCfg)

    # server send scope
    server_scope: str = "all"  # "all" | "selected"
    server_selected: Set[str] = field(default_factory=set)


@dataclass
class UdpCfg:
    bind_host: str = "0.0.0.0"
    bind_port: int = 7001
    target_host: str = "127.0.0.1"
    target_port: int = 7001
    allow_broadcast: bool = False
    # UDP is connectionless; no client/server role, only bind/send endpoints.


@dataclass
class RedisCfg:
    enabled: bool = False  # if dependency is missing, UI should keep this off
    host: str = "127.0.0.1"
    port: int = 6379
    db: int = 0
    password: str = ""
    pub_channel: str = "tcp_test_pub"
    sub_channels: List[str] = field(default_factory=lambda: ["tcp_test_sub"])


@dataclass
class SerialCfg:
    enabled: bool = False  # if dependency is missing, UI should keep this off
    port: str = "COM3"
    baudrate: int = 115200
    timeout_sec: float = 0.2  # read timeout
    write_timeout_sec: float = 1.0


@dataclass
class AppCfg:
    transport: str = "tcp"  # "tcp" | "udp" | "redis" | "serial"

    tcp: TcpCfg = field(default_factory=TcpCfg)
    udp: UdpCfg = field(default_factory=UdpCfg)
    redis: RedisCfg = field(default_factory=RedisCfg)
    serial: SerialCfg = field(default_factory=SerialCfg)

    frame: FrameCfg = field(default_factory=FrameCfg)

    sendTimer_1: JobCfg = field(default_factory=JobCfg)
    sendTimer_2: JobCfg = field(default_factory=JobCfg)
    sendTimer_3: JobCfg = field(default_factory=JobCfg)
    heartbeat: JobCfg = field(default_factory=JobCfg)


@dataclass
class RuntimeStats:
    transport: str = "-"
    role: str = "-"
    state: str = "idle"         # idle/connecting/connected/reconnecting/disconnected/listening
    peer: str = ""              # client: connected peer, server: last peer, udp: bind addr, etc.
    clients: int = 0

    rx_bytes: int = 0
    tx_bytes: int = 0
    rx_frames: int = 0
    tx_frames: int = 0
    last_rx_ts: float = 0.0
    last_tx_ts: float = 0.0

    reconnecting: bool = False
    retry: int = 0
    next_retry_in: float = 0.0


# ---------- Optional deps ----------
try:
    import redis.asyncio as aioredis  # type: ignore
except Exception:  # pragma: no cover
    aioredis = None  # type: ignore

try:
    import serial  # type: ignore
except Exception:  # pragma: no cover
    serial = None  # type: ignore


# ---------- Utilities ----------
def now_ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def safe_put_ui(ui_q: "queue.Queue", item):
    try:
        ui_q.put_nowait(item)
    except queue.Full:
        pass


def normalize_hex_string(s: str) -> str:
    s = (s or "").strip().replace(" ", "").replace(",", "")
    s = s.replace("0x", "").replace("0X", "")
    return s


def parse_hex_bytes(s: str) -> bytes:
    h = normalize_hex_string(s)
    if not h:
        return b""
    if len(h) % 2 == 1:
        raise ValueError("HEX string must have an even number of digits (2 chars per byte).")
    return bytes.fromhex(h)


def get_delimiter_bytes(frame: FrameCfg) -> bytes:
    if frame.delim_kind == "LF":
        return b"\n"
    if frame.delim_kind == "CRLF":
        return b"\r\n"
    if frame.delim_kind == "CUSTOMHEX":
        return parse_hex_bytes(frame.custom_hex)
    return b"\n"


def apply_fixed_send_policy(data: bytes, frame: FrameCfg, pad_byte: int) -> Optional[bytes]:
    n = int(frame.fixed_len)
    if n <= 0:
        return data

    if len(data) == n:
        return data

    policy = frame.send_policy
    if policy == "strict":
        return None
    if policy == "truncate":
        return data[:n]
    if policy == "pad":
        if len(data) > n:
            return data[:n]
        return data + bytes([pad_byte]) * (n - len(data))
    return None


def split_by_delim(buf: bytearray, delim: bytes) -> List[bytes]:
    out: List[bytes] = []
    if not delim:
        return out
    while True:
        idx = buf.find(delim)
        if idx == -1:
            break
        out.append(bytes(buf[:idx]))
        del buf[:idx + len(delim)]
    return out


def split_fixed(buf: bytearray, n: int) -> List[bytes]:
    out: List[bytes] = []
    if n <= 0:
        return out
    while len(buf) >= n:
        out.append(bytes(buf[:n]))
        del buf[:n]
    return out


def guard_rx_buffer(buf: bytearray) -> bool:
    """Trim receive buffer when it grows past RX_BUF_MAX. Return True if trimmed."""
    if len(buf) <= RX_BUF_MAX:
        return False
    keep = buf[-RX_BUF_KEEP:] if RX_BUF_KEEP > 0 else bytearray()
    buf.clear()
    buf.extend(keep)
    return True


# ---------- TCP Connection Context ----------
class TcpServerConn:
    def __init__(self, peer: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.peer = peer
        self.reader = reader
        self.writer = writer
        self.buf = bytearray()

        self.send_q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=CONN_SEND_Q_MAX)
        self.sender_task: Optional[asyncio.Task] = None
        self.reader_task: Optional[asyncio.Task] = None

    async def sender_loop(self):
        try:
            while True:
                data = await self.send_q.get()
                self.writer.write(data)
                await self.writer.drain()
        except asyncio.CancelledError:
            pass
        except Exception:
            pass


# ---------- UDP protocol ----------
class _UdpProtocol(asyncio.DatagramProtocol):
    def __init__(self, engine: "NetEngine"):
        self.engine = engine

    def datagram_received(self, data: bytes, addr):
        peer = f"{addr[0]}:{addr[1]}"
        self.engine._on_message_bytes(peer, data)

    def error_received(self, exc):
        self.engine.log(f"[udp] error_received: {exc}")

    def connection_lost(self, exc):
        # UDP close can arrive with or without an exception.
        if exc:
            self.engine.log(f"[udp] connection_lost: {exc}")


# ---------- Network Engine (runs on asyncio background thread) ----------
class NetEngine:
    def __init__(self, ui_q: "queue.Queue"):
        self.ui_q = ui_q

        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_ready = threading.Event()
        self.thread: Optional[threading.Thread] = None

        self.cfg = AppCfg()

        # frame runtime cache
        self._delim_bytes: bytes = b"\n"
        self._pad_byte: int = 0x00

        # stats
        self.stats = RuntimeStats()
        self._last_stats_push_mono = 0.0
        self._stats_push_scheduled = False

        # jobs
        self.job_tasks: List[asyncio.Task] = []
        self._job_paused: Dict[str, bool] = {}

        # TCP client runtime
        self.tcp_client_reader: Optional[asyncio.StreamReader] = None
        self.tcp_client_writer: Optional[asyncio.StreamWriter] = None
        self.tcp_client_buf = bytearray()
        self.tcp_client_send_q: Optional[asyncio.Queue[bytes]] = None
        self.tcp_client_conn_tasks: List[asyncio.Task] = []
        self.tcp_client_supervisor_task: Optional[asyncio.Task] = None

        # TCP server runtime
        self.tcp_server: Optional[asyncio.AbstractServer] = None
        self.tcp_server_conns: Dict[str, TcpServerConn] = {}

        # UDP runtime
        self.udp_transport: Optional[asyncio.DatagramTransport] = None
        self.udp_protocol: Optional[_UdpProtocol] = None
        self.udp_send_q: Optional[asyncio.Queue[bytes]] = None
        self.udp_tasks: List[asyncio.Task] = []

        # Redis runtime
        self.redis_client = None
        self.redis_pubsub = None
        self.redis_send_q: Optional[asyncio.Queue[bytes]] = None
        self.redis_tasks: List[asyncio.Task] = []

        # Serial runtime
        self.serial_obj = None
        self.serial_buf = bytearray()
        self.serial_send_q: Optional[asyncio.Queue[bytes]] = None
        self.serial_tasks: List[asyncio.Task] = []

    # ----- Thread / Event loop lifecycle -----
    def start_thread(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._thread_main, daemon=True)
        self.thread.start()
        self._loop_ready.wait(timeout=3.0)

    def _thread_main(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self._loop_ready.set()
        self.loop.run_forever()

    def call(self, coro):
        if not self.loop:
            return None
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def shutdown(self, timeout_sec: float = 2.0):
        if not self.loop:
            return
        try:
            fut = self.call(self.stop_all())
            if fut:
                fut.result(timeout=timeout_sec)
        except Exception:
            pass
        try:
            self.loop.call_soon_threadsafe(self.loop.stop)
        except Exception:
            pass

    # ----- UI event push / stats publish -----
    def log(self, msg: str):
        safe_put_ui(self.ui_q, ("log", msg))

    def push_tcp_clients(self):
        peers = sorted(self.tcp_server_conns.keys())
        safe_put_ui(self.ui_q, ("clients", peers))

    def _stats_snapshot(self) -> Dict[str, Any]:
        s = self.stats
        return {
            "transport": s.transport,
            "role": s.role,
            "state": s.state,
            "peer": s.peer,
            "clients": s.clients,
            "rx_bytes": s.rx_bytes,
            "tx_bytes": s.tx_bytes,
            "rx_frames": s.rx_frames,
            "tx_frames": s.tx_frames,
            "last_rx_ts": s.last_rx_ts,
            "last_tx_ts": s.last_tx_ts,
            "reconnecting": s.reconnecting,
            "retry": s.retry,
            "next_retry_in": s.next_retry_in,
        }

    def _push_stats(self, force: bool = False):
        now_m = time.monotonic()
        if (not force) and (now_m - self._last_stats_push_mono) < STATS_PUSH_MIN_INTERVAL_SEC:
            # Ensure short tx/rx bursts still get one eventual UI stats update.
            if self.loop and not self._stats_push_scheduled:
                remain = max(0.001, STATS_PUSH_MIN_INTERVAL_SEC - (now_m - self._last_stats_push_mono))
                self._stats_push_scheduled = True
                self.loop.call_later(remain, self._push_stats_deferred)
            return
        self._stats_push_scheduled = False
        self._last_stats_push_mono = now_m
        safe_put_ui(self.ui_q, ("stats", self._stats_snapshot()))

    def _push_stats_deferred(self):
        self._stats_push_scheduled = False
        self._push_stats(force=True)

    def _reset_counters(self):
        self.stats.rx_bytes = 0
        self.stats.tx_bytes = 0
        self.stats.rx_frames = 0
        self.stats.tx_frames = 0
        self.stats.last_rx_ts = 0.0
        self.stats.last_tx_ts = 0.0

    def _set_state(
        self,
        *,
        transport: Optional[str] = None,
        role: Optional[str] = None,
        state: Optional[str] = None,
        peer: Optional[str] = None,
        clients: Optional[int] = None,
        reconnecting: Optional[bool] = None,
        retry: Optional[int] = None,
        next_retry_in: Optional[float] = None,
        force: bool = True,
    ):
        if transport is not None:
            self.stats.transport = transport
        if role is not None:
            self.stats.role = role
        if state is not None:
            self.stats.state = state
        if peer is not None:
            self.stats.peer = peer
        if clients is not None:
            self.stats.clients = clients
        if reconnecting is not None:
            self.stats.reconnecting = reconnecting
        if retry is not None:
            self.stats.retry = retry
        if next_retry_in is not None:
            self.stats.next_retry_in = next_retry_in
        self._push_stats(force=force)

    def _mark_rx(self, nbytes: int, nframes: int = 0, peer: Optional[str] = None):
        self.stats.rx_bytes += int(nbytes)
        if nframes:
            self.stats.rx_frames += int(nframes)
        self.stats.last_rx_ts = time.time()
        if peer:
            self.stats.peer = peer
        self._push_stats(force=False)

    def _mark_tx(self, nbytes: int, nframes: int = 0, peer: Optional[str] = None):
        self.stats.tx_bytes += int(nbytes)
        if nframes:
            self.stats.tx_frames += int(nframes)
        self.stats.last_tx_ts = time.time()
        if peer:
            self.stats.peer = peer
        self._push_stats(force=False)

    # ----- Keepalive setup (TCP only) -----
    def _apply_keepalive_to_socket(self, sock_obj: socket.socket):
        ka = self.cfg.tcp.keepalive
        if not ka.enabled:
            return

        try:
            sock_obj.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except Exception as e:
            self.log(f"[keepalive] failed to set SO_KEEPALIVE: {e}")
            return

        # Windows: SIO_KEEPALIVE_VALS uses milliseconds for idle/interval.
        if hasattr(sock_obj, "ioctl") and hasattr(socket, "SIO_KEEPALIVE_VALS"):
            try:
                sock_obj.ioctl(
                    socket.SIO_KEEPALIVE_VALS,
                    (1, int(ka.idle_sec * 1000), int(ka.interval_sec * 1000)),
                )
                self.log("[keepalive] Windows SIO_KEEPALIVE_VALS configured")
            except Exception as e:
                self.log(f"[keepalive] Windows ioctl failed: {e}")
            return

        # Unix-like socket options
        for opt_name, val in [
            ("TCP_KEEPIDLE", ka.idle_sec),
            ("TCP_KEEPINTVL", ka.interval_sec),
            ("TCP_KEEPCNT", ka.count),
        ]:
            opt = getattr(socket, opt_name, None)
            if opt is None:
                continue
            try:
                sock_obj.setsockopt(socket.IPPROTO_TCP, opt, int(val))
            except Exception as e:
                self.log(f"[keepalive] failed to set {opt_name}: {e}")

    def _apply_keepalive_to_writer(self, writer: asyncio.StreamWriter):
        sock_obj = writer.get_extra_info("socket")
        if isinstance(sock_obj, socket.socket):
            self._apply_keepalive_to_socket(sock_obj)

    # ----- frame runtime compile -----
    def _compile_frame_runtime(self):
        f = self.cfg.frame

        if f.mode == "delimiter":
            try:
                d = get_delimiter_bytes(f)
                self._delim_bytes = d if d else b"\n"
                if not d:
                    self.log("[cfg] delimiter is empty; fallback to LF")
            except Exception as e:
                self._delim_bytes = b"\n"
                self.log(f"[cfg] delimiter parse failed ({e}); fallback to LF")
        else:
            self._delim_bytes = b""

        try:
            pb = parse_hex_bytes(f.pad_byte_hex or "00")
            self._pad_byte = pb[0] if pb else 0x00
        except Exception as e:
            self._pad_byte = 0x00
            self.log(f"[cfg] pad byte parse failed ({e}); fallback to 00")

    def _frames_from_buffer(self, conn_buf: bytearray) -> List[bytes]:
        f = self.cfg.frame
        if f.mode == "fixed":
            n = int(f.fixed_len)
            if n <= 0:
                return []
            return split_fixed(conn_buf, n)
        return split_by_delim(conn_buf, self._delim_bytes)
    def _normalize_log_view(self, mode: str, *, legacy_show_utf8: bool = False) -> str:
        m = (mode or "").strip().lower().replace(" ", "").replace("+", "_").replace("-", "_")
        aliases = {
            "hexutf8": "hex_utf8",
            "hex_utf8": "hex_utf8",
            "both": "hex_utf8",
            "utf": "utf8",
            "utf_8": "utf8",
            "utf8": "utf8",
            "hex": "hex",
        }
        normalized = aliases.get(m, "")
        if normalized in {"hex", "utf8", "hex_utf8"}:
            return normalized
        return "hex_utf8" if legacy_show_utf8 else "hex"

    def _format_payload_for_log(self, direction: str, peer: str, payload: bytes, view: str) -> str:
        total_len = len(payload)
        preview = payload[:LOG_FRAME_PREVIEW_MAX]

        hx = binascii.hexlify(preview).decode()
        if total_len > LOG_FRAME_PREVIEW_MAX:
            hx += f"...(+{total_len - LOG_FRAME_PREVIEW_MAX}B)"

        txt = preview.decode("utf-8", errors="replace")
        if total_len > LOG_FRAME_PREVIEW_MAX:
            txt += f"...(+{total_len - LOG_FRAME_PREVIEW_MAX}B)"

        if view == "utf8":
            return f"[{direction}] {peer} LEN={total_len} UTF8='{txt}'"
        if view == "hex_utf8":
            return f"[{direction}] {peer} LEN={total_len} HEX={hx} | UTF8='{txt}'"
        return f"[{direction}] {peer} LEN={total_len} HEX={hx}"

    def _tx_log_peer(self) -> str:
        t = self.cfg.transport
        if t == "tcp":
            if self.cfg.tcp.role == "client":
                return str(self.stats.peer or f"{self.cfg.tcp.host}:{self.cfg.tcp.port}")
            return "tcp:server"
        if t == "udp":
            return f"{self.cfg.udp.target_host}:{self.cfg.udp.target_port}"
        if t == "redis":
            return f"redis:{self.cfg.redis.pub_channel}"
        if t == "serial":
            return f"serial:{self.cfg.serial.port}"
        return t or "-"

    def _format_tx_for_log(self, payload: bytes) -> str:
        view = self._normalize_log_view(
            getattr(self.cfg.frame, "tx_log_view", ""),
            legacy_show_utf8=bool(getattr(self.cfg.frame, "show_utf8", False)),
        )
        return self._format_payload_for_log("tx", self._tx_log_peer(), payload, view)

    def _format_frame_for_log(self, peer: str, frame: bytes) -> str:
        view = self._normalize_log_view(
            getattr(self.cfg.frame, "rx_log_view", ""),
            legacy_show_utf8=bool(getattr(self.cfg.frame, "show_utf8", False)),
        )
        return self._format_payload_for_log("rx", peer, frame, view)

    # ----- Payload build -----
    def _build_payload_bytes(self, payload: str, is_hex: bool) -> bytes:
        payload = payload or ""
        if is_hex:
            return parse_hex_bytes(payload)
        return payload.encode("utf-8")

    def _finalize_send_bytes(self, data: bytes) -> Optional[bytes]:
        f = self.cfg.frame
        if f.mode == "fixed":
            return apply_fixed_send_policy(data, f, self._pad_byte)
        if f.append_delim_on_send:
            return data + self._delim_bytes
        return data

    # ----- Jobs: check whether current transport can send now -----
    def _job_send_ready(self) -> Tuple[bool, str]:
        t = self.cfg.transport
        if t == "tcp":
            if self.cfg.tcp.role == "client":
                if self.stats.state != "connected" or not self.tcp_client_send_q:
                    return False, "tcp client not connected"
                return True, "tcp client connected"

            # tcp server
            targets = list(self.tcp_server_conns.keys())
            if self.cfg.tcp.server_scope == "selected":
                targets = [p for p in targets if p in (self.cfg.tcp.server_selected or set())]
            if not targets:
                return False, "no tcp server targets"
            return True, f"tcp server targets={len(targets)}"

        if t == "udp":
            if not self.udp_send_q or not self.udp_transport:
                return False, "udp not started"
            return True, "udp started"

        if t == "redis":
            if not self.redis_client:
                return False, "redis not connected"
            return True, "redis connected"

        if t == "serial":
            if not self.serial_obj:
                return False, "serial not opened"
            return True, "serial opened"

        return False, "no active transport"

    # ----- Manual send -----
    async def send_manual(self, payload: str, is_hex: bool):
        try:
            raw = self._build_payload_bytes(payload, is_hex)
        except Exception as e:
            self.log(f"[tx] payload parse failed: {e}")
            return
        out = self._finalize_send_bytes(raw)
        if out is None:
            self.log("[tx] strict policy dropped send (fixed length mismatch)")
            return
        ok, detail = await self._send_bytes(out)
        if ok:
            self.log(self._format_tx_for_log(out))
            self.log(f"[tx] sent ({detail})")
        else:
            self.log(f"[tx] failed ({detail})")

    async def apply_cfg_and_send(self, cfg: AppCfg, payload: str, is_hex: bool):
        await self.update_cfg(cfg)
        await self.send_manual(payload, is_hex)

    async def update_tcp_server_targets(self, scope: str, selected: Set[str]):
        # Only target selection is updated here; no transport restart required.
        self.cfg.tcp.server_scope = scope
        self.cfg.tcp.server_selected = set(selected)
        self._push_stats(force=False)

    async def _send_bytes(self, out: bytes) -> Tuple[bool, str]:
        t = self.cfg.transport
        if t == "tcp":
            if self.cfg.tcp.role == "client":
                if not self.tcp_client_send_q:
                    return False, "tcp client not connected"
                try:
                    self.tcp_client_send_q.put_nowait(out)
                    # Count enqueue as one transmitted frame in stats.
                    self._mark_tx(len(out), nframes=1, peer=self.stats.peer or "")
                    return True, f"tcp client enqueued {len(out)}B"
                except asyncio.QueueFull:
                    return False, "tcp client send_q full(drop)"

            # tcp server
            targets = list(self.tcp_server_conns.keys())
            if self.cfg.tcp.server_scope == "selected":
                targets = [p for p in targets if p in (self.cfg.tcp.server_selected or set())]

            if not targets:
                return False, "no tcp server targets"

            sent = 0
            dropped = 0
            for peer in targets:
                c = self.tcp_server_conns.get(peer)
                if not c:
                    continue
                try:
                    c.send_q.put_nowait(out)
                    sent += 1
                except asyncio.QueueFull:
                    dropped += 1

            if sent:
                self._mark_tx(len(out) * sent, nframes=sent, peer=self.stats.peer or "")
                if dropped:
                    return True, f"tcp server enqueued={sent}, dropped={dropped} ({len(out)}B each)"
                return True, f"tcp server enqueued={sent} ({len(out)}B each)"
            return False, f"tcp server dropped all ({dropped})"

        if t == "udp":
            if not self.udp_send_q:
                return False, "udp not started"
            try:
                self.udp_send_q.put_nowait(out)
                # One UDP datagram is counted as one frame.
                self._mark_tx(len(out), nframes=1)
                return True, f"udp enqueued {len(out)}B"
            except asyncio.QueueFull:
                return False, "udp send_q full(drop)"

        if t == "redis":
            if not self.redis_send_q:
                return False, "redis not connected"
            try:
                self.redis_send_q.put_nowait(out)
                self._mark_tx(len(out), nframes=1)
                return True, f"redis enqueued {len(out)}B"
            except asyncio.QueueFull:
                return False, "redis send_q full(drop)"

        if t == "serial":
            if not self.serial_send_q:
                return False, "serial not opened"
            try:
                self.serial_send_q.put_nowait(out)
                self._mark_tx(len(out), nframes=1)
                return True, f"serial enqueued {len(out)}B"
            except asyncio.QueueFull:
                return False, "serial send_q full(drop)"

        return False, "unknown transport"

    # ----- Incoming message path (datagram-based transports) -----
    def _on_message_bytes(self, peer: str, data: bytes):
        self._mark_rx(len(data), nframes=1, peer=peer)
        # Datagram-based transports already deliver complete message chunks.
        self.log(self._format_frame_for_log(peer, data))

    # ----- Periodic job loop -----
    async def _job_loop(self, name: str, job: JobCfg):
        try:
            while True:
                await asyncio.sleep(float(job.every_sec))
                if not job.enabled:
                    continue

                ready, reason = self._job_send_ready()
                if not ready:
                    if not self._job_paused.get(name, False):
                        self._job_paused[name] = True
                        self.log(f"[job:{name}] paused ({reason})")
                    continue
                else:
                    if self._job_paused.pop(name, None):
                        self.log(f"[job:{name}] resumed")

                try:
                    raw = self._build_payload_bytes(job.payload, job.payload_is_hex)
                except Exception as e:
                    self.log(f"[job:{name}] payload parse failed: {e}")
                    continue

                out = self._finalize_send_bytes(raw)
                if out is None:
                    self.log(f"[job:{name}] strict policy dropped send (fixed length mismatch)")
                    continue
                ok, detail = await self._send_bytes(out)
                if ok:
                    self.log(self._format_tx_for_log(out))
                    self.log(f"[job:{name}] fired ({detail})")
                else:
                    self.log(f"[job:{name}] skipped ({detail})")
        except asyncio.CancelledError:
            pass

    async def _cancel_and_gather(self, tasks: List[asyncio.Task], timeout: float = 2.0):
        if not tasks:
            return
        for t in tasks:
            t.cancel()
        try:
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=timeout)
        except asyncio.TimeoutError:
            self.log("[engine] task cancel timeout (some tasks did not finish in time)")
        finally:
            tasks.clear()

    async def _start_jobs(self):
        await self._cancel_and_gather(self.job_tasks, timeout=1.0)
        self._job_paused.clear()

        for name, job in [
            ("sendTimer_1", self.cfg.sendTimer_1),
            ("sendTimer_2", self.cfg.sendTimer_2),
            ("sendTimer_3", self.cfg.sendTimer_3),
            ("heartbeat", self.cfg.heartbeat),
        ]:
            if job.enabled and float(job.every_sec) > 0:
                self.job_tasks.append(asyncio.create_task(self._job_loop(name, job)))

    # ----- Apply config while running -----
    async def update_cfg(self, new_cfg: AppCfg):
        # Apply new config immediately; runtime behavior changes without restart.
        self.cfg = new_cfg
        self._compile_frame_runtime()

        # Re-apply keepalive to active TCP sockets when enabled.
        if self.cfg.transport == "tcp" and self.cfg.tcp.keepalive.enabled:
            if self.cfg.tcp.role == "client" and self.tcp_client_writer:
                self._apply_keepalive_to_writer(self.tcp_client_writer)
            if self.cfg.tcp.role == "server":
                for c in self.tcp_server_conns.values():
                    self._apply_keepalive_to_writer(c.writer)

        await self._start_jobs()
        self.log("[engine] configuration updated (jobs restarted, keepalive reapplied)")
        self._push_stats(force=True)

    # ----- STOP (close active transports) -----
    async def stop_all(self):
        # jobs
        await self._cancel_and_gather(self.job_tasks, timeout=1.0)
        self._job_paused.clear()

        # --- TCP client ---
        await self._cancel_and_gather(self.tcp_client_conn_tasks, timeout=1.5)
        if self.tcp_client_supervisor_task:
            self.tcp_client_supervisor_task.cancel()
            try:
                await asyncio.gather(self.tcp_client_supervisor_task, return_exceptions=True)
            except Exception:
                pass
            self.tcp_client_supervisor_task = None

        self.tcp_client_send_q = None
        if self.tcp_client_writer:
            try:
                self.tcp_client_writer.close()
                await self.tcp_client_writer.wait_closed()
            except Exception:
                pass
        self.tcp_client_reader = None
        self.tcp_client_writer = None
        self.tcp_client_buf.clear()

        # --- TCP server ---
        conn_tasks: List[asyncio.Task] = []
        for c in list(self.tcp_server_conns.values()):
            if c.sender_task:
                conn_tasks.append(c.sender_task)
            if c.reader_task:
                conn_tasks.append(c.reader_task)

        if conn_tasks:
            for t in conn_tasks:
                t.cancel()
            try:
                await asyncio.wait_for(asyncio.gather(*conn_tasks, return_exceptions=True), timeout=2.0)
            except asyncio.TimeoutError:
                self.log("[engine] tcp server conn task cancel timeout")

        for c in list(self.tcp_server_conns.values()):
            try:
                c.writer.close()
                await c.writer.wait_closed()
            except Exception:
                pass

        self.tcp_server_conns.clear()
        self.push_tcp_clients()

        if self.tcp_server:
            self.tcp_server.close()
            try:
                await self.tcp_server.wait_closed()
            except Exception:
                pass
        self.tcp_server = None

        # --- UDP ---
        await self._cancel_and_gather(self.udp_tasks, timeout=1.0)
        self.udp_send_q = None
        if self.udp_transport:
            try:
                self.udp_transport.close()
            except Exception:
                pass
        self.udp_transport = None
        self.udp_protocol = None

        # --- Redis ---
        await self._cancel_and_gather(self.redis_tasks, timeout=1.0)
        self.redis_send_q = None
        try:
            if self.redis_pubsub:
                await self.redis_pubsub.close()
        except Exception:
            pass
        self.redis_pubsub = None
        try:
            if self.redis_client:
                await self.redis_client.close()
        except Exception:
            pass
        self.redis_client = None

        # --- Serial ---
        await self._cancel_and_gather(self.serial_tasks, timeout=1.0)
        self.serial_send_q = None
        self.serial_buf.clear()
        try:
            if self.serial_obj:
                self.serial_obj.close()
        except Exception:
            pass
        self.serial_obj = None

        # stats
        self._set_state(transport="-", role="-", state="idle", peer="", clients=0, reconnecting=False, retry=0, next_retry_in=0.0, force=True)
        self.log("[engine] STOP completed")

    # ----- START (start selected transport) -----
    async def start(self, cfg: AppCfg):
        await self.stop_all()

        self.cfg = cfg
        self._compile_frame_runtime()
        self._reset_counters()

        t = self.cfg.transport
        if t == "tcp":
            if self.cfg.tcp.role == "client":
                await self._start_tcp_client()
            else:
                await self._start_tcp_server()
            return

        if t == "udp":
            await self._start_udp()
            return

        if t == "redis":
            await self._start_redis()
            return

        if t == "serial":
            await self._start_serial()
            return

        self.log(f"[engine] unknown transport: {t}")

    # ----- TCP client -----
    async def _start_tcp_client(self):
        self._set_state(transport="tcp", role="client", state="connecting", peer="", clients=0, reconnecting=False, retry=0, next_retry_in=0.0, force=True)
        await self._start_jobs()
        self.tcp_client_supervisor_task = asyncio.create_task(self._tcp_client_supervisor_loop())
        self.log("[tcp client] supervisor started")

    def _compute_backoff(self, attempt: int) -> float:
        """
        Exponential backoff policy:
        - quick initial retries (0.5s, 1s, 2s)
        - then doubling with cap (4s, 8s, 16s, ... up to configured max)
        """
        base = max(0.0, float(self.cfg.tcp.backoff_min_sec))
        cap = max(base, float(self.cfg.tcp.backoff_max_sec))
        
        # Keep very early retries fast for better first reconnect responsiveness.
        if attempt <= 1:
            delay = 0.5
        elif attempt <= 3:
            delay = 1.0 * (2 ** (attempt - 2))  # 1s, 2s
        else:
            delay = 2.0 * (2 ** (attempt - 3))  # 4s, 8s, 16s, 32s...
        
        # Apply configured cap.
        delay = min(cap, delay)
        
        # Add jitter (+/-10%) to avoid synchronized reconnect storms.
        jitter = random.uniform(0.9, 1.1)
        
        return max(0.0, delay * jitter)

    async def _tcp_client_supervisor_loop(self):
        retry = 0
        try:
            while True:
                ok = await self._tcp_client_connect_once()
                if ok:
                    retry = 0
                    # Wait for reader task to finish; disconnect is handled below.
                    reader_task = None
                    for t in self.tcp_client_conn_tasks:
                        if getattr(t, "_role", "") == "tcp_client_reader":
                            reader_task = t
                            break
                    if reader_task:
                        try:
                            await reader_task
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            pass

                # Cleanup disconnected socket/tasks.
                await self._tcp_client_cleanup_connection(set_state=False)

                if not self.cfg.tcp.auto_reconnect:
                    self._set_state(state="disconnected", reconnecting=False, next_retry_in=0.0, force=True)
                    self.log("[tcp client] disconnected (auto_reconnect=off)")
                    return

                retry += 1
                if self.cfg.tcp.max_retry and retry > int(self.cfg.tcp.max_retry):
                    self._set_state(state="disconnected", reconnecting=False, retry=retry, next_retry_in=0.0, force=True)
                    self.log(f"[tcp client] auto_reconnect stop: max_retry reached ({self.cfg.tcp.max_retry})")
                    return

                delay = self._compute_backoff(retry)
                self._set_state(state="reconnecting", reconnecting=True, retry=retry, next_retry_in=delay, force=True)
                self.log(f"[tcp client] reconnect in {delay:.1f}s (retry={retry})")
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            await self._tcp_client_cleanup_connection(set_state=False)
            raise
        except Exception as e:
            self.log(f"[tcp client] supervisor error: {e}")
            await self._tcp_client_cleanup_connection(set_state=False)
            self._set_state(state="disconnected", reconnecting=False, force=True)

    async def _tcp_client_connect_once(self) -> bool:
        self._set_state(state="connecting", reconnecting=False, next_retry_in=0.0, force=True)
        self.log(f"[tcp client] connect to {self.cfg.tcp.host}:{self.cfg.tcp.port} timeout={self.cfg.tcp.connect_timeout_sec}s")

        try:
            coro = asyncio.open_connection(self.cfg.tcp.host, int(self.cfg.tcp.port))
            r, w = await asyncio.wait_for(coro, timeout=float(self.cfg.tcp.connect_timeout_sec))
        except Exception as e:
            self.log(f"[tcp client] connect failed: {e}")
            return False

        self.tcp_client_reader, self.tcp_client_writer = r, w
        self._apply_keepalive_to_writer(w)

        peer = str(w.get_extra_info("peername"))
        self.stats.peer = peer
        self._set_state(state="connected", peer=peer, reconnecting=False, retry=0, next_retry_in=0.0, force=True)
        self.log(f"[tcp client] connected -> {peer}")

        self.tcp_client_send_q = asyncio.Queue(maxsize=CONN_SEND_Q_MAX)

        sender = asyncio.create_task(self._tcp_client_sender_loop(peer))
        setattr(sender, "_role", "tcp_client_sender")
        reader = asyncio.create_task(self._tcp_client_read_loop(peer))
        setattr(reader, "_role", "tcp_client_reader")

        self.tcp_client_conn_tasks.extend([sender, reader])
        return True

    async def _tcp_client_cleanup_connection(self, *, set_state: bool):
        await self._cancel_and_gather(self.tcp_client_conn_tasks, timeout=1.0)
        self.tcp_client_send_q = None

        if self.tcp_client_writer:
            try:
                self.tcp_client_writer.close()
                await self.tcp_client_writer.wait_closed()
            except Exception:
                pass
        self.tcp_client_reader = None
        self.tcp_client_writer = None
        self.tcp_client_buf.clear()

        if set_state:
            self._set_state(state="disconnected", peer="", reconnecting=False, force=True)

    async def _tcp_client_sender_loop(self, peer: str):
        try:
            while True:
                if not self.tcp_client_send_q or not self.tcp_client_writer:
                    return
                data = await self.tcp_client_send_q.get()
                self.tcp_client_writer.write(data)
                await self.tcp_client_writer.drain()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.log(f"[tcp client] tx error to {peer}: {e}")

    async def _tcp_client_read_loop(self, peer: str):
        buf = self.tcp_client_buf
        try:
            while True:
                if not self.tcp_client_reader:
                    return
                chunk = await self.tcp_client_reader.read(4096)
                if not chunk:
                    self.log(f"[tcp client] peer closed: {peer}")
                    return
                self._mark_rx(len(chunk), peer=peer)
                buf.extend(chunk)

                if guard_rx_buffer(buf):
                    self.log(f"[tcp client] rx buffer overflow -> trimmed (peer={peer})")

                frames = self._frames_from_buffer(buf)
                if frames:
                    self._mark_rx(0, nframes=len(frames), peer=peer)
                for fr in frames:
                    self.log(self._format_frame_for_log(peer, fr))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.log(f"[tcp client] rx error: {e}")

    # ----- TCP server -----
    async def _start_tcp_server(self):
        self._set_state(transport="tcp", role="server", state="listening", peer="", clients=0, reconnecting=False, retry=0, next_retry_in=0.0, force=True)
        self.log(f"[tcp server] listen on {self.cfg.tcp.host}:{self.cfg.tcp.port} max_clients={self.cfg.tcp.max_clients}")

        async def on_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            peer = str(writer.get_extra_info("peername"))

            if len(self.tcp_server_conns) >= int(self.cfg.tcp.max_clients):
                self.log(f"[tcp server] reject {peer}: max_clients reached")
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass
                return

            c = TcpServerConn(peer, reader, writer)
            self.tcp_server_conns[peer] = c
            self._apply_keepalive_to_writer(writer)

            c.sender_task = asyncio.create_task(c.sender_loop())
            c.reader_task = asyncio.create_task(self._tcp_server_read_loop(c))

            self.log(f"[tcp server] accepted {peer}")
            self.push_tcp_clients()
            self._set_state(peer=peer, clients=len(self.tcp_server_conns), force=True)

            try:
                await c.reader_task
            finally:
                try:
                    if c.sender_task:
                        c.sender_task.cancel()
                        await asyncio.gather(c.sender_task, return_exceptions=True)
                except Exception:
                    pass

                if peer in self.tcp_server_conns:
                    del self.tcp_server_conns[peer]

                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass

                self.log(f"[tcp server] disconnected {peer}")
                if self.cfg.tcp.server_selected and peer in self.cfg.tcp.server_selected:
                    self.cfg.tcp.server_selected.discard(peer)
                self.push_tcp_clients()
                self._set_state(clients=len(self.tcp_server_conns), force=True)

        try:
            self.tcp_server = await asyncio.start_server(on_client, host=self.cfg.tcp.host, port=int(self.cfg.tcp.port))
        except Exception as e:
            self.log(f"[tcp server] start failed: {e}")
            self._set_state(state="disconnected", force=True)
            return

        self.log("[tcp server] listening...")
        self.push_tcp_clients()
        await self._start_jobs()

    async def _tcp_server_read_loop(self, c: TcpServerConn):
        try:
            while True:
                chunk = await c.reader.read(4096)
                if not chunk:
                    return
                self._mark_rx(len(chunk), peer=c.peer)
                c.buf.extend(chunk)

                if guard_rx_buffer(c.buf):
                    self.log(f"[tcp server] rx buffer overflow -> trimmed (peer={c.peer})")

                frames = self._frames_from_buffer(c.buf)
                if frames:
                    self._mark_rx(0, nframes=len(frames), peer=c.peer)
                for fr in frames:
                    self.log(self._format_frame_for_log(c.peer, fr))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.log(f"[tcp server] rx error {c.peer}: {e}")

    # ----- UDP -----
    async def _start_udp(self):
        bind = (self.cfg.udp.bind_host, int(self.cfg.udp.bind_port))
        target = (self.cfg.udp.target_host, int(self.cfg.udp.target_port))
        self._set_state(transport="udp", role="-", state="listening", peer=f"{bind[0]}:{bind[1]} -> {target[0]}:{target[1]}", clients=0, force=True)
        self.log(f"[udp] bind {bind[0]}:{bind[1]} (target {target[0]}:{target[1]})")

        loop = asyncio.get_running_loop()
        try:
            transport, protocol = await loop.create_datagram_endpoint(
                lambda: _UdpProtocol(self),
                local_addr=bind,
                allow_broadcast=bool(self.cfg.udp.allow_broadcast),
            )
        except Exception as e:
            self.log(f"[udp] start failed: {e}")
            self._set_state(state="disconnected", force=True)
            return

        self.udp_transport = transport  # type: ignore
        self.udp_protocol = protocol  # type: ignore
        self.udp_send_q = asyncio.Queue(maxsize=CONN_SEND_Q_MAX)

        self.udp_tasks.append(asyncio.create_task(self._udp_sender_loop()))
        await self._start_jobs()

        self._set_state(state="connected", force=True)
        self.log("[udp] ready")

    async def _udp_sender_loop(self):
        try:
            while True:
                if not self.udp_send_q or not self.udp_transport:
                    return
                data = await self.udp_send_q.get()
                target = (self.cfg.udp.target_host, int(self.cfg.udp.target_port))
                self.udp_transport.sendto(data, target)  # type: ignore
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.log(f"[udp] sender error: {e}")

    # ----- Redis -----
    async def _start_redis(self):
        if aioredis is None:
            self.log("[redis] redis-py package is not installed; Redis transport is unavailable.")
            self._set_state(transport="redis", role="-", state="disconnected", force=True)
            return

        cfg = self.cfg.redis
        self._set_state(transport="redis", role="-", state="connecting", peer=f"{cfg.host}:{cfg.port}/{cfg.db}", force=True)
        self.log(f"[redis] connect {cfg.host}:{cfg.port} db={cfg.db}")

        try:
            self.redis_client = aioredis.Redis(
                host=cfg.host,
                port=int(cfg.port),
                db=int(cfg.db),
                password=cfg.password or None,
                decode_responses=False,  # bytes
            )
            # ping to verify
            await self.redis_client.ping()
        except Exception as e:
            self.log(f"[redis] connect failed: {e}")
            self.redis_client = None
            self._set_state(state="disconnected", force=True)
            return

        self.redis_send_q = asyncio.Queue(maxsize=CONN_SEND_Q_MAX)
        self.redis_tasks.append(asyncio.create_task(self._redis_sender_loop()))
        self.redis_tasks.append(asyncio.create_task(self._redis_subscriber_loop()))
        await self._start_jobs()

        self._set_state(state="connected", force=True)
        self.log("[redis] connected")

    async def _redis_sender_loop(self):
        try:
            while True:
                if not self.redis_send_q or not self.redis_client:
                    return
                data = await self.redis_send_q.get()
                ch = self.cfg.redis.pub_channel
                try:
                    await self.redis_client.publish(ch, data)
                except Exception as e:
                    self.log(f"[redis] publish error: {e}")
        except asyncio.CancelledError:
            pass

    async def _redis_subscriber_loop(self):
        try:
            if not self.redis_client:
                return

            channels = [c for c in (self.cfg.redis.sub_channels or []) if c]
            if not channels:
                self.log("[redis] sub_channels is empty; subscriber loop disabled")
                return

            self.redis_pubsub = self.redis_client.pubsub()
            await self.redis_pubsub.subscribe(*channels)
            self.log(f"[redis] subscribed: {channels}")

            async for msg in self.redis_pubsub.listen():
                if not msg:
                    continue
                if msg.get("type") != "message":
                    continue
                ch = msg.get("channel")
                data = msg.get("data")
                peer = f"redis:{ch.decode() if isinstance(ch, (bytes, bytearray)) else ch}"
                if isinstance(data, str):
                    b = data.encode("utf-8", errors="replace")
                else:
                    b = bytes(data) if data is not None else b""
                self._on_message_bytes(peer, b)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.log(f"[redis] subscribe loop error: {e}")

    # ----- Serial -----
    async def _start_serial(self):
        if serial is None:
            self.log("[serial] pyserial package is not installed; Serial transport is unavailable.")
            self._set_state(transport="serial", role="-", state="disconnected", force=True)
            return

        cfg = self.cfg.serial
        self._set_state(transport="serial", role="-", state="connecting", peer=cfg.port, force=True)
        self.log(f"[serial] open {cfg.port} baud={cfg.baudrate}")

        try:
            # read/write timeouts are configured from current settings
            self.serial_obj = serial.Serial(
                port=cfg.port,
                baudrate=int(cfg.baudrate),
                timeout=float(cfg.timeout_sec),
                write_timeout=float(cfg.write_timeout_sec),
            )
        except Exception as e:
            self.log(f"[serial] open failed: {e}")
            self.serial_obj = None
            self._set_state(state="disconnected", force=True)
            return

        self.serial_send_q = asyncio.Queue(maxsize=CONN_SEND_Q_MAX)
        self.serial_tasks.append(asyncio.create_task(self._serial_sender_loop()))
        self.serial_tasks.append(asyncio.create_task(self._serial_reader_loop()))
        await self._start_jobs()

        self._set_state(state="connected", force=True)
        self.log("[serial] opened")

    async def _serial_sender_loop(self):
        try:
            while True:
                if not self.serial_send_q or not self.serial_obj:
                    return
                data = await self.serial_send_q.get()
                try:
                    # Serial write is blocking; run in a thread to avoid event loop stalls.
                    await asyncio.to_thread(self.serial_obj.write, data)
                except Exception as e:
                    self.log(f"[serial] write error: {e}")
        except asyncio.CancelledError:
            pass

    async def _serial_reader_loop(self):
        buf = self.serial_buf
        try:
            while True:
                if not self.serial_obj:
                    return
                try:
                    chunk: bytes = await asyncio.to_thread(self.serial_obj.read, 4096)
                except Exception as e:
                    self.log(f"[serial] read error: {e}")
                    return

                if not chunk:
                    # Timeout read returns empty bytes; continue polling.
                    continue

                peer = f"serial:{self.cfg.serial.port}"
                self._mark_rx(len(chunk), peer=peer)
                buf.extend(chunk)

                if guard_rx_buffer(buf):
                    self.log(f"[serial] rx buffer overflow -> trimmed (port={self.cfg.serial.port})")

                frames = self._frames_from_buffer(buf)
                if frames:
                    self._mark_rx(0, nframes=len(frames), peer=peer)
                for fr in frames:
                    self.log(self._format_frame_for_log(peer, fr))
        except asyncio.CancelledError:
            pass




