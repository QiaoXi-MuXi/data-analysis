@echo off
setlocal enabledelayedexpansion

rem ============================================================
rem  把 data 里所有 txt 补进 stack.xlsx
rem  已有的行原样保留（label、normalize、peaks 等改动不会丢），只追加缺的
rem  stack.xlsx 不存在时，按 data 里全部文件新建
rem ============================================================

cd /d "%~dp0"

if not exist "data" (
    echo   [错误] 找不到 data 文件夹
    echo.
    pause
    exit /b 1
)

rem ---- 便携 Python: 与本目录同级的 python\ 文件夹 ----
for %%i in ("%~dp0..\python\python.exe") do set "PY=%%~fi"
if not exist "%PY%" (
    set "PY="
    where python >nul 2>nul && set "PY=python"
    if not defined PY (
        echo   [错误] 没找到便携 Python: ..\python\python.exe  ^(PATH 里也没有 python^)
        echo.
        pause
        exit /b 1
    )
    echo   [提示] 正在使用系统 Python: !PY!
)

set "PYTHONNOUSERSITE=1"
set "PYTHONPATH="

echo.
echo   正在把 data 里的文件填进 stack.xlsx ...
echo.

"%PY%" "%~dp0_fill_stack.py"
set "RC=%errorlevel%"

echo.
if not "%RC%"=="0" (
    echo   [错误] 填写失败，请检查上面的提示
    echo          如果 stack.xlsx 正开着 Excel，先关掉再重试
    echo.
    pause
    exit /b 1
)

echo   完成。双击 run.bat 就能按这张表出堆叠图。
exit /b 0
