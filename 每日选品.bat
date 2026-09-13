@echo off
chcp 65001 >nul
title 每日选品工作流 - xuanpin
cd /d "%~dp0"

set PYEXE=C:\Users\20298\AppData\Local\Programs\Python\Python312\python.exe
if not exist "%PYEXE%" set PYEXE=python

echo ================================================
echo   每日选品工作流 (xuanpin)
echo   候选清单: keywords.txt (记事本可编辑)
echo ================================================
echo.

"%PYEXE%" main.py batch

echo.
echo ================================================
echo   完成! 汇总报告在 reports 文件夹里
echo ================================================
pause
