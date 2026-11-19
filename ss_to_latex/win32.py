"""Windows API bindings, DPI/identity setup, clipboard, screen bounds, and hotkeys."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys
import threading
import time
import tkinter as tk
from types import SimpleNamespace
from typing import Callable, Optional, Tuple

from .config import APP_NAME, MUTEX_NAME, logger
from .hotkeys import (
    BARE_ALLOWED_VKS,
    MOD_ALT,
    MOD_CONTROL,
    MOD_SHIFT,
    MOD_WIN,
    MODIFIER_VKS,
    VK_CONTROL,
    VK_ESCAPE,
    VK_LCONTROL,
    VK_LMENU,
    VK_LSHIFT,
    VK_LWIN,
    VK_MENU,
    VK_RCONTROL,
    VK_RMENU,
    VK_RSHIFT,
    VK_RWIN,
    VK_SHIFT,
    format_hotkey,
    parse_hotkey,
    vk_to_key_name,
)

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
ERROR_ALREADY_EXISTS = 183

WH_KEYBOARD_LL = 13
STD_INPUT_HANDLE = -10
ENABLE_PROCESSED_INPUT = 0x0001
ENABLE_LINE_INPUT = 0x0002
ENABLE_ECHO_INPUT = 0x0004

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

if sys.platform == "win32":
    LRESULT = ctypes.c_ssize_t
    HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

    class _KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("vkCode", wintypes.DWORD),
            ("scanCode", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]


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
    k32.GetStdHandle.argtypes = [wintypes.DWORD]
    k32.GetStdHandle.restype = wintypes.HANDLE
    k32.FlushConsoleInputBuffer.argtypes = [wintypes.HANDLE]
    k32.FlushConsoleInputBuffer.restype = wintypes.BOOL
    k32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.GetConsoleMode.restype = wintypes.BOOL
    k32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.SetConsoleMode.restype = wintypes.BOOL

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
    u32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    u32.GetAsyncKeyState.restype = ctypes.c_short
    u32.SetWindowsHookExW.argtypes = [
        ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD,
    ]
    u32.SetWindowsHookExW.restype = ctypes.c_void_p
    u32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
    u32.UnhookWindowsHookEx.restype = wintypes.BOOL
    u32.CallNextHookEx.argtypes = [
        ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM,
    ]
    u32.CallNextHookEx.restype = LRESULT

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


class HotkeyListener:
    """Background daemon thread listening for a single Win32 global hotkey."""

    def __init__(
        self,
        hotkey: str,
        on_trigger: Callable[[], None],
        on_error: Callable[[str], None],
    ) -> None:
        self.hotkey = hotkey
        self._on_trigger = on_trigger
        self._on_error = on_error
        self._stop_event = threading.Event()
        self._thread_id: Optional[int] = None

    def start(self) -> None:
        modifiers, vk = parse_hotkey(self.hotkey)
        threading.Thread(
            target=self._run, args=(modifiers, vk), daemon=True
        ).start()

    def _run(self, modifiers: int, vk: int) -> None:
        if _WIN32 is None:
            self._on_error("Global hotkeys require Windows.")
            return

        u32, k32 = _WIN32.user32, _WIN32.kernel32
        self._thread_id = int(k32.GetCurrentThreadId())
        hotkey_id = 1

        if not u32.RegisterHotKey(None, hotkey_id, modifiers, vk):
            err = ctypes.get_last_error()
            logger.error("RegisterHotKey failed for '%s' (code %s).", self.hotkey, err)
            self._on_error(f"Could not bind '{self.hotkey}'. Another app may use it.")
            return

        logger.info("Listening for global hotkey '%s'.", self.hotkey)
        msg = wintypes.MSG()
        try:
            while not self._stop_event.is_set():
                if u32.GetMessageW(ctypes.byref(msg), None, 0, 0) <= 0:
                    break
                if msg.message == WM_HOTKEY and msg.wParam == hotkey_id:
                    self._on_trigger()
                u32.TranslateMessage(ctypes.byref(msg))
                u32.DispatchMessageW(ctypes.byref(msg))
        finally:
            u32.UnregisterHotKey(None, hotkey_id)

    def stop(self) -> None:
        self._stop_event.set()
        if _WIN32 is not None and self._thread_id is not None:
            try:
                _WIN32.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            except OSError:
                pass


def _is_key_down(vk: int) -> bool:
    assert _WIN32 is not None
    return bool(_WIN32.user32.GetAsyncKeyState(vk) & 0x8000)


def _current_modifiers(held_vks: Optional[set] = None) -> int:
    held = held_vks or set()
    mods = 0
    if (
        _is_key_down(VK_CONTROL) or _is_key_down(VK_LCONTROL) or _is_key_down(VK_RCONTROL)
        or (held & {VK_CONTROL, VK_LCONTROL, VK_RCONTROL})
    ):
        mods |= MOD_CONTROL
    if (
        _is_key_down(VK_MENU) or _is_key_down(VK_LMENU) or _is_key_down(VK_RMENU)
        or (held & {VK_MENU, VK_LMENU, VK_RMENU})
    ):
        mods |= MOD_ALT
    if (
        _is_key_down(VK_SHIFT) or _is_key_down(VK_LSHIFT) or _is_key_down(VK_RSHIFT)
        or (held & {VK_SHIFT, VK_LSHIFT, VK_RSHIFT})
    ):
        mods |= MOD_SHIFT
    if _is_key_down(VK_LWIN) or _is_key_down(VK_RWIN) or (held & {VK_LWIN, VK_RWIN}):
        mods |= MOD_WIN
    return mods


def _flush_console_input() -> None:
    if _WIN32 is None:
        return
    stdin_handle = _WIN32.kernel32.GetStdHandle(STD_INPUT_HANDLE)
    if stdin_handle and stdin_handle != -1:
        _WIN32.kernel32.FlushConsoleInputBuffer(stdin_handle)


def _wait_keys_released(vks: tuple, timeout_s: float = 0.4) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not any(_is_key_down(vk) for vk in vks):
            return
        time.sleep(0.015)


def _capture_hotkey_hook(on_invalid: Optional[Callable[[str], None]] = None) -> Optional[str]:
    assert _WIN32 is not None
    k32, u32 = _WIN32.kernel32, _WIN32.user32
    thread_id = int(k32.GetCurrentThreadId())

    _wait_keys_released((0x0D,))  # Let Enter from the menu selection release first.

    state = {"done": False, "hotkey": None}
    held_vks: set = set()
    hook_id: Optional[int] = None

    def low_level_proc(n_code: int, w_param: int, l_param: int) -> int:
        if n_code >= 0 and l_param and not state["done"]:
            kb = ctypes.cast(l_param, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
            vk = int(kb.vkCode)

            if w_param in (WM_KEYUP, WM_SYSKEYUP):
                held_vks.discard(vk)
                return u32.CallNextHookEx(hook_id, n_code, w_param, l_param)

            if w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
                if vk in MODIFIER_VKS:
                    held_vks.add(vk)
                    return u32.CallNextHookEx(hook_id, n_code, w_param, l_param)

                mods = _current_modifiers(held_vks)
                if vk == VK_ESCAPE and mods == 0:
                    state["done"] = True
                    state["hotkey"] = None
                    u32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
                    return 1

                if vk_to_key_name(vk) is not None:
                    if mods == 0 and vk not in BARE_ALLOWED_VKS:
                        if on_invalid is not None:
                            on_invalid(
                                "Please include a modifier key (Ctrl, Alt, Shift, or Win), "
                                "or use F1-F24 / PrintScreen."
                            )
                        return 1
                    state["hotkey"] = format_hotkey(mods, vk)
                    state["done"] = True
                    u32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
                    return 1

        return u32.CallNextHookEx(hook_id, n_code, w_param, l_param)

    hook_proc = HOOKPROC(low_level_proc)
    hook_id = u32.SetWindowsHookExW(WH_KEYBOARD_LL, hook_proc, None, 0)
    if not hook_id:
        raise OSError(ctypes.get_last_error(), "SetWindowsHookExW failed")

    msg = wintypes.MSG()
    try:
        while not state["done"]:
            ret = u32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret <= 0:
                break
            u32.TranslateMessage(ctypes.byref(msg))
            u32.DispatchMessageW(ctypes.byref(msg))
    finally:
        u32.UnhookWindowsHookEx(hook_id)
        _flush_console_input()

    return state["hotkey"]


def _capture_hotkey_poll(on_invalid: Optional[Callable[[str], None]] = None) -> Optional[str]:
    assert _WIN32 is not None
    k32 = _WIN32.kernel32
    stdin_handle = k32.GetStdHandle(STD_INPUT_HANDLE)
    orig_mode = wintypes.DWORD()
    has_mode = bool(stdin_handle and stdin_handle != -1 and k32.GetConsoleMode(stdin_handle, ctypes.byref(orig_mode)))

    candidate_vks = [vk for vk in range(0x08, 0xE0) if vk not in MODIFIER_VKS and vk_to_key_name(vk) is not None]

    try:
        if has_mode:
            raw_mode = orig_mode.value & ~(ENABLE_PROCESSED_INPUT | ENABLE_LINE_INPUT | ENABLE_ECHO_INPUT)
            k32.SetConsoleMode(stdin_handle, raw_mode)

        _wait_keys_released((0x0D, ord("5")))
        prev_down = {vk for vk in candidate_vks if _is_key_down(vk)}

        while True:
            time.sleep(0.015)
            now_down = {vk for vk in candidate_vks if _is_key_down(vk)}
            pressed = now_down - prev_down
            prev_down = now_down
            if not pressed:
                continue

            vk = next(iter(pressed))
            mods = _current_modifiers()
            if vk == VK_ESCAPE and mods == 0:
                _wait_keys_released((VK_ESCAPE,))
                return None

            if mods == 0 and vk not in BARE_ALLOWED_VKS:
                if on_invalid is not None:
                    on_invalid(
                        "Please include a modifier key (Ctrl, Alt, Shift, or Win), "
                        "or use F1-F24 / PrintScreen."
                    )
                continue

            hotkey = format_hotkey(mods, vk)
            _wait_keys_released((vk,))
            return hotkey
    finally:
        if has_mode:
            k32.SetConsoleMode(stdin_handle, orig_mode.value)
        _flush_console_input()


def capture_hotkey(on_invalid: Optional[Callable[[str], None]] = None) -> Optional[str]:
    """Wait for a physical key combination on Windows and return its shortcut string.

    Returns ``None`` if the user cancels with bare ``Esc``.
    """
    if _WIN32 is None:
        raise RuntimeError("Interactive key capture requires Windows.")
    try:
        return _capture_hotkey_hook(on_invalid=on_invalid)
    except OSError:
        return _capture_hotkey_poll(on_invalid=on_invalid)
