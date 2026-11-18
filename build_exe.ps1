# 用系统自带的 .NET Framework 编译器重新打包启动器
# 用法：powershell -ExecutionPolicy Bypass -File .\build_exe.ps1

$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    $csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
    if (-not (Test-Path -LiteralPath $csc)) {
        $csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
    }
    if (-not (Test-Path -LiteralPath $csc)) {
        throw '找不到 csc.exe，这台机器没有 .NET Framework 4.x 编译器。'
    }

    & $csc /nologo /target:winexe /optimize `
        /out:'连点器.exe' `
        /win32icon:'icon.ico' `
        /win32manifest:'admin.manifest' `
        'launcher.cs'

    if ($LASTEXITCODE -ne 0) { throw "编译失败，csc 退出码 $LASTEXITCODE。" }
    Get-Item -LiteralPath '连点器.exe' | Select-Object Name, Length, LastWriteTime
}
finally {
    Pop-Location
}
