# Install img2cad for the current user (no admin required):
#   - copies the built app to %LOCALAPPDATA%\Programs\img2cad
#   - creates Start Menu + Desktop shortcuts (pin either to the taskbar)
#   - registers "Open with img2cad" for common image types
#
# Usage:  powershell -ExecutionPolicy Bypass -File packaging\install.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$src = Join-Path $root "dist\img2cad"
if (-not (Test-Path (Join-Path $src "img2cad.exe"))) {
    throw "Build first:  powershell -ExecutionPolicy Bypass -File packaging\build.ps1"
}

$dst = Join-Path $env:LOCALAPPDATA "Programs\img2cad"

# Stop any running instance and WAIT for the exe lock to release before copying.
$procs = Get-Process img2cad -ErrorAction SilentlyContinue
if ($procs) {
    $procs | Stop-Process -Force
    $procs | Wait-Process -Timeout 10 -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 300
}
# Clean the target first so a smaller/renamed new build can't leave stale files.
if (Test-Path $dst) { Remove-Item -Recurse -Force $dst -ErrorAction SilentlyContinue }
New-Item -ItemType Directory -Force -Path $dst | Out-Null
Copy-Item -Recurse -Force (Join-Path $src "*") $dst
$exe = Join-Path $dst "img2cad.exe"
Write-Host "Installed app to $dst"

# --- Shortcuts ---
$ws = New-Object -ComObject WScript.Shell
foreach ($dir in @([Environment]::GetFolderPath("Programs"), [Environment]::GetFolderPath("Desktop"))) {
    $lnk = $ws.CreateShortcut((Join-Path $dir "img2cad.lnk"))
    $lnk.TargetPath = $exe
    $lnk.WorkingDirectory = $dst
    $lnk.IconLocation = "$exe,0"
    $lnk.Description = "img2cad - image to Onshape DXF"
    $lnk.Save()
}
Write-Host "Created Start Menu + Desktop shortcuts"

# --- File association / Open-with (per-user HKCU) ---
$cmd = "`"$exe`" `"%1`""
$progid = "img2cad.image"
$exts = ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp"

function Set-Default($path, $value) {
    New-Item -Force -Path $path | Out-Null
    Set-ItemProperty -Path $path -Name "(default)" -Value $value
}

# Application entry (gives the friendly name + command)
$appKey = "HKCU:\Software\Classes\Applications\img2cad.exe"
Set-Default "$appKey\shell\open\command" $cmd
Set-ItemProperty $appKey "FriendlyAppName" "img2cad"
Set-Default "$appKey\DefaultIcon" "$exe,0"
New-Item -Force "$appKey\SupportedTypes" | Out-Null

# ProgID (makes img2cad show directly in the Open-with flyout)
Set-Default "HKCU:\Software\Classes\$progid" "Image (img2cad)"
Set-Default "HKCU:\Software\Classes\$progid\shell\open\command" $cmd
Set-Default "HKCU:\Software\Classes\$progid\DefaultIcon" "$exe,0"

foreach ($e in $exts) {
    New-ItemProperty "$appKey\SupportedTypes" -Name $e -Value "" -PropertyType String -Force | Out-Null
    $ow = "HKCU:\Software\Classes\$e\OpenWithProgids"
    New-Item -Force $ow | Out-Null
    New-ItemProperty $ow -Name $progid -Value "" -PropertyType String -Force | Out-Null
}
Write-Host "Registered 'Open with img2cad' for: $($exts -join ' ')"

# Tell Explorer associations changed (guard Add-Type so re-runs in the same
# PowerShell session don't abort with "type already exists").
if (-not ([System.Management.Automation.PSTypeName]"Shell.Ass").Type) {
    Add-Type -Namespace Shell -Name Ass -MemberDefinition @"
[System.Runtime.InteropServices.DllImport("shell32.dll")]
public static extern void SHChangeNotify(int e, uint f, System.IntPtr a, System.IntPtr b);
"@
}
[Shell.Ass]::SHChangeNotify(0x08000000, 0, [IntPtr]::Zero, [IntPtr]::Zero)  # SHCNE_ASSOCCHANGED

Write-Host ""
Write-Host "Done. To pin to the taskbar: open img2cad (Start menu or Desktop shortcut),"
Write-Host "right-click its taskbar icon, and choose 'Pin to taskbar'."
Write-Host "Right-click any image > Open with > img2cad."
