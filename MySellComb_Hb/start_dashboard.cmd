@echo off
setlocal

cd /d "%~dp0"

set "MYSELLCOMB_HOST=127.0.0.1"
set "MYSELLCOMB_PORT=5010"
set "MYSELLCOMB_DEBUG=0"
set "MYSELLCOMB_USE_RELOADER=0"
set "MYSELLCOMB_OPEN_BROWSER=0"
set "MYSELLCOMB_BROWSER_PROFILE_ROOT=%~dp0crawler\browser_profile"
set "TIKTOK_WORKSHEET_NAME=TikTok_Hb"

"C:\Program Files\Python314\python.exe" app.py 1>hb_server_stdout.log 2>hb_server_stderr.log
