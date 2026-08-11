@echo off
setlocal
cd /d "%~dp0"
where pythonw.exe >nul 2>nul
if errorlevel 1 (
  start "Ivory Lace" python.exe "%~dp0pet_ui.py"
) else (
  start "Ivory Lace" pythonw.exe "%~dp0pet_ui.py"
)
endlocal
