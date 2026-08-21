@echo off
rem Describe a project, with the model on the llama-server started by
rem start-llama-server.bat. The stack's address, its token and the project
rem are yours to pass:
rem   start-worker.bat --api http://192.168.1.10:3003 --token abc --project kurum
rem WORKER_API_URL, WORKER_API_TOKEN and WORKER_PROJECT work instead.
setlocal
cd /d "%~dp0"
set PY=py
if exist ".venv\Scripts\python.exe" set PY=.venv\Scripts\python.exe
if "%WORKER_LLAMA_SERVER%"=="" set WORKER_LLAMA_SERVER=http://127.0.0.1:8080
"%PY%" -m ctxworker %*
pause
