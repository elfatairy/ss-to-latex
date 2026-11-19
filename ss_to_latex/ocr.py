"""LaTeX output cleanup and agy CLI vision OCR invocation."""

from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Optional

from PIL import Image

from .config import Config, DEFAULT_AGY_PATH

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
