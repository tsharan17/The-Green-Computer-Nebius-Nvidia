@echo off
rem Double-click to start The Green Computer and open it in your browser.
title The Green Computer
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (py -3 scripts\start.py %*) else (python scripts\start.py %*)
if errorlevel 1 pause
