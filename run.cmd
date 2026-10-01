@echo off
setlocal
pushd "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Project Python was not found: .venv\Scripts\python.exe
    echo Create the project environment and install requirements.txt first.
    pause
    popd
    exit /b 1
)
".venv\Scripts\python.exe" "main.py" %*
set "refer_exit_code=%errorlevel%"
if not "%refer_exit_code%"=="0" pause
popd
exit /b %refer_exit_code%
