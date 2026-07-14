$ErrorActionPreference = "Stop"

$Architecture = $env:PROCESSOR_ARCHITEW6432
if ([string]::IsNullOrWhiteSpace($Architecture)) {
    $Architecture = $env:PROCESSOR_ARCHITECTURE
}
if (-not [Environment]::Is64BitOperatingSystem) {
    throw "ImageStatsExplorer requires a 64-bit Windows operating system."
}
if (
    -not [string]::IsNullOrWhiteSpace($Architecture) -and
    $Architecture -notin @("AMD64", "x86_64")
) {
    throw "ImageStatsExplorer requires Windows x64; detected $Architecture."
}

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectDir

uv sync --locked --dev --no-install-project
if ($LASTEXITCODE -ne 0) {
    throw "Locked dependency installation failed with exit code $LASTEXITCODE."
}

# Build the local package with the locked setuptools already in the environment.
uv sync --locked --dev --no-build-isolation
if ($LASTEXITCODE -ne 0) {
    throw "Local package installation failed with exit code $LASTEXITCODE."
}

Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
uv run --locked pyinstaller --clean --noconfirm ImageStatsExplorer.spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE."
}

$Executable = Join-Path $ProjectDir "dist\ImageStatsExplorer.exe"
if (-not (Test-Path $Executable)) {
    throw "Build did not produce $Executable"
}
Write-Host "Built: $Executable"
