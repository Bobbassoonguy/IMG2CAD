@echo off
rem Double-click to open the img2cad GUI. Optionally drag an image onto this file.
setlocal
cd /d "%~dp0"
set PY=.venv\Scripts\pythonw.exe
if not exist "%PY%" set PY=pythonw
"%PY%" img2cad_gui.py %*
