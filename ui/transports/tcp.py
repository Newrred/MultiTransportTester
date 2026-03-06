from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

import tkinter as tk
from tkinter import ttk

from engine import AppCfg, KeepAliveCfg, TcpCfg
from ui_widgets import CollapsibleSection

from .base import SafeFloatFn, SafeIntFn, TransportCallbacks


class TcpTransportUI:
    name = "tcp"

    def __init__(self, master: tk.Misc, callbacks: Optional[TransportCallbacks] = None):
        self._master = master
        self._cb = callbacks or TransportCallbacks()

        # --- top vars ---
        self.role = tk.StringVar(master, value="client")
        self.host = tk.StringVar(master, value="127.0.0.1")
        self.port = tk.StringVar(master, value="7000")

        # --- settings vars ---
        self.timeout = tk.StringVar(master, value="5.0")
        self.max_clients = tk.StringVar(master, value="50")

        self.auto_reconnect = tk.BooleanVar(master, value=False)
        self.backoff_min = tk.StringVar(master, value="1.0")
        self.backoff_max = tk.StringVar(master, value="30.0")
        self.max_retry = tk.StringVar(master, value="0")

        self.ka_enabled = tk.BooleanVar(master, value=False)
        self.ka_idle = tk.StringVar(master, value="60")
        self.ka_intvl = tk.StringVar(master, value="10")
        self.ka_cnt = tk.StringVar(master, value="5")

        self.server_scope = tk.StringVar(master, value="all")

        self.send_scope_last10 = tk.BooleanVar(master, value=False)
        self.send_scope_random = tk.BooleanVar(master, value=False)

        # dynamic list
        self.client_list_items: List[str] = []

        # UI refs
        self.top_frame: Optional[ttk.Frame] = None
        self.settings_frame: Optional[ttk.Frame] = None

        self.rb_client: Optional[ttk.Radiobutton] = None
        self.rb_server: Optional[ttk.Radiobutton] = None
        self.ent_host: Optional[ttk.Entry] = None
        self.ent_port: Optional[ttk.Entry] = None

        self.ent_timeout: Optional[ttk.Entry] = None
        self.ent_backoff_min: Optional[ttk.Entry] = None
        self.ent_backoff_max: Optional[ttk.Entry] = None
        self.ent_max_retry: Optional[ttk.Entry] = None
        self.ent_max_clients: Optional[ttk.Entry] = None

        self.ent_ka_idle: Optional[ttk.Entry] = None
        self.ent_ka_intvl: Optional[ttk.Entry] = None
        self.ent_ka_cnt: Optional[ttk.Entry] = None

        self.client_listbox: Optional[tk.Listbox] = None
        self.btn_sync_selection: Optional[ttk.Button] = None

        # containers
        self._tcp_role_stack: Optional[ttk.Frame] = None

        # widgets to lock when running
        self.lock_widgets: List[tk.Misc] = []

    # ------------------ UI builders ------------------
    def build_top(self, parent: ttk.Frame) -> None:
        fr = ttk.Frame(parent)
        self.top_frame = fr

        ttk.Label(fr, text="TCP Role").pack(side="left")
        self.rb_client = ttk.Radiobutton(fr, text="Client", variable=self.role, value="client")
        self.rb_client.pack(side="left", padx=4)
        self.rb_server = ttk.Radiobutton(fr, text="Server", variable=self.role, value="server")
        self.rb_server.pack(side="left", padx=4)

        ttk.Label(fr, text="Host").pack(side="left", padx=(12, 4))
        self.ent_host = ttk.Entry(fr, textvariable=self.host, width=18)
        self.ent_host.pack(side="left")

        ttk.Label(fr, text="Port").pack(side="left", padx=(12, 4))
        self.ent_port = ttk.Entry(fr, textvariable=self.port, width=8)
        self.ent_port.pack(side="left")

        # lock widgets (endpoint/role)
        self.lock_widgets.extend([w for w in [self.rb_client, self.rb_server, self.ent_host, self.ent_port] if w is not None])

    def build_settings(self, parent: ttk.Frame) -> None:
        fr = ttk.Frame(parent)
        self.settings_frame = fr

        sec = CollapsibleSection(fr, "TCP Settings", expanded=True)
        sec.pack(fill="x", pady=6)
        box = sec.content

        # stacked role panels
        self._tcp_role_stack = ttk.Frame(box)
        self._tcp_role_stack.pack(fill="x", padx=6, pady=6)

        tcp_client_panel = ttk.Frame(self._tcp_role_stack)
        tcp_server_panel = ttk.Frame(self._tcp_role_stack)

        self._tcp_role_stack.grid_rowconfigure(0, weight=1)
        self._tcp_role_stack.grid_columnconfigure(0, weight=1)

        tcp_client_panel.grid(row=0, column=0, sticky="nsew")
        tcp_server_panel.grid(row=0, column=0, sticky="nsew")

        # client-only
        lf = ttk.LabelFrame(tcp_client_panel, text="TCP Client")
        lf.pack(fill="both", expand=True)

        row = ttk.Frame(lf)
        row.pack(fill="x", padx=6, pady=4)
        ttk.Label(row, text="Connect Timeout (s)", width=20, anchor="w").pack(side="left")
        self.ent_timeout = ttk.Entry(row, textvariable=self.timeout, width=10)
        self.ent_timeout.pack(side="left", padx=6)

        row2 = ttk.Frame(lf)
        row2.pack(fill="x", padx=6, pady=4)
        ttk.Checkbutton(row2, text="Auto Reconnect", variable=self.auto_reconnect).pack(side="left")

        row3 = ttk.Frame(lf)
        row3.pack(fill="x", padx=6, pady=4)
        ttk.Label(row3, text="Backoff", width=10, anchor="w").pack(side="left")
        ttk.Label(row3, text="Min").pack(side="left")
        self.ent_backoff_min = ttk.Entry(row3, textvariable=self.backoff_min, width=8)
        self.ent_backoff_min.pack(side="left", padx=(4, 8))
        ttk.Label(row3, text="Max").pack(side="left")
        self.ent_backoff_max = ttk.Entry(row3, textvariable=self.backoff_max, width=8)
        self.ent_backoff_max.pack(side="left", padx=(4, 10))
        ttk.Label(row3, text="Max Retry (0=inf)").pack(side="left", padx=(0, 4))
        self.ent_max_retry = ttk.Entry(row3, textvariable=self.max_retry, width=6)
        self.ent_max_retry.pack(side="left")

        # server-only
        lf2 = ttk.LabelFrame(tcp_server_panel, text="TCP Server")
        lf2.pack(fill="both", expand=True)

        row = ttk.Frame(lf2)
        row.pack(fill="x", padx=6, pady=4)
        ttk.Label(row, text="Max Clients", width=20, anchor="w").pack(side="left")
        self.ent_max_clients = ttk.Entry(row, textvariable=self.max_clients, width=10)
        self.ent_max_clients.pack(side="left", padx=6)

        scope_row = ttk.Frame(lf2)
        scope_row.pack(fill="x", padx=6, pady=4)
        ttk.Label(scope_row, text="Send Scope", width=20, anchor="w").pack(side="left")
        ttk.Radiobutton(scope_row, text="All", variable=self.server_scope, value="all").pack(side="left", padx=4)
        ttk.Radiobutton(scope_row, text="Selected", variable=self.server_scope, value="selected").pack(side="left", padx=4)

        self.client_listbox = tk.Listbox(lf2, selectmode="extended", height=6)
        self.client_listbox.pack(fill="x", padx=6, pady=4)

        self.btn_sync_selection = ttk.Button(
            lf2,
            text="Sync Selection -> Engine",
            command=lambda: self._notify_targets_changed(light=True),
        )
        self.btn_sync_selection.pack(fill="x", padx=6, pady=(0, 6))

        # Send Scope Options (Advanced)
        scope_sec = CollapsibleSection(lf2, "Advanced Send Options", expanded=False)
        scope_sec.pack(fill="x", padx=6, pady=(6, 0))
        scope_box = scope_sec.content

        row_opt = ttk.Frame(scope_box)
        row_opt.pack(fill="x", pady=2)
        ttk.Checkbutton(row_opt, text="Last 10 only (debug)", variable=self.send_scope_last10).pack(side="left")
        ttk.Checkbutton(row_opt, text="Random (load test)", variable=self.send_scope_random).pack(side="left", padx=10)

        # keepalive section (TCP only)
        ka_sec = CollapsibleSection(fr, "TCP Keepalive", expanded=False)
        ka_sec.pack(fill="x", pady=6)
        ka = ka_sec.content

        ttk.Checkbutton(ka, text="Enable", variable=self.ka_enabled).pack(anchor="w", padx=6)
        row = ttk.Frame(ka)
        row.pack(fill="x", padx=6, pady=2)
        ttk.Label(row, text="Idle (s)").pack(side="left")
        self.ent_ka_idle = ttk.Entry(row, textvariable=self.ka_idle, width=6)
        self.ent_ka_idle.pack(side="left", padx=4)
        ttk.Label(row, text="Interval (s)").pack(side="left", padx=(8, 0))
        self.ent_ka_intvl = ttk.Entry(row, textvariable=self.ka_intvl, width=6)
        self.ent_ka_intvl.pack(side="left", padx=4)
        ttk.Label(row, text="Count").pack(side="left", padx=(8, 0))
        self.ent_ka_cnt = ttk.Entry(row, textvariable=self.ka_cnt, width=6)
        self.ent_ka_cnt.pack(side="left", padx=4)

        self.lock_widgets.extend(
            [
                w
                for w in [
                    self.ent_timeout,
                    self.ent_backoff_min,
                    self.ent_backoff_max,
                    self.ent_max_retry,
                    self.ent_max_clients,
                    self.ent_ka_idle,
                    self.ent_ka_intvl,
                    self.ent_ka_cnt,
                ]
                if w is not None
            ]
        )

        # role change handler: raise client/server panels
        def _show_role_panel(*_):
            (tcp_client_panel if self.role.get() == "client" else tcp_server_panel).tkraise()

        self.role.trace_add("write", _show_role_panel)
        _show_role_panel()

        self.server_scope.trace_add("write", lambda *_: self._notify_targets_changed(light=True))
        self.client_listbox.bind("<<ListboxSelect>>", lambda _evt=None: self._notify_targets_changed(light=True))

    # ------------------ runtime + helpers ------------------
    def _notify_targets_changed(self, *, light: bool):
        cb = self._cb.on_tcp_targets_changed
        if cb:
            cb(light)

    def get_selected_peers(self) -> Set[str]:
        if not self.client_listbox:
            return set()
        idxs = self.client_listbox.curselection()
        peers: Set[str] = set()
        for i in idxs:
            if 0 <= i < len(self.client_list_items):
                peers.add(self.client_list_items[i])
        return peers

    def get_send_targets(self) -> Set[str]:
        """Resolve send targets from current scope and selection."""
        scope = self.server_scope.get()
        
        if scope == "all":
            return set(self.client_list_items)
        
        if scope == "selected":
            return self.get_selected_peers()
        
        if self.send_scope_last10.get():
            return set(self.client_list_items[-10:]) if len(self.client_list_items) > 10 else set(self.client_list_items)
        
        if self.send_scope_random.get() and self.client_list_items:
            import random
            return {random.choice(self.client_list_items)}
        
        return self.get_selected_peers()

    def on_clients(self, peers: List[str]) -> None:
        """Refresh the client list while preserving current selection."""
        prev_selected = self.get_selected_peers()

        self.client_list_items = list(peers)
        if self.client_listbox:
            self.client_listbox.delete(0, "end")
            for p in self.client_list_items:
                self.client_listbox.insert("end", p)

            for i, p in enumerate(self.client_list_items):
                if p in prev_selected:
                    self.client_listbox.selection_set(i)

    def fill_cfg(self, cfg: AppCfg, safe_int: SafeIntFn, safe_float: SafeFloatFn) -> None:
        scope = self.server_scope.get()
        targets = self.get_send_targets()

        # UX fallback: if scope is "selected" but no peer is selected,
        # use all connected peers to avoid an apparent "server send is broken" state.
        if scope == "selected" and not targets and self.client_list_items:
            scope = "all"
            targets = set(self.client_list_items)

        cfg.tcp = TcpCfg(
            role=self.role.get(),
            host=self.host.get().strip() or "127.0.0.1",
            port=safe_int(self.port.get(), 7000, "TCP port", 1, 65535),
            connect_timeout_sec=safe_float(self.timeout.get(), 5.0, "TCP timeout", 0.1, 120.0),
            max_clients=safe_int(self.max_clients.get(), 50, "TCP max clients", 1, 5000),
            auto_reconnect=bool(self.auto_reconnect.get()),
            backoff_min_sec=safe_float(self.backoff_min.get(), 1.0, "Backoff min", 0.0, 3600.0),
            backoff_max_sec=safe_float(self.backoff_max.get(), 30.0, "Backoff max", 0.0, 3600.0),
            max_retry=safe_int(self.max_retry.get(), 0, "Max retry", 0, 1_000_000),
            keepalive=KeepAliveCfg(
                enabled=bool(self.ka_enabled.get()),
                idle_sec=safe_int(self.ka_idle.get(), 60, "KA idle", 1, 86400),
                interval_sec=safe_int(self.ka_intvl.get(), 10, "KA interval", 1, 86400),
                count=safe_int(self.ka_cnt.get(), 5, "KA count", 1, 50),
            ),
            server_scope=scope,
            server_selected=targets,
        )

    def can_send(self, stats: Dict[str, Any]) -> bool:
        transport = (stats.get("transport") or "-").lower()
        role = (stats.get("role") or "-").lower()
        state = (stats.get("state") or "idle").lower()
        clients = int(stats.get("clients", 0) or 0)

        if transport != "tcp":
            return False

        if role == "client":
            return state == "connected"

        if role == "server":
            if clients <= 0:
                return False
            if self.server_scope.get() == "selected":
                # UX fallback keeps send enabled when clients exist.
                # Actual cfg build will switch to "all" if no peer is selected.
                return len(self.get_selected_peers()) > 0 or len(self.client_list_items) > 0
            return True

        return False

    def apply_runtime_state(self, stats: Dict[str, Any]) -> None:
        """Enable runtime-only controls for TCP server state."""
        if not self.btn_sync_selection:
            return

        transport = (stats.get("transport") or "-").lower()
        role = (stats.get("role") or "-").lower()
        clients = int(stats.get("clients", 0) or 0)

        if transport == "tcp" and role == "server":
            try:
                self.btn_sync_selection.configure(state=("normal" if clients > 0 else "disabled"))
            except Exception:
                pass
        else:
            try:
                self.btn_sync_selection.configure(state="disabled")
            except Exception:
                pass

    def apply_tk_colors(self, palette: Dict[str, str]) -> None:
        if not self.client_listbox:
            return
        try:
            self.client_listbox.configure(bg=palette.get("text_bg", "#ffffff"), fg=palette.get("text_fg", "#111111"))
        except Exception:
            pass



