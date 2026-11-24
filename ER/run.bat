@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONNOUSERSITE=1"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONPATH="
set "PYTHONHOME="
set "PYTHONIOENCODING=utf-8"
set "PYEXE=%~dp0..\python\python.exe"
if not exist "%PYEXE%" goto missing_python
"%PYEXE%" -B -X utf8 "%~dp0fill_reimbursement.py" %*
set "ER_EXIT=%ERRORLEVEL%"
if "%ER_EXIT%"=="0" exit /b 0
goto failed
:missing_python
echo Portable Python missing: "%PYEXE%"
set "ER_EXIT=1"
:failed
if "%~1"=="--no-pause" exit /b %ER_EXIT%
echo.
echo Processing failed. See the message above.
if not defined DATA_ANALYSIS_NO_PAUSE pause
exit /b %ER_EXIT%
