@echo off
rem Установка Digger на Windows 7 (Python 3.8 x64)
cd /d "%~dp0"
set PY=
py -3.8 --version >nul 2>nul && set PY=py -3.8
if "%PY%"=="" python --version >nul 2>nul && set PY=python
if "%PY%"=="" (
  echo Не найден Python 3.8. Установите python-3.8.10-amd64.exe
  echo и отметьте "Add Python to PATH". См. README.md
  pause
  exit /b 1
)
%PY% --version
echo Создаю окружение venv...
%PY% -m venv venv || goto err
if exist wheels (
  echo Устанавливаю пакеты из папки wheels, без интернета...
  venv\Scripts\python -m pip install --no-index --find-links wheels -r requirements.txt || goto err
) else (
  echo Устанавливаю пакеты из интернета, PyPI...
  venv\Scripts\python -m pip install -r requirements.txt || goto err
)
echo.
echo Проверка программы...
venv\Scripts\python run.py --selftest
echo.
echo Готово. Запуск: run.bat
pause
exit /b 0
:err
echo ОШИБКА установки. Подробности выше.
pause
exit /b 1
