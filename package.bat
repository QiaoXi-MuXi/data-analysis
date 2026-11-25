@echo off
setlocal EnableExtensions DisableDelayedExpansion
title Data Analysis - Package for Distribution
set "NOPAUSE=0"
if /i "%~1"=="--no-pause" set "NOPAUSE=1"
pushd "%~dp0"
if errorlevel 1 goto :fail_dir

rem 固定包含便携 Python、GUI 与启动器；业务模块由用户逐项选择。
for %%F in (python\python.exe python\pythonw.exe DataAnalysis.exe gui\app.py) do (
    if not exist "%%F" (
        set "MSG=缺少 %%F，请确认项目完整。"
        goto :fail
    )
)
set "EXDIR=/XD data out __pycache__ .pytest_cache .mypy_cache .ruff_cache .backups backups reports results surface_plots package_stage"
set "EXFILE=/XF *.pyc *.pyo Thumbs.db Desktop.ini ~$* .env .env.* ra_results.xlsx groove_results.xlsx groove_paper_results*.xlsx 物品验收单_*.xlsx"

rem 压缩包顶层文件夹名：解压后得到唯一的项目目录，避免文件散落在解压位置。
set "PKGNAME=data analysis"

echo.
echo === Data Analysis 分发打包 ===
echo Python 运行时、GUI、启动器与说明文件始终包含；业务模块按需选择。
echo 解压后得到单一顶层文件夹：%PKGNAME%
echo 直接回车 = 是，输入 n = 否。
echo.
set "SEL="
set "TOK="
call :ask "Ra" "Ra" "Zygo 面粗糙度 Ra 提取"
call :ask "Raman" "Raman" "Raman 光谱绘图"
call :ask "CLSM" "CLSM" "CLSM 槽宽/槽深测量"
call :ask "ER" "ER" "报销票据验收单"
call :ask "sci-pic" "sci-pic" "科研绘图"
call :ask "Nanoindentation Analysis" "Nano" "纳米压痕分析"
echo.
chcp 936 >nul

set "STAGE=%~dp0package_stage"
if exist "%STAGE%" rmdir /s /q "%STAGE%"
if exist "%STAGE%" (
    set "MSG=无法清理临时目录 %STAGE%，请关闭占用该目录的程序后重试。"
    goto :fail
)
mkdir "%STAGE%"
if errorlevel 1 (
    set "MSG=无法创建临时目录 %STAGE%。"
    goto :fail
)
set "DEST=%STAGE%\%PKGNAME%"
mkdir "%DEST%"
if errorlevel 1 (
    set "MSG=无法创建压缩包顶层目录 %DEST%。"
    goto :fail
)

echo [1/4] 复制根目录文件...
for %%F in (DataAnalysis.exe run_gui.bat check.bat check_projects.py README.md .editorconfig .gitattributes) do (
    if exist "%%F" copy /y "%%F" "%DEST%\" >nul
    if errorlevel 1 (
        set "MSG=复制 %%F 失败。"
        goto :fail
    )
)

echo [2/4] 复制 Python 与 GUI...
call :copytree python python
if errorlevel 1 goto :fail_tree
call :copytree gui gui
if errorlevel 1 goto :fail_tree
for %%M in (%SEL%) do call :copytree "%%~M" "%%~M"
if errorlevel 1 goto :fail_tree

echo [3/4] 预建 data / out 目录...
if not exist "%DEST%\out" mkdir "%DEST%\out"
if errorlevel 1 goto :fail_mkdir
for %%M in (%SEL%) do (
    if /i not "%%~M"=="sci-pic" (
        if not exist "%DEST%\%%~M\data" mkdir "%DEST%\%%~M\data"
        if errorlevel 1 goto :fail_mkdir
    )
    if not exist "%DEST%\%%~M\out" mkdir "%DEST%\%%~M\out"
    if errorlevel 1 goto :fail_mkdir
)

echo [4/4] 生成 ZIP...
set "STAMP="
for /f "delims=" %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmm"') do set "STAMP=%%I"
if not defined STAMP set "STAMP=date"
set "ZIP=%~dp0DataAnalysis_%STAMP%%TOK%.zip"
if exist "%ZIP%" del /f /q "%ZIP%"
tar -a -c -f "%ZIP%" -C "%STAGE%" "%PKGNAME%"
if errorlevel 1 (
    set "MSG=ZIP 生成失败，请确认系统自带 tar.exe 可用。"
    goto :fail
)
if not exist "%ZIP%" (
    set "MSG=未找到生成的 ZIP 文件。"
    goto :fail
)

rmdir /s /q "%STAGE%"
for %%F in ("%ZIP%") do echo [完成] %%~nxF  %%~zF bytes
echo 顶层文件夹：%PKGNAME%
echo 模块：%SEL%
echo 提示：输入放入 各模块\data\，结果在 各模块\out\。
if "%NOPAUSE%"=="0" pause
popd
endlocal
exit /b 0

:ask
set "ANS="
set /p "ANS=  打包 %~3 [Y/n]: "
if /i "%ANS%"=="n" goto :eof
if /i "%ANS%"=="no" goto :eof
if not exist "%~1\" (
    echo    跳过：未找到目录 %~1
    goto :eof
)
set "SEL=%SEL% "%~1""
set "TOK=%TOK%_%~2"
goto :eof

:copytree
robocopy "%~dp0%~1" "%DEST%\%~2" /E /NFL /NDL /NJH /NJS /NP /R:1 /W:1 %EXDIR% %EXFILE%
if errorlevel 8 exit /b 1
exit /b 0

:fail_tree
set "MSG=复制目录失败，请查看上方 robocopy 错误。"
goto :fail

:fail_mkdir
set "MSG=无法创建 data / out 目录，请检查磁盘空间或权限。"
goto :fail

:fail_dir
set "MSG=无法进入脚本所在目录。"
goto :fail

:fail
echo.
echo [失败] %MSG%
if "%NOPAUSE%"=="0" pause
popd
endlocal
exit /b 1
