@echo off
rem Double-clickable wrapper for install.ps1, so the execution policy is not the first thing a
rem person meets. It runs the script by path - no command string is built, and nothing elevates.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
pause
