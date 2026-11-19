"""Interactive CLI configuration tool for ss-to-latex."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Callable, List, Optional, Tuple

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ss_to_latex.config import (
    CONFIG_PATH,
    PROJECT_ROOT,
    TOAST_LEVEL_DESCRIPTIONS,
    Config,
    load_config,
    parse_toast_level,
    save_config,
)
from ss_to_latex.hotkeys import parse_hotkey
from ss_to_latex.ocr import resolve_agy_cli
from ss_to_latex.startup import register_startup, resolve_launcher_exe
from ss_to_latex.win32 import capture_hotkey

EFFORT_CHOICES = ("low", "medium", "high", "xhigh", "max")
SHORTCUT_PRESETS = ["ctrl+alt+l", "ctrl+shift+l", "ctrl+alt+s", "alt+shift+l"]

_MODEL_SPECS = (
    ("gemini-4-hydrogen", "Gemini 4 Hydrogen", ("Low", "Medium", "High", "XHigh")),
    ("gemini-4-argon", "Gemini 4 Argon", ("Low", "Medium", "High")),
    ("gemini-4-neon", "Gemini 4 Neon", ("Low", "Medium", "High", "XHigh")),
    ("gemini-3.8-flash", "Gemini 3.8 Flash", ("Low", "Medium", "High")),
    ("claude-sonnet-5-5", "Claude Sonnet 5.5", ("Low", "Medium", "High")),
    ("claude-opus-5-5", "Claude Opus 5.5", ("Low", "Medium", "High")),
)

FALLBACK_MODELS: List[Tuple[str, str]] = [
    (f"{slug}-{level.lower()}", f"{label} ({level})")
    for slug, label, levels in _MODEL_SPECS
    for level in levels
]


def fetch_available_models(agy_path: str = "") -> Tuple[List[Tuple[str, str]], bool]:
    """Fetch available models via ``agy models``; return ``(models, from_cli)``."""
    try:
        exe = resolve_agy_cli(agy_path)
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        proc = subprocess.run(
            [exe, "models"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            creationflags=flags,
        )
        if proc.returncode == 0:
            parsed = [
                (parts[0].strip(), parts[1].strip() or parts[0].strip())
                for line in proc.stdout.splitlines()
                if "\t" in line
                for parts in [line.strip().split("\t", 1)]
                if parts[0].strip()
            ]
            if parsed:
                return parsed, True
    except (OSError, RuntimeError, subprocess.SubprocessError):
        pass

    return list(FALLBACK_MODELS), False


def infer_effort_from_model(model_id: str, current_effort: str) -> str:
    """Extract thinking effort suffix from ``model_id`` if present."""
    lowered = model_id.strip().lower()
    for effort in ("xhigh", "high", "medium", "low", "max"):
        if lowered.endswith(f"-{effort}"):
            return effort
    return current_effort


def format_current_settings(cfg: Config) -> str:
    """Return a readable summary of the current configuration."""
    toast_desc = TOAST_LEVEL_DESCRIPTIONS.get(cfg.toast_level, str(cfg.toast_level))
    return (
        f"  Model           : {cfg.model}\n"
        f"  Thinking effort : {cfg.effort or '(default)'}\n"
        f"  Shortcut        : {cfg.hotkey}\n"
        f"  Toasts          : {cfg.toast_level} - {toast_desc}"
    )


def prompt_model(
    current_model: str,
    current_effort: str,
    models: List[Tuple[str, str]],
    from_cli: bool,
    input_fn: Callable[[str], str] = input,
) -> Tuple[str, str]:
    """Interactively prompt the user to select or enter a model and effort."""
    source_note = "from `agy models`" if from_cli else "built-in list (`agy models` unavailable)"
    print(f"\n[1/3] Choose AI Model ({source_note}):")
    for idx, (model_id, display_name) in enumerate(models, start=1):
        marker = " * (current)" if model_id == current_model else ""
        print(f"  [{idx:>2}] {display_name:<26} ({model_id}){marker}")

    while True:
        choice = input_fn(
            f"\nEnter number [1-{len(models)}], custom model ID, or Enter to keep '{current_model}': "
        ).strip()
        if not choice:
            return current_model, current_effort

        if choice.isdigit():
            num = int(choice)
            if 1 <= num <= len(models):
                chosen = models[num - 1][0]
                return chosen, infer_effort_from_model(chosen, current_effort)
            print(f"Please enter a number between 1 and {len(models)}.")
            continue

        inferred = infer_effort_from_model(choice, current_effort) or "low"
        while True:
            effort_in = input_fn(
                f"Thinking effort ({'/'.join(EFFORT_CHOICES)}) [Enter for '{inferred}']: "
            ).strip().lower()
            if not effort_in:
                return choice, inferred
            if effort_in in EFFORT_CHOICES:
                return choice, effort_in
            print(f"Invalid effort '{effort_in}'. Choose one of: {', '.join(EFFORT_CHOICES)}.")


def prompt_hotkey(
    current_hotkey: str,
    input_fn: Callable[[str], str] = input,
    capture_fn: Optional[Callable[[], Optional[str]]] = None,
) -> str:
    """Interactively prompt the user to choose or record a global screenshot shortcut."""
    print("\n[2/3] Choose Screenshot Shortcut:")
    for idx, preset in enumerate(SHORTCUT_PRESETS, start=1):
        marker = " * (current)" if preset.lower() == current_hotkey.lower() else ""
        print(f"  [{idx}] {preset}{marker}")
    print("  [5] Record a custom shortcut (just press the keys!)")

    while True:
        choice = input_fn(
            f"\nEnter option [1-5], shortcut string, or Enter to keep '{current_hotkey}': "
        ).strip()
        if not choice:
            return current_hotkey

        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(SHORTCUT_PRESETS):
                candidate = SHORTCUT_PRESETS[idx - 1]
            elif idx == 5:
                if capture_fn is not None:
                    recorded = capture_fn()
                elif sys.platform == "win32" and sys.stdin.isatty():
                    print(
                        "Press your desired shortcut now (e.g. Ctrl+Q, Alt+Shift+S) "
                        "— press Esc to cancel..."
                    )
                    recorded = capture_hotkey(on_invalid=print)
                else:
                    recorded = (
                        input_fn(
                            "Type custom shortcut (modifiers: ctrl, alt, shift, win + key): "
                        ).strip()
                        or None
                    )
                if not recorded:
                    print("Recording cancelled.")
                    continue
                print(f"Recorded shortcut: {recorded}")
                candidate = recorded
            else:
                print("Please choose 1-5 or type a shortcut like 'ctrl+shift+e'.")
                continue
        else:
            candidate = choice

        try:
            parse_hotkey(candidate)
            return candidate.lower()
        except ValueError as exc:
            print(f"Invalid shortcut: {exc}")


def prompt_toast_level(current_level: int, input_fn: Callable[[str], str] = input) -> int:
    """Interactively prompt the user to choose how many toasts to show."""
    print("\n[3/3] Choose Toast Notifications:")
    for level, desc in sorted(TOAST_LEVEL_DESCRIPTIONS.items()):
        marker = " * (current)" if level == current_level else ""
        print(f"  [{level}] {desc}{marker}")

    while True:
        choice = input_fn(
            f"\nEnter toast level [0-3] or Enter to keep '{current_level}': "
        ).strip()
        if not choice:
            return current_level
        try:
            return parse_toast_level(choice)
        except ValueError as exc:
            print(exc)


def restart_background_process() -> bool:
    """Stop any running ss-to-latex background process and launch a fresh one."""
    if sys.platform != "win32":
        return False

    ps_stop = (
        "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | "
        "Where-Object { $_.CommandLine -like '*ss_to_latex*' } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_stop],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        pass

    time.sleep(0.3)

    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{PROJECT_ROOT}{os.pathsep}{existing}" if existing else str(PROJECT_ROOT)

    detached = (
        subprocess.CREATE_NEW_PROCESS_GROUP
        | subprocess.DETACHED_PROCESS
        | subprocess.CREATE_NO_WINDOW
    )
    try:
        subprocess.Popen(
            [resolve_launcher_exe(), "-m", "ss_to_latex"],
            cwd=str(PROJECT_ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=detached,
        )
        return True
    except OSError as exc:
        print(f"Could not start background process: {exc}", file=sys.stderr)
        return False


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ss_to_latex.cli_config",
        description="Interactive CLI configuration tool for ss-to-latex.",
    )
    parser.add_argument("--show", action="store_true", help="Print current configuration and exit.")
    parser.add_argument("--no-restart", action="store_true", help="Save configuration without prompting to restart ss-to-latex.")
    parser.add_argument("--no-pause", action="store_true", help="Exit without waiting for Enter at the end.")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="Optional path to config JSON file.")
    return parser


def run_interactive(
    config_path: Path = CONFIG_PATH,
    no_restart: bool = False,
    no_pause: bool = False,
    input_fn: Callable[[str], str] = input,
    capture_fn: Optional[Callable[[], Optional[str]]] = None,
) -> int:
    """Run the interactive configuration flow."""
    cfg = load_config(config_path)

    print("=" * 56)
    print("  ss-to-latex Configuration Tool")
    print("=" * 56)
    print("\nCurrent settings:")
    print(format_current_settings(cfg))

    models, from_cli = fetch_available_models(cfg.agy_path)
    cfg.model, cfg.effort = prompt_model(cfg.model, cfg.effort, models, from_cli, input_fn=input_fn)
    cfg.hotkey = prompt_hotkey(cfg.hotkey, input_fn=input_fn, capture_fn=capture_fn)
    cfg.toast_level = prompt_toast_level(cfg.toast_level, input_fn=input_fn)

    saved_path = save_config(cfg, config_path=config_path)
    print(f"\nSaved configuration to: {saved_path}")
    print(format_current_settings(cfg))

    if sys.platform == "win32":
        try:
            print(f"Updated Windows startup entry: {register_startup()}")
        except Exception as exc:
            print(f"Warning: could not update Windows startup entry ({exc}).")

    if not no_restart and sys.platform == "win32":
        answer = input_fn("\nRestart ss-to-latex now to apply changes? [Y/n]: ").strip().lower()
        if answer in ("", "y", "yes"):
            if restart_background_process():
                print("ss-to-latex restarted in the background.")
            else:
                print("Could not restart ss-to-latex automatically; launch start_background.bat manually.")

    if not no_pause:
        input_fn("\nPress Enter to close...")

    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.show:
        cfg = load_config(args.config)
        print("Current ss-to-latex settings:")
        print(format_current_settings(cfg))
        print(f"  Config file     : {args.config}")
        return 0

    try:
        return run_interactive(args.config, args.no_restart, args.no_pause)
    except KeyboardInterrupt:
        print("\nConfiguration cancelled.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
