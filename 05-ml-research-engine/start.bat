@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo [BLAD] Brak Python launcher "py". Zainstaluj Python 3.11+ i Add Python to PATH.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo [SETUP] Tworze .venv...
  py -3 -m venv .venv || goto :fail
  .venv\Scripts\python.exe -m pip install --upgrade pip || goto :fail
  .venv\Scripts\python.exe -m pip install -r requirements.txt || goto :fail
)
echo.
echo ================================================
echo MARKET ML RESEARCH - STREAMING TAR.GZ + ENRICH
echo ================================================
.venv\Scripts\python.exe main.py
set ERR=%ERRORLEVEL%
echo.
if not "%ERR%"=="0" echo [BLAD] Kod %ERR%
pause
exit /b %ERR%
:fail
echo [BLAD] Instalacja nie powiodla sie.
pause
exit /b 1
