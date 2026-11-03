@echo off
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv
  .venv\Scripts\pip install -r requirements.txt
)
echo Carpool-app draait op http://127.0.0.1:5000  (sluit dit venster om te stoppen)
.venv\Scripts\python start.py
