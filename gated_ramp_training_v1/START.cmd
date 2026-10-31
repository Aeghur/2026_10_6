@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Please run INSTALL.cmd first.
  pause
  exit /b 1
)
call train_gated.cmd %*
if errorlevel 1 pause
