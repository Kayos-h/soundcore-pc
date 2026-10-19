@echo off
REM One-click launcher for Soundcore for PC
cd /d "%~dp0"
python soundcore_app.py
if errorlevel 1 pause
