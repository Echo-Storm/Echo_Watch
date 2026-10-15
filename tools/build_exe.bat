@echo off
rem Builds EchoWatch.exe (single file) and copies it to the project root.
rem Run from anywhere; needs the venv created by run.bat.
cd /d "%~dp0\.."

if not exist venv\Scripts\python.exe (
  echo Run run.bat once first to create the venv.
  exit /b 1
)

venv\Scripts\python.exe -m pip install -q pyinstaller || exit /b 1
venv\Scripts\python.exe tools\make_icon.py || exit /b 1

rem Use "python -m PyInstaller": the pyinstaller.exe shim can silently build nothing.
venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name EchoWatch --icon assets\icon.ico ^
  --add-data "assets\logo.svg;assets" ^
  --exclude-module PyQt6.QtWebEngineCore --exclude-module PyQt6.QtQml --exclude-module PyQt6.QtQuick ^
  echo_watch.py || exit /b 1

copy /y dist\EchoWatch.exe EchoWatch.exe >nul
echo Built EchoWatch.exe
