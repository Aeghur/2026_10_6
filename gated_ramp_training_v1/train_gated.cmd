@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" train_gated.py %*
  exit /b
)
if exist ".venv_active\Scripts\python.exe" (
  ".venv_active\Scripts\python.exe" train_gated.py %*
  exit /b
)
py -3 train_gated.py %*
