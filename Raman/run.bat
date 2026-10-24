@echo off
setlocal enabledelayedexpansion

rem ============================================================
rem  Raman 出图
rem
rem  有 stack.xlsx  -> 按表格堆叠对比出图，全自动，不问任何参数
rem  没有 stack.xlsx -> 逐个 txt 单谱出图，运行时问区间和峰高
rem                    (直接回车用默认值，区间默认 = 数据本身的波数范围)
rem
rem  成功后窗口自动关闭；只有出错时才停下等按键
rem ============================================================

set "HEIGHT=5"
set "XSHIFT=0"

cd /d "%~dp0"

if not exist "data" (
    echo   [错误] 找不到 data 文件夹
    echo          请把 txt 数据放进 data 文件夹后重试
    echo.
    pause
    exit /b 1
)

set "DATA_N=0"
for %%f in (data\*.txt) do set /a DATA_N+=1
if "%DATA_N%"=="0" (
    echo   [错误] data 文件夹里没有 txt 文件
    echo.
    pause
    exit /b 1
)

rem ---- 便携 Python: 与本目录同级的 python\ 文件夹 (整个目录一起拷贝迁移) ----
for %%i in ("%~dp0..\python\python.exe") do set "PY=%%~fi"
if not exist "%PY%" (
    rem 兜底: 便携版缺失时退回 PATH 里的 python
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

rem 只用本地临时态的包, 不加载用户 site-packages
set "PYTHONNOUSERSITE=1"
set "PYTHONPATH="

rem 依赖缺失由 plot_raman.py 自己报错 (少起一个解释器, 省 ~1 s)

if exist "stack.xlsx" goto :stacked

rem ================= 单谱模式 =================
rem ---- 默认区间: 取 data 里所有 txt 第一列的最小/最大值 ----
rem 结果先落到临时文件再读: for /f 直接跑带引号的命令行会拆错参数
set "DEF_XMIN="
set "DEF_XMAX="
set "RANGE_TMP=%TEMP%\raman_range.tmp"
"%PY%" -c "import sys,glob,numpy as np;d=sys.argv[1];b=[np.loadtxt(f,comments='#')[:,0] for f in glob.glob(d+'/*.txt')];print(round(min(a.min() for a in b),2),round(max(a.max() for a in b),2))" "%~dp0data" > "%RANGE_TMP%" 2>nul
for /f "usebackq tokens=1,2" %%a in ("%RANGE_TMP%") do (
    set "DEF_XMIN=%%a"
    set "DEF_XMAX=%%b"
)
del "%RANGE_TMP%" >nul 2>nul
if not defined DEF_XMAX (
    rem 读不出数据范围时退回旧默认值
    set "DEF_XMIN=500"
    set "DEF_XMAX=1000"
    echo   [提示] 读不出数据波数范围，默认区间用 500 - 1000
)

echo.
echo   找到 %DATA_N% 个 txt 文件，直接回车使用默认值
call :ask XMIN "区间左边界 cm-1" !DEF_XMIN!
call :ask XMAX "区间右边界 cm-1" !DEF_XMAX!
call :ask HEIGHT "峰高阈值 H" !HEIGHT!

echo.
echo   Raman 出图参数
echo   区间    !XMIN! - !XMAX! cm-1
echo   峰高    H = !HEIGHT! counts
echo   峰位校正 %XSHIFT% cm-1
echo.
echo   使用解释器: %PY%
echo   正在出图 ...
echo.

"%PY%" "%~dp0plot_raman.py" --xmin !XMIN! --xmax !XMAX! --height !HEIGHT! --shift %XSHIFT%
goto :done

rem ================= 堆叠模式 =================
:stacked
echo.
echo   检测到 stack.xlsx，按表格堆叠对比出图（用 Excel 打开表格改内容）
echo.
echo   使用解释器: %PY%
echo   正在出图 ...
echo.

"%PY%" "%~dp0plot_raman.py" --stack "%~dp0stack.xlsx"

:done
set "RC=%errorlevel%"

if "%RC%"=="2" (
    echo.
    echo   [注意] 有部分文件没有写出，详情见上方 FAILED 行
    echo          其余图片正常生成
    start "" "out"
    echo.
    pause
    exit /b 2
)
if not "%RC%"=="0" (
    echo.
    echo   [错误] 出图失败，请检查上面的提示
    echo.
    pause
    exit /b 1
)

echo.
echo   完成，图片已生成到 out 文件夹
start "" "out"
exit /b 0

rem ---- 子程序: 读一项参数, 空输入或纯空格则用默认值 ----
:ask
set "IN="
set /p "IN=  %~2 [默认 %~3]: "
if defined IN set "IN=%IN: =%"
if not defined IN set "IN=%~3"
set "%~1=%IN%"
goto :eof
