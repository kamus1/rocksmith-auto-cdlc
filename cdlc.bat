@echo off
rem Shortcut for: .venv\Scripts\python -m cdlc ... (works from any folder)
setlocal
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
"%~dp0.venv\Scripts\python" -m cdlc %*
