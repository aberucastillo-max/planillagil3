@echo off
chcp 65001 >nul
cd /d "%~dp0"
python --version >nul 2>&1
if errorlevel 1 (
  echo Primero instale Python desde python.org/downloads y marque la casilla "Add python.exe to PATH".
  pause
  exit /b
)
echo Instalando componentes, espere un momento...
python -m pip install -r requirements.txt -q
python configurar.py
echo.
pause
