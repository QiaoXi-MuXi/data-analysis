param([string]$Python = '', [string]$Output = '')
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
if (-not $Python) { $Python = Join-Path $projectRoot 'python\python.exe' }
if (-not $Output) { $Output = Join-Path $projectRoot 'DataAnalysis.exe' }
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) {
    $compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
}
if (-not (Test-Path -LiteralPath $compiler)) { throw '找不到 .NET Framework C# 编译器。' }
if (-not (Test-Path -LiteralPath $Python)) { throw '找不到项目便携 Python。' }
& $Python -I -B -X utf8 (Join-Path $PSScriptRoot 'make_icon.py')
if ($LASTEXITCODE -ne 0) { throw '图标构建失败。' }
$icon = Join-Path $projectRoot 'gui\assets\app.ico'
$manifest = Join-Path $PSScriptRoot 'app.manifest'
$source = Join-Path $PSScriptRoot 'Launcher.cs'
# 先写临时EXE，编译失败时保留已有的可用版本。
$temporaryExe = Join-Path ([IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($Output))) ('launcher-' + [Guid]::NewGuid().ToString('N') + '.exe')
try {
    & $compiler /nologo /target:winexe /platform:anycpu /optimize+ /codepage:65001 /reference:System.Windows.Forms.dll "/win32icon:$icon" "/win32manifest:$manifest" "/out:$temporaryExe" $source
    if ($LASTEXITCODE -ne 0) { throw '启动器编译失败，原EXE未替换。' }
    Move-Item -LiteralPath $temporaryExe -Destination $Output -Force
    Get-Item -LiteralPath $Output | Select-Object FullName, Length
} finally {
    if (Test-Path -LiteralPath $temporaryExe) { Remove-Item -LiteralPath $temporaryExe }
}
