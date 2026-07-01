# Remove the per-user img2cad install (shortcuts, taskbar pin, registry, app files).
#   powershell -ExecutionPolicy Bypass -File packaging\uninstall.ps1
$ErrorActionPreference = "SilentlyContinue"

# Stop and WAIT so the exe lock releases before we delete its folder.
$procs = Get-Process img2cad
if ($procs) {
    $procs | Stop-Process -Force
    $procs | Wait-Process -Timeout 10
    Start-Sleep -Milliseconds 300
}

# Start-Menu / Desktop shortcuts
foreach ($dir in @([Environment]::GetFolderPath("Programs"), [Environment]::GetFolderPath("Desktop"))) {
    Remove-Item (Join-Path $dir "img2cad.lnk") -Force
}

# Best-effort: remove a taskbar pin (its .lnk lives in the User Pinned folder).
$pinDir = Join-Path $env:APPDATA "Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar"
Get-ChildItem $pinDir -Filter "img2cad*.lnk" | Remove-Item -Force

# File association / Open-with, reversing exactly what install created.
Remove-Item -Recurse -Force "HKCU:\Software\Classes\Applications\img2cad.exe"
Remove-Item -Recurse -Force "HKCU:\Software\Classes\img2cad.image"
$exts = ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp"
foreach ($e in $exts) {
    $ow = "HKCU:\Software\Classes\$e\OpenWithProgids"
    Remove-ItemProperty $ow -Name "img2cad.image"
    # Drop the OpenWithProgids key (and the bare .<ext> key) only if now empty, so
    # we never clobber a pre-existing user association.
    $k = Get-Item $ow
    if ($k -and -not $k.Property -and -not $k.GetSubKeyNames()) {
        Remove-Item $ow -Force
        $ext = Get-Item "HKCU:\Software\Classes\$e"
        if ($ext -and -not $ext.Property -and -not $ext.GetSubKeyNames()) {
            Remove-Item "HKCU:\Software\Classes\$e" -Force
        }
    }
}

Remove-Item -Recurse -Force (Join-Path $env:LOCALAPPDATA "Programs\img2cad")

if (-not ([System.Management.Automation.PSTypeName]"Shell.Ass2").Type) {
    Add-Type -Namespace Shell -Name Ass2 -MemberDefinition @"
[System.Runtime.InteropServices.DllImport("shell32.dll")]
public static extern void SHChangeNotify(int e, uint f, System.IntPtr a, System.IntPtr b);
"@
}
[Shell.Ass2]::SHChangeNotify(0x08000000, 0, [IntPtr]::Zero, [IntPtr]::Zero)
Write-Host "Uninstalled img2cad. (If a taskbar pin lingers, right-click it > Unpin.)"
