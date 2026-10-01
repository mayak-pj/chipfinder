# Журнал выполнения

| Дата | Шаг | Что сделано | Коммит | Проверить на Win7 |
|---|---|---|---|---|
| 2026-09-29 | v1 | Рабочая версия: OCR, локальная база, поиск v1, сверка, память, окно | — | установка, самопроверка |
| 2026-09-29 | 0.1 | git init, .gitignore, первый коммит v1, приватный репозиторий chipfinder на GitHub (SSH) | — | не нужно |
| 2026-09-29 | 0.2 | Окружение Mac M4: .venv (Python 3.8), PyQt5-Qt5 5.15.19 для darwin, tools/setup_mac.sh; поворот в OCR определяется по gray и clahe (Tesseract 5.5) | — | самопроверка (OCR изменён) |
| 2026-09-29 | 0.3 | Тесты на pytest (conftest с фикстурой ctx, метки live/needs_tesseract/mytest), requirements-dev.txt, pyproject.toml (ruff py38), --selftest запускает pytest | — | не нужно |
| 2026-09-29 | 0.4 | CI: .github/workflows/ci.yml (windows-2022 + macos-14, pytest/ruff/vermin, Tesseract через choco/brew) | — | не нужно |
| 2026-09-29 | 0.5 | Транспорт в SafeHttp (`_send`, параметр `transport`), tests/fakes/fake_http.py, tools/record_fixture.py, тесты редиректа и записи фикстуры | — | не нужно |
| 2026-09-29 | 0.6 ★ | tools/build_portable.py (embeddable Python 3.8.10 + колёса cp38 win_amd64 + Tesseract 5.3.0 + ChipFinder.bat/Самопроверка.bat), встроенный Tesseract ищется в папке программы, CI-задача portable: самопроверка встроенным Python, артефакт, релиз по тегу v* | — | распаковать архив, Самопроверка.bat, ChipFinder.bat, OCR фото |
| 2026-10-01 | 0.6 ★ (исправление) | По отчёту с Win7: окно не запускалось из папки с русскими буквами (Qt не находил плагины) — путь к плагинам задаётся явно; `run.py --smoke`; CI открывает окно из папки с русским именем | b8528bd | новый архив: ChipFinder.bat из папки с русским именем, OCR фото |
