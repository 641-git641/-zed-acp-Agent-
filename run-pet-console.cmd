@echo off
setlocal
cd /d "%~dp0"
python.exe "%~dp0pet_ui.py" --debug
pause
endlocal
