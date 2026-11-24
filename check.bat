@echo off
setlocal
set "PYEXE=%~dp0python\python.exe"
if not exist "%PYEXE%" (
    echo [ERROR] Shared Python not found: "%PYEXE%"
    if /i not "%~1"=="--no-pause" pause
    exit /b 1
)
set "PYTHONNOUSERSITE=1"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONPATH="
set "PYTHONHOME="
"%PYEXE%" -B -X utf8 "%~dp0check_projects.py"
set "RC=%ERRORLEVEL%"
if /i not "%~1"=="--no-pause" pause
exit /b %RC%
