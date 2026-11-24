@echo off
setlocal
rem 使用项目共享解释器；不回退系统 Python，不自动安装依赖。
for %%i in ("%~dp0..\python\python.exe") do set "PYEXE=%%~fi"
if not exist "%PYEXE%" (
    echo [ERROR] Shared Python not found: "%PYEXE%"
    echo Keep sci-pic and the project python folder together.
    if not defined DATA_ANALYSIS_NO_PAUSE pause
    exit /b 1
)
set "PYTHONNOUSERSITE=1"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONPATH="
set "PYTHONHOME="
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
"%PYEXE%" -s -B "%~dp0plot_sci_pic.py" %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    if not defined DATA_ANALYSIS_NO_PAUSE pause
    exit /b %RC%
)
if "%~1"=="" if not defined DATA_ANALYSIS_NO_OPEN start "" "%~dp0out"
exit /b %RC%
