"""Launcher resolution and Windows auto-start registration."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Any, Dict, Optional

from .config import APP_NAME, PACKAGE_DIR, logger

if sys.platform == "win32":
    import winreg

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
_STARTUP_FLAGS = (
    ("hotkey", "--hotkey"),
    ("model", "--model"),
    ("effort", "--effort"),
    ("toast_level", "--toast-level"),
    ("agy_path", "--agy-path"),
)


def resolve_launcher_exe() -> str:
    """Locate ``pythonw.exe`` next to ``sys.executable`` (fallback ``sys.executable``)."""
    exe = Path(sys.executable).resolve()
    pythonw = exe.with_name("pythonw.exe")
    return str(pythonw if pythonw.exists() else exe)


def _startup_vbs_path() -> Path:
    base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    return (
        base / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        / f"{APP_NAME}.vbs"
    )


def build_startup_cmd(overrides: Optional[Dict[str, Any]] = None) -> str:
    """Build the command line used for Windows startup registration."""
    cmd = f'"{resolve_launcher_exe()}" "{PACKAGE_DIR / "__main__.py"}"'

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

    command = build_startup_cmd(overrides)
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
