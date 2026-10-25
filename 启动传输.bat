@echo off
cd /d "%~dp0"

where py >nul 2>nul
if not errorlevel 1 goto usePy
where python >nul 2>nul
if not errorlevel 1 goto usePython
goto noPython

:usePy
py -3 "%~dp0server.py"
goto done

:usePython
python "%~dp0server.py"
goto done

:noPython
echo.
echo   [!] Python 3 not found.
echo       Install it from https://www.python.org/downloads/
echo       and tick "Add Python to PATH" during setup.
echo.
pause
exit /b 1

:done
echo.
echo   Server stopped.
pause
