"""Configuration schema, paths, logging setup, and JSON persistence."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
from typing import Any, Dict

from .hotkeys import parse_hotkey

APP_NAME = "ss-to-latex"
MUTEX_NAME = "Local\\ss-to-latex-mutex"

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
CONFIG_PATH = PROJECT_ROOT / "config.json"
LOG_PATH = PROJECT_ROOT / "ss-to-latex.log"

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

TOAST_CATEGORIES = {
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
