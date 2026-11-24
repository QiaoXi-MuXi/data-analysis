@echo off
setlocal
call "%~dp0run.bat" --excel "%~dp0templates\removal_rate.xlsx" %*
set "RC=%ERRORLEVEL%"
if "%RC%"=="0" if "%~1"=="" if not defined DATA_ANALYSIS_NO_OPEN start "" "%~dp0out"
exit /b %RC%
