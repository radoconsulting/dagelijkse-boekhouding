@echo off
REM Eenmalige installatie: maakt een virtuele omgeving en installeert de afhankelijkheden.
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -m venv .venv
) else (
    python -m venv .venv
)
if not exist ".venv\Scripts\python.exe" (
    echo Python 3.11 of nieuwer is nodig. Installeer het via https://www.python.org of de Microsoft Store.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -e ".[dev,ai]"

if not exist ".env" copy ".env.example" ".env" >nul
echo.
echo Klaar. Vul eventueel een AI-sleutel in .env in (bv. GEMINI_API_KEY) en start de app met start.bat
pause
