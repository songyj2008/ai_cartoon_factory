@echo off
setlocal
cd /d "%~dp0"
where conda >nul 2>nul
if errorlevel 1 (
    echo Please run this script from Anaconda Prompt.
    pause
    exit /b 1
)
if not exist "config.json" (
    copy /y "config.example.json" "config.json" >nul
    if errorlevel 1 exit /b 1
)
call conda run --no-capture-output -n ai_cartoon_factory python "%~dp0app.py"
set "APP_EXIT_CODE=%ERRORLEVEL%"
if not "%APP_EXIT_CODE%"=="0" pause
exit /b %APP_EXIT_CODE%
