# Packaging img2cad as a Windows app

Turns the tool into a standalone `img2cad.exe` you can pin to the taskbar and use
as an **Open with** target for image files. No admin rights needed (installs per-user).

## Build + install

```powershell
# 1. Build the standalone app  -> dist\img2cad\img2cad.exe
powershell -ExecutionPolicy Bypass -File packaging\build.ps1

# 2. Install for the current user (copies to %LOCALAPPDATA%\Programs\img2cad,
#    makes Start Menu + Desktop shortcuts, registers "Open with img2cad")
powershell -ExecutionPolicy Bypass -File packaging\install.ps1
```

Then:
- **Taskbar:** launch img2cad from the Start Menu / Desktop shortcut, right-click its
  taskbar button, **Pin to taskbar**.
- **Open with:** right-click any PNG/JPG/BMP/GIF/TIFF/WebP → **Open with → img2cad**
  (or *Choose another app* the first time, then "Always").

## Uninstall

```powershell
powershell -ExecutionPolicy Bypass -File packaging\uninstall.ps1
```

## Files
- `make_icon.py` — generates `img2cad.ico` (slate tile + teal spline). Rerun to restyle.
- `build.ps1` — PyInstaller build (windowed, onedir, icon embedded, bundles
  scikit-image / ezdxf data). Produces `dist\img2cad\`.
- `install.ps1` / `uninstall.ps1` — per-user (HKCU) install: app copy, shortcuts,
  and `Open with` registration for common image extensions.

## Notes
- The app sets an explicit AppUserModelID (`img2cad.app`) so it pins and groups under
  its own icon rather than under python.
- The build is a folder app (onedir) for fast startup; the whole `dist\img2cad`
  folder is copied on install. Rebuild + re-run `install.ps1` to update.
- Build artifacts (`build\`, `dist\`, root `img2cad.spec`) are regenerated and can be
  deleted freely.
