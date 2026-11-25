@echo off
setlocal enabledelayedexpansion

rem ============================================================
rem  无参数: 按 data 里现有的 txt 重建 stack.xlsx
rem  带参数: 原样转发给 _fill_stack.py，例如按目录重填拼图表
rem        fill_stack.bat --target montage --src ..\20261123\Raman\1100
rem  行顺序跟数据一致，数据里已删掉的文件对应行会去掉
rem  同名条目改过的 label、normalize、peaks 等保留，其它列按默认填
rem ============================================================

cd /d "%~dp0"

if "%~1"=="" if not exist "data" (
    echo   [错误] 找不到 data 文件夹（只影响不带参数的 stack 填充）
    echo.
    if not defined DATA_ANALYSIS_NO_PAUSE pause
    exit /b 1
)

rem ---- 便携 Python: 与本目录同级的 python\ 文件夹 ----
for %%i in ("%~dp0..\python\python.exe") do set "PY=%%~fi"
if not exist "%PY%" (
    echo [ERROR] Shared Python not found: "%PY%"
    echo Keep the project and its python folder together.
    if not defined DATA_ANALYSIS_NO_PAUSE pause
    exit /b 1
)

set "PYTHONNOUSERSITE=1"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONPATH="
set "PYTHONHOME="

echo.
echo   正在按数据重建配置表 ...
echo.

"%PY%" -B "%~dp0_fill_stack.py" %*
set "RC=%errorlevel%"

echo.
if not "%RC%"=="0" (
    echo   [错误] 填写失败，请检查上面的提示
    echo          如果配置文件正开着 Excel，先关掉再重试
    echo.
    if not defined DATA_ANALYSIS_NO_PAUSE pause
    exit /b 1
)

echo   完成。堆叠直接双击 run.bat；拼图用 run.bat --montage --src ^<目录^>。
exit /b 0
