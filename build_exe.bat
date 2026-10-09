@echo off
rem Сборка Digger.exe (папка dist\Digger), чтобы запускать без установленного Python
cd /d "%~dp0"
if exist wheels (
  venv\Scripts\python -m pip install --no-index --find-links wheels pyinstaller==5.13.2 || goto err
) else (
  venv\Scripts\python -m pip install pyinstaller==5.13.2 || goto err
)
venv\Scripts\pyinstaller --noconfirm --windowed --name Digger ^
  --hidden-import digger.modules.enhance_opencv ^
  --hidden-import digger.modules.ocr_tesseract ^
  --hidden-import digger.modules.identify_rules ^
  --hidden-import digger.modules.localdb_sqlite ^
  --hidden-import digger.modules.web.search ^
  --hidden-import digger.modules.pdftext_pypdf ^
  --hidden-import digger.modules.compare_basic ^
  --hidden-import digger.modules.memory_rules ^
  --hidden-import digger.modules.report_html ^
  run.py || goto err
xcopy /E /I /Y data\*.json dist\Digger\data\ >nul
copy /Y config.default.json dist\Digger\ >nul
copy /Y README.md dist\Digger\ >nul
xcopy /E /I /Y plugins dist\Digger\plugins >nul
echo Готово: dist\Digger\Digger.exe
pause
exit /b 0
:err
echo ОШИБКА сборки
pause
exit /b 1
