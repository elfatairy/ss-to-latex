"""Global hotkey parsing, formatting, and Win32 virtual-key constants."""

from __future__ import annotations

from typing import Dict, Optional, Tuple

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_ESCAPE = 0x1B
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5

MODIFIER_VKS = frozenset({
    VK_SHIFT, VK_CONTROL, VK_MENU,
    VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL, VK_LMENU, VK_RMENU,
    VK_LWIN, VK_RWIN,
})

# Keys that are safe to bind without a modifier key (F1-F24, PrintScreen, Insert).
BARE_ALLOWED_VKS = frozenset(range(0x70, 0x88)) | frozenset({0x2C, 0x2D})

_SPECIAL_VK_MAP: Dict[str, int] = {
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

_VK_TO_NAME: Dict[int, str] = {
    0x08: "backspace", 0x09: "tab", 0x0D: "enter", 0x1B: "esc", 0x20: "space",
    0x21: "pageup", 0x22: "pagedown", 0x23: "end", 0x24: "home",
    0x25: "left", 0x26: "up", 0x27: "right", 0x28: "down",
    0x2C: "printscreen", 0x2D: "insert", 0x2E: "delete",
    0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/",
    0xC0: "`", 0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'",
}

_MODIFIER_MAP: Dict[str, int] = {
    "alt": MOD_ALT, "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "shift": MOD_SHIFT, "win": MOD_WIN, "windows": MOD_WIN,
    "super": MOD_WIN, "meta": MOD_WIN,
}

_ORDERED_MODIFIERS = (
    (MOD_CONTROL, "ctrl"),
    (MOD_ALT, "alt"),
    (MOD_SHIFT, "shift"),
    (MOD_WIN, "win"),
)


def vk_to_key_name(vk: int) -> Optional[str]:
    """Return canonical key name for a Win32 virtual-key code, or ``None``."""
    if vk in MODIFIER_VKS:
        return None
    if 0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39:
        return chr(vk).lower()
    if 0x70 <= vk <= 0x87:
        return f"f{vk - 0x70 + 1}"
    return _VK_TO_NAME.get(vk)


def format_hotkey(modifiers: int, vk: int) -> str:
    """Format ``(modifiers, vk)`` into a canonical shortcut string like ``'ctrl+q'``."""
    key_name = vk_to_key_name(vk)
    if key_name is None:
        raise ValueError(f"Unsupported virtual key code: 0x{vk:02X}.")
    parts = [name for bit, name in _ORDERED_MODIFIERS if modifiers & bit]
    parts.append(key_name)
    return "+".join(parts)


def parse_hotkey(hotkey_str: str) -> Tuple[int, int]:
    """Parse a shortcut like ``'ctrl+alt+l'`` into ``(modifiers, vk_code)``."""
    if any(ord(ch) < 32 for ch in hotkey_str):
        raise ValueError(
            "Control characters detected. Please type key names like 'ctrl+q' or use key recording."
        )

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
