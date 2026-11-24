@echo off
rem ============================================================
rem  CLSM 3D topography - groove width / depth batch measurement
rem  Double-click to run. Options can also be passed:
rem      run.bat --no-plot
rem      run.bat --dir ..\CLSM\data
rem      run.bat --groove-axis vertical
rem      run.bat --samples 3 4
rem      run.bat --strict-inputs
rem ============================================================
setlocal

cd /d "%~dp0"
title CLSM groove width / depth - TXT and LSM

rem ---- keep Python output in the console code page ----
set "CP=936"
for /f "tokens=2 delims=:" %%c in ('chcp') do set "CP=%%c"
set "CP=%CP: =%"
if not defined CP set "CP=936"
set "PYTHONIOENCODING=cp%CP%"
set "PYTHONUTF8=0"
set "PYTHONNOUSERSITE=1"
set "PYTHONDONTWRITEBYTECODE=1"
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
    echo              ^<project^>\CLSM\run.bat
    echo.
    if not defined DATA_ANALYSIS_NO_PAUSE pause
    exit /b 1
)

if not exist "%~dp0..\python\Lib\site-packages\numpy" (
    echo  [ERROR] portable runtime is missing packages, run:
    echo          "%~dp0..\python\python.exe" -m pip install -r "%~dp0..\python\requirements.txt"
    echo.
    if not defined DATA_ANALYSIS_NO_PAUSE pause
    exit /b 1
)

echo  Python : %PYEXE%
echo  Console: cp%CP%
echo  Inputs : TXT / LSM; same-name TXT takes priority.
echo  Groove: global straight / local normal tracking; quality flags in Excel.
echo  LSM height calibration options are described in README.md.
echo  Unsupported / uncalibrated LSM: skip with reasons; --strict-inputs stops.
echo.

"%PYEXE%" -B "%~dp0extract_groove.py" --clean-out %*
set "ERRCODE=%ERRORLEVEL%"

echo.
if not "%ERRCODE%"=="0" (
    echo  [FAILED] exit code %ERRCODE%, see messages above
    echo.
    if not defined DATA_ANALYSIS_NO_PAUSE pause
    endlocal
    exit /b %ERRCODE%
)

echo  Output: results\groove_paper_results.xlsx  +  results\out\paper_v5\*.png  +  results\out\2D\*.png  +  results\out\3D\*.png
echo.
echo  Finished. Window closes in 3 seconds.
if not defined DATA_ANALYSIS_NO_PAUSE timeout /t 3 >nul
endlocal
exit /b 0
