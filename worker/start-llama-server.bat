@echo off
rem Start llama-server on this machine, downloading the binaries and the
rem weights first if they are not here yet. Everything it does is in
rem ctxworker\runserver.py. Extra arguments go straight to llama-server:
rem   start-llama-server.bat --port 8090
rem   start-llama-server.bat --model qwen-3b
setlocal
cd /d "%~dp0"
set PY=py
if exist ".venv\Scripts\python.exe" set PY=.venv\Scripts\python.exe
"%PY%" -m ctxworker.runserver --install %*
pause
