@echo off
setlocal
cd /d "%~dp0"
title PUMA
where python >nul 2>nul
if errorlevel 1 (
  echo Python not found. Install Python 3.11+ first.
  pause
  exit /b 1
)
python -m pip install -e .
if errorlevel 1 (
  echo PUMA installation failed.
  pause
  exit /b 1
)
start "PUMA" http://localhost:8501
python -m streamlit run src/puma_scouts/app.py --server.port 8501
