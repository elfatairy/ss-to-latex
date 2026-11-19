"""CLI entrypoint for `python -m ss_to_latex`."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any, Dict, Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ss_to_latex.config import (
    APP_NAME,
    DEFAULT_EFFORT,
    DEFAULT_HOTKEY,
    DEFAULT_MODEL,
    DEFAULT_TOAST_LEVEL,
    load_config,
    logger,
    parse_toast_level,
)
from ss_to_latex.startup import register_startup, unregister_startup
from ss_to_latex.ui import SsToLatexApp
from ss_to_latex.win32 import SingleInstanceLock


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ss_to_latex",
        description="Snip an equation from the screen and copy its LaTeX via agy CLI.",
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
