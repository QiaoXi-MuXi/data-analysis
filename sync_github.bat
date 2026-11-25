@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 936 >nul
title Data Analysis - GitHub Sync
set "SYNC_EXIT=1"
set "SYNC_ENTERED="
set "SYNC_PAUSE=1"
if /i "%~1"=="--no-pause" set "SYNC_PAUSE=0"

rem 使用脚本所在目录，支持空格和中文路径。
pushd "%~dp0"
if errorlevel 1 goto directory_error
set "SYNC_ENTERED=1"
where git >nul 2>nul
if errorlevel 1 goto dependency_error
where gh >nul 2>nul
if errorlevel 1 goto dependency_error

rem 仅影响本脚本进程，使用 GitHub CLI 已保存的登录。
set "GH_TOKEN="
set "GITHUB_TOKEN="
git rev-parse --is-inside-work-tree >nul 2>nul
if errorlevel 1 goto repository_error
set "SYNC_BRANCH="
for /f "delims=" %%B in ('git branch --show-current') do set "SYNC_BRANCH=%%B"
if not "%SYNC_BRANCH%"=="main" goto repository_error
set "SYNC_REMOTE="
for /f "delims=" %%R in ('git config --get remote.origin.url') do set "SYNC_REMOTE=%%R"
if not "%SYNC_REMOTE%"=="https://github.com/QiaoXi-MuXi/data-analysis.git" goto repository_error
for /f "delims=" %%R in ('git config --get-all remote.origin.pushurl') do if not "%%R"=="https://github.com/QiaoXi-MuXi/data-analysis.git" goto repository_error

rem 合并或变基尚未完成时不自动提交。
for %%S in (MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD rebase-merge rebase-apply) do (
    for /f "delims=" %%P in ('git rev-parse --git-path %%S') do if exist "%%P" goto state_error
)

rem 先检查忽略规则，避免规则被误删后把实验数据暂存。
for %%P in ("Ra/data/__sync_check__" "Raman/data/__sync_check__" "Raman/out/__sync_check__" "nested/data/__sync_check__" "nested/out/__sync_check__" "Ra/ra_results.xlsx" "Ra/surface_plots/__sync_check__") do (
    git check-ignore --quiet --no-index -- "%%~P"
    if errorlevel 1 goto ignore_error
)
call :check_excluded
if errorlevel 1 goto excluded_error

echo.
echo [1/3] 正在暂存代码、文档和配置改动...
git add --all
if errorlevel 1 goto git_error
call :check_excluded
if errorlevel 1 goto excluded_error

echo.
echo [2/3] 正在检查并提交...
git diff --cached --quiet
if errorlevel 2 goto git_error
if errorlevel 1 (
    git diff --cached --stat
    git commit --quiet -m "自动同步 %date% %time%"
    if errorlevel 1 goto git_error
) else (
    echo 没有新的文件改动，跳过提交。
)

echo.
echo [3/3] 正在推送到 GitHub 仓库...
git push origin main
if errorlevel 1 goto push_error
echo.
echo [完成] 已同步到 https://github.com/QiaoXi-MuXi/data-analysis
git status --short --branch
set "SYNC_EXIT=0"
goto finish

:check_excluded
set "SYNC_BLOCKED="
for /f "delims=" %%F in ('git ls-files -- ":(glob)**/data/**" ":(glob)**/out/**" ":(glob)**/__pycache__/**" "Ra/ra_results.xlsx" "Ra/surface_plots"') do (
    echo [禁止上传] "%%F"
    set "SYNC_BLOCKED=1"
)
if defined SYNC_BLOCKED exit /b 1
exit /b 0

:directory_error
echo [错误] 无法进入脚本所在目录。
goto finish
:dependency_error
echo [错误] 需要 Git 和 GitHub CLI，请确认 git、gh 在 PATH 中。
goto finish
:repository_error
echo [错误] 需要在本项目的 main 分支运行，origin 必须指向指定的 data-analysis 仓库。
goto finish
:state_error
echo [错误] 有未完成的合并、变基或拣选，请先手动处理。
goto finish
:ignore_error
echo [错误] data、out 或 Ra 结果的忽略规则不完整。请先恢复 .gitignore。
goto finish
:excluded_error
echo [错误] 发现已被 Git 跟踪的数据或输出文件，已停止同步。请检查上方路径。
goto finish
:git_error
echo [错误] 暂存或提交失败，请查看上方 Git 提示，处理后重试。
goto finish
:push_error
echo [错误] 推送失败，本地提交已保留，未强制推送。
echo 请查看上方提示；网络恢复后可重试。登录失效时运行 gh auth login。
echo 若远端已有新提交，请先处理分支差异后重试；本脚本不会自动合并。
goto finish

:finish
if defined SYNC_ENTERED popd
echo.
if "%SYNC_PAUSE%"=="1" pause
exit /b %SYNC_EXIT%
