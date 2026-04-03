@echo off
REM Creates a Windows Task Scheduler job to run the agent every morning at 8:00 AM.
REM Run this file once as Administrator to register the scheduled task.

set PROJECT_DIR=%~dp0
set PYTHON=%PROJECT_DIR%venv\Scripts\python.exe
set SCRIPT=%PROJECT_DIR%orchestrator.py
set LOG=%PROJECT_DIR%logs\run.log

schtasks /create ^
  /tn "JobAgent\DailyRun" ^
  /tr "cmd /c cd /d \"%PROJECT_DIR%\" && set PYTHONUTF8=1 && \"%PYTHON%\" \"%SCRIPT%\" >> \"%LOG%\" 2>&1" ^
  /sc DAILY ^
  /st 08:00 ^
  /ru "%USERNAME%" ^
  /f

echo.
echo Task created! The agent will run every morning at 8:00 AM.
echo Logs will be written to: %LOG%
echo.
echo To change the time: Task Scheduler ^> Task Scheduler Library ^> JobAgent ^> DailyRun
echo To remove the task: schtasks /delete /tn "JobAgent\DailyRun" /f
pause
