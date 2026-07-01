@echo off
rem Drag one or more image files onto this .bat to make a .dxf next to each.
setlocal
cd /d "%~dp0"
set PY=.venv\Scripts\python.exe
if not exist "%PY%" set PY=python
if "%~1"=="" (
  echo Drag a PNG/JPG onto this file to convert it to DXF.
  pause
  exit /b
)
:loop
if "%~1"=="" goto done
echo Converting "%~1" ...
"%PY%" img2cad.py "%~1"
shift
goto loop
:done
echo.
echo Done. Import the .dxf into an Onshape sketch: right-click a plane ^> Import DXF/DWG.
pause
