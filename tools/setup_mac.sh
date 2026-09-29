#!/usr/bin/env bash
# Окружение разработки на Mac (Apple Silicon): Python 3.8, .venv, зависимости, Tesseract.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v uv >/dev/null || { echo "Нужен uv: brew install uv"; exit 1; }
command -v tesseract >/dev/null || brew install tesseract

uv python install 3.8
[ -d .venv ] || uv venv --python 3.8 .venv
uv pip install --python .venv/bin/python -r requirements.txt
uv pip install --python .venv/bin/python pytest ruff vermin

.venv/bin/python run.py --selftest
echo "Готово. Запуск: .venv/bin/python run.py"
