@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
if not exist ".venv\Scripts\python.exe" (
  echo Run START_WINDOWS.bat first.
  pause
  exit /b 1
)
echo Installing Windows EXE builder...
".venv\Scripts\python.exe" -m pip install pyinstaller
if errorlevel 1 goto failed
".venv\Scripts\pyinstaller.exe" --noconfirm --clean --onefile --windowed --name "HEIC_PDF_Toma" --collect-all pillow_heif --collect-all pymupdf app.py
if errorlevel 1 goto failed
copy /y "dist\HEIC_PDF_Toma.exe" "HEIC_PDF_Toma.exe" >nul
if errorlevel 1 goto failed
echo.
echo READY: HEIC_PDF_Toma.exe
pause
exit /b 0
:failed
echo Could not build EXE. Keep using START_WINDOWS.bat.
pause
exit /b 1
