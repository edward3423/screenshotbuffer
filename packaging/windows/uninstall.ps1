<#
    Removes Screenshot Buffer installed by install.ps1 (this file ships inside
    the install folder and is what "Apps & features" runs). Settings in
    %APPDATA%\ScreenshotBuffer are kept.
#>
$ErrorActionPreference = 'Stop'

$AppName    = 'Screenshot Buffer'
$InstallDir = $PSScriptRoot
$Exe        = Join-Path $InstallDir 'ScreenshotBuffer.exe'
$UninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\ScreenshotBuffer'

function Get-AppProcesses {
    Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -and $_.Path.StartsWith($InstallDir, [StringComparison]::OrdinalIgnoreCase)
    }
}

# Ask the running app to quit, then make sure it is gone.
if (Test-Path -LiteralPath $Exe) {
    Start-Process -FilePath $Exe -ArgumentList '--quit' -Wait -WindowStyle Hidden
}
$deadline = (Get-Date).AddSeconds(5)
while ((Get-AppProcesses) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 200 }
Get-AppProcesses | Stop-Process -Force -ErrorAction SilentlyContinue

foreach ($folder in 'Programs', 'Startup') {
    $link = Join-Path ([Environment]::GetFolderPath($folder)) "$AppName.lnk"
    if (Test-Path -LiteralPath $link) { Remove-Item -LiteralPath $link -Force }
}
if (Test-Path -LiteralPath $UninstallKey) { Remove-Item -LiteralPath $UninstallKey -Recurse -Force }

Set-Location -LiteralPath $env:TEMP
Remove-Item -LiteralPath $InstallDir -Recurse -Force

Write-Host "$AppName was uninstalled. Your settings in $env:APPDATA\ScreenshotBuffer were kept."
