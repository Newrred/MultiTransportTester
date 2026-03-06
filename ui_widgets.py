# ui_widgets.py
# Tkinter/ttk UI helper widgets: always-open section + stable vertical scrollable frame.
import tkinter as tk
from tkinter import ttk
from typing import Optional, Tuple


class CollapsibleSection(ttk.Frame):
    """Section container that is always expanded.

    The `content` frame is kept for compatibility with existing app code.
    The `expanded` argument is accepted but intentionally ignored.
    """

    def __init__(self, master, title: str, *, expanded: bool = True):
        super().__init__(master)

        card = ttk.Frame(self, style="SectionCard.TFrame", padding=(8, 6, 8, 8))
        card.pack(fill="x")

        header = ttk.Frame(card, style="SectionHeader.TFrame")
        header.pack(fill="x")
        ttk.Label(header, text=title, style="SectionTitle.TLabel").pack(side="left", fill="x", expand=True)

        ttk.Separator(card, orient="horizontal", style="SectionLine.TSeparator").pack(fill="x", pady=(4, 6))

        self.content = ttk.Frame(card, style="SectionBody.TFrame")
        self.content.pack(fill="x")


class VerticalScrollableFrame(ttk.Frame):
    """Scrollable frame built from Canvas + interior frame + vertical scrollbar.

    Stabilization points:
    1) Use the interior window item bbox to set scrollregion.
    2) Ignore wheel scroll when content is smaller than viewport.
    3) Debounce configure events to reduce drag jitter in PanedWindow.
    4) Apply scrollregion/width only when values change.
    """

    def __init__(self, master, *, debounce_ms: int = 16):
        super().__init__(master)

        self.canvas = tk.Canvas(self, highlightthickness=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vsb.set)

        self.vsb.grid(row=0, column=1, sticky="ns")
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.interior = ttk.Frame(self.canvas)
        self._win_id = self.canvas.create_window((0, 0), window=self.interior, anchor="nw")

        self._debounce_ms = int(debounce_ms)
        self._pending_after: Optional[str] = None

        self._last_canvas_w = -1
        self._last_scrollregion: Optional[Tuple[int, int, int, int]] = None

        self.interior.bind("<Configure>", self._on_any_configure)
        self.canvas.bind("<Configure>", self._on_any_configure)

        self.master.bind("<Button-1>", self._on_paned_drag_start, add="+")
        self.master.bind("<ButtonRelease-1>", self._on_paned_drag_end, add="+")
        self.master.bind("<B1-Motion>", self._on_paned_drag_move, add="+")

        self._is_paned_dragging = False
        self._schedule_update()

    # ---------- Public helpers ----------
    def is_descendant(self, widget: Optional[tk.Misc]) -> bool:
        """Return True when widget belongs to this scroll area."""
        w = widget
        while w is not None:
            if w == self.interior or w == self.canvas:
                return True
            w = getattr(w, "master", None)
        return False

    def can_scroll(self) -> bool:
        """Return True only when content is taller than viewport."""
        bbox = self.canvas.bbox(self._win_id)
        if not bbox:
            return False
        _, _, _, y2 = bbox
        content_h = max(0, int(y2))
        view_h = max(1, int(self.canvas.winfo_height()))
        return content_h > view_h

    def _on_paned_drag_start(self, _event=None):
        self._is_paned_dragging = True

    def _on_paned_drag_end(self, _event=None):
        self._is_paned_dragging = False

    def _on_paned_drag_move(self, _event=None):
        if self._is_paned_dragging:
            return "break"
        return None

    def handle_mousewheel(self, event) -> str:
        """Handle wheel events routed from global bindings."""
        if not self.can_scroll():
            return "break"

        if self._is_paned_dragging:
            return "break"

        if getattr(event, "num", None) == 4:
            delta = 120
        elif getattr(event, "num", None) == 5:
            delta = -120
        else:
            delta = int(getattr(event, "delta", 0) or 0)

        if delta == 0:
            return "break"

        if abs(delta) < 120:
            step = -1 if delta > 0 else 1
        else:
            step = int(-delta / 120)

        self.canvas.yview_scroll(step, "units")
        return "break"

    # ---------- Internal ----------
    def _on_any_configure(self, _evt=None):
        self._schedule_update()

    def _schedule_update(self):
        if self._pending_after is not None:
            try:
                self.after_cancel(self._pending_after)
            except Exception:
                pass
            self._pending_after = None
        self._pending_after = self.after(self._debounce_ms, self._update_layout)

    def _update_layout(self):
        self._pending_after = None

        try:
            self.canvas.coords(self._win_id, 0, 0)
        except Exception:
            pass

        cw = int(self.canvas.winfo_width())
        if cw > 1 and cw != self._last_canvas_w:
            try:
                self.canvas.itemconfigure(self._win_id, width=cw)
            except Exception:
                pass
            self._last_canvas_w = cw

        bbox = self.canvas.bbox(self._win_id)
        if not bbox:
            return

        x1, y1, x2, y2 = bbox
        content_w = max(0, int(x2 - x1))
        content_h = max(0, int(y2 - y1))

        sr = (0, 0, max(cw, content_w), content_h)
        if sr != self._last_scrollregion:
            try:
                self.canvas.configure(scrollregion=sr)
            except Exception:
                pass
            self._last_scrollregion = sr

        if content_h <= int(self.canvas.winfo_height()):
            try:
                self.canvas.yview_moveto(0.0)
            except Exception:
                pass

        if not hasattr(self, "_initial_unlock_done"):
            self._initial_unlock_done = True
            return
