@echo off
chcp 65001 >nul
title xuanpin 选品助手界面
cd /d "%~dp0"

set PYEXE=C:\Users\20298\AppData\Local\Programs\Python\Python312\python.exe
if not exist "%PYEXE%" set PYEXE=python

"%PYEXE%" app.py

echo.
echo 如果上方出现红色报错, 请截图发给维护者; 否则浏览器应已自动打开 http://127.0.0.1:8765
pause
