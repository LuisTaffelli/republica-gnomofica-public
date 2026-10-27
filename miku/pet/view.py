from __future__ import annotations

import sys
import tkinter as tk
from dataclasses import dataclass

from core.activity import ActivityEvent, ActivityState

from .controller import PetController


TRANSPARENT_COLOR = "#010203"


@dataclass(frozen=True, slots=True)
class VisualState:
    label: str
    symbol: str
    accent: str
    mouth: str = "smile"


VISUALS = {
    ActivityState.IDLE: VisualState("descansando", "·", "#71d7c2"),
    ActivityState.LISTENING: VisualState("escuchando", "♫", "#72c7ff"),
    ActivityState.THINKING: VisualState("pensando", "…", "#c7a0ff"),
    ActivityState.INSPECTING: VisualState("inspeccionando", "⌕", "#ffc878"),
    ActivityState.WAITING_APPROVAL: VisualState("esperando aprobación", "?", "#ffe46b"),
    ActivityState.ACTING: VisualState("actuando", "!", "#6ed5ff"),
    ActivityState.SPEAKING: VisualState("hablando", "♪", "#84e7a5"),
    ActivityState.ERROR: VisualState("error", "×", "#ff7f8a", mouth="sad"),
}


class PetView:
    """Vista pasiva: dibuja eventos y nunca llama al core."""

    def __init__(self, root: tk.Tk, controller: PetController, *, topmost: bool) -> None:
        self.root = root
        self.controller = controller
        self._closed = False
        self._drag_origin: tuple[int, int] | None = None
        self._transparent = self._configure_window(topmost)
        background = TRANSPARENT_COLOR if self._transparent else "#17202a"
        self.canvas = tk.Canvas(
            root, width=210, height=190, bg=background,
            highlightthickness=0, borderwidth=0,
        )
        self.canvas.pack()
        self.canvas.bind("<ButtonPress-1>", self._start_drag)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<Button-3>", lambda _event: self._close_visual())
        root.protocol("WM_DELETE_WINDOW", self._close_visual)
        controller.subscribe(self.render)
        self.render(ActivityEvent(controller.state))
        root.after(40, self._poll)

    def _configure_window(self, topmost: bool) -> bool:
        self.root.title("Miku Pet")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", topmost)
        if sys.platform != "win32":
            return False
        try:
            self.root.overrideredirect(True)
            self.root.configure(bg=TRANSPARENT_COLOR)
            self.root.wm_attributes("-transparentcolor", TRANSPARENT_COLOR)
            return True
        except tk.TclError:
            self.root.overrideredirect(False)
            self.root.configure(bg="#17202a")
            return False

    def render(self, event: ActivityEvent) -> None:
        visual = VISUALS[event.state]
        self.canvas.delete("miku")
        # Placeholder vectorial aislado: puede reemplazarse por sprites en la vista.
        self.canvas.create_polygon(
            48, 72, 18, 20, 74, 50, 136, 50, 192, 20, 162, 72,
            fill="#55d6c2", outline="#20343f", width=2, tags="miku",
        )
        self.canvas.create_oval(
            52, 42, 158, 148, fill="#ffe1dd", outline="#20343f", width=3,
            tags="miku",
        )
        self.canvas.create_oval(78, 82, 87, 94, fill="#20343f", outline="", tags="miku")
        self.canvas.create_oval(123, 82, 132, 94, fill="#20343f", outline="", tags="miku")
        if visual.mouth == "sad":
            self.canvas.create_arc(88, 105, 122, 128, start=20, extent=140, style="arc", width=2, tags="miku")
        else:
            self.canvas.create_arc(88, 96, 122, 121, start=200, extent=140, style="arc", width=2, tags="miku")
        self.canvas.create_oval(158, 48, 194, 84, fill=visual.accent, outline="#20343f", width=2, tags="miku")
        self.canvas.create_text(176, 66, text=visual.symbol, fill="#20343f", font=("Segoe UI", 17, "bold"), tags="miku")
        self.canvas.create_text(105, 168, text=visual.label, fill=visual.accent, font=("Segoe UI", 10, "bold"), tags="miku")

    def _poll(self) -> None:
        if self._closed:
            return
        self.controller.dispatch_pending()
        if self.controller.close_requested:
            self._close_visual()
            return
        self.root.after(40, self._poll)

    def _close_visual(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.controller.close_visual()
        self.root.destroy()

    def _start_drag(self, event: tk.Event) -> None:
        self._drag_origin = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _drag(self, event: tk.Event) -> None:
        if self._drag_origin is None:
            return
        offset_x, offset_y = self._drag_origin
        self.root.geometry(f"+{event.x_root - offset_x}+{event.y_root - offset_y}")


def run_pet_window(controller: PetController, *, topmost: bool = True) -> None:
    root = tk.Tk()
    try:
        PetView(root, controller, topmost=topmost)
        root.mainloop()
    finally:
        controller.close_visual()
        try:
            if root.winfo_exists():
                root.destroy()
        except tk.TclError:
            pass
