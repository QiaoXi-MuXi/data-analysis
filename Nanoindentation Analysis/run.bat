@echo off
rem ============================================================
rem  纳米压痕批量分析 - 一键出图
rem  流程: fill_config.bat 导入 Excel -> 用户设置 config.xlsx -> run.bat 出图
rem  参数原样传递, 例如:
rem    run.bat --src "D:\batch" --out "D:\result"
rem    run.bat --config "另一张表.xlsx"
rem    run.bat --no-curves --no-plots
rem ============================================================
setlocal enabledelayedexpansion

cd /d "%~dp0"
title Nanoindentation Analysis

rem ---- 让 Python 输出与控制台代码页一致 ----
set "CP=936"
for /f "tokens=2 delims=:" %%c in ('chcp') do set "CP=%%c"
set "CP=%CP: =%"
if not defined CP set "CP=936"
set "PYTHONIOENCODING=cp%CP%"
set "PYTHONUTF8=0"

echo.
echo  ============================================================
echo    纳米压痕数据分析  Nanoindentation Analysis
echo  ============================================================
echo.

rem ---- 便携 Python: 与本目录同级的 python\ ----
for %%i in ("%~dp0..\python\python.exe") do set "PYEXE=%%~fi"
if not exist "%PYEXE%" (
    echo  [错误] 找不到便携 Python: %PYEXE%
    echo         请保持目录结构: 项目根\python\python.exe
    echo.
    if not defined DATA_ANALYSIS_NO_PAUSE pause
    exit /b 1
)

set "PYTHONNOUSERSITE=1"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONPATH="
set "PYTHONHOME="

rem ---- 依赖自检: 只读包目录, 不启动 Python ----
if not exist "%~dp0..\python\Lib\site-packages\xlrd" (
    echo  [错误] 便携运行时缺少 xlrd, 无法读取 xls
    echo         请执行:
    echo         "%PYEXE%" -m pip install -r "%~dp0..\python\requirements.txt"
    echo.
    if not defined DATA_ANALYSIS_NO_PAUSE pause
    exit /b 1
)

if not exist "%~dp0config.xlsx" (
    echo  第一次运行: 先按 data\ 生成 config.xlsx 配置表
)

echo  使用解释器: %PYEXE%
echo.

"%PYEXE%" -B -X utf8 "%~dp0nano_analysis.py" %*
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
    echo  [失败] 退出码 %RC%, 请查看上方提示
    echo.
    if not defined DATA_ANALYSIS_NO_PAUSE pause
    endlocal
    exit /b %RC%
)

echo  [完成] 结果已写入 out\ :
echo          nano_results.xlsx   分组统计/配对检验/质控
echo          report.md           数据结论
echo          summary.json        机器可读汇总
echo          indent_detail.csv   逐次压痕数据
echo          figures\index.md    图片索引(总图/每样品/每文件)
echo.
echo  想改图例名称或样品编号: 打开 config.xlsx 修改后重跑本脚本
echo  想重新导入 data\ 里的文件: 双击 fill_config.bat
echo.
echo  本次运行结束, 窗口自动关闭 (出错时才停住)
endlocal
exit /b 0
