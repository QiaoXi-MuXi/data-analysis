@echo off
rem ============================================================
rem  Zygo 面粗糙度 Ra 提取 - 一键启动
rem  双击本文件运行, 会提示每个区域取几张图 (直接回车 = 读取该区域全部图片)
rem  也可命令行传参:  run_ra.bat 3          (每区域 3 张)
rem                    run_ra.bat --batch 3 --no-plot
rem ============================================================
setlocal enabledelayedexpansion

cd /d "%~dp0"
title Zygo Ra 提取

rem ---- 让 Python 输出与 cmd 代码页一致, 避免中文乱码 ----
set "CP=936"
for /f "tokens=2 delims=:" %%c in ('chcp') do set "CP=%%c"
set "CP=%CP: =%"
if not defined CP set "CP=936"
set "PYTHONIOENCODING=cp%CP%"
set "PYTHONUTF8=0"

echo.
echo  ============================================================
echo    Zygo 面粗糙度 Ra 批量提取
echo  ============================================================
echo.

rem ---- 便携 Python: 与本目录同级的 python\ 文件夹 (整个父目录一起拷走即可迁移) ----
for %%i in ("%~dp0..\python\python.exe") do set "PYEXE=%%~fi"
if not exist "%PYEXE%" (
    echo  [错误] 找不到便携 Python: %PYEXE%
    echo         请保持目录结构不变:
    echo             ^<任意位置^>\python\python.exe
    echo             ^<任意位置^>\Ra\run_ra.bat
    echo.
    pause
    exit /b 1
)

rem 只用运行时自带的包, 不读本机用户 site-packages
set "PYTHONNOUSERSITE=1"
set "PYTHONPATH="
set "PYTHONHOME="

rem ---- 依赖自检: 只看包目录在不在, 不启动 python (快 0.3 s) ----
if not exist "%~dp0..\python\Lib\site-packages\numpy" (
    echo  [错误] 便携运行时缺少依赖包, 请执行:
    echo         "%~dp0..\python\python.exe" -m pip install -r "%~dp0..\python\requirements.txt"
    echo.
    pause
    exit /b 1
)

echo  使用解释器: %PYEXE%
echo  控制台代码页: %CP%    输出编码: %PYTHONIOENCODING%
echo.

rem ---- 每个区域几张图 ----
rem 三种写法: run_ra.bat 3 / run_ra.bat --batch 3 / 双击后按提示输入
set "BATCH_ARGS="
if "%~1"=="" goto :ask_batch

rem 第一个参数是纯数字时视为每个区域的图片数, 其余参数原样传给 Python
echo %~1|findstr /r /c:"^[1-9][0-9]*$" >nul
if errorlevel 1 goto :pass_all

set "N=%~1"
set "REST="
shift
:collect_rest
if "%~1"=="" goto :collect_done
set "REST=!REST! "%~1""
shift
goto :collect_rest
:collect_done
set "BATCH_ARGS=--batch %N%!REST!"
goto :args_ready

:pass_all
set "BATCH_ARGS=%*"
goto :args_ready

:ask_batch
set "BATCH="
set /p "BATCH=  每个区域取几张图 (填 5 = 只读 XX-1 ~ XX-5)? 直接回车 = 全部: "
if not defined BATCH goto :args_ready
echo !BATCH!|findstr /r /c:"^ *[1-9][0-9]* *$" >nul
if errorlevel 1 (
    echo  [提示] "!BATCH!" 不是正整数, 按整区全读处理
    goto :args_ready
)
set "BATCH_ARGS=--batch !BATCH!"
:args_ready

rem ---- 运行主脚本 ----
"%PYEXE%" "%~dp0extract_ra.py" %BATCH_ARGS%
set "ERRCODE=%ERRORLEVEL%"

echo.
if not "%ERRCODE%"=="0" (
    echo  [失败] 退出码 %ERRCODE%, 请查看上方提示
    echo.
    pause
    endlocal
    exit /b %ERRCODE%
)

echo  [完成] 结果已输出到本目录:
echo           ra_results.xlsx      主表格
echo           out 文件夹   三维表面图 (每张图一个 PNG)
echo.

echo  本次运行结束, 窗口自动关闭 (出错时才会停住)
endlocal
exit /b 0
