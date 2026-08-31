@echo off
setlocal
cd /d "%~dp0"
py -3 run_study.py
if errorlevel 1 pause
endlocal
