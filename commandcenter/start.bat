@echo off
REM JARVIS Command Center launcher for Windows (auto-bootstraps on first run)
cd /d "%~dp0"
where py >nul 2>nul && (py start.py %*) || (python start.py %*)
