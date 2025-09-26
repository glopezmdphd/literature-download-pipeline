@echo off
echo Running Medical Literature Pipeline - LOCAL VERSION...
set PYTHON_EXE="C:\Users\george.a.lopez\literature-download-pipeline\venv\Scripts\python.exe"
set MAIN_PY="C:\Users\george.a.lopez\literature-download-pipeline\main.py"

echo.
echo =================================================
echo LOCAL SINGLE TOPIC TEST RUN
echo This will:
echo - Search PubMed for ONE topic only (CT neurological prognosis)
echo - Download available articles  
echo - Create database at: C:\Users\george.a.lopez\Medical_Literature_Pipeline
echo - Send you an email summary to: george.lopez@swedish.org
echo.
echo ✅ ADVANTAGES OF LOCAL VERSION:
echo - No UNC path issues
echo - Faster execution
echo - Reliable Git operations
echo - Works offline
echo =================================================
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
