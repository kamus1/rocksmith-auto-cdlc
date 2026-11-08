@echo off
rem One-time setup: Python environment, dependencies and RsBuild, then a check of what is missing.
cd /d "%~dp0"
where python >nul 2>&1 || (echo Python 3.12 not found: winget install Python.Python.3.12 & exit /b 1)
where dotnet >nul 2>&1 || (echo .NET 10 SDK not found: winget install Microsoft.DotNet.SDK.10 & exit /b 1)
git submodule update --init || exit /b 1
if not exist .venv python -m venv .venv || exit /b 1
.venv\Scripts\python -m pip install -q --disable-pip-version-check -r requirements.txt || exit /b 1
dotnet build src\RsBuild -c Release -v q -nologo || exit /b 1
echo.
.venv\Scripts\python -m cdlc check || exit /b 1
echo Run ".\cdlc web" to open the web app.
