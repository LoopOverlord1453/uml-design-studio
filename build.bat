@echo off
setlocal EnableExtensions
REM ===========================================================================
REM  UML Design Studio - build a standalone Windows executable
REM
REM  Double-click this file. It produces:
REM
REM      dist\UML-Design-Studio.exe
REM
REM  One file, no console window, with the icon and the version resource
REM  embedded. It runs on a machine with no Python and no PyQt6 installed.
REM
REM  PyInstaller is not a cross-compiler: this script can only build the
REM  WINDOWS executable. For Linux or macOS, run build.sh on that system.
REM ===========================================================================

set "HERE=%~dp0"
set "VENV=%HERE%.venv"
set "VPY=%VENV%\Scripts\python.exe"

if exist "%VPY%" goto have_env

set "BOOT="
py -3 --version >nul 2>&1 && set "BOOT=py -3"
if not defined BOOT (
    python --version >nul 2>&1 && set "BOOT=python"
)
if not defined BOOT goto no_python

echo.
echo  Setting up the environment (this happens once)...
echo.
%BOOT% -m venv "%VENV%" || goto failed
"%VPY%" -m pip install --upgrade pip || goto failed
"%VPY%" -m pip install -r "%HERE%requirements.txt" || goto failed

:have_env
echo.
echo  Making sure PyInstaller is available...
"%VPY%" -m pip install --upgrade pyinstaller || goto failed

echo.
echo  Drawing the application icon...
"%VPY%" "%HERE%tools\make_icon.py" >nul 2>&1

echo.
echo  Building...
echo.
"%VPY%" -m PyInstaller --clean --noconfirm "%HERE%UML-Design-Studio.spec" || goto failed

echo.
echo  ------------------------------------------------------------------
echo   Done:  %HERE%dist\UML-Design-Studio.exe
echo  ------------------------------------------------------------------
echo.
echo  Copy that single file anywhere -- it needs no Python installation.
echo.
pause
exit /b 0

:no_python
echo.
echo  Python 3 was not found.
echo.
echo  Install it from https://www.python.org/downloads/ and tick
echo  "Add Python to PATH" during setup, then run this file again.
echo.
pause
exit /b 1

:failed
echo.
echo  The build failed (see the message above).
echo.
echo  If the error mentions a path being too long, the interpreter is most
echo  likely the Microsoft Store build of Python. Install Python from
echo  python.org instead, delete the .venv folder and try again.
echo.
pause
exit /b 1
