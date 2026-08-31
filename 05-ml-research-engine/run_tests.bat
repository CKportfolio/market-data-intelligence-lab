@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Najpierw uruchom start.bat albo wykonaj instalacje z README.
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m unittest discover -s tests -v
pause
