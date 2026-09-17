@echo off
setlocal EnableExtensions
cd /d "%~dp0.."
set "PY=%CD%\contracts\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo  Python venv not found at contracts\.venv — killing by port with taskkill fallback.
  goto :fallback
)
"%PY%" "%CD%\scripts\stop_stack.py"
exit /b %errorlevel%

:fallback
echo  Stopping listeners on 8000, 3000, 8545...
for %%P in (8000 3000 8545) do (
  for /f "tokens=5" %%A in ('netstat -ano ^| findstr /R /C:":%%P .*LISTENING"') do (
    echo  taskkill PID %%A ^(port %%P^)
    taskkill /F /T /PID %%A >nul 2>&1
  )
)
where docker >nul 2>&1
if not errorlevel 1 (
  docker compose -f "%CD%\infra\docker-compose.yml" stop
)
echo  Done.
exit /b 0
