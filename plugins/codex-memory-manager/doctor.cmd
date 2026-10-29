@echo off
setlocal
echo Codex Memory Manager Doctor 0.1.3
where py >nul 2>nul
if not errorlevel 1 goto use_py
where python >nul 2>nul
if not errorlevel 1 goto use_python
echo Python 3.10+ is required.
set "MEMORY_DOCTOR_RESULT=1"
goto done
:use_py
py -3 "%~dp0doctor.py" %*
set "MEMORY_DOCTOR_RESULT=%errorlevel%"
goto done
:use_python
python "%~dp0doctor.py" %*
set "MEMORY_DOCTOR_RESULT=%errorlevel%"
:done
if "%~1"=="" pause
exit /b %MEMORY_DOCTOR_RESULT%
