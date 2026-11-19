"""Screen-snipping equation-to-LaTeX utility powered by the Antigravity CLI (agy).

Auto-starts in the background on Windows, listens for a global hotkey
(default: Ctrl+Alt+L), lets the user drag-select a screen region, sends the
image to the ``agy`` CLI, cleans the returned LaTeX, and copies it to the
Windows clipboard.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from dataclasses import asdict, dataclass
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from types import SimpleNamespace
from typing import Any, Callable, Dict, Optional, Tuple

from PIL import Image, ImageGrab

if sys.platform == "win32":
    import winreg


APP_NAME = "ss-to-latex"
MUTEX_NAME = "Local\\ss-to-latex-mutex"
DEFAULT_HOTKEY = os.environ.get("SS_TO_LATEX_HOTKEY", "ctrl+alt+l")
DEFAULT_MODEL = os.environ.get("AGY_MODEL", "gemini-4-hydrogen-low")
DEFAULT_EFFORT = os.environ.get("AGY_EFFORT", "low")
DEFAULT_AGY_PATH = os.environ.get("AGY_CLI_PATH", "")

TOAST_LEVEL_ALIASES = {
    "0": 0, "none": 0, "silent": 0, "off": 0,
    "1": 1, "error": 1, "errors": 1,
    "2": 2, "result": 2, "minimal": 2,
    "3": 3, "all": 3, "full": 3,
}

TOAST_LEVEL_DESCRIPTIONS = {
    0: "None (0 toasts - silent)",
    1: "Errors only",
    2: "Result & errors only (1 toast per snip)",
    3: "All toasts (startup + converting + copied + errors)",
}

_TOAST_CATEGORIES = {
    0: frozenset(),
    1: frozenset({"error"}),
    2: frozenset({"result", "error"}),
    3: frozenset({"startup", "progress", "result", "error"}),
}


def parse_toast_level(value: Any) -> int:
    """Parse a toast level (0-3 or alias like 'none', 'errors', 'result', 'all')."""
    if isinstance(value, bool):
        raise ValueError(f"Invalid toast level: {value!r}")
    if isinstance(value, int):
        if 0 <= value <= 3:
            return value
        raise ValueError(f"Toast level must be between 0 and 3, got {value}.")
    key = str(value).strip().lower()
    if key in TOAST_LEVEL_ALIASES:
        return TOAST_LEVEL_ALIASES[key]
    raise ValueError(
        f"Invalid toast level '{value}'. Expected 0-3 or one of: none, errors, result, all."
    )


try:
    DEFAULT_TOAST_LEVEL = parse_toast_level(
        os.environ.get("SS_TO_LATEX_TOAST_LEVEL", "3")
    )
except ValueError:
    DEFAULT_TOAST_LEVEL = 3

CONFIG_PATH = Path(__file__).resolve().with_name("config.json")
LOG_PATH = Path(__file__).resolve().with_name("ss-to-latex.log")


def _configure_logging() -> logging.Logger:
    log = logging.getLogger(APP_NAME)
    if log.handlers:
        return log
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    try:
        fh = RotatingFileHandler(LOG_PATH, maxBytes=512 * 1024, backupCount=1, encoding="utf-8")
        fh.setFormatter(fmt)
        log.addHandler(fh)
    except OSError:
        pass
    if sys.stderr is not None:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        log.addHandler(sh)
    return log


logger = _configure_logging()


# ---------------------------------------------------------------------------
# Win32 bindings & process setup
# ---------------------------------------------------------------------------

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
ERROR_ALREADY_EXISTS = 183

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


def _init_win32() -> Optional[SimpleNamespace]:
    """Load and configure 64-bit-safe Win32 DLL bindings once."""
    if sys.platform != "win32":
        return None

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    s32 = ctypes.WinDLL("shell32", use_last_error=True)

    k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    k32.GlobalUnlock.restype = wintypes.BOOL
    k32.GlobalFree.argtypes = [ctypes.c_void_p]
    k32.GlobalFree.restype = ctypes.c_void_p
    k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    k32.CreateMutexW.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    k32.GetCurrentThreadId.argtypes = []
    k32.GetCurrentThreadId.restype = wintypes.DWORD

    u32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    ]
    u32.CreateWindowExW.restype = wintypes.HWND
    u32.DestroyWindow.argtypes = [wintypes.HWND]
    u32.DestroyWindow.restype = wintypes.BOOL
    u32.OpenClipboard.argtypes = [wintypes.HWND]
    u32.OpenClipboard.restype = wintypes.BOOL
    u32.EmptyClipboard.argtypes = []
    u32.EmptyClipboard.restype = wintypes.BOOL
    u32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
    u32.SetClipboardData.restype = ctypes.c_void_p
    u32.CloseClipboard.argtypes = []
    u32.CloseClipboard.restype = wintypes.BOOL
    u32.GetSystemMetrics.argtypes = [ctypes.c_int]
    u32.GetSystemMetrics.restype = ctypes.c_int
    u32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    u32.RegisterHotKey.restype = wintypes.BOOL
    u32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    u32.UnregisterHotKey.restype = wintypes.BOOL
    u32.GetMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
    ]
    u32.GetMessageW.restype = wintypes.BOOL
    u32.PostThreadMessageW.argtypes = [
        wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    ]
    u32.PostThreadMessageW.restype = wintypes.BOOL

    s32.SetCurrentProcessExplicitAppUserModelID.argtypes = [wintypes.LPCWSTR]
    s32.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long

    return SimpleNamespace(kernel32=k32, user32=u32, shell32=s32)


_WIN32 = _init_win32()


def _enable_dpi_awareness() -> None:
    """Enable per-monitor DPI awareness before Tkinter or ImageGrab runs."""
    if _WIN32 is None:
        return
    attempts = (
        lambda: ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)),
        lambda: ctypes.windll.shcore.SetProcessDpiAwareness(2),
        lambda: ctypes.windll.user32.SetProcessDPIAware(),
    )
    for attempt in attempts:
        try:
            attempt()
            return
        except (AttributeError, OSError):
            continue


def _set_process_identity() -> None:
    """Set the Windows AppUserModelID so the process is identified as ss-to-latex."""
    if _WIN32 is None:
        return
    try:
        _WIN32.shell32.SetCurrentProcessExplicitAppUserModelID(APP_NAME)
    except (AttributeError, OSError):
        pass


_enable_dpi_awareness()
_set_process_identity()


class SingleInstanceLock:
    """Context manager holding a named Win32 mutex to prevent duplicate daemons."""

    def __init__(self, name: str = MUTEX_NAME) -> None:
        self.name = name
        self._handle: Optional[int] = None
        self.acquired = True

    def __enter__(self) -> SingleInstanceLock:
        if _WIN32 is None:
            return self
        handle = _WIN32.kernel32.CreateMutexW(None, False, self.name)
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            if handle:
                _WIN32.kernel32.CloseHandle(handle)
            self.acquired = False
            return self
        self._handle = int(handle) if handle else None
        return self

    def __exit__(self, *_exc: object) -> None:
        if _WIN32 is not None and self._handle:
            _WIN32.kernel32.CloseHandle(self._handle)
            self._handle = None


# ---------------------------------------------------------------------------
# Hotkeys, clipboard & screen metrics
# ---------------------------------------------------------------------------

_SPECIAL_VK_MAP = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "return": 0x0D,
    "esc": 0x1B, "escape": 0x1B, "space": 0x20,
    "pageup": 0x21, "pgup": 0x21, "pagedown": 0x22, "pgdn": 0x22,
    "end": 0x23, "home": 0x24,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "printscreen": 0x2C, "prtsc": 0x2C, "snapshot": 0x2C,
    "insert": 0x2D, "ins": 0x2D, "delete": 0x2E, "del": 0x2E,
    ";": 0xBA, "semicolon": 0xBA, "=": 0xBB, "equal": 0xBB, "equals": 0xBB,
    ",": 0xBC, "comma": 0xBC, "-": 0xBD, "minus": 0xBD,
    ".": 0xBE, "period": 0xBE, "/": 0xBF, "slash": 0xBF,
    "`": 0xC0, "grave": 0xC0, "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE,
}

_MODIFIER_MAP = {
    "alt": MOD_ALT, "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "shift": MOD_SHIFT, "win": MOD_WIN, "windows": MOD_WIN,
    "super": MOD_WIN, "meta": MOD_WIN,
}


def parse_hotkey(hotkey_str: str) -> Tuple[int, int]:
    """Parse a shortcut like ``'ctrl+alt+l'`` into ``(modifiers, vk_code)``."""
    parts = [p.strip().lower() for p in hotkey_str.split("+") if p.strip()]
    if not parts:
        raise ValueError("Hotkey string cannot be empty.")

    modifiers = MOD_NOREPEAT
    key_part: Optional[str] = None
    for part in parts:
        if part in _MODIFIER_MAP:
            modifiers |= _MODIFIER_MAP[part]
        elif key_part is None:
            key_part = part
        else:
            raise ValueError(
                f"Invalid hotkey '{hotkey_str}': multiple non-modifier keys "
                f"('{key_part}' and '{part}')."
            )

    if key_part is None:
        raise ValueError(f"Hotkey '{hotkey_str}' is missing a main key.")

    if len(key_part) == 1 and ("a" <= key_part <= "z" or "0" <= key_part <= "9"):
        vk = ord(key_part.upper())
    elif key_part.startswith("f") and key_part[1:].isdigit():
        fn = int(key_part[1:])
        if not 1 <= fn <= 24:
            raise ValueError(f"Unsupported function key '{key_part}'.")
        vk = 0x70 + (fn - 1)
    elif key_part in _SPECIAL_VK_MAP:
        vk = _SPECIAL_VK_MAP[key_part]
    else:
        raise ValueError(f"Unrecognized key '{key_part}' in hotkey '{hotkey_str}'.")

    return modifiers, vk


def _win32_copy_to_clipboard(text: str, hwnd: Optional[int] = None) -> None:
    """Write Unicode text directly to the Win32 clipboard."""
    assert _WIN32 is not None
    k32, u32 = _WIN32.kernel32, _WIN32.user32

    encoded = text.encode("utf-16-le") + b"\x00\x00"
    h_mem = k32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
    if not h_mem:
        raise ctypes.WinError(ctypes.get_last_error())

    owns_mem = True
    temp_hwnd = None
    try:
        ptr = k32.GlobalLock(h_mem)
        if not ptr:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            ctypes.memmove(ptr, encoded, len(encoded))
        finally:
            k32.GlobalUnlock(h_mem)

        owner_hwnd = hwnd
        if not owner_hwnd:
            temp_hwnd = u32.CreateWindowExW(
                0, "STATIC", "", 0, 0, 0, 0, 0, None, None, None, None
            )
            owner_hwnd = temp_hwnd

        opened = False
        for _ in range(8):
            if u32.OpenClipboard(owner_hwnd):
                opened = True
                break
            time.sleep(0.02)
        if not opened:
            raise ctypes.WinError(ctypes.get_last_error())

        try:
            if not u32.EmptyClipboard():
                raise ctypes.WinError(ctypes.get_last_error())
            if not u32.SetClipboardData(CF_UNICODETEXT, h_mem):
                raise ctypes.WinError(ctypes.get_last_error())
            owns_mem = False
        finally:
            u32.CloseClipboard()
    finally:
        if temp_hwnd:
            u32.DestroyWindow(temp_hwnd)
        if owns_mem:
            k32.GlobalFree(h_mem)


def copy_to_clipboard(text: str, tk_root: Optional[tk.Tk] = None) -> None:
    """Copy ``text`` to the Windows clipboard, with a Tkinter fallback."""
    if _WIN32 is not None:
        hwnd = int(tk_root.winfo_id()) if tk_root is not None else None
        try:
            _win32_copy_to_clipboard(text, hwnd=hwnd)
            return
        except OSError as exc:
            logger.warning("Win32 clipboard copy failed (%s); falling back to Tk.", exc)

    if tk_root is not None:
        tk_root.clipboard_clear()
        tk_root.clipboard_append(text)
        tk_root.update()
        return

    raise RuntimeError("Failed to copy text to clipboard.")


def get_virtual_screen_bounds(tk_root: Optional[tk.Tk] = None) -> Tuple[int, int, int, int]:
    """Return ``(left, top, width, height)`` covering all monitors."""
    if _WIN32 is not None:
        sm = _WIN32.user32.GetSystemMetrics
        vx, vy = int(sm(SM_XVIRTUALSCREEN)), int(sm(SM_YVIRTUALSCREEN))
        vw, vh = int(sm(SM_CXVIRTUALSCREEN)), int(sm(SM_CYVIRTUALSCREEN))
        if vw > 0 and vh > 0:
            return vx, vy, vw, vh
    if tk_root is not None:
        return 0, 0, tk_root.winfo_screenwidth(), tk_root.winfo_screenheight()
    return 0, 0, 1920, 1080


# ---------------------------------------------------------------------------
# Config persistence
# ---------------------------------------------------------------------------


@dataclass
class Config:
    """Validated user configuration for ss-to-latex."""

    hotkey: str = DEFAULT_HOTKEY
    model: str = DEFAULT_MODEL
    effort: str = DEFAULT_EFFORT
    toast_level: int = DEFAULT_TOAST_LEVEL
    agy_path: str = DEFAULT_AGY_PATH

    def validate(self) -> Config:
        self.hotkey = str(self.hotkey).strip()
        parse_hotkey(self.hotkey)
        self.model = str(self.model).strip()
        self.effort = str(self.effort).strip()
        self.toast_level = parse_toast_level(self.toast_level)
        self.agy_path = str(self.agy_path).strip()
        return self

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def load_config(config_path: Path = CONFIG_PATH) -> Config:
    """Load configuration from ``config_path``, falling back to defaults per field."""
    cfg = Config()
    if not config_path.is_file():
        return cfg

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read config '%s' (%s); using defaults.", config_path, exc)
        return cfg

    if not isinstance(raw, dict):
        logger.warning("Config '%s' must contain a JSON object; using defaults.", config_path)
        return cfg

    if isinstance(raw.get("hotkey"), str):
        try:
            parse_hotkey(raw["hotkey"])
            cfg.hotkey = raw["hotkey"].strip()
        except ValueError as exc:
            logger.warning("Ignoring invalid hotkey in config: %s", exc)

    for key in ("model", "effort", "agy_path"):
        if isinstance(raw.get(key), str):
            setattr(cfg, key, raw[key].strip())

    if "toast_level" in raw:
        try:
            cfg.toast_level = parse_toast_level(raw["toast_level"])
        except ValueError as exc:
            logger.warning("Ignoring invalid toast_level in config: %s", exc)

    return cfg


def save_config(cfg: Config, config_path: Path = CONFIG_PATH) -> Path:
    """Validate and save configuration settings as UTF-8 JSON."""
    cfg.validate()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(cfg.to_dict(), indent=2) + "\n", encoding="utf-8")
    return config_path


# ---------------------------------------------------------------------------
# Auto-start registration
# ---------------------------------------------------------------------------

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
_STARTUP_FLAGS = (
    ("hotkey", "--hotkey"),
    ("model", "--model"),
    ("effort", "--effort"),
    ("toast_level", "--toast-level"),
    ("agy_path", "--agy-path"),
)


def resolve_launcher_exe() -> str:
    """Locate ``ss-to-latex.exe``, ``pythonw.exe``, or ``sys.executable``."""
    named_exe = Path(__file__).resolve().with_name(f"{APP_NAME}.exe")
    if named_exe.exists():
        return str(named_exe)
    exe = Path(sys.executable).resolve()
    pythonw = exe.with_name("pythonw.exe")
    return str(pythonw if pythonw.exists() else exe)


def _startup_vbs_path() -> Path:
    base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    return (
        base / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        / f"{APP_NAME}.vbs"
    )


def _build_startup_cmd(overrides: Optional[Dict[str, Any]] = None) -> str:
    cmd = f'"{resolve_launcher_exe()}" "{Path(__file__).resolve()}"'
    if not overrides:
        return cmd
    for key, flag in _STARTUP_FLAGS:
        val = overrides.get(key)
        if val is not None:
            cmd += f" {flag} {val}" if isinstance(val, int) else f' {flag} "{val}"'
    return cmd


def register_startup(overrides: Optional[Dict[str, Any]] = None) -> str:
    """Register auto-start in HKCU Run, falling back to a Startup folder VBS."""
    if sys.platform != "win32":
        raise RuntimeError("Windows auto-start is only supported on Windows.")

    command = _build_startup_cmd(overrides)
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command)
        logger.info("Registered Windows startup entry in HKCU\\%s", RUN_KEY_PATH)
        return f"Registry (HKCU\\{RUN_KEY_PATH}\\{APP_NAME})"
    except OSError as exc:
        logger.warning(
            "Registry startup registration failed (%s); falling back to Startup folder.",
            exc,
        )

    vbs_path = _startup_vbs_path()
    vbs_path.parent.mkdir(parents=True, exist_ok=True)
    escaped = command.replace('"', '""')
    vbs_path.write_text(
        f'Set WshShell = CreateObject("WScript.Shell")\nWshShell.Run "{escaped}", 0, False\n',
        encoding="utf-8",
    )
    logger.info("Registered Windows startup VBS at %s", vbs_path)
    return str(vbs_path)


def unregister_startup() -> bool:
    """Remove any registered Windows startup entry (Registry and Startup VBS)."""
    removed = False
    if sys.platform == "win32":
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, APP_NAME)
            removed = True
            logger.info("Removed startup registry entry '%s'.", APP_NAME)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("Could not remove registry startup entry: %s", exc)

    vbs_path = _startup_vbs_path()
    if vbs_path.exists():
        try:
            vbs_path.unlink()
            removed = True
            logger.info("Removed startup VBS '%s'.", vbs_path)
        except OSError as exc:
            logger.warning("Could not remove startup VBS '%s': %s", vbs_path, exc)
    return removed


# ---------------------------------------------------------------------------
# LaTeX cleanup & agy CLI client
# ---------------------------------------------------------------------------

_THINK_PAIR_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_THINK_UNCLOSED_RE = re.compile(r"<think>.*$", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(
    r"```(?:latex|tex|math)?\s*\n?(.*?)\n?```",
    re.DOTALL | re.IGNORECASE,
)
_OUTER_DELIMITERS = (("$$", "$$"), (r"\[", r"\]"), (r"\(", r"\)"), ("$", "$"))


def _has_interior_unescaped_dollar(text: str) -> bool:
    return any(
        ch == "$" and (idx == 0 or text[idx - 1] != "\\")
        for idx, ch in enumerate(text)
    )


def clean_latex(raw: str) -> str:
    """Strip reasoning tags, markdown code fences, and outer math delimiters."""
    text = (raw or "").strip()
    if not text:
        return ""

    text = _THINK_UNCLOSED_RE.sub("", _THINK_PAIR_RE.sub("", text)).strip()
    fence_match = _FENCE_RE.search(text)
    if fence_match:
        text = fence_match.group(1).strip()

    while text:
        for open_d, close_d in _OUTER_DELIMITERS:
            if not (
                text.startswith(open_d)
                and text.endswith(close_d)
                and len(text) >= len(open_d) + len(close_d)
            ):
                continue
            inner = text[len(open_d) : len(text) - len(close_d)].strip()
            if open_d == "$$" and "$$" in inner:
                continue
            if open_d == "$" and (
                text.startswith("$$") or _has_interior_unescaped_dollar(inner)
            ):
                continue
            text = inner
            break
        else:
            break

    return text.strip()


def resolve_agy_cli(agy_path: str = DEFAULT_AGY_PATH) -> str:
    """Locate the ``agy`` CLI executable."""
    if agy_path:
        explicit = Path(agy_path)
        if explicit.is_file():
            return str(explicit)
        found = shutil.which(agy_path)
        if found:
            return found
        raise RuntimeError(f"Configured agy CLI was not found: '{agy_path}'.")

    for candidate in ("agy", "agy.exe"):
        found = shutil.which(candidate)
        if found:
            return found

    default_exe = Path.home() / ".gemini" / "bin" / "agy.exe"
    if default_exe.is_file():
        return str(default_exe)

    raise RuntimeError(
        "Could not find 'agy' CLI on PATH or at ~/.gemini/bin/agy.exe. "
        "Pass --agy-path or set AGY_CLI_PATH."
    )


def image_to_latex(
    image: Image.Image,
    config: Optional[Config] = None,
    *,
    model: Optional[str] = None,
    effort: Optional[str] = None,
    agy_path: Optional[str] = None,
    timeout: int = 90,
) -> str:
    """Save a PIL Image to a temp PNG and convert it to LaTeX via ``agy`` CLI."""
    cfg = config or Config()
    eff_model = cfg.model if model is None else model
    eff_effort = cfg.effort if effort is None else effort
    eff_agy_path = cfg.agy_path if agy_path is None else agy_path

    exe = resolve_agy_cli(eff_agy_path)
    rgb_image = image.convert("RGB") if image.mode != "RGB" else image

    with tempfile.TemporaryDirectory(prefix="ss_to_latex_") as temp_dir:
        img_path = Path(temp_dir) / "equation.png"
        rgb_image.save(img_path, format="PNG")

        prompt = (
            f"Read the image file at '{img_path}' and convert the mathematical "
            "equation shown in it into clean, valid LaTeX code. "
            "Output ONLY the raw LaTeX expression itself: no markdown code "
            "fences, no surrounding $ or $$ or \\[ \\] delimiters, and no "
            "explanations or commentary."
        )
        cmd = [
            exe, "-p", prompt,
            "--output-format", "text",
            "--add-dir", temp_dir,
            "--dangerously-skip-permissions",
            "--disable-slash-commands",
        ]
        if eff_model:
            cmd.extend(["--model", eff_model])
        if eff_effort:
            cmd.extend(["--effort", eff_effort])

        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                creationflags=flags,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"agy CLI timed out after {timeout}s.") from exc
        except OSError as exc:
            raise RuntimeError(f"Failed to launch agy CLI ({exe}): {exc}") from exc

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            f"agy CLI exited with code {proc.returncode}"
            + (f": {detail}" if detail else ".")
        )

    latex = clean_latex(proc.stdout or "")
    if not latex:
        raise RuntimeError("agy CLI returned an empty LaTeX response.")
    return latex


# ---------------------------------------------------------------------------
# Tkinter snipping overlay & floating toast UI
# ---------------------------------------------------------------------------


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
        self._hotkey_thread_id: Optional[int] = None
        self._stop_event = threading.Event()

    # -- Toast notifications --

    def _should_show_toast(self, category: str, is_error: bool) -> bool:
        allowed = _TOAST_CATEGORIES.get(self.config.toast_level, _TOAST_CATEGORIES[3])
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

    # -- Event loop & hotkey thread --

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
        modifiers, vk = parse_hotkey(self.config.hotkey)

        def hotkey_loop() -> None:
            if _WIN32 is None:
                self.ui_queue.put(
                    ("TOAST", ("Unsupported OS", "Global hotkeys require Windows.", True))
                )
                return

            u32, k32 = _WIN32.user32, _WIN32.kernel32
            self._hotkey_thread_id = int(k32.GetCurrentThreadId())
            hotkey_id = 1

            if not u32.RegisterHotKey(None, hotkey_id, modifiers, vk):
                err = ctypes.get_last_error()
                logger.error("RegisterHotKey failed for '%s' (code %s).", self.config.hotkey, err)
                self.ui_queue.put((
                    "TOAST",
                    ("Hotkey registration failed", f"Could not bind '{self.config.hotkey}'. Another app may use it.", True),
                ))
                return

            logger.info("Listening for global hotkey '%s'.", self.config.hotkey)
            msg = wintypes.MSG()
            try:
                while not self._stop_event.is_set():
                    if u32.GetMessageW(ctypes.byref(msg), None, 0, 0) <= 0:
                        break
                    if msg.message == WM_HOTKEY and msg.wParam == hotkey_id:
                        self.ui_queue.put(("CAPTURE", None))
                    u32.TranslateMessage(ctypes.byref(msg))
                    u32.DispatchMessageW(ctypes.byref(msg))
            finally:
                u32.UnregisterHotKey(None, hotkey_id)

        threading.Thread(target=hotkey_loop, daemon=True).start()

    def quit(self) -> None:
        self._stop_event.set()
        if _WIN32 is not None and self._hotkey_thread_id is not None:
            try:
                _WIN32.user32.PostThreadMessageW(self._hotkey_thread_id, WM_QUIT, 0, 0)
            except OSError:
                pass
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


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Snip an equation from the screen and copy its LaTeX via agy CLI."
    )
    parser.add_argument(
        "--hotkey", default=None,
        help=f"Global shortcut to trigger capture (default from config.json or {DEFAULT_HOTKEY}).",
    )
    parser.add_argument(
        "--model", default=None,
        help=f"Model name to pass to `agy --model` (default from config.json or {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--effort", default=None,
        help=f"Reasoning effort to pass to `agy --effort` (default from config.json or {DEFAULT_EFFORT}).",
    )
    parser.add_argument(
        "--toast-level", default=None,
        help=(
            "Toast notifications to show: 0/none (silent), 1/errors (errors only), "
            f"2/result (result & errors), 3/all (default from config.json or {DEFAULT_TOAST_LEVEL})."
        ),
    )
    parser.add_argument(
        "--agy-path", default=None,
        help="Optional path to the `agy` CLI executable.",
    )
    parser.add_argument(
        "--install-startup", action="store_true",
        help="Register Windows auto-start and exit.",
    )
    parser.add_argument(
        "--uninstall-startup", action="store_true",
        help="Remove Windows auto-start registration and exit.",
    )
    parser.add_argument(
        "--no-autostart", action="store_true",
        help="Do not register Windows auto-start when starting the background listener.",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Trigger one screen capture immediately and exit.",
    )
    return parser


def main(argv: Optional[list] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    cfg = load_config()

    overrides: Dict[str, Any] = {}
    try:
        if args.toast_level is not None:
            overrides["toast_level"] = parse_toast_level(args.toast_level)
        for key in ("hotkey", "model", "effort", "agy_path"):
            val = getattr(args, key)
            if val is not None:
                overrides[key] = val
        for key, val in overrides.items():
            setattr(cfg, key, val)
        cfg.validate()
    except ValueError as exc:
        logger.error("%s", exc)
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if args.uninstall_startup:
        removed = unregister_startup()
        print("Removed startup entry." if removed else "No startup entry found.")
        return 0

    if args.install_startup:
        print(f"Auto-start registered at: {register_startup(overrides)}")
        return 0

    if args.once:
        SsToLatexApp(cfg, once=True).run()
        return 0

    with SingleInstanceLock() as lock:
        if not lock.acquired:
            logger.info("Another instance of %s is already running; exiting.", APP_NAME)
            return 0

        if not args.no_autostart:
            try:
                logger.info("Auto-start ensured at: %s", register_startup(overrides))
            except Exception as exc:
                logger.warning("Could not ensure auto-start: %s", exc)

        SsToLatexApp(cfg).run()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
