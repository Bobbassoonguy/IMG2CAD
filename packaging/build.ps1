# Build img2cad into a standalone windowed app (dist\img2cad\img2cad.exe).
# Run from the project root:  powershell -ExecutionPolicy Bypass -File packaging\build.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

# refresh the icon in case make_icon.py changed
& $py packaging\make_icon.py

& $py -m PyInstaller --noconfirm --clean --windowed --onedir --name img2cad `
    --icon packaging\img2cad.ico `
    --add-data "packaging\img2cad.ico;." `
    --collect-all skimage `
    --collect-data ezdxf `
    img2cad_gui.py

Write-Host ""
Write-Host "Built: $root\dist\img2cad\img2cad.exe"
