@echo off
chcp 65001 >nul
cd /d "%~dp0"
REM 优先使用 DSH 自带解释器（如果存在），否则使用 PATH 中的 python
set "PY=%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if not exist "%PY%" set "PY=python"
set "PYTHONIOENCODING=utf-8"
echo 地空协同林业监测决策系统启动中… 按 Ctrl+C 停止
echo 浏览器打开 http://127.0.0.1:8766
"%PY%" run.py --port 8766
pause
