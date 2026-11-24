@echo off
setlocal
if not exist "%~dp0DataAnalysis.exe" goto missing_exe
if /i "%~1"=="--check" goto check
"%~dp0DataAnalysis.exe" %*
exit /b %ERRORLEVEL%
:check
"%~dp0DataAnalysis.exe" --check >nul 2>&1
set "CODE=%ERRORLEVEL%"
set "CP=936"
for /f "tokens=2 delims=:" %%c in ('chcp') do set "CP=%%c"
set "CP=%CP: =%"
chcp 65001 >nul
if "%CODE%"=="0" (
    if exist "%~dp0out\gui\startup_check.log" type "%~dp0out\gui\startup_check.log"
) else (
    if exist "%~dp0out\gui\launcher_error.log" type "%~dp0out\gui\launcher_error.log"
)
chcp %CP% >nul
exit /b %CODE%
:missing_exe
echo [错误] 找不到 DataAnalysis.exe，请检查项目是否完整。
echo 启动器重建说明：gui\launcher\README.md
if /i "%~1"=="--check" exit /b 1
if not defined DATA_ANALYSIS_NO_PAUSE pause
exit /b 1
