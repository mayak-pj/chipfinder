@echo off
rem Сборка ChipFinder.exe (папка dist\ChipFinder), чтобы запускать без установленного Python
cd /d "%~dp0"
if exist wheels (
  venv\Scripts\python -m pip install --no-index --find-links wheels pyinstaller==5.13.2 || goto err
) else (
  venv\Scripts\python -m pip install pyinstaller==5.13.2 || goto err
)
venv\Scripts\pyinstaller --noconfirm --windowed --name ChipFinder ^
  --hidden-import chipfinder.modules.enhance_opencv ^
  --hidden-import chipfinder.modules.ocr_tesseract ^
  --hidden-import chipfinder.modules.identify_rules ^
  --hidden-import chipfinder.modules.localdb_sqlite ^
  --hidden-import chipfinder.modules.web.search ^
  --hidden-import chipfinder.modules.pdftext_pypdf ^
  --hidden-import chipfinder.modules.compare_basic ^
  --hidden-import chipfinder.modules.memory_rules ^
  --hidden-import chipfinder.modules.report_html ^
  run.py || goto err
xcopy /E /I /Y data\*.json dist\ChipFinder\data\ >nul
copy /Y config.default.json dist\ChipFinder\ >nul
copy /Y README.md dist\ChipFinder\ >nul
xcopy /E /I /Y plugins dist\ChipFinder\plugins >nul
echo Готово: dist\ChipFinder\ChipFinder.exe
pause
exit /b 0
:err
echo ОШИБКА сборки
pause
exit /b 1
