@echo off
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
title AI-Agent v4

set "ROOT=%~dp0"
set "PYTHON=%ROOT%venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    set "PYTHON=%ROOT%.venv\Scripts\python.exe"
)

if not exist "%PYTHON%" (
    echo [ERROR] Python venv not found!
    echo Checked: %ROOT%venv\Scripts\python.exe
    echo Checked: %ROOT%.venv\Scripts\python.exe
    pause
    exit /b 1
)

cd /d "%ROOT%ai-agent-system"
"%PYTHON%" server.py
pause
