@echo off
setlocal
rem 将data里的新文件追加到ra_config.xlsx；无参数时强制只扫描本目录data。
rem 外部目录必须显式传入 --dir；已有配置行不会作为本次导入来源。
cd /d "%~dp0"

for %%i in ("%~dp0..\python\python.exe") do set "PYEXE=%%~fi"
if not exist "%PYEXE%" (
    echo [ERROR] Shared Python not found: ..\python\python.exe
    if not defined DATA_ANALYSIS_NO_PAUSE pause
    exit /b 1
)
set "PYTHONNOUSERSITE=1"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONPATH="
set "PYTHONHOME="
set "PYTHONUTF8=0"

echo %*|findstr /I /C:"--dir" >nul
if errorlevel 1 (
    "%PYEXE%" -B "%~dp0extract_ra.py" --import-config --prune-stale --dir "%~dp0data" %*
) else (
    "%PYEXE%" -B "%~dp0extract_ra.py" --import-config %*
)
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [ERROR] Import failed. Close Excel or WPS if the configuration workbook is open.
    if not defined DATA_ANALYSIS_NO_PAUSE pause
    exit /b %RC%
)
echo.
echo Import completed. Review ra_config.xlsx, save and close it, then run run.bat.
endlocal
exit /b 0
