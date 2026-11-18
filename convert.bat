@echo off
REM Drag one or more .stp/.step files onto this file.
cd /d "%~dp0"
if "%~1"=="" (
  echo Drag one or more .stp files onto convert.bat
  pause
  exit /b
)
python convert.py %* -o "%~dp1drawings"
echo.
echo Done. Drawings are in: %~dp1drawings
pause
