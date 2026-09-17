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
REM  NO CONSOLE WINDOW IS LEFT BEHIND. The application is started with
REM  pythonw.exe -- the windowed interpreter -- and this script exits at once,
REM  so nothing black sits behind the interface. python.exe is used only for
REM  the set-up and for the import check below, because those two DO have to
REM  be able to show an error; once they pass, there is nothing left to print.
REM ===========================================================================

set "HERE=%~dp0"
set "VENV=%HERE%.venv"
set "VPY=%VENV%\Scripts\python.exe"
set "VPYW=%VENV%\Scripts\pythonw.exe"

if exist "%VPY%" goto check

REM -- Find an interpreter to build the environment with. -------------------
REM  "py -3" is tried first: it resolves to a real installation, while a bare
REM  "python" on PATH can be the Microsoft Store build, whose very long
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

:check
REM  Verify the environment BEFORE handing over to pythonw.exe: after the
REM  handover nothing can be printed any more, so a broken install would
REM  look like the application simply never opening.
"%VPY%" -c "import PyQt6.QtWidgets" >nul 2>&1
if errorlevel 1 goto broken_env

REM  Start detached and leave. "start" needs the empty "" as the window title,
REM  otherwise it treats the quoted path as one.
start "" "%VPYW%" "%HERE%main.py" %*
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

:broken_env
echo.
echo  The environment in .venv is incomplete -- PyQt6 cannot be imported.
echo.
echo  Delete the .venv folder next to this file and run it again; the
echo  set-up will be done from scratch.
echo.
pause
exit /b 1
