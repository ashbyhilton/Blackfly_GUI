# Build the standalone Windows app and zip it for sharing.
#   powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "No .venv found. See README 'Developer setup'." }

& $py -m PyInstaller blackfly_gui.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$version = (& $py -c "import blackfly_gui; print(blackfly_gui.__version__)").Trim()
$zip = Join-Path $root "dist\BlackflyGUI-$version-win64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path (Join-Path $root "dist\BlackflyGUI") -DestinationPath $zip
Write-Host "Built dist\BlackflyGUI\BlackflyGUI.exe and $zip"
