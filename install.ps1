# Spotarchive install script for Windows (PowerShell 5.1 or newer).
#
#   irm https://raw.githubusercontent.com/LargePenguin/spotarchive/main/install.ps1 | iex
#
# or, from a clone: powershell -ExecutionPolicy Bypass -File install.ps1
#
# Installs Spotarchive with all its dependencies into a virtualenv (.venv) in
# the project folder: the Python packages, SomeDL (the default download
# backend), the Chromium browser used for logging in and FFmpeg (via winget).
# Then puts a `spotarchive` command on your user PATH. Safe to run again to
# repair or update an install.
#
# Parameters (also settable through the environment variables in brackets
# when piping into iex):
#   -Dir DIR      where to clone Spotarchive when not run from a clone
#                 [SPOTARCHIVE_DIR, default %USERPROFILE%\spotarchive]
#   -BinDir DIR   where to put the command [SPOTARCHIVE_BIN_DIR,
#                 default %LOCALAPPDATA%\spotarchive\bin]
#   -NoBrowser    don't install Playwright's Chromium (Chrome or Edge is used instead)
#   -NoFfmpeg     don't install FFmpeg
#   -NoLink       don't put the command on PATH
#   -Dev          also install the test dependencies
#   -Yes          don't ask before installing system packages
[CmdletBinding()]
param(
    [string]$Dir = $(if ($env:SPOTARCHIVE_DIR) { $env:SPOTARCHIVE_DIR } else { Join-Path $HOME "spotarchive" }),
    [string]$BinDir = $(if ($env:SPOTARCHIVE_BIN_DIR) { $env:SPOTARCHIVE_BIN_DIR } else { Join-Path $env:LOCALAPPDATA "spotarchive\bin" }),
    [switch]$NoBrowser,
    [switch]$NoFfmpeg,
    [switch]$NoLink,
    [switch]$Dev,
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
$RepoUrl = "https://github.com/LargePenguin/spotarchive.git"

function Step($Message) { Write-Host "==> " -ForegroundColor Green -NoNewline; Write-Host $Message }
function Warn($Message) { Write-Host "[!] $Message" -ForegroundColor Yellow }
# Throws instead of exit: exit would close the window when run through iex.
function Die($Message) { Write-Host "[x] $Message" -ForegroundColor Red; throw "Spotarchive wasn't installed." }

function Confirm-Action($Question) {
    if ($Yes) { return $true }
    $answer = Read-Host "$Question [Y/n]"
    return ($answer -eq "" -or $answer -match "^[Yy]")
}

# Runs a native program and stops if it fails ($ErrorActionPreference doesn't
# cover exit codes in Windows PowerShell). Uses $args so that arguments like
# -m reach the program instead of being bound as parameters.
function Invoke-Checked {
    $program, $rest = $args
    & $program @rest
    if ($LASTEXITCODE -ne 0) { throw "$program $($rest -join ' ') failed with exit code $LASTEXITCODE" }
}

function Update-SessionPath {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
        [Environment]::GetEnvironmentVariable("Path", "User")
}

# --- Project folder ---------------------------------------------------------

$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { $null }
$pyproject = if ($ScriptDir) { Join-Path $ScriptDir "pyproject.toml" } else { $null }

if ($pyproject -and (Test-Path $pyproject) -and (Select-String -Path $pyproject -Pattern '^name = "spotarchive"' -Quiet)) {
    $ProjectDir = $ScriptDir
    Step "Using the Spotarchive folder $ProjectDir"
} elseif (Test-Path (Join-Path $Dir ".git") -PathType Container) {
    $ProjectDir = $Dir
    Step "Updating $ProjectDir"
    & git -C $ProjectDir pull --ff-only
    if ($LASTEXITCODE -ne 0) { Warn "Couldn't update the clone; installing the version that's there." }
} else {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Die "git is needed to download Spotarchive. Install it (winget install Git.Git) and run this again."
    }
    if (Test-Path $Dir) { Die "$Dir already exists and isn't a Spotarchive clone. Pick another folder with -Dir." }
    Step "Downloading Spotarchive into $Dir"
    Invoke-Checked git clone --depth 1 $RepoUrl $Dir
    $ProjectDir = $Dir
}

