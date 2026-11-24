@echo off
rem ============================================================
rem  纳米压痕分析 - 重建 config.xlsx 的导入表
rem  按 data\ 里现有的 xls 重建行，保留已填的图例/编号/分组
rem ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Nanoindentation - fill config

for %%i in ("%~dp0..\python\python.exe") do set "PYEXE=%%~fi"
if not exist "%PYEXE%" (
    echo  [错误] 找不到便携 Python: %PYEXE%
    if not defined DATA_ANALYSIS_NO_PAUSE pause
    exit /b 1
)
set "PYTHONIOENCODING=cp936"
set "PYTHONUTF8=0"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONNOUSERSITE=1"

"%PYEXE%" -B -X utf8 "%~dp0_fill_config.py" %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo  [失败] 退出码 %RC%
    if not defined DATA_ANALYSIS_NO_PAUSE pause
    exit /b %RC%
)
echo  [完成] 打开 config.xlsx 修改设置，再双击 run.bat
if not defined DATA_ANALYSIS_NO_PAUSE pause
endlocal
exit /b 0
