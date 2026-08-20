@echo off
rem Download llama-server into worker\llama-server and unpack it there.
rem Everything this does is in ctxworker\getserver.py - the file exists so
rem it can be double-clicked. Arguments are passed straight through:
rem   get-llama-server.bat --variant cpu
setlocal
cd /d "%~dp0"
set PY=py
if exist ".venv\Scripts\python.exe" set PY=.venv\Scripts\python.exe
"%PY%" -m ctxworker.getserver %*
pause
