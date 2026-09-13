@echo off
rem 重新打包桌面软件: 修改代码后运行本脚本, 产物在 dist\XuanPin\
cd /d "%~dp0"
python -m PyInstaller --noconfirm --clean --noconsole --name XuanPin --icon app.ico --collect-all playwright --collect-all webview app.py
if errorlevel 1 (echo 打包失败 & pause & exit /b 1)
copy /y config.yaml dist\XuanPin\ >nul
copy /y keywords.txt dist\XuanPin\ >nul
echo 完成: dist\XuanPin\XuanPin.exe (data/和reports/请自行保留或迁移)
pause
