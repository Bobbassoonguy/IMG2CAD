<#
.SYNOPSIS
  Screenshot just the img2cad app window (not the whole desktop).

.DESCRIPTION
  Finds the top-level window whose title contains -Match (default "img2cad")
  and captures only that window to a PNG using the Win32 PrintWindow API, so
  the grab is clean even if the window is partially occluded or off-screen.
  Intended for Claude Code to visually check the GUI: run it, then read the PNG.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\screenshot_app.ps1
  powershell -ExecutionPolicy Bypass -File tools\screenshot_app.ps1 -Out C:\tmp\ui.png -Match img2cad
#>
param(
  [string]$Out   = "$env:TEMP\img2cad_ui.png",
  [string]$Match = "img2cad"
)

Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class Win32Cap {
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr h);
  public delegate bool EnumProc(IntPtr h, IntPtr p);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr p);
  public struct RECT { public int Left, Top, Right, Bottom; }
}
"@

# Find first visible top-level window whose title contains $Match.
$target = [IntPtr]::Zero
$found  = ""
$cb = [Win32Cap+EnumProc]{
  param($h, $p)
  if (-not [Win32Cap]::IsWindowVisible($h)) { return $true }
  $len = [Win32Cap]::GetWindowTextLength($h)
  if ($len -le 0) { return $true }
  $sb = New-Object System.Text.StringBuilder ($len + 1)
  [void][Win32Cap]::GetWindowText($h, $sb, $sb.Capacity)
  $t = $sb.ToString()
  if ($t -like "*$Match*") {
    $script:target = $h
    $script:found  = $t
    return $false   # stop enumerating
  }
  return $true
}
[void][Win32Cap]::EnumWindows($cb, [IntPtr]::Zero)

if ($target -eq [IntPtr]::Zero) {
  Write-Error "No visible window matching '*$Match*' found. Is the app running?"
  exit 2
}

# Un-minimize and raise so PrintWindow renders live content.
[void][Win32Cap]::ShowWindow($target, 9)   # SW_RESTORE
[void][Win32Cap]::SetForegroundWindow($target)
Start-Sleep -Milliseconds 250

$r = New-Object Win32Cap+RECT
[void][Win32Cap]::GetWindowRect($target, [ref]$r)
$w = $r.Right - $r.Left
$h = $r.Bottom - $r.Top
if ($w -le 0 -or $h -le 0) { Write-Error "Window has zero size."; exit 3 }

$bmp = New-Object System.Drawing.Bitmap $w, $h
$gfx = [System.Drawing.Graphics]::FromImage($bmp)
$hdc = $gfx.GetHdc()
# flag 2 = PW_RENDERFULLCONTENT (captures DWM-composited/hardware content)
$ok = [Win32Cap]::PrintWindow($target, $hdc, 2)
$gfx.ReleaseHdc($hdc)
$gfx.Dispose()

if (-not $ok) {
  # Fallback: copy straight from the screen at the window rect.
  $bmp2 = New-Object System.Drawing.Bitmap $w, $h
  $g2 = [System.Drawing.Graphics]::FromImage($bmp2)
  $g2.CopyFromScreen($r.Left, $r.Top, 0, 0, (New-Object System.Drawing.Size $w, $h))
  $g2.Dispose()
  $bmp.Dispose()
  $bmp = $bmp2
}

$dir = Split-Path -Parent $Out
if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()

Write-Output "Saved '$found' ($w x $h) -> $Out"
