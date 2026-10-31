@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto install
py -3.12 -c "import sys; assert sys.version_info >= (3,10)" >nul 2>nul
if not errorlevel 1 goto py312
py -3 -c "import sys; assert sys.version_info >= (3,10)" >nul 2>nul
if not errorlevel 1 goto py3
python -c "import sys; assert sys.version_info >= (3,10)" >nul 2>nul
if not errorlevel 1 goto python
echo Python 3.10+ was not found. Install 64-bit Python 3.12, then retry.
goto fail
:py312
py -3.12 -m venv .venv
goto created
:py3
py -3 -m venv .venv
goto created
:python
python -m venv .venv
:created
if errorlevel 1 goto fail
:install
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -c "import torch,numpy,PIL; print('Dependencies OK. PyTorch:',torch.__version__)"
if errorlevel 1 goto fail
echo Setup completed. Run START.cmd and open http://127.0.0.1:8776/
exit /b 0
:fail
echo Setup failed. See the error above. Retry this script after fixing it.
pause
exit /b 1
