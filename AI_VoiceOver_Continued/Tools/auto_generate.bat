@echo off
rem Double-click to watch for WoW and generate voiceovers after each session.
rem Pass generator options through, e.g.:  auto_generate.bat --language de
powershell -ExecutionPolicy Bypass -File "%~dp0auto_generate.ps1" -GeneratorArgs %*
pause
