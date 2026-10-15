@echo off
cd /d "%~dp0"
if not exist venv\Scripts\pythonw.exe (
  echo First run: creating a private Python environment...
  py -3 -m venv venv || python -m venv venv
  venv\Scripts\python.exe -m pip install -q -r requirements.txt
  if errorlevel 1 (
    echo Setup failed. See the messages above.
    pause
    exit /b 1
  )
)
start "" venv\Scripts\pythonw.exe echo_watch.py
