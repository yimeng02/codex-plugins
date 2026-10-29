@echo off
setlocal
echo Codex Memory Manager Installer 0.2.0
where py >nul 2>nul
if not errorlevel 1 goto use_py
where python >nul 2>nul
if not errorlevel 1 goto use_python
echo Python 3.10+ is required. Install Python, then run this script again.
set "MEMORY_INSTALL_RESULT=1"
goto done
:use_py
py -3 "%~dp0install.py" %*
set "MEMORY_INSTALL_RESULT=%errorlevel%"
goto done
:use_python
python "%~dp0install.py" %*
set "MEMORY_INSTALL_RESULT=%errorlevel%"
:done
if "%~1"=="" pause
exit /b %MEMORY_INSTALL_RESULT%
