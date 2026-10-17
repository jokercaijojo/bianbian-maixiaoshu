@echo off
rem Auto-restart launcher for the WeChat daemon.
rem Resolves paths relative to this file, so it works on any machine / folder.
rem
rem NOTE: never call bare "timeout" here. If Git Bash is in PATH it
rem resolves to GNU timeout.exe which rejects /t, so the 10s backoff
rem silently disappears and a crashing daemon restart-loops instantly.
rem Use the absolute path of the Windows ping as a sleep instead.
rem
rem Run_daemon already calls daemon.py directly, so PYTHONUTF8 must be
rem set WITHOUT a trailing space before the value.
cd /d "%~dp0.."
set PYTHONUTF8=1
set "PING=%SystemRoot%\System32\ping.exe"
echo ============================================================
echo  Maimai WeChat bot - auto restart mode
echo  Log file: %~dp0..\daemon.log
echo  Run "type daemon.log" in another window to follow it.
echo ============================================================
:loop
echo [%date% %time%] daemon starting >> daemon.log
.venv\Scripts\python.exe scripts\daemon.py >> daemon.log 2>&1
echo [%date% %time%] daemon exited with %errorlevel%, restarting in 10s >> daemon.log
%PING% -n 11 127.0.0.1 >nul
goto loop
