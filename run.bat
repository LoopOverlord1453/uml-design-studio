@echo off
REM UML State Diagram Tool - Windows baslatici
setlocal
set HERE=%~dp0
if exist "%HERE%.venv\Scripts\python.exe" (
    "%HERE%.venv\Scripts\python.exe" "%HERE%main.py" %*
) else (
    python "%HERE%main.py" %*
)
endlocal
