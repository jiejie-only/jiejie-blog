@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Starting blog on http://127.0.0.1:8080/
echo Admin write: http://127.0.0.1:8080/admin/login
python server.py
pause
