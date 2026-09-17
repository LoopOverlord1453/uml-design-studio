@echo off
setlocal EnableExtensions
REM ===========================================================================
REM  UML Design Studio - Windows launcher
REM
REM  Double-click this file. On the FIRST run it creates a private virtual
REM  environment under .venv and installs PyQt6 into it; after that it just
REM  starts the application.
REM
REM  Nothing is installed system-wide: everything lands in .venv next to this
REM  file, and deleting that folder undoes it completely.
REM
REM  The window is kept open on failure -- otherwise the error scrolls past
REM  and closes, which looks exactly like "nothing happens".
REM ===========================================================================

set "HERE=%~dp0"
set "VENV=%HERE%.venv"
set "VPY=%VENV%\Scripts\python.exe"

if exist "%VPY%" goto run

REM -- Find an interpreter to build the environment with. -------------------
REM  "py -3" is tried first: it resolves to a real installation, while a bare
REM  "python" on PATH can be the Microsoft Store stub, whose very long
REM  site-packages path makes "pip install PyQt6" fail.
set "BOOT="
py -3 --version >nul 2>&1 && set "BOOT=py -3"
if not defined BOOT (
    python --version >nul 2>&1 && set "BOOT=python"
)
if not defined BOOT goto no_python

echo.
echo  First run: setting up the environment (this happens once).
echo.
%BOOT% -m venv "%VENV%"
if errorlevel 1 goto venv_failed

"%VPY%" -m pip install --upgrade pip
if errorlevel 1 goto pip_failed

"%VPY%" -m pip install -r "%HERE%requirements.txt"
if errorlevel 1 goto pip_failed

echo.
echo  Setup finished. Starting UML Design Studio...
echo.

:run
"%VPY%" "%HERE%main.py" %*
if errorlevel 1 goto app_failed
exit /b 0

REM -- Failure paths --------------------------------------------------------

:no_python
echo.
echo  Python 3 was not found.
echo.
echo  Install it from https://www.python.org/downloads/ and tick
echo  "Add Python to PATH" during setup, then run this file again.
echo.
pause
exit /b 1

:venv_failed
echo.
echo  Could not create the virtual environment in:
echo    %VENV%
echo.
echo  Check that you can write to this folder. If the project sits in a
echo  synced folder (OneDrive, a network drive), try a local one.
echo.
pause
exit /b 1

:pip_failed
echo.
echo  Installing the dependencies failed.
echo.
echo  If the error mentions a path being too long, the interpreter is most
echo  likely the Microsoft Store build of Python. Install Python from
echo  python.org instead, delete the .venv folder and run this file again.
echo.
pause
exit /b 1

:app_failed
echo.
echo  UML Design Studio exited with an error (see the message above).
echo.
pause
exit /b 1
