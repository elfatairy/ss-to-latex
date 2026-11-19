@echo off
setlocal
set "PYTHONPATH=%~dp0;%PYTHONPATH%"

where pythonw >nul 2>&1
if %errorlevel%==0 (
    start "" /D "%~dp0" pythonw -m ss_to_latex %*
) else (
    start "" /D "%~dp0" python -m ss_to_latex %*
)
