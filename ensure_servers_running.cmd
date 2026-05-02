@echo off
setlocal

cd /d "%~dp0"

"C:\Program Files\Python314\python.exe" ensure_servers_running.py
