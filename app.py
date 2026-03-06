import json
import os
import queue
import time
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Any, Dict, Optional

SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "settings.json")

from engine import AppCfg, FrameCfg, JobCfg, NetEngine
from ui_widgets import CollapsibleSection, VerticalScrollableFrame

from ui.transports import RedisTransportUI, SerialTransportUI, TcpTransportUI, UdpTransportUI
from ui.transports.base import TransportCallbacks

LOG_MAX_LINES = 5000
UI_EVENT_Q_MAX = 20000
UI_POLL_MS = 50
UI_POLL_BATCH = 300


class App(tk.Tk):
    def apply_theme_colors(self, preset: str):
        palettes = {
            "dark": {
                "bg": "#1e1e1e",
                "fg": "#e6e6e6",
                "entry": "#2a2a2a",
                "text_bg": "#111111",
                "text_fg": "#e6e6e6",
                "button": "#303030",
            },
            "light": {
                "bg": "#f2f2f2",
                "fg": "#111111",
                "entry": "#ffffff",
                "text_bg": "#ffffff",
                "text_fg": "#111111",
                "button": "#e8e8e8",
            },
        }
        p = palettes.get(preset, palettes["light"])

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".", background=p["bg"], foreground=p["fg"])
        style.configure("TFrame", background=p["bg"])
        style.configure("TLabel", background=p["bg"], foreground=p["fg"])
        style.configure("TLabelframe", background=p["bg"])
        style.configure("TLabelframe.Label", background=p["bg"], foreground=p["fg"])
        style.configure("TEntry", fieldbackground=p["entry"], foreground=p["fg"])
        style.configure("TButton", padding=(8, 4), background=p["button"], foreground=p["fg"])
        style.configure("TCheckbutton", background=p["bg"], foreground=p["fg"])
        style.configure("TCombobox", fieldbackground=p["entry"], foreground=p["fg"])
        style.configure("TopBar.TFrame", background=p["bg"])
        style.configure("StatusBar.TFrame", background=p["bg"])

        if preset == "dark":
            section_bg = "#252b33"
            section_line = "#3f4a58"
            section_fg = "#eef2f7"
        else:
            section_bg = "#f8fafc"
            section_line = "#d4d9e0"
            section_fg = p["fg"]

        style.configure("SectionCard.TFrame", background=section_bg, borderwidth=1, relief="solid")
        style.configure("SectionHeader.TFrame", background=section_bg)
        style.configure("SectionBody.TFrame", background=section_bg)
        style.configure("SectionTitle.TLabel", background=section_bg, foreground=section_fg, font=("Segoe UI", 10, "bold"))
        style.configure("SectionLine.TSeparator", background=section_line)

        self._ui_colors = p

    def apply_text_widget_colors(self):
        if hasattr(self, "log") and hasattr(self, "_ui_colors"):
            p = self._ui_colors
            self.log.configure(
                bg=p["text_bg"],
                fg=p["text_fg"],
                insertbackground=p["text_fg"],
                selectbackground="#2f6db3" if self.color_preset.get() == "dark" else "#b6d7ff",
                font=("Consolas", 10),
            )
            self._refresh_log_tag_colors()

        if hasattr(self, "_transports") and hasattr(self, "_ui_colors"):
            for t in self._transports.values():
                try:
                    t.apply_tk_colors(self._ui_colors)
                except Exception:
                    pass

    def _refresh_log_tag_colors(self):
        if not hasattr(self, "log"):
            return

        if self.color_preset.get() == "dark":
            match_bg = "#6f6400"
            current_bg = "#ffc107"
            error_fg = "#ff7b72"
            conn_fg = "#7ee787"
            info_fg = "#a5d6ff"
        else:
            match_bg = "#fff59d"
            current_bg = "#ffcc80"
            error_fg = "#b00020"
            conn_fg = "#00695c"
            info_fg = "#0d47a1"

        self.log.tag_configure("search_match", background=match_bg)
        self.log.tag_configure("search_current", background=current_bg, foreground="#000000")
        self.log.tag_configure("log_error", foreground=error_fg)
        self.log.tag_configure("log_conn", foreground=conn_fg)
        self.log.tag_configure("log_info", foreground=info_fg)

    def __init__(self):
        super().__init__()
        self.title("MultiTransportTester (TCP/UDP/Redis/Serial)")
        self.geometry("1180x760")

        self.ui_q = queue.Queue(maxsize=UI_EVENT_Q_MAX)
        self.engine = NetEngine(self.ui_q)
        self.engine.start_thread()

        # --- transport selection ---
        self.transport = tk.StringVar(self, value="tcp")

        # --- common connection buttons ---
        self.btn_start: Optional[ttk.Button] = None
        self.btn_stop: Optional[ttk.Button] = None
        self.btn_apply: Optional[ttk.Button] = None
        self.transport_combo: Optional[ttk.Combobox] = None

        self._lock_widgets: list[tk.Misc] = []

        # --- framing vars (shared) ---
        self.frame_mode = tk.StringVar(self, value="delimiter")
        self.delim_kind = tk.StringVar(self, value="CRLF")
        self.custom_delim_hex = tk.StringVar(self, value="0D0A")
        self.append_delim_on_send = tk.BooleanVar(self, value=True)

        self.fixed_len = tk.StringVar(self, value="16")
        self.send_policy = tk.StringVar(self, value="strict")
        self.pad_byte_hex = tk.StringVar(self, value="00")
        self.rx_log_view = tk.StringVar(self, value="hex")
        self.tx_log_view = tk.StringVar(self, value="hex_utf8")

        # --- manual send (shared) ---
        self.manual_is_hex = tk.BooleanVar(self, value=False)
        self.manual_payload = tk.StringVar(self, value="")
        self.btn_send_now: Optional[ttk.Button] = None

        # --- jobs (shared) ---
        self.sendTimer_1_en = tk.BooleanVar(self, value=False)
        self.sendTimer_1_every = tk.StringVar(self, value="120.0")
        self.sendTimer_1_hex = tk.BooleanVar(self, value=False)
        self.sendTimer_1_payload = tk.StringVar(self, value="sendTimer_1")

        self.sendTimer_2_en = tk.BooleanVar(self, value=False)
        self.sendTimer_2_every = tk.StringVar(self, value="3600.0")
        self.sendTimer_2_hex = tk.BooleanVar(self, value=False)
        self.sendTimer_2_payload = tk.StringVar(self, value="sendTimer_2")

        self.sendTimer_3_en = tk.BooleanVar(self, value=False)
        self.sendTimer_3_every = tk.StringVar(self, value="3600.0")
        self.sendTimer_3_hex = tk.BooleanVar(self, value=False)
        self.sendTimer_3_payload = tk.StringVar(self, value="sendTimer_3")

        self.hb_en = tk.BooleanVar(self, value=False)
        self.hb_every = tk.StringVar(self, value="30.0")
        self.hb_hex = tk.BooleanVar(self, value=False)
        self.hb_payload = tk.StringVar(self, value="ping")

        self.job_send_buttons: Dict[str, ttk.Button] = {}

        # --- status bar vars ---
        self.st_transport = tk.StringVar(self, value="TRANSPORT:-")
        self.st_role = tk.StringVar(self, value="ROLE:-")
        self.st_state = tk.StringVar(self, value="STATE:idle")
        self.st_peer = tk.StringVar(self, value="PEER:-")
        self.st_rx = tk.StringVar(self, value="RX:0B/0f")
        self.st_tx = tk.StringVar(self, value="TX:0B/0f")
        self.st_rx_speed = tk.StringVar(self, value="R:0KB/s")
        self.st_tx_speed = tk.StringVar(self, value="T:0KB/s")
        self.st_last = tk.StringVar(self, value="Last RX:-  TX:-")
        self.st_recon = tk.StringVar(self, value="")

        # rolling speed sample cache
        self._speed_sample_window = 1.0
        self._speed_rx_bytes = 0
        self._speed_tx_bytes = 0
        self._speed_last_update = 0.0
        self._speed_last_rx = 0
        self._speed_last_tx = 0

        # --- theme preset ---
        self.color_preset = tk.StringVar(self, value="light")
        self.apply_theme_colors(self.color_preset.get())

        # --- last stats cache ---
        self._last_stats: Dict[str, Any] = {
            "transport": "-",
            "role": "-",
            "state": "idle",
            "peer": "",
            "clients": 0,
            "reconnecting": False,
            "retry": 0,
            "next_retry_in": 0.0,
            "rx_bytes": 0,
            "tx_bytes": 0,
            "rx_frames": 0,
            "tx_frames": 0,
            "last_rx_ts": 0.0,
            "last_tx_ts": 0.0,
        }

        self._last_rx_bytes = 0
        self._last_tx_bytes = 0
        self._last_rx_ts = 0.0
        self._last_tx_ts = 0.0
        self._send_speed = 0.0
        self._rx_speed = 0.0
        self._search_hits: list[str] = []
        self._search_idx = -1
        self.search_count_var = tk.StringVar(self, value="0/0")
        self.log_wrap = tk.BooleanVar(self, value=False)

        # transport UI plugins
        self._transports: Dict[str, Any] = {}

        self._build_ui()
        self._load_settings()
        self.apply_theme_colors(self.color_preset.get())
        self.apply_text_widget_colors()
        self._show_transport_panels()
        self._apply_control_states()

        # window close handler
        self.protocol("WM_DELETE_WINDOW", self._on_window_close)

        # wheel routing
        self._install_global_mousewheel_routing()

        self.after(UI_POLL_MS, self._poll_ui)

    # ----- UI parse helpers -----
    def _safe_int(self, s: str, default: int, name: str, min_v: int = None, max_v: int = None) -> int:
        try:
            v = int(str(s).strip())
        except Exception:
            self._append_log(f"[ui] invalid {name}; fallback={default}")
            v = default
        if min_v is not None and v < min_v:
            v = min_v
        if max_v is not None and v > max_v:
            v = max_v
        return v

    def _safe_float(self, s: str, default: float, name: str, min_v: float = None, max_v: float = None) -> float:
        try:
            v = float(str(s).strip())
        except Exception:
            self._append_log(f"[ui] invalid {name}; fallback={default}")
            v = default
        if min_v is not None and v < min_v:
            v = min_v
        if max_v is not None and v > max_v:
            v = max_v
        return v

    def _fmt_bytes(self, n: int) -> str:
        n = int(n)
        if n < 1024:
            return f"{n}B"
        if n < 1024**2:
            return f"{n/1024:.1f}KB"
        if n < 1024**3:
            return f"{n/1024**2:.1f}MB"
        return f"{n/1024**3:.1f}GB"

    def _fmt_time(self, ts: float) -> str:
        if not ts:
            return "-"
        try:
            return time.strftime("%H:%M:%S", time.localtime(ts))
        except Exception:
            return "-"

    # ----- build UI -----
    def _build_ui(self):
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=0)
        self.grid_columnconfigure(0, weight=1)

        # ---------- TOP ----------
        top = ttk.Frame(self, style="TopBar.TFrame")
        top.grid(row=0, column=0, sticky="ew", padx=8, pady=6)
        top.grid_columnconfigure(0, weight=1)

        top_left = ttk.Frame(top, style="TopBar.TFrame")
        top_left.grid(row=0, column=0, sticky="w")
        top_right = ttk.Frame(top, style="TopBar.TFrame")
        top_right.grid(row=0, column=1, sticky="e")

        ttk.Label(top_left, text="Transport").pack(side="left")
        self.transport_combo = ttk.Combobox(
            top_left,
            textvariable=self.transport,
            values=["TCP", "UDP", "REDIS", "SERIAL"],
            width=10,
            state="readonly",
        )
        self.transport_combo.pack(side="left", padx=(6, 16))

        ttk.Label(top_left, text="Theme").pack(side="left")
        theme_combo = ttk.Combobox(top_left, textvariable=self.color_preset, values=["light", "dark"], width=8, state="readonly")
        theme_combo.pack(side="left", padx=(6, 14))

        self.btn_start = ttk.Button(top_right, text="START", command=self._on_start)
        self.btn_start.pack(side="left", padx=(4, 0))
        self.btn_stop = ttk.Button(top_right, text="STOP", command=self._on_stop)
        self.btn_stop.pack(side="left", padx=(4, 0))
        self.btn_apply = ttk.Button(top_right, text="APPLY", command=self._on_apply)
        self.btn_apply.pack(side="left", padx=(4, 0))

        # transport-specific top row (row=1)
        self.top_stack = ttk.Frame(top)
        self.top_stack.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.top_stack.grid_columnconfigure(0, weight=1)

        # transport plugins
        tcp_cb = TransportCallbacks(on_tcp_targets_changed=self._on_tcp_targets_changed)
        self._transports = {
            "tcp": TcpTransportUI(self, callbacks=tcp_cb),
            "udp": UdpTransportUI(self),
            "redis": RedisTransportUI(self),
            "serial": SerialTransportUI(self),
        }

        # build top frames
        for t in self._transports.values():
            t.build_top(self.top_stack)
            t.top_frame.grid(row=0, column=0, sticky="ew")

        # lock widgets: transport combobox + transport-specific lock widgets
        self._lock_widgets.append(self.transport_combo)

        # theme change
        def _on_theme_change(_evt=None):
            self.apply_theme_colors(self.color_preset.get())
            self.apply_text_widget_colors()

        theme_combo.bind("<<ComboboxSelected>>", _on_theme_change)

        # transport change -> swap panels
        self.transport_combo.bind("<<ComboboxSelected>>", lambda _evt=None: self._show_transport_panels())
        self.transport.trace_add("write", lambda *_: self._show_transport_panels())

        # ---------- BODY: Panedwindow ----------
        self.pw = ttk.Panedwindow(self, orient="horizontal")
        self.pw.grid(row=1, column=0, sticky="nsew", padx=8, pady=6)

        left = ttk.Frame(self.pw)
        right = ttk.Frame(self.pw)
        self.pw.add(left, weight=4)
        self.pw.add(right, weight=1)

        # LEFT: log
        left.grid_rowconfigure(0, weight=0)
        left.grid_rowconfigure(1, weight=1)
        left.grid_rowconfigure(2, weight=0)
        left.grid_columnconfigure(0, weight=1)

        self.log_search_var = tk.StringVar(self)
        self.log_search_var.trace_add("write", lambda *args: self._on_log_search())

        log_top = ttk.Frame(left)
        log_top.grid(row=0, column=0, sticky="ew", pady=(0, 2))
        log_top.grid_columnconfigure(1, weight=1)

        ttk.Label(log_top, text="Search:").grid(row=0, column=0, sticky="w")
        self.ent_log_search = ttk.Entry(log_top, textvariable=self.log_search_var, width=40)
        self.ent_log_search.grid(row=0, column=1, sticky="ew", padx=(4, 4))
        ttk.Button(log_top, text="Prev", width=6, command=self._search_prev).grid(row=0, column=2, padx=(2, 0))
        ttk.Button(log_top, text="Next", width=6, command=self._search_next).grid(row=0, column=3, padx=(2, 0))
        ttk.Label(log_top, textvariable=self.search_count_var, width=7, anchor="e").grid(row=0, column=4, padx=(6, 2))
        ttk.Button(log_top, text="Copy", width=6, command=self._copy_selected_log).grid(row=0, column=5, padx=(2, 0))
        ttk.Button(log_top, text="Save", width=6, command=self._save_log_to_file).grid(row=0, column=6, padx=(2, 0))
        ttk.Checkbutton(log_top, text="Wrap", variable=self.log_wrap, command=self._apply_log_wrap).grid(row=0, column=7, padx=(8, 0))
        ttk.Button(log_top, text="Clear", command=self._clear_log).grid(row=0, column=8, padx=2)
        self.log_auto_scroll_btn = ttk.Button(log_top, text="Auto-Scroll: ON", command=self._toggle_auto_scroll)
        self.log_auto_scroll_btn.grid(row=0, column=9, padx=2)

        self.log = tk.Text(left, height=20, wrap="none")
        self.log.grid(row=1, column=0, sticky="nsew", pady=(2, 0))

        yscroll = ttk.Scrollbar(left, orient="vertical", command=self.log.yview)
        yscroll.grid(row=1, column=1, sticky="ns")
        self.log.configure(yscrollcommand=yscroll.set)
        self.log_xscroll = ttk.Scrollbar(left, orient="horizontal", command=self.log.xview)
        self.log_xscroll.grid(row=2, column=0, sticky="ew")
        self.log.configure(xscrollcommand=self.log_xscroll.set)
        
        self._log_auto_scroll = True
        self.log.bind("<Button-1>", self._on_log_click)
        self.log.bind("<MouseWheel>", self._on_log_mousewheel)
        self.log.bind("<Button-4>", self._on_log_mousewheel)
        self.log.bind("<Button-5>", self._on_log_mousewheel)
        self._apply_log_wrap()

        # RIGHT: scrollable settings
        self.right_scroll = VerticalScrollableFrame(right)
        self.right_scroll.pack(fill="both", expand=True)

        right_inner = self.right_scroll.interior

        # transport-specific settings stack
        self.settings_stack = ttk.Frame(right_inner)
        self.settings_stack.pack(fill="x", expand=False)
        self.settings_stack.grid_rowconfigure(0, weight=1)
        self.settings_stack.grid_columnconfigure(0, weight=1)

        # build settings frames
        for t in self._transports.values():
            t.build_settings(self.settings_stack)
            t.settings_frame.grid(row=0, column=0, sticky="nsew")
            t.settings_frame.grid_remove()
            self._lock_widgets.extend([w for w in getattr(t, "lock_widgets", []) if w is not None])

        # shared: framing
        framing_sec = CollapsibleSection(right_inner, "Message Framing", expanded=True)
        framing_sec.pack(fill="x", pady=6)
        fs = framing_sec.content
        fs.grid_columnconfigure(1, weight=1)
        label_w = 12

        ttk.Label(fs, text="Mode", width=label_w, anchor="w").grid(row=0, column=0, sticky="w", padx=6, pady=2)
        ttk.Combobox(fs, textvariable=self.frame_mode, values=["delimiter", "fixed"], width=12, state="readonly").grid(
            row=0, column=1, sticky="w", padx=6, pady=2
        )

        ttk.Label(fs, text="Delimiter", width=label_w, anchor="w").grid(row=1, column=0, sticky="w", padx=6, pady=2)
        delim_row = ttk.Frame(fs)
        delim_row.grid(row=1, column=1, sticky="w", padx=6, pady=2)
        ttk.Combobox(delim_row, textvariable=self.delim_kind, values=["LF", "CRLF", "CUSTOM HEX"], width=12, state="readonly").pack(
            side="left"
        )
        ttk.Label(delim_row, text="Custom HEX").pack(side="left", padx=(10, 4))
        ttk.Entry(delim_row, textvariable=self.custom_delim_hex, width=10).pack(side="left")

        ttk.Label(fs, text="Fixed Frame", width=label_w, anchor="w").grid(row=2, column=0, sticky="w", padx=6, pady=2)
        fixed_row = ttk.Frame(fs)
        fixed_row.grid(row=2, column=1, sticky="w", padx=6, pady=2)
        ttk.Label(fixed_row, text="Len").pack(side="left")
        ttk.Entry(fixed_row, textvariable=self.fixed_len, width=8).pack(side="left", padx=(4, 8))
        ttk.Label(fixed_row, text="Policy").pack(side="left")
        ttk.Combobox(fixed_row, textvariable=self.send_policy, values=["strict", "pad", "truncate"], width=10, state="readonly").pack(
            side="left", padx=(4, 8)
        )
        ttk.Label(fixed_row, text="Pad HEX").pack(side="left")
        ttk.Entry(fixed_row, textvariable=self.pad_byte_hex, width=6).pack(side="left", padx=(4, 0))

        ttk.Checkbutton(fs, text="Append delimiter when send mode is delimiter", variable=self.append_delim_on_send).grid(
            row=3, column=1, sticky="w", padx=6, pady=2
        )
        ttk.Label(fs, text="RX Log View", width=label_w, anchor="w").grid(row=4, column=0, sticky="w", padx=6, pady=2)
        ttk.Combobox(fs, textvariable=self.rx_log_view, values=["hex", "utf8", "hex_utf8"], width=12, state="readonly").grid(
            row=4, column=1, sticky="w", padx=6, pady=2
        )

        ttk.Label(fs, text="TX Log View", width=label_w, anchor="w").grid(row=5, column=0, sticky="w", padx=6, pady=2)
        ttk.Combobox(fs, textvariable=self.tx_log_view, values=["hex", "utf8", "hex_utf8"], width=12, state="readonly").grid(
            row=5, column=1, sticky="w", padx=6, pady=2
        )

        # shared: manual send
        ms_sec = CollapsibleSection(right_inner, "Manual Send", expanded=True)
        ms_sec.pack(fill="x", pady=6)
        ms = ms_sec.content
        ms.grid_columnconfigure(1, weight=1)
        ttk.Label(ms, text="", width=18, anchor="w").grid(row=0, column=0, sticky="w", padx=6, pady=2)
        ttk.Entry(ms, textvariable=self.manual_payload).grid(row=0, column=1, sticky="ew", padx=6, pady=2)
        ctrl_row = ttk.Frame(ms)
        ctrl_row.grid(row=1, column=1, sticky="e", padx=6, pady=(0, 6))
        ttk.Checkbutton(ctrl_row, text="HEX", variable=self.manual_is_hex).pack(side="left")
        self.btn_send_now = ttk.Button(ctrl_row, text="SEND", command=self._on_send_now)
        self.btn_send_now.pack(side="left", padx=(8, 0))

        # shared: jobs
        jobs_sec = CollapsibleSection(right_inner, "Timer Sender", expanded=False)
        jobs_sec.pack(fill="x", pady=6)
        jobs = jobs_sec.content

        self._job_ui(jobs, "sendTimer_1", self.sendTimer_1_en, self.sendTimer_1_every, self.sendTimer_1_hex, self.sendTimer_1_payload, self._send_job_now_sendTimer_1)
        self._job_ui(jobs, "sendTimer_2", self.sendTimer_2_en, self.sendTimer_2_every, self.sendTimer_2_hex, self.sendTimer_2_payload, self._send_job_now_sendTimer_2)
        self._job_ui(jobs, "sendTimer_3", self.sendTimer_3_en, self.sendTimer_3_every, self.sendTimer_3_hex, self.sendTimer_3_payload, self._send_job_now_sendTimer_3)
        self._job_ui(jobs, "heartbeat", self.hb_en, self.hb_every, self.hb_hex, self.hb_payload, self._send_job_now_hb)

        # ---------- STATUS BAR ----------
        status = ttk.Frame(self, style="StatusBar.TFrame")
        status.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 6))

        ttk.Label(status, textvariable=self.st_transport).pack(side="left")
        ttk.Label(status, text=" | ").pack(side="left")
        ttk.Label(status, textvariable=self.st_role).pack(side="left")
        ttk.Label(status, text=" | ").pack(side="left")
        ttk.Label(status, textvariable=self.st_state).pack(side="left")
        ttk.Label(status, text=" | ").pack(side="left")
        ttk.Label(status, textvariable=self.st_peer).pack(side="left")
        ttk.Label(status, text=" | ").pack(side="left")
        ttk.Label(status, textvariable=self.st_rx).pack(side="left")
        ttk.Label(status, text=" | ").pack(side="left")
        ttk.Label(status, textvariable=self.st_tx).pack(side="left")
        ttk.Label(status, text=" | ").pack(side="left")
        ttk.Label(status, textvariable=self.st_rx_speed).pack(side="left")
        ttk.Label(status, text=" | ").pack(side="left")
        ttk.Label(status, textvariable=self.st_tx_speed).pack(side="left")
        ttk.Label(status, text=" | ").pack(side="left")
        ttk.Label(status, textvariable=self.st_last).pack(side="left")
        ttk.Label(status, textvariable=self.st_recon).pack(side="right")

        self.bind("<Control-f>", lambda e: self.ent_log_search.focus())
        self.ent_log_search.bind("<Return>", lambda e: (self._search_next(), "break")[1])
        self.ent_log_search.bind("<Shift-Return>", lambda e: (self._search_prev(), "break")[1])
        self.bind("<F3>", lambda e: (self._search_next(), "break")[1])
        self.bind("<Shift-F3>", lambda e: (self._search_prev(), "break")[1])
        self.bind("<Control-l>", lambda e: (self._clear_log(), "break")[1])
        self.bind("<Control-Shift-S>", lambda e: (self._save_log_to_file(), "break")[1])
        self.bind("<Control-Shift-C>", lambda e: (self._copy_selected_log(), "break")[1])

        self._show_transport_panels()
        self._apply_control_states()
        self.after_idle(self._init_pane_layout)
        self.after(250, self._init_pane_layout)
        self.after(800, self._init_pane_layout)

    def _show_transport_panels(self):
        t = self.transport.get().lower().strip() or "tcp"

        if not self._transports:
            return

        for ui in self._transports.values():
            try:
                ui.settings_frame.grid_remove()
            except Exception:
                pass

        ui = self._transports.get(t)
        if ui:
            try:
                ui.settings_frame.grid()
                ui.settings_frame.tkraise()
            except Exception:
                pass
            try:
                ui.top_frame.tkraise()
            except Exception:
                pass

        self._apply_control_states()

    # ----- job UI helper -----
    def _job_ui(self, parent, name, en, every, is_hex, payload, send_now_cb):
        box = ttk.LabelFrame(parent, text=name)
        box.pack(fill="x", padx=6, pady=4)

        row = ttk.Frame(box)
        row.pack(fill="x", padx=6, pady=(4, 2))
        ttk.Checkbutton(row, text="Enable", variable=en).pack(side="left")
        ttk.Label(row, text="Interval (s)", width=10, anchor="w").pack(side="left", padx=(12, 2))
        ttk.Entry(row, textvariable=every, width=10).pack(side="left")
        #ttk.Checkbutton(row, text="HEX", variable=is_hex).pack(side="left", padx=(10, 0))
        btn = ttk.Button(row, text="Send", command=send_now_cb)
        btn.pack(side="right")
        self.job_send_buttons[name] = btn

        payload_row = ttk.Frame(box)
        payload_row.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Checkbutton(payload_row, text="HEX",variable=is_hex, width=10).pack(side="left", padx=(10, 0))
        ttk.Entry(payload_row, textvariable=payload).pack(side="left", fill="x", expand=True)

    # ----- config build -----
    def _make_cfg(self) -> AppCfg:
        cfg = AppCfg()
        cfg.transport = self.transport.get().strip().lower() or "tcp"

        # framing
        cfg.frame = FrameCfg(
            mode=self.frame_mode.get(),
            delim_kind=self.delim_kind.get(),
            custom_hex=self.custom_delim_hex.get(),
            append_delim_on_send=bool(self.append_delim_on_send.get()),
            fixed_len=self._safe_int(self.fixed_len.get(), 16, "Fixed Len", 1, 10_000_000),
            send_policy=self.send_policy.get(),
            pad_byte_hex=self.pad_byte_hex.get(),
            rx_log_view=self.rx_log_view.get(),
            tx_log_view=self.tx_log_view.get(),
            show_utf8=bool(self.rx_log_view.get() != "hex"),
        )

        # jobs
        cfg.sendTimer_1 = JobCfg(
            bool(self.sendTimer_1_en.get()),
            self._safe_float(self.sendTimer_1_every.get(), 120.0, "sendTimer_1 every", 0.1, 10**9),
            bool(self.sendTimer_1_hex.get()),
            self.sendTimer_1_payload.get(),
        )
        cfg.sendTimer_2 = JobCfg(
            bool(self.sendTimer_2_en.get()),
            self._safe_float(self.sendTimer_2_every.get(), 3600.0, "sendTimer_2 every", 0.1, 10**9),
            bool(self.sendTimer_2_hex.get()),
            self.sendTimer_2_payload.get(),
        )
        cfg.sendTimer_3 = JobCfg(
            bool(self.sendTimer_3_en.get()),
            self._safe_float(self.sendTimer_3_every.get(), 3600.0, "sendTimer_3 every", 0.1, 10**9),
            bool(self.sendTimer_3_hex.get()),
            self.sendTimer_3_payload.get(),
        )
        cfg.heartbeat = JobCfg(
            bool(self.hb_en.get()),
            self._safe_float(self.hb_every.get(), 30.0, "heartbeat every", 0.1, 10**9),
            bool(self.hb_hex.get()),
            self.hb_payload.get(),
        )

        # per transport
        ui = self._transports.get(cfg.transport)
        if ui:
            ui.fill_cfg(cfg, self._safe_int, self._safe_float)

        return cfg

    # ----- transport-specific callbacks -----
    def _on_tcp_targets_changed(self, light: bool):
        if (self._last_stats.get("transport") or "-").lower() != "tcp":
            return
        if (self._last_stats.get("role") or "-").lower() != "server":
            return

        tcp_ui = self._transports.get("tcp")
        if not tcp_ui:
            return

        scope = tcp_ui.server_scope.get()
        selected = tcp_ui.get_selected_peers()

        self.engine.call(self.engine.update_tcp_server_targets(scope, selected))
        self._apply_control_states()

    # ----- button actions -----
    def _on_start(self):
        cfg = self._make_cfg()
        self.engine.call(self.engine.start(cfg))

    def _on_stop(self):
        self.engine.call(self.engine.stop_all())

    def _on_apply(self):
        cfg = self._make_cfg()
        self.engine.call(self.engine.update_cfg(cfg))

    def _on_send_now(self):
        cfg = self._make_cfg()
        self.engine.call(self.engine.apply_cfg_and_send(cfg, self.manual_payload.get(), bool(self.manual_is_hex.get())))

    # send-now for jobs
    def _send_job_now_sendTimer_1(self):
        self._send_job_now(self.sendTimer_1_payload.get(), bool(self.sendTimer_1_hex.get()))

    def _send_job_now_sendTimer_2(self):
        self._send_job_now(self.sendTimer_2_payload.get(), bool(self.sendTimer_2_hex.get()))

    def _send_job_now_sendTimer_3(self):
        self._send_job_now(self.sendTimer_3_payload.get(), bool(self.sendTimer_3_hex.get()))

    def _send_job_now_hb(self):
        self._send_job_now(self.hb_payload.get(), bool(self.hb_hex.get()))

    def _send_job_now(self, payload: str, is_hex: bool):
        cfg = self._make_cfg()
        self.engine.call(self.engine.apply_cfg_and_send(cfg, payload, is_hex))

    # ----- UI event handling -----
    def _poll_ui(self):
        drained = 0
        while drained < UI_POLL_BATCH:
            try:
                typ, data = self.ui_q.get_nowait()
            except queue.Empty:
                break

            if typ == "log":
                self._append_log(str(data))
            elif typ == "clients":
                # tcp server client list
                tcp_ui = self._transports.get("tcp")
                if tcp_ui:
                    tcp_ui.on_clients(data)
                self._apply_control_states()
            elif typ == "stats":
                self._update_stats(data)

            drained += 1

        self.after(UI_POLL_MS, self._poll_ui)

    def _on_log_click(self, event):
        """Disable auto-scroll when user interacts with log manually."""
        self._log_auto_scroll = False
        self._update_auto_scroll_btn_text()

    def _on_log_mousewheel(self, event):
        """Disable auto-scroll when user scrolls the log manually."""
        self._log_auto_scroll = False
        self._update_auto_scroll_btn_text()
        return None

    def _toggle_auto_scroll(self):
        """Toggle auto-scroll mode for the log view."""
        self._log_auto_scroll = not self._log_auto_scroll
        self._update_auto_scroll_btn_text()

    def _update_auto_scroll_btn_text(self):
        """Refresh auto-scroll button label."""
        text = "Auto-Scroll: ON" if self._log_auto_scroll else "Auto-Scroll: OFF"
        self.log_auto_scroll_btn.configure(text=text)

    def _init_pane_layout(self, retry: int = 0):
        try:
            self.update_idletasks()
        except Exception:
            pass

        try:
            pw_w = int(self.pw.winfo_width()) if hasattr(self, "pw") else 0
            win_w = int(self.winfo_width())
            total_w = max(pw_w, win_w)

            # During early startup, width can still be 1 or very small.
            if total_w < 700:
                if retry < 20:
                    self.after(60, lambda: self._init_pane_layout(retry + 1))
                return

            desired = int(total_w * 0.68)
            min_left = 480
            min_right = 320

            if total_w > (min_left + min_right):
                left = max(min_left, min(desired, total_w - min_right))
            else:
                left = max(1, int(total_w * 0.62))

            self.pw.sashpos(0, left)
        except Exception:
            if retry < 20:
                self.after(60, lambda: self._init_pane_layout(retry + 1))

    def _apply_log_wrap(self):
        wrap_mode = "word" if self.log_wrap.get() else "none"
        self.log.configure(wrap=wrap_mode)
        if wrap_mode == "none":
            self.log_xscroll.grid()
        else:
            self.log_xscroll.grid_remove()

    def _copy_selected_log(self):
        try:
            text = self.log.get("sel.first", "sel.last")
        except tk.TclError:
            text = self.log.get("1.0", "end-1c")
        if not text:
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self._append_log("[ui] copied log text to clipboard")

    def _save_log_to_file(self):
        content = self.log.get("1.0", "end-1c")
        if not content.strip():
            self._append_log("[ui] no log content to save")
            return

        default_name = f"log_{time.strftime('%Y%m%d_%H%M%S')}.txt"
        path = filedialog.asksaveasfilename(
            title="Save Log",
            defaultextension=".txt",
            initialfile=default_name,
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            self._append_log(f"[ui] log saved: {path}")
        except Exception as e:
            self._append_log(f"[ui] log save failed: {e}")

    def _clear_log(self):
        self.log.delete("1.0", "end")
        self.log.tag_remove("search_match", "1.0", "end")
        self.log.tag_remove("search_current", "1.0", "end")
        self._search_hits = []
        self._search_idx = -1
        self.search_count_var.set("0/0")

    def _on_log_search(self):
        pattern = self.log_search_var.get().strip()
        self.log.tag_remove("search_match", "1.0", "end")
        self.log.tag_remove("search_current", "1.0", "end")
        self._search_hits = []
        self._search_idx = -1
        self.search_count_var.set("0/0")
        if not pattern:
            return

        start = "1.0"
        while True:
            pos = self.log.search(pattern, start, stopindex="end", nocase=True)
            if not pos:
                break
            end = f"{pos}+{len(pattern)}c"
            self.log.tag_add("search_match", pos, end)
            self._search_hits.append(pos)
            start = end

        self._refresh_log_tag_colors()
        if self._search_hits:
            self._goto_search_hit(0)

    def _goto_search_hit(self, idx: int):
        if not self._search_hits:
            self._search_idx = -1
            self.search_count_var.set("0/0")
            return

        total = len(self._search_hits)
        self._search_idx = idx % total
        pos = self._search_hits[self._search_idx]
        pattern = self.log_search_var.get().strip()
        if not pattern:
            self.search_count_var.set("0/0")
            return
        end = f"{pos}+{len(pattern)}c"
        self.log.tag_remove("search_current", "1.0", "end")
        self.log.tag_add("search_current", pos, end)
        self.log.mark_set("insert", pos)
        self.log.see(pos)
        self.search_count_var.set(f"{self._search_idx + 1}/{total}")

    def _search_next(self):
        if not self._search_hits:
            return
        if self._search_idx < 0:
            self._goto_search_hit(0)
        else:
            self._goto_search_hit(self._search_idx + 1)

    def _search_prev(self):
        if not self._search_hits:
            return
        if self._search_idx < 0:
            self._goto_search_hit(len(self._search_hits) - 1)
        else:
            self._goto_search_hit(self._search_idx - 1)

    def _classify_log_tag(self, line: str) -> Optional[str]:
        s = (line or "").lower()

        if any(k in s for k in ["error", "failed", "fail", "exception", "traceback", "timeout", "denied"]):
            return "log_error"

        if any(
            k in s
            for k in [
                "connected",
                "disconnected",
                "connect ",
                "listening",
                "listen ",
                "accepted",
                "opened",
                "closed",
                "reconnect",
                "bind ",
            ]
        ):
            return "log_conn"

        if any(k in s for k in ["[setting]", "[ui]"]):
            return "log_info"

        return None

    def _append_log(self, line: str):
        timestamp = time.strftime("%H:%M:%S", time.localtime())
        formatted_line = f"[{timestamp}] {line}"

        insert_idx = self.log.index("end-1c")
        self.log.insert("end", formatted_line + "\n")
        tag = self._classify_log_tag(line)
        if tag:
            self.log.tag_add(tag, f"{insert_idx} linestart", f"{insert_idx} lineend")

        if self._log_auto_scroll:
            self.log.see("end")

        lines = int(self.log.index("end-1c").split(".")[0])
        if lines > LOG_MAX_LINES:
            self.log.delete("1.0", f"{lines-LOG_MAX_LINES}.0")

    def _update_stats(self, s: Dict[str, Any]):
        self._last_stats.update(s)

        transport = s.get("transport", "-")
        role = s.get("role", "-")
        state = s.get("state", "idle")
        peer = s.get("peer", "") or "-"

        clients = int(s.get("clients", 0) or 0)

        rx_b = int(s.get("rx_bytes", 0) or 0)
        tx_b = int(s.get("tx_bytes", 0) or 0)
        rx_f = int(s.get("rx_frames", 0) or 0)
        tx_f = int(s.get("tx_frames", 0) or 0)

        last_rx = float(s.get("last_rx_ts", 0.0) or 0.0)
        last_tx = float(s.get("last_tx_ts", 0.0) or 0.0)

        reconnecting = bool(s.get("reconnecting", False))
        retry = int(s.get("retry", 0) or 0)
        next_in = float(s.get("next_retry_in", 0.0) or 0.0)

        self.st_transport.set(f"TRANSPORT:{str(transport).upper()}")
        self.st_role.set(f"ROLE:{str(role).upper()}")

        if transport == "tcp" and role == "server":
            self.st_state.set(f"STATE:{state} (clients={clients})")
        else:
            self.st_state.set(f"STATE:{state}")

        self.st_peer.set(f"PEER:{peer}")
        self.st_rx.set(f"RX:{self._fmt_bytes(rx_b)}/{rx_f}f")
        self.st_tx.set(f"TX:{self._fmt_bytes(tx_b)}/{tx_f}f")

        now = time.time()
        if self._speed_last_update <= 0:
            self._speed_last_update = now
            self._speed_last_rx = rx_b
            self._speed_last_tx = tx_b
        else:
            delta = now - self._speed_last_update
            if delta >= self._speed_sample_window:
                rx_delta = rx_b - self._speed_last_rx
                tx_delta = tx_b - self._speed_last_tx
                self._speed_rx_bytes = max(0, rx_delta)
                self._speed_tx_bytes = max(0, tx_delta)
                self._speed_last_rx = rx_b
                self._speed_last_tx = tx_b
                self._speed_last_update = now

        rx_speed = self._speed_rx_bytes / self._speed_sample_window if self._speed_sample_window > 0 else 0
        tx_speed = self._speed_tx_bytes / self._speed_sample_window if self._speed_sample_window > 0 else 0
        self.st_rx_speed.set(f"R:{self._fmt_bytes(int(rx_speed))}/s")
        self.st_tx_speed.set(f"T:{self._fmt_bytes(int(tx_speed))}/s")

        self.st_last.set(f"Last RX:{self._fmt_time(last_rx)}  TX:{self._fmt_time(last_tx)}")

        if reconnecting:
            self.st_recon.set(f"  RECONN retry={retry} next={next_in:.1f}s")
        else:
            self.st_recon.set("")

        self._apply_control_states()

    def _apply_control_states(self):
        """Apply enabled/disabled state to controls based on runtime status."""

        state = (self._last_stats.get("state") or "idle").lower()
        reconnecting = bool(self._last_stats.get("reconnecting", False))

        running_states = {"connecting", "connected", "reconnecting", "listening"}
        running = state in running_states or reconnecting

        start_enabled = not running

        def _set(widget, enabled: bool, *, readonly_ok: bool = False):
            if widget is None:
                return
            try:
                if readonly_ok and isinstance(widget, ttk.Combobox):
                    widget.configure(state=("readonly" if enabled else "disabled"))
                else:
                    widget.configure(state=("normal" if enabled else "disabled"))
            except Exception:
                pass

        # start/stop/apply
        _set(self.btn_start, start_enabled)
        _set(self.btn_stop, running or state == "disconnected")
        _set(self.btn_apply, True)

        for w in self._lock_widgets:
            if w is self.transport_combo:
                _set(w, start_enabled, readonly_ok=True)
            else:
                _set(w, start_enabled)

        for ui in self._transports.values():
            try:
                ui.apply_runtime_state(self._last_stats)
            except Exception:
                pass

        active_t = (self._last_stats.get("transport") or "-").lower()
        ui = self._transports.get(active_t)
        can_send = bool(ui.can_send(self._last_stats)) if ui else False

        _set(self.btn_send_now, can_send)
        for b in self.job_send_buttons.values():
            _set(b, can_send)

    # ----- global mousewheel routing (right panel only) -----
    def _install_global_mousewheel_routing(self):
        self.bind_all("<MouseWheel>", self._on_global_mousewheel, add="+")
        self.bind_all("<Button-4>", self._on_global_mousewheel, add="+")
        self.bind_all("<Button-5>", self._on_global_mousewheel, add="+")  # Linux

    def _on_global_mousewheel(self, event):
        w = self.winfo_containing(event.x_root, event.y_root)
        if self.right_scroll.is_descendant(w):
            return self.right_scroll.handle_mousewheel(event)
        return None

    # ----- destroy -----
    def _on_window_close(self):
        self._save_settings()
        self.destroy()

    def _save_settings(self):
        try:
            settings = {
                "transport": self.transport.get(),
                "color_preset": self.color_preset.get(),
                "frame_mode": self.frame_mode.get(),
                "delim_kind": self.delim_kind.get(),
                "custom_delim_hex": self.custom_delim_hex.get(),
                "append_delim_on_send": self.append_delim_on_send.get(),
                "fixed_len": self.fixed_len.get(),
                "send_policy": self.send_policy.get(),
                "pad_byte_hex": self.pad_byte_hex.get(),
                "rx_log_view": self.rx_log_view.get(),
                "tx_log_view": self.tx_log_view.get(),
                "show_utf8": self.rx_log_view.get() != "hex",
                "manual_is_hex": self.manual_is_hex.get(),
                "manual_payload": self.manual_payload.get(),
                "log_wrap": self.log_wrap.get(),
                "sendTimer_1_en": self.sendTimer_1_en.get(),
                "sendTimer_1_every": self.sendTimer_1_every.get(),
                "sendTimer_1_hex": self.sendTimer_1_hex.get(),
                "sendTimer_1_payload": self.sendTimer_1_payload.get(),
                "sendTimer_2_en": self.sendTimer_2_en.get(),
                "sendTimer_2_every": self.sendTimer_2_every.get(),
                "sendTimer_2_hex": self.sendTimer_2_hex.get(),
                "sendTimer_2_payload": self.sendTimer_2_payload.get(),
                "sendTimer_3_en": self.sendTimer_3_en.get(),
                "sendTimer_3_every": self.sendTimer_3_every.get(),
                "sendTimer_3_hex": self.sendTimer_3_hex.get(),
                "sendTimer_3_payload": self.sendTimer_3_payload.get(),
                "hb_en": self.hb_en.get(),
                "hb_every": self.hb_every.get(),
                "hb_hex": self.hb_hex.get(),
                "hb_payload": self.hb_payload.get(),
            }

            for name, ui in self._transports.items():
                if hasattr(ui, "save_settings"):
                    settings[f"transport_{name}"] = ui.save_settings()

            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(settings, f, indent=2, ensure_ascii=False)
            self._append_log(f"[setting] saved: {SETTINGS_FILE}")
        except Exception as e:
            self._append_log(f"[setting] save failed: {e}")

    def _load_settings(self):
        if not os.path.exists(SETTINGS_FILE):
            return

        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                settings = json.load(f)

            self.transport.set(settings.get("transport", "tcp"))
            self.color_preset.set(settings.get("color_preset", "light"))
            self.frame_mode.set(settings.get("frame_mode", "delimiter"))
            self.delim_kind.set(settings.get("delim_kind", "CRLF"))
            self.custom_delim_hex.set(settings.get("custom_delim_hex", "0D0A"))
            self.append_delim_on_send.set(settings.get("append_delim_on_send", True))
            self.fixed_len.set(settings.get("fixed_len", "16"))
            self.send_policy.set(settings.get("send_policy", "strict"))
            self.pad_byte_hex.set(settings.get("pad_byte_hex", "00"))
            legacy_show_utf8 = bool(settings.get("show_utf8", False))
            self.rx_log_view.set(settings.get("rx_log_view", "hex_utf8" if legacy_show_utf8 else "hex"))
            self.tx_log_view.set(settings.get("tx_log_view", "hex_utf8" if legacy_show_utf8 else "hex"))
            self.manual_is_hex.set(settings.get("manual_is_hex", False))
            self.manual_payload.set(settings.get("manual_payload", ""))
            self.log_wrap.set(settings.get("log_wrap", False))

            self.sendTimer_1_en.set(settings.get("sendTimer_1_en", False))
            self.sendTimer_1_every.set(settings.get("sendTimer_1_every", "120.0"))
            self.sendTimer_1_hex.set(settings.get("sendTimer_1_hex", False))
            self.sendTimer_1_payload.set(settings.get("sendTimer_1_payload", "sendTimer_1"))

            self.sendTimer_2_en.set(settings.get("sendTimer_2_en", False))
            self.sendTimer_2_every.set(settings.get("sendTimer_2_every", "3600.0"))
            self.sendTimer_2_hex.set(settings.get("sendTimer_2_hex", False))
            self.sendTimer_2_payload.set(settings.get("sendTimer_2_payload", "sendTimer_2"))

            self.sendTimer_3_en.set(settings.get("sendTimer_3_en", False))
            self.sendTimer_3_every.set(settings.get("sendTimer_3_every", "3600.0"))
            self.sendTimer_3_hex.set(settings.get("sendTimer_3_hex", False))
            self.sendTimer_3_payload.set(settings.get("sendTimer_3_payload", "sendTimer_3"))

            self.hb_en.set(settings.get("hb_en", False))
            self.hb_every.set(settings.get("hb_every", "30.0"))
            self.hb_hex.set(settings.get("hb_hex", False))
            self.hb_payload.set(settings.get("hb_payload", "ping"))

            for name in ["tcp", "udp", "redis", "serial"]:
                key = f"transport_{name}"
                if key in settings and hasattr(self._transports.get(name), "load_settings"):
                    self._transports[name].load_settings(settings[key])

            self._apply_log_wrap()
            self._append_log(f"[setting] loaded: {SETTINGS_FILE}")
        except Exception as e:
            self._append_log(f"[setting] load failed: {e}")

    # ----- destroy -----
    def destroy(self):
        try:
            self.engine.shutdown(timeout_sec=1.5)
        except Exception:
            pass
        super().destroy()


if __name__ == "__main__":
    App().mainloop()





