@echo off
setlocal

cd /d "%~dp0"

set "PYTHON_EXE="

if exist ".venv-local\Scripts\python.exe" (
    ".venv-local\Scripts\python.exe" -c "import streamlit, openai" >nul 2>&1
    if not errorlevel 1 set "PYTHON_EXE=.venv-local\Scripts\python.exe"
)

if not defined PYTHON_EXE if exist ".venv-runtime\Scripts\python.exe" (
    ".venv-runtime\Scripts\python.exe" -c "import streamlit, openai" >nul 2>&1
    if not errorlevel 1 set "PYTHON_EXE=.venv-runtime\Scripts\python.exe"
)

if not defined PYTHON_EXE if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import streamlit, openai" >nul 2>&1
    if not errorlevel 1 set "PYTHON_EXE=.venv\Scripts\python.exe"
)

if not defined PYTHON_EXE (
    echo Working Python environment not found.
    echo Please set up the project environment first.
    pause
    exit /b 1
)

powershell.exe -NoProfile -Command "try { $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 'http://127.0.0.1:8501'; if ($response.StatusCode -eq 200) { exit 0 } } catch {}; exit 1"
if not errorlevel 1 (
    start "" "http://127.0.0.1:8501"
    exit /b 0
)

start "" powershell.exe -NoProfile -WindowStyle Hidden -Command "$deadline = (Get-Date).AddSeconds(90); while ((Get-Date) -lt $deadline) { try { $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 'http://127.0.0.1:8501'; if ($response.StatusCode -eq 200) { Start-Process 'http://127.0.0.1:8501'; exit } } catch {}; Start-Sleep -Milliseconds 500 }"

echo Opening http://127.0.0.1:8501 in your browser...
echo Keep this window open. Press Ctrl+C to stop the server.
"%PYTHON_EXE%" -m streamlit run app.py --server.port=8501 --server.address=127.0.0.1 --server.headless=true

if errorlevel 1 (
    echo.
    echo The web server stopped with an error.
    pause
)

endlocal