# A git worktree shares the main checkout's virtualenv; installing here would
# repoint it at the worktree (see CLAUDE.md).
if (Test-Path (Join-Path $ProjectDir ".git") -PathType Leaf) {
    Die "$ProjectDir is a git worktree. Run the install script from the main checkout."
}

# --- Python -----------------------------------------------------------------

$versionCheck = "import sys; sys.exit(sys.version_info < (3, 10))"
$Python = $null
foreach ($candidate in @(@("py", "-3"), @("python"), @("python3"))) {
    if (-not (Get-Command $candidate[0] -ErrorAction SilentlyContinue)) { continue }
    $launcherArgs = @($candidate | Select-Object -Skip 1)
    # The Microsoft Store "python" stub only prints an error; skip it.
    try { & $candidate[0] @launcherArgs -c $versionCheck 2>$null } catch { continue }
    if ($LASTEXITCODE -eq 0) { $Python = $candidate; break }
}
if (-not $Python) {
    Die "Spotarchive needs Python 3.10 or newer. Install it from https://www.python.org/downloads/ (or winget install Python.Python.3.12) and run this again."
}
$PythonExe = $Python[0]
$PythonArgs = @($Python | Select-Object -Skip 1)
Step "Using $(& $PythonExe @PythonArgs --version)"

$Venv = Join-Path $ProjectDir ".venv"
$VenvPython = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Step "Creating the virtualenv $Venv"
    Invoke-Checked $PythonExe @PythonArgs -m venv $Venv
}

$extras = if ($Dev) { "dev,somedl" } else { "somedl" }
Step "Installing Spotarchive and its Python dependencies (including SomeDL)"
Invoke-Checked $VenvPython -m pip install --upgrade --quiet pip
Invoke-Checked $VenvPython -m pip install --upgrade --editable "$ProjectDir[$extras]"

if (-not $NoBrowser) {
    Step "Installing the Chromium browser for the Spotify login"
    & $VenvPython -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { Warn "Couldn't install Chromium. The login can still use an installed Chrome or Edge." }
}

# --- FFmpeg -----------------------------------------------------------------

if (-not $NoFfmpeg) {
    if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
        Step "FFmpeg is already installed"
    } elseif (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Warn "winget isn't available. Install FFmpeg yourself before downloading: https://ffmpeg.org/download.html"
    } elseif (Confirm-Action "SomeDL needs FFmpeg. Install it with winget?") {
        Step "Installing FFmpeg"
        & winget install --id Gyan.FFmpeg --exact --silent --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) {
            Warn "Couldn't install FFmpeg. Install it yourself before downloading: https://ffmpeg.org/download.html"
        } else {
            Update-SessionPath
        }
    } else {
        Warn "Skipped FFmpeg. Install it before downloading: https://ffmpeg.org/download.html"
    }
}

# --- Command ----------------------------------------------------------------

$SpotarchiveExe = Join-Path $Venv "Scripts\spotarchive.exe"
if (-not $NoLink) {
    # A small launcher instead of adding .venv\Scripts to PATH, which would
    # also shadow python and pip.
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    Set-Content -Path (Join-Path $BinDir "spotarchive.cmd") -Encoding ASCII -Value "@`"$SpotarchiveExe`" %*"
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $entries = @(if ($userPath) { $userPath -split ";" | Where-Object { $_ } })
    if ($entries -notcontains $BinDir) {
        [Environment]::SetEnvironmentVariable("Path", (($entries + $BinDir) -join ";"), "User")
        Step "Added $BinDir to your user PATH (open a new terminal to use it)"
    }
    Update-SessionPath
    Step "Installed the spotarchive command into $BinDir"
}

& $SpotarchiveExe --version | Out-Null
if ($LASTEXITCODE -ne 0) { Die "The installed spotarchive command doesn't start." }

Write-Host ""
Write-Host "Spotarchive is installed." -ForegroundColor Green
Write-Host "Run spotarchive in the folder that should hold your music: it logs in,"
Write-Host "lets you pick playlists and downloads them into .\data\."
Write-Host "Docs: https://github.com/LargePenguin/spotarchive/blob/main/docs/README.md"
