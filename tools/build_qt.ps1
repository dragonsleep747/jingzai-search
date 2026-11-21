param([string]$OutputDirectory = 'dist\qt-desktop')
$ErrorActionPreference = 'Stop'
$workspacePath = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$qtPythonExecutable = Join-Path $workspacePath '.venv-qt\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $qtPythonExecutable)) {
    throw '请先创建 .venv-qt，并安装 PySide6。'
}
$buildOutput = [IO.Path]::GetFullPath((Join-Path $workspacePath $OutputDirectory))
if (-not $buildOutput.StartsWith($workspacePath + [IO.Path]::DirectorySeparatorChar)) {
    throw '构建输出必须位于当前工作区内。'
}
Push-Location (Join-Path $workspacePath 'search_app')
try {
    & $qtPythonExecutable (Join-Path $workspacePath 'tools\qt_package.py') --noconfirm --distpath $buildOutput --workpath (Join-Path $workspacePath 'build\qt-desktop') '镜仔搜索新版.spec'
    if ($LASTEXITCODE -ne 0) { throw "构建失败：$LASTEXITCODE" }
    $hashes = Get-ChildItem -LiteralPath (Join-Path $workspacePath 'search_app\qt_frontend') -Filter '*.py' |
        ForEach-Object { [PSCustomObject]@{ file = $_.Name; sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash } }
    $manifest = [PSCustomObject]@{
        built_at = (Get-Date -Format o)
        entry_sha256 = (Get-FileHash -LiteralPath (Join-Path $workspacePath 'search_app\qt_main.py') -Algorithm SHA256).Hash
        sources = @($hashes)
    }
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $buildOutput '镜仔搜索新版\build-manifest.json') -Encoding utf8
}
finally { Pop-Location }
