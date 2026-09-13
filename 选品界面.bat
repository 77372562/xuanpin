@echo off
chcp 65001 >nul
title xuanpin 选品助手界面
cd /d "%~dp0"

set PYEXE=C:\Users\20298\AppData\Local\Programs\Python\Python312\python.exe
if not exist "%PYEXE%" set PYEXE=python

echo 正在启动选品助手界面, 浏览器将自动打开...
start /min cmd /c "timeout /t 2 >nul & start http://127.0.0.1:8765"
"%PYEXE%" app.py
pause
