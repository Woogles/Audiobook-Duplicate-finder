[CmdletBinding()]
param(
    [string]$LibraryPath,
    [switch]$ValidateOnly,
    [switch]$SkipLaunch
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function New-AppShortcut {
    $shell = $null
    try {
        $desktop = [Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)
        if ([string]::IsNullOrWhiteSpace($desktop)) {
            throw "The current user's Desktop folder could not be located."
        }
        $shortcutPath = Join-Path $desktop "Audiobook Duplicate Finder.lnk"
        $pythonw = Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"
        if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) {
            $pythonw = $VenvPython
        }

        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut($shortcutPath)
        $shortcut.TargetPath = $pythonw
        $shortcut.Arguments = "-m audiobook_finder.app"
        $shortcut.WorkingDirectory = $ProjectRoot
        $shortcut.Description = "Open Audiobook Duplicate Finder"
        $shortcut.IconLocation = "$pythonw,0"
        $shortcut.Save()
        Write-Host "Created one-click launcher: $shortcutPath" -ForegroundColor DarkGreen
    }
    catch {
        Write-Warning "Setup succeeded, but the Desktop shortcut could not be created: $_"
    }
    finally {
        if ($shell) {
            [void][Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
        }
    }
}

function Test-PythonExecutable([string]$Executable) {
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
        return $null
    }

    try {
        $output = @(& $Executable -c "import sys; print(sys.executable); print(str(sys.version_info.major) + '.' + str(sys.version_info.minor))" 2>$null)
        if ($LASTEXITCODE -ne 0 -or $output.Count -lt 2) {
            return $null
        }
        $versionText = [string]$output[-1]
        $version = [version]"$versionText.0"
        if ($version -lt [version]"3.11.0") {
            return $null
        }
        return [pscustomobject]@{
            Executable = [string]$output[0]
            Version = $versionText
        }
    }
    catch {
        return $null
    }
}

function Get-PythonRuntime {
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        foreach ($requestedVersion in @("3.14", "3.13", "3.12", "3.11")) {
            try {
                $output = @(& $launcher.Source "-$requestedVersion" -c "import sys; print(sys.executable); print(str(sys.version_info.major) + '.' + str(sys.version_info.minor))" 2>$null)
                if ($LASTEXITCODE -eq 0 -and $output.Count -ge 2) {
                    $versionText = [string]$output[-1]
                    $version = [version]"$versionText.0"
                    if ($version -ge [version]"3.11.0") {
                        return [pscustomobject]@{
                            Executable = [string]$output[0]
                            Version = $versionText
                        }
                    }
                }
            }
            catch {
                continue
            }
        }
    }

    $candidates = @()
    foreach ($commandName in @("python.exe", "python3.exe")) {
        $command = Get-Command $commandName -ErrorAction SilentlyContinue
        if ($command -and $command.Source -notmatch "\\WindowsApps\\") {
            $candidates += $command.Source
        }
    }

    if ($env:LOCALAPPDATA) {
        $candidatePatterns = @(
            (Join-Path $env:LOCALAPPDATA "Programs\Python\Python*\python.exe"),
            (Join-Path $env:LOCALAPPDATA "Python\pythoncore-*\python.exe")
        )
        foreach ($pattern in $candidatePatterns) {
            $candidates += @(Get-ChildItem -Path $pattern -File -ErrorAction SilentlyContinue |
                Sort-Object FullName -Descending |
                ForEach-Object { $_.FullName })
        }
    }

    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        $runtime = Test-PythonExecutable $candidate
        if ($runtime) {
            return $runtime
        }
    }
    return $null
}

function Resolve-LibraryPath([string]$Value) {
    $trimmed = $Value.Trim().Trim('"')
    if ([string]::IsNullOrWhiteSpace($trimmed)) {
        return $null
    }
    try {
        $fullPath = [System.IO.Path]::GetFullPath($trimmed)
    }
    catch {
        throw "The library path is not valid: $trimmed"
    }
    if (-not (Test-Path -LiteralPath $fullPath -PathType Container)) {
        throw "The library folder does not exist or is not a folder: $fullPath"
    }
    try {
        $enumerator = [System.IO.Directory]::EnumerateFileSystemEntries($fullPath).GetEnumerator()
        try { $null = $enumerator.MoveNext() } finally { $enumerator.Dispose() }
    }
    catch {
        throw "The library folder cannot be read with the current account: $fullPath"
    }
    return $fullPath
}

