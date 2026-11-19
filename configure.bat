@echo off
setlocal
title ss-to-latex Config
set "PYTHONPATH=%~dp0;%PYTHONPATH%"

where python >nul 2>&1
if %errorlevel%==0 (
    python -m ss_to_latex.cli_config %*
) else (
    "C:\Users\omar\AppData\Local\Programs\Python\Python39\python.exe" -m ss_to_latex.cli_config %*
)

if %errorlevel% neq 0 (
    echo.
    pause
)
