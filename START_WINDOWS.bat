@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1

if exist ".venv\Scripts\python.exe" goto install

where py >nul 2>nul
if %errorlevel% equ 0 (
    py -3.12 -c "import sys" >nul 2>nul
    if %errorlevel% equ 0 (set "PYTHON_LAUNCH=py -3.12") else (set "PYTHON_LAUNCH=py -3")
) else (
    where python >nul 2>nul
    if %errorlevel% equ 0 (set "PYTHON_LAUNCH=python") else goto missing_python
)

echo Creating isolated Python environment...
%PYTHON_LAUNCH% -m venv .venv
if errorlevel 1 goto failed

:install
".venv\Scripts\python.exe" -c "import fitz, pillow_heif, PIL" >nul 2>nul
if %errorlevel% equ 0 goto run

echo Installing HEIC and PDF modules (internet required once)...
".venv\Scripts\python.exe" -m pip install --only-binary=:all: -r requirements.txt
if errorlevel 1 goto failed

:run
".venv\Scripts\python.exe" app.py
if errorlevel 1 goto failed
exit /b 0

:missing_python
echo.
echo Python 3.12 was not found.
echo Please install 64-bit Python 3.12 from https://www.python.org/downloads/
echo When installing, select Add python.exe to PATH.
echo Then start this BAT file again.
pause
exit /b 1

:failed
echo.
echo Something went wrong. Please copy the error text or screenshot this window.
pause
exit /b 1
