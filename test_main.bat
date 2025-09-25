@echo off
echo Testing Medical Literature Pipeline Configuration...
set PYTHON_EXE="\\wn18037.wa.providence.org\home\george.a.lopez\python-projects\literature-download-pipeline\venv\Scripts\python.exe"
set MAIN_PY="\\wn18037.wa.providence.org\home\george.a.lopez\python-projects\literature-download-pipeline\main.py"

echo.
echo =================================================
echo TESTING CONFIGURATION VALIDATION
echo =================================================
%PYTHON_EXE% %MAIN_PY% --validate

echo.
echo =================================================  
echo TESTING HELP/OPTIONS
echo =================================================
%PYTHON_EXE% %MAIN_PY% --help

pause
