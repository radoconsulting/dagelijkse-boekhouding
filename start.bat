@echo off
REM Start de boekhoud-app in je browser.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Eerst setup.bat uitvoeren.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m streamlit run app\main.py
