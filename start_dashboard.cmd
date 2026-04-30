@echo off
setlocal

cd /d "%~dp0"

set "MYSELLCOMB_HOST=127.0.0.1"
set "MYSELLCOMB_PORT=5000"
set "MYSELLCOMB_DEBUG=0"
set "MYSELLCOMB_USE_RELOADER=0"
set "MYSELLCOMB_OPEN_BROWSER=0"

"C:\Program Files\Python314\python.exe" app.py 1>codex_server_stdout.log 2>codex_server_stderr.log