try {
    Write-Host "Audiobook Duplicate Finder setup" -ForegroundColor Green
    if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot "pyproject.toml") -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $ProjectRoot "audiobook_finder\app.py") -PathType Leaf)) {
        throw "The installer must be run from the project folder; required application files were not found."
    }

    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Warning "This shell is elevated. Administrator access is not required; setup will still install per-user and into .venv."
    }
    else {
        Write-Host "Running as a standard user. No administrator access is needed." -ForegroundColor DarkGreen
    }

    if ([string]::IsNullOrWhiteSpace($LibraryPath) -and -not $ValidateOnly) {
        $LibraryPath = Read-Host "Audiobook library folder to save as the app default (press Enter to choose later)"
    }
    $ValidatedLibraryPath = Resolve-LibraryPath $LibraryPath

    Write-Step "Checking Python 3.11 or newer"
    $runtime = Get-PythonRuntime
    if (-not $runtime -and $ValidateOnly) {
        throw "No supported Python installation was found. Run install.ps1 normally to install Python per-user, or install Python 3.11+ and retry."
    }
    if (-not $runtime) {
        $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
        if (-not $winget) {
            throw "Python is missing and winget is unavailable. Install Python 3.11 or newer for your user, then run this installer again. No administrator terminal is required."
        }
        Write-Host "Installing Python 3.14 for the current user through winget..."
        & $winget.Source install --id Python.Python.3.14 --exact --scope user --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) {
            throw "The per-user Python installation failed (winget exit code $LASTEXITCODE). Install Python 3.11+ for your user and rerun this setup."
        }
        $runtime = Get-PythonRuntime
        if (-not $runtime) {
            throw "Python installation completed but this shell could not locate it. Close this window, open the installer again, and retry."
        }
    }
    Write-Host "Using Python $($runtime.Version): $($runtime.Executable)"

    if ($ValidateOnly) {
        if ($ValidatedLibraryPath) {
            Write-Host "Library path is readable: $ValidatedLibraryPath" -ForegroundColor DarkGreen
        }
        else {
            Write-Host "No default library path was provided."
        }
        Write-Host "Validation passed. No packages, settings, or files were changed." -ForegroundColor Green
        exit 0
    }

    Write-Step "Preparing the project virtual environment"
    if (Test-Path -LiteralPath $VenvPython -PathType Leaf) {
        $existingRuntime = Test-PythonExecutable $VenvPython
        if (-not $existingRuntime) {
            throw "The existing .venv is incomplete or uses Python older than 3.11. Rename or remove .venv yourself, then rerun setup."
        }
        Write-Host "Reusing .venv with Python $($existingRuntime.Version)."
    }
    else {
        $venvDirectory = Join-Path $ProjectRoot ".venv"
        if (Test-Path -LiteralPath $venvDirectory) {
            throw "The .venv folder exists but has no usable Python executable. Rename or remove it yourself, then rerun setup."
        }
        & $runtime.Executable -m venv $venvDirectory
        if ($LASTEXITCODE -ne 0) {
            throw "Could not create the project virtual environment."
        }
    }

    Write-Step "Installing application and test dependencies"
    & $VenvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "Could not update pip in .venv." }
    & $VenvPython -m pip install -e $ProjectRoot
    if ($LASTEXITCODE -ne 0) { throw "Could not install the application dependencies." }

    Write-Step "Checking the application and test suite"
    & $VenvPython -c "import mutagen, PySide6; from audiobook_finder.app import MainWindow; print('Imports passed')"
    if ($LASTEXITCODE -ne 0) { throw "An application dependency failed its import check." }
    & $VenvPython -m unittest discover -s (Join-Path $ProjectRoot "tests") -v
    if ($LASTEXITCODE -ne 0) { throw "The test suite failed. Review the test output before using the app." }

    if ($ValidatedLibraryPath) {
        if (-not $env:LOCALAPPDATA) {
            $localAppData = Join-Path $HOME "AppData\Local"
        }
        else {
            $localAppData = $env:LOCALAPPDATA
        }
        $settingsDirectory = Join-Path $localAppData "AudiobookDuplicateFinder"
        New-Item -ItemType Directory -Path $settingsDirectory -Force | Out-Null
        $settingsFile = Join-Path $settingsDirectory "settings.json"
        $settingsJson = @{ library_path = $ValidatedLibraryPath } | ConvertTo-Json
        Set-Content -LiteralPath $settingsFile -Value $settingsJson -Encoding UTF8
        Write-Host "Saved default library folder: $ValidatedLibraryPath"
    }

    if (Get-Command fpcalc.exe -ErrorAction SilentlyContinue) {
        Write-Host "Optional audio fingerprinting is available (fpcalc found)." -ForegroundColor DarkGreen
    }
    elseif ($env:FPCALC -and (Test-Path -LiteralPath $env:FPCALC -PathType Leaf)) {
        Write-Host "Optional audio fingerprinting is available through FPCALC." -ForegroundColor DarkGreen
    }
    else {
        Write-Warning "Optional fingerprint matching is not configured. Normal scanning works; add Chromaprint fpcalc to PATH or set FPCALC to its full path to enable it."
    }

    New-AppShortcut

    Write-Host "`nSetup complete. No administrator access was used or required." -ForegroundColor Green
    if (-not $SkipLaunch) {
        $launch = Read-Host "Launch Audiobook Duplicate Finder now? [Y/n]"
        if ([string]::IsNullOrWhiteSpace($launch) -or $launch -match "^(y|yes)$") {
            & $VenvPython -m audiobook_finder.app
        }
    }
}
catch {
    Write-Error $_
    exit 1
}