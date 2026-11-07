@echo off
rem Double-click to open the Image-to-Cash launcher window.
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" -m i2c gui
