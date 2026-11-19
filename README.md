# ss-to-latex

A lightweight Windows background tool that lets you snip any mathematical equation on your screen with a keyboard shortcut, converts it to clean LaTeX using the **Antigravity CLI (`agy`)** (`gemini-4-hydrogen-low` with `low` thinking effort), and copies the result straight to your clipboard.

---

## Project Structure

```text
ss-to-latex/
├── start_background.bat        # Clickable background launcher
├── configure.bat               # Clickable interactive config CLI launcher
├── README.md
├── .gitignore
└── ss_to_latex/
    ├── __init__.py             # Public package exports
    ├── __main__.py             # CLI entrypoint (`python -m ss_to_latex`)
    ├── cli_config.py           # Interactive configuration wizard
    ├── config.py               # Config dataclass, defaults, JSON load/save, logging
    ├── hotkeys.py              # Shortcut parsing & Win32 virtual-key mappings
    ├── ocr.py                  # LaTeX cleanup & `agy` CLI invocation
    ├── startup.py              # Windows Registry / Startup-folder registration
    ├── ui.py                   # Screen snipping overlay & toast notifications
    └── win32.py                # Win32 clipboard, DPI, screen metrics, mutex & hotkey loop
```

---

## Step 1: Set Up `agy` CLI

1. **Install `agy` CLI** in PowerShell:
   ```powershell
   irm https://antigravity.google/cli/install.ps1 | iex
   ```
   *(Or in Command Prompt: `curl -fsSL https://antigravity.google/cli/install.cmd -o install.cmd && install.cmd && del install.cmd`)*

2. **Sign in & verify:**
   Run `agy` once in your terminal:
   ```powershell
   agy
   ```
   - On first launch, your browser will open to sign in with your Google account.
   - Once signed in, type `/exit` (or press `Ctrl+D` twice) to close `agy`.
   - You can verify available models anytime with:
     ```powershell
     agy models
     ```

> **Note:** `ss-to-latex` automatically finds `agy` on your `PATH` or at `%USERPROFILE%\.gemini\bin\agy.exe`.

---

## Step 2: Check Python Requirement

Make sure Python 3.9+ and **Pillow** are installed:

```powershell
pip install pillow
```

---

## Step 3: Start `ss-to-latex`

Double-click **`start_background.bat`** — or run from PowerShell in the project folder:

```powershell
pythonw -m ss_to_latex
```

- A brief **"ss-to-latex ready"** notification will appear in the bottom-right corner.
- On first run, it automatically registers **`ss-to-latex`** in Windows startup so it launches silently whenever you start your laptop:
  - **Primary:** `HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run\ss-to-latex`
  - **Fallback:** `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\ss-to-latex.vbs`

---

## Configuration Tool (Recommended)

Double-click **`configure.bat`** (or run `python -m ss_to_latex.cli_config`) to open an interactive CLI wizard where you can choose:

1. **AI Model** — picks from available `agy models` (and automatically matches thinking effort like `low` / `medium` / `high`).
2. **Screenshot Shortcut** — choose a preset or select **`[5]`** to record any custom shortcut simply by pressing the keys (e.g. `Ctrl+Q`, `Ctrl+Shift+E`, `Alt+F9`).
3. **Toast Notifications** — choose how many popups to show:
   - `0`: None (0 toasts — silent)
   - `1`: Errors only
   - `2`: Result & errors only (1 toast per snip)
   - `3`: All toasts (startup + converting + copied + errors)

Your selections are saved to **`config.json`** in the project root, loaded automatically on every launch and Windows startup, and `configure.bat` offers to restart the running `ss-to-latex` background process right away. You can also view saved settings anytime with:

```powershell
python -m ss_to_latex.cli_config --show
```

---

## Step 4: Snip an Equation

1. Press **`Ctrl+Alt+L`** anywhere on your screen.
2. Click and drag a box around the mathematical equation *(press **`Esc`** or **right-click** to cancel)*.
3. Wait a moment for `agy` to process the image — you'll see a **"Copied LaTeX to clipboard!"** toast with a preview.
4. Press **`Ctrl+V`** wherever you want to paste the LaTeX.

---

## Customization

Settings are stored in **`config.json`** (managed via **`configure.bat`**) and can also be overridden with command-line flags or environment variables:

| Flag | Env Var | Default | Description |
| --- | --- | --- | --- |
| `--hotkey` | `SS_TO_LATEX_HOTKEY` | `ctrl+alt+l` | Global shortcut (`ctrl`, `alt`, `shift`, `win` + key, e.g. `ctrl+shift+e`, `alt+f9`) |
| `--model` | `AGY_MODEL` | `gemini-4-hydrogen-low` | Model passed to `agy --model` |
| `--effort` | `AGY_EFFORT` | `low` | Thinking effort passed to `agy --effort` (`low`, `medium`, `high`, `xhigh`, `max`) |
| `--toast-level` | `SS_TO_LATEX_TOAST_LEVEL` | `3` | Toast notifications: `0`/`none`, `1`/`errors`, `2`/`result`, `3`/`all` |
| `--agy-path` | `AGY_CLI_PATH` | *(auto-detected)* | Custom path to `agy.exe` |

### Useful One-Off Flags

- **Test a single snip immediately** (no background listener):
  ```powershell
  python -m ss_to_latex --once
  ```
- **Run without modifying Windows startup:**
  ```powershell
  pythonw -m ss_to_latex --no-autostart
  ```
- **Register startup with a custom shortcut** (without launching yet):
  ```powershell
  python -m ss_to_latex --install-startup --hotkey "ctrl+shift+e"
  ```

---

## Stopping or Uninstalling

- **Stop the running background process:**
  ```powershell
  Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" | Where-Object { $_.CommandLine -like '*ss_to_latex*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
  ```

- **Remove Windows auto-start:**
  ```powershell
  python -m ss_to_latex --uninstall-startup
  ```

---

## Troubleshooting

- **Logs:** Check `ss-to-latex.log` in the project folder for any errors.
- **`agy` not found:** Make sure `%USERPROFILE%\.gemini\bin` is in your `PATH`, or pass `--agy-path "C:\path\to\agy.exe"`.
- **Shortcut doesn't open overlay:** Another app may already use `Ctrl+Alt+L` — try a different shortcut with `--hotkey "ctrl+shift+e"` or via `configure.bat`.
