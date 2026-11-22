<#
.SYNOPSIS
    Installs or updates Screenshot Buffer on Windows from GitHub Releases.

.DESCRIPTION
    Copy-paste into PowerShell:

        irm https://github.com/edward3423/screenshotbuffer/releases/latest/download/install.ps1 | iex

    With options:

        & ([scriptblock]::Create((irm https://github.com/edward3423/screenshotbuffer/releases/latest/download/install.ps1))) -StartAtLogin

    Installs for the current user only (no administrator rights) into
    %LOCALAPPDATA%\Programs\ScreenshotBuffer, adds a Start menu shortcut and an
    entry in Settings > Apps, and starts the app. Running it again updates to
    the newest release and keeps your settings.

.PARAMETER StartAtLogin
    Also start Screenshot Buffer when you sign in to Windows.

.PARAMETER Version
    Release tag to install, e.g. v0.1.0. Defaults to the latest release.

.PARAMETER NoLaunch
    Do not start the app after installing.

.PARAMETER Uninstall
    Remove Screenshot Buffer (settings are kept).
#>
[CmdletBinding()]
param(
    [switch]$StartAtLogin,
    [string]$Version = 'latest',
    [switch]$NoLaunch,
    [switch]$Uninstall
)

function Install-ScreenshotBuffer {
    param([bool]$StartAtLogin, [string]$Version, [bool]$NoLaunch, [bool]$Uninstall)

    # Function scope, so piping into iex does not change the caller's session.
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'  # the progress bar makes downloads very slow on PowerShell 5
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

    $Repo         = 'edward3423/screenshotbuffer'
    $AppName      = 'Screenshot Buffer'
    $Asset        = 'ScreenshotBuffer-windows-x64.zip'
    $InstallDir   = Join-Path $env:LOCALAPPDATA 'Programs\ScreenshotBuffer'
    $Exe          = Join-Path $InstallDir 'ScreenshotBuffer.exe'
    $MenuLink     = Join-Path ([Environment]::GetFolderPath('Programs')) "$AppName.lnk"
    $StartupLink  = Join-Path ([Environment]::GetFolderPath('Startup')) "$AppName.lnk"
    $UninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\ScreenshotBuffer'

    if ($Uninstall) {
        $uninstaller = Join-Path $InstallDir 'uninstall.ps1'
        if (-not (Test-Path -LiteralPath $uninstaller)) {
            Write-Host "$AppName is not installed."
            return
        }
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $uninstaller
        return
    }

    if (-not [Environment]::Is64BitOperatingSystem) { throw "$AppName needs 64-bit Windows." }

    $base = if ($Version -eq 'latest') {
        "https://github.com/$Repo/releases/latest/download"
    } else {
        "https://github.com/$Repo/releases/download/$Version"
    }

    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("ScreenshotBuffer-" + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tmp | Out-Null
    try {
        Write-Host "Downloading $AppName ($Version)..."
        $zip = Join-Path $tmp $Asset
        try {
            Invoke-WebRequest -UseBasicParsing -Uri "$base/$Asset" -OutFile $zip
            $sums = (Invoke-WebRequest -UseBasicParsing -Uri "$base/SHA256SUMS").Content
        }
        catch {
            throw "Could not download $Asset from $base ($($_.Exception.Message)). Check the version and your connection."
        }
        if ($sums -is [byte[]]) { $sums = [Text.Encoding]::UTF8.GetString($sums) }

        $expected = ($sums -split "`n" | Where-Object { $_ -match "^\s*([0-9a-fA-F]{64})\s+\*?$([regex]::Escape($Asset))\s*$" } |
            ForEach-Object { $Matches[1] } | Select-Object -First 1)
        if (-not $expected) { throw "No checksum for $Asset in SHA256SUMS." }
        $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash
        if ($actual -ne $expected.ToUpperInvariant()) {
            throw "Checksum mismatch for $Asset (expected $expected, got $actual). Try again."
        }

        $staging = Join-Path $tmp 'extract'
        Expand-Archive -LiteralPath $zip -DestinationPath $staging
        $newDir = Join-Path $staging 'ScreenshotBuffer'
        $newExe = Join-Path $newDir 'ScreenshotBuffer.exe'
        if (-not (Test-Path -LiteralPath $newExe)) { throw "The download does not contain ScreenshotBuffer.exe." }
        $newVersion = (Get-Content -LiteralPath (Join-Path $newDir 'VERSION.txt') -ErrorAction SilentlyContinue | Select-Object -First 1)

        # Stop a running copy (any version) so its files can be replaced.
        Start-Process -FilePath $newExe -ArgumentList '--quit' -Wait -WindowStyle Hidden
        $running = { Get-Process -ErrorAction SilentlyContinue | Where-Object {
                $_.Path -and $_.Path.StartsWith($InstallDir, [StringComparison]::OrdinalIgnoreCase) } }
        $deadline = (Get-Date).AddSeconds(5)
        while ((& $running) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 200 }
        & $running | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 300

        if (Test-Path -LiteralPath $InstallDir) { Remove-Item -LiteralPath $InstallDir -Recurse -Force }
        New-Item -ItemType Directory -Path (Split-Path $InstallDir) -Force | Out-Null
        Move-Item -LiteralPath $newDir -Destination $InstallDir
    }
    finally {
        Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }

    $shell = New-Object -ComObject WScript.Shell
    $links = @($MenuLink)
    if ($StartAtLogin) { $links += $StartupLink }
    foreach ($path in $links) {
        $link = $shell.CreateShortcut($path)
        $link.TargetPath = $Exe
        $link.WorkingDirectory = $InstallDir
        $link.IconLocation = "$Exe,0"
        $link.Description = 'Copy what was on screen a few seconds ago'
        $link.Save()
    }

    # Entry in Settings > Apps so it can be uninstalled like any other app.
    New-Item -Path $UninstallKey -Force | Out-Null
    $size = [int]((Get-ChildItem -LiteralPath $InstallDir -Recurse -File | Measure-Object Length -Sum).Sum / 1KB)
    $values = @{
        DisplayName     = $AppName
        DisplayVersion  = "$newVersion"
        Publisher       = 'edward3423'
        DisplayIcon     = "$Exe,0"
        InstallLocation = $InstallDir
        URLInfoAbout    = "https://github.com/$Repo"
        UninstallString = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$InstallDir\uninstall.ps1`""
    }
    foreach ($name in $values.Keys) { Set-ItemProperty -Path $UninstallKey -Name $name -Value $values[$name] }
    Set-ItemProperty -Path $UninstallKey -Name EstimatedSize -Value $size -Type DWord
    Set-ItemProperty -Path $UninstallKey -Name NoModify -Value 1 -Type DWord
    Set-ItemProperty -Path $UninstallKey -Name NoRepair -Value 1 -Type DWord

    Write-Host ""
    Write-Host "$AppName $newVersion installed to $InstallDir"
    Write-Host "  Start menu: $AppName"
    if (Test-Path -LiteralPath $StartupLink) { Write-Host "  Starts automatically when you sign in." }
    Write-Host "  Ctrl+PrtScn copies the screen from a few seconds ago; Shift+PrtScn shows or hides the preview."

    if (-not $NoLaunch) { Start-Process -FilePath $Exe -WorkingDirectory $InstallDir }
}

Install-ScreenshotBuffer -StartAtLogin $StartAtLogin.IsPresent -Version $Version -NoLaunch $NoLaunch.IsPresent -Uninstall $Uninstall.IsPresent
