@echo off
rem Запуск Digger. Фото можно перетащить прямо на этот файл.
cd /d "%~dp0"
if not exist venv\Scripts\pythonw.exe (
  echo Сначала запустите install_win7.bat
  pause
  exit /b 1
)
start "" venv\Scripts\pythonw.exe run.py %*
