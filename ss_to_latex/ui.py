"""Tkinter screen-snipping overlay, toast notifications, and UI event loop."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from typing import Any, Callable, Dict, Optional, Tuple

from PIL import Image, ImageGrab

from .config import APP_NAME, Config, TOAST_CATEGORIES, logger
from .ocr import image_to_latex
from .win32 import HotkeyListener, copy_to_clipboard, get_virtual_screen_bounds


def _safe_tk(fn: Callable[[], Any]) -> None:
    try:
        fn()
    except tk.TclError:
        pass


def _safe_destroy(widget: Optional[tk.Misc]) -> None:
    if widget is not None:
        _safe_tk(widget.destroy)


def _crop_region(
    screenshot: Image.Image,
    bounds: Tuple[int, int, int, int],
    selection: Tuple[int, int, int, int],
) -> Optional[Image.Image]:
    """Map canvas selection coordinates to screenshot pixels and crop."""
    _, _, vw, vh = bounds
    x1, x2 = sorted((selection[0], selection[2]))
    y1, y2 = sorted((selection[1], selection[3]))
    if (x2 - x1) < 5 or (y2 - y1) < 5:
        return None

    sx = screenshot.width / float(vw) if vw > 0 else 1.0
    sy = screenshot.height / float(vh) if vh > 0 else 1.0
    px1 = max(0, min(screenshot.width, int(round(x1 * sx))))
    py1 = max(0, min(screenshot.height, int(round(y1 * sy))))
    px2 = max(0, min(screenshot.width, int(round(x2 * sx))))
    py2 = max(0, min(screenshot.height, int(round(y2 * sy))))
    if px2 <= px1 or py2 <= py1:
        return None
    return screenshot.crop((px1, py1, px2, py2))


class SsToLatexApp:
    """Main Tkinter UI and background worker coordinator."""

    def __init__(self, config: Optional[Config] = None, once: bool = False) -> None:
        self.config = (config or Config()).validate()
        self.once = once

        self.ui_queue: "queue.Queue[Tuple[str, object]]" = queue.Queue()
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.withdraw()

        self._overlay: Optional[tk.Toplevel] = None
        self._toast: Optional[tk.Toplevel] = None
        self._toast_after_id: Optional[str] = None
        self._hotkey_listener: Optional[HotkeyListener] = None
        self._stop_event = threading.Event()

    # -- Toast notifications --

    def _should_show_toast(self, category: str, is_error: bool) -> bool:
        allowed = TOAST_CATEGORIES.get(self.config.toast_level, TOAST_CATEGORIES[3])
        return ("error" in allowed if is_error else False) or (category in allowed)

    def show_toast(
        self,
        title: str,
        message: str,
        duration_ms: int = 3500,
        is_error: bool = False,
        category: str = "result",
    ) -> None:
        """Display a small floating notification in the bottom-right corner."""
        if not self._should_show_toast(category, is_error):
            if is_error or category in ("result", "error"):
                self._dismiss_toast()
            return

        self._dismiss_toast()

        toast = tk.Toplevel(self.root)
        toast.withdraw()
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)

        accent = "#ff5252" if is_error else "#00e5ff"
        bg, fg, sub_fg = "#1b1e24", "#f4f6f8", "#b8c0cc"

        frame = tk.Frame(
            toast, bg=bg, highlightbackground=accent, highlightthickness=2, padx=14, pady=10
        )
        frame.pack(fill="both", expand=True)

        title_lbl = tk.Label(
            frame, text=title, bg=bg, fg=accent,
            font=("Segoe UI", 10, "bold"), anchor="w", justify="left",
        )
        title_lbl.pack(fill="x")

        widgets: list = [frame, title_lbl]
        if message:
            snippet = message if len(message) <= 220 else message[:217] + "..."
            msg_lbl = tk.Label(
                frame,
                text=snippet,
                bg=bg,
                fg=sub_fg if is_error else fg,
                font=("Segoe UI", 9) if is_error else ("Consolas", 9),
                anchor="w",
                justify="left",
                wraplength=340,
            )
            msg_lbl.pack(fill="x", pady=(4, 0))
            widgets.append(msg_lbl)

        for widget in widgets:
            widget.bind("<Button-1>", lambda _e: self._dismiss_toast())

        toast.update_idletasks()
        width = max(260, min(380, toast.winfo_reqwidth()))
        height = toast.winfo_reqheight()
        pos_x = self.root.winfo_screenwidth() - width - 20
        pos_y = self.root.winfo_screenheight() - height - 60

        toast.geometry(f"{width}x{height}+{pos_x}+{pos_y}")
        toast.deiconify()
        toast.lift()

        self._toast = toast
        if duration_ms > 0:
            self._toast_after_id = self.root.after(duration_ms, self._dismiss_toast)

    def _dismiss_toast(self) -> None:
        if self._toast_after_id is not None:
            _safe_tk(lambda aid=self._toast_after_id: self.root.after_cancel(aid))
            self._toast_after_id = None
        _safe_destroy(self._toast)
        self._toast = None

    # -- Screen snipping overlay --

    def start_capture(self) -> None:
        """Capture the screen and present a drag-select overlay."""
        if self._overlay is not None:
            return

        self._dismiss_toast()
        self.root.update_idletasks()

        try:
            screenshot = ImageGrab.grab(all_screens=True)
        except Exception as exc:
            logger.exception("Screen capture failed.")
            self.show_toast("Capture failed", str(exc), 5000, is_error=True, category="error")
            if self.once:
                self.root.after(2000 if self.config.toast_level >= 1 else 200, self.quit)
            return

        bounds = get_virtual_screen_bounds(self.root)
        vx, vy, vw, vh = bounds

        overlay = tk.Toplevel(self.root)
        overlay.withdraw()
        overlay.overrideredirect(True)
        overlay.attributes("-topmost", True)
        overlay.attributes("-alpha", 0.35)
        overlay.configure(bg="black")
        overlay.geometry(f"{vw}x{vh}+{vx}+{vy}")

        canvas = tk.Canvas(overlay, bg="black", highlightthickness=0, cursor="crosshair")
        canvas.pack(fill="both", expand=True)
        canvas.create_text(
            (self.root.winfo_screenwidth() // 2) - vx,
            max(32, 32 - vy),
            text="Drag to select equation  •  Esc or Right-click to cancel",
            fill="#ffffff",
            font=("Segoe UI", 12, "bold"),
        )

        state: Dict[str, Any] = {"x0": 0, "y0": 0, "rect": None, "active": False}

        def close_overlay() -> None:
            if self._overlay is not None:
                _safe_tk(self._overlay.grab_release)
                _safe_destroy(self._overlay)
                self._overlay = None

        def on_cancel(_event: object = None) -> None:
            close_overlay()
            if self.once:
                self.quit()

        def on_press(event: tk.Event) -> None:
            state["x0"], state["y0"], state["active"] = event.x, event.y, True
            if state["rect"] is not None:
                canvas.delete(state["rect"])
            state["rect"] = canvas.create_rectangle(
                event.x, event.y, event.x, event.y,
                outline="#00e5ff", width=2, fill="#112233",
            )

        def on_drag(event: tk.Event) -> None:
            if state["active"] and state["rect"] is not None:
                canvas.coords(state["rect"], state["x0"], state["y0"], event.x, event.y)

        def on_release(event: tk.Event) -> None:
            if not state["active"]:
                return
            state["active"] = False
            close_overlay()
            self.root.update_idletasks()

            cropped = _crop_region(screenshot, bounds, (state["x0"], state["y0"], event.x, event.y))
            if cropped is None:
                if self.once:
                    self.quit()
                return
            self._process_equation_async(cropped)

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)
        canvas.bind("<ButtonPress-3>", on_cancel)
        overlay.bind("<Escape>", on_cancel)

        self._overlay = overlay
        overlay.deiconify()
        overlay.lift()
        overlay.focus_force()
        overlay.grab_set()

    def _process_equation_async(self, cropped: Image.Image) -> None:
        target = f"agy ({self.config.model})" if self.config.model else "agy CLI"
        self.show_toast("Converting equation...", f"Sending image to {target}", 0, category="progress")

        def worker() -> None:
            try:
                latex = image_to_latex(cropped, self.config)
                self.ui_queue.put(("RESULT", latex))
            except Exception as exc:
                logger.exception("Equation conversion failed.")
                self.ui_queue.put(("ERROR", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    # -- Event loop & hotkey handling --

    def _on_result(self, latex: str) -> None:
        try:
            copy_to_clipboard(latex, self.root)
            logger.info("Copied LaTeX (%d chars) to clipboard.", len(latex))
            self.show_toast("Copied LaTeX to clipboard!", latex, 4000, category="result")
        except Exception as exc:
            logger.exception("Clipboard copy failed.")
            self.show_toast("Clipboard error", str(exc), 5000, is_error=True, category="error")
        if self.once:
            self.root.after(2200 if self.config.toast_level >= 2 else 200, self.quit)

    def _on_error(self, message: str) -> None:
        self.show_toast("Conversion failed", message, 5500, is_error=True, category="error")
        if self.once:
            self.root.after(3000 if self.config.toast_level >= 1 else 200, self.quit)

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == "CAPTURE":
                    self.start_capture()
                elif kind == "RESULT":
                    self._on_result(str(payload))
                elif kind == "ERROR":
                    self._on_error(str(payload))
                elif kind == "TOAST":
                    title, msg, is_err = payload  # type: ignore[misc]
                    self.show_toast(
                        str(title), str(msg), 4500,
                        is_error=bool(is_err),
                        category="error" if is_err else "result",
                    )
                elif kind == "QUIT":
                    self.quit()
                    return
        except queue.Empty:
            pass

        if not self._stop_event.is_set():
            self.root.after(50, self._poll_queue)

    def _start_hotkey_listener(self) -> None:
        self._hotkey_listener = HotkeyListener(
            self.config.hotkey,
            on_trigger=lambda: self.ui_queue.put(("CAPTURE", None)),
            on_error=lambda msg: self.ui_queue.put(
                ("TOAST", ("Hotkey registration failed", msg, True))
            ),
        )
        self._hotkey_listener.start()

    def quit(self) -> None:
        self._stop_event.set()
        if self._hotkey_listener is not None:
            self._hotkey_listener.stop()
        self._dismiss_toast()
        _safe_tk(self.root.quit)
        _safe_destroy(self.root)

    def run(self) -> None:
        if self.once:
            self.root.after(100, self.start_capture)
        else:
            self._start_hotkey_listener()
            target = f"agy ({self.config.model})" if self.config.model else "agy CLI"
            self.show_toast(
                f"{APP_NAME} ready",
                f"Press {self.config.hotkey.upper()} to snip an equation ({target})",
                3000,
                category="startup",
            )
        self.root.after(50, self._poll_queue)
        self.root.mainloop()
