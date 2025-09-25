@echo off
echo Running Medical Literature Pipeline - SINGLE TOPIC TEST...
set PYTHON_EXE="\\wn18037.wa.providence.org\home\george.a.lopez\python-projects\literature-download-pipeline\venv\Scripts\python.exe"
set MAIN_PY="\\wn18037.wa.providence.org\home\george.a.lopez\python-projects\literature-download-pipeline\main.py"

echo.
echo =================================================
echo SINGLE TOPIC TEST RUN
echo This will:
echo - Search PubMed for ONE topic only (CT neurological prognosis)
echo - Download available articles  
echo - Create database at: C:\Users\george.a.lopez\Medical_Literature_Pipeline
echo - Send you an email summary to: george.lopez@swedish.org
echo =================================================
echo.
echo IMPORTANT: Keep your laptop awake and unlocked during this process!
echo Expected runtime: 5-15 minutes
echo.
pause

REM Run with --no-incremental for first run to get recent articles
%PYTHON_EXE% %MAIN_PY% --no-incremental

echo.
echo =================================================
echo PIPELINE COMPLETE! Check:
echo 1. Your email for results summary
echo 2. C:\Users\george.a.lopez\Medical_Literature_Pipeline for files
echo =================================================
pause
