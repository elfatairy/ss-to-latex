"""Screen-snipping equation-to-LaTeX utility powered by the Antigravity CLI."""

from __future__ import annotations

from .config import (
    APP_NAME,
    CONFIG_PATH,
    LOG_PATH,
    PROJECT_ROOT,
    Config,
    load_config,
    parse_toast_level,
    save_config,
)
from .hotkeys import parse_hotkey
from .ocr import clean_latex, image_to_latex, resolve_agy_cli
from .startup import register_startup, resolve_launcher_exe, unregister_startup
from .ui import SsToLatexApp
from .win32 import (
    SingleInstanceLock,
    capture_hotkey,
    copy_to_clipboard,
    get_virtual_screen_bounds,
)

__all__ = [
    "APP_NAME",
    "CONFIG_PATH",
    "LOG_PATH",
    "PROJECT_ROOT",
    "Config",
    "load_config",
    "save_config",
    "parse_hotkey",
    "parse_toast_level",
    "clean_latex",
    "image_to_latex",
    "resolve_agy_cli",
    "resolve_launcher_exe",
    "register_startup",
    "unregister_startup",
    "SingleInstanceLock",
    "capture_hotkey",
    "copy_to_clipboard",
    "get_virtual_screen_bounds",
    "SsToLatexApp",
]
