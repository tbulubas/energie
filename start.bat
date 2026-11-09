@echo off
rem Copyright (c) 2026 Taras Bulubas. All rights reserved.
rem Use requires prior written permission; see LICENSE.

rem Double-click launcher: starts the app from the project's .venv.
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo .venv not found. Create it once in a terminal in this folder:
    echo     make venv
    echo or without make:
    echo     py -3.12 -m venv .venv
    echo     .venv\Scripts\python -m pip install -e ".[dev]"
    pause
    exit /b 1
)

echo Starting Energie at http://localhost:8510 ...
start http://localhost:8510
"%PY%" -m energie
pause
