@echo off
setlocal EnableDelayedExpansion

REM Enterprise-bulletproof pipeline runner
REM Handles UNC paths, network issues, and terminal problems

echo Running Medical Literature Pipeline - BULLETPROOF VERSION...

REM Try to determine best working method
set USE_MAPPED_DRIVE=0
set USE_LOCAL_COPY=0

REM Check if Z: drive exists (mapped network drive)
if exist "Z:\python-projects\literature-download-pipeline\" (
    set USE_MAPPED_DRIVE=1
    set PYTHON_EXE="Z:\python-projects\literature-download-pipeline\venv\Scripts\python.exe"
    set MAIN_PY="Z:\python-projects\literature-download-pipeline\main.py"
    echo Using mapped drive Z: for execution...
) else if exist "C:\dev\literature-download-pipeline\" (
    set USE_LOCAL_COPY=1
    set PYTHON_EXE="C:\dev\literature-download-pipeline\venv\Scripts\python.exe"
    set MAIN_PY="C:\dev\literature-download-pipeline\main.py"
    echo Using local development copy for execution...
) else (
    REM Fall back to UNC paths
    set PYTHON_EXE="\\wn18037.wa.providence.org\home\george.a.lopez\python-projects\literature-download-pipeline\venv\Scripts\python.exe"
    set MAIN_PY="\\wn18037.wa.providence.org\home\george.a.lopez\python-projects\literature-download-pipeline\main.py"
    echo Using UNC paths (may have issues)...
)

echo.
echo =================================================
echo ENTERPRISE-BULLETPROOF PIPELINE RUN
echo Method: !PYTHON_EXE!
echo =================================================
echo.
pause

REM Run with error handling
%PYTHON_EXE% %MAIN_PY% --no-incremental
if !ERRORLEVEL! NEQ 0 (
    echo.
    echo ERROR: Pipeline failed with exit code !ERRORLEVEL!
    echo Check the log file for details.
    pause
    exit /b !ERRORLEVEL!
)

echo.
echo =================================================
echo PIPELINE COMPLETED SUCCESSFULLY!
echo =================================================
pause
