@echo off
rem ============================================================
rem  CLSM 3D topography - groove width / depth batch measurement
rem  Double-click to run. Options can also be passed:
rem      run_clsm.bat --no-plot
rem      run_clsm.bat --dir ..\CLSM\data\3d
rem ============================================================
setlocal

cd /d "%~dp0"
title CLSM groove width / depth

rem ---- keep Python output in the console code page ----
set "CP=936"
for /f "tokens=2 delims=:" %%c in ('chcp') do set "CP=%%c"
set "CP=%CP: =%"
if not defined CP set "CP=936"
set "PYTHONIOENCODING=cp%CP%"
set "PYTHONUTF8=0"
set "PYTHONNOUSERSITE=1"
set "PYTHONPATH="
set "PYTHONHOME="

echo.
echo  ============================================================
echo    CLSM 3D topography - groove width / depth
echo  ============================================================
echo.

rem ---- portable Python lives one level up: ..\python ----
for %%i in ("%~dp0..\python\python.exe") do set "PYEXE=%%~fi"
if not exist "%PYEXE%" (
    echo  [ERROR] portable Python not found: %PYEXE%
    echo          expected layout:
    echo              ^<project^>\python\python.exe
    echo              ^<project^>\CLSM\run_clsm.bat
    echo.
    pause
    exit /b 1
)

if not exist "%~dp0..\python\Lib\site-packages\numpy" (
    echo  [ERROR] portable runtime is missing packages, run:
    echo          "%~dp0..\python\python.exe" -m pip install -r "%~dp0..\python\requirements.txt"
    echo.
    pause
    exit /b 1
)

echo  Python : %PYEXE%
echo  Console: cp%CP%
echo.

"%PYEXE%" "%~dp0extract_groove.py" --clean-out %*
set "ERRCODE=%ERRORLEVEL%"

echo.
if not "%ERRCODE%"=="0" (
    echo  [FAILED] exit code %ERRCODE%, see messages above
    echo.
    pause
    endlocal
    exit /b %ERRCODE%
)

echo  Output: out\paper_v4\groove_paper_results.xlsx  +  out\2D\*.png  +  out\3D\*.png
echo.
echo  Finished. Window closes in 3 seconds.
timeout /t 3 >nul
endlocal
exit /b 0