@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0.."
title OverUnder stack launcher

set "ROOT=%CD%"
set "PY=%ROOT%\contracts\.venv\Scripts\python.exe"
set "API_URL=http://127.0.0.1:8000"
set "WEB_URL=http://127.0.0.1:3000"
set "RPC_URL=http://127.0.0.1:8545"

echo.
echo  === OverUnder local stack ===
echo  repo: %ROOT%
echo.

if not exist "%ROOT%\.env" (
  copy /Y "%ROOT%\.env.example" "%ROOT%\.env" >nul
  echo  created .env from .env.example
)

call :ensure_python
if errorlevel 1 goto :fail

call :ensure_node
if errorlevel 1 goto :fail

call :start_anvil
if errorlevel 1 goto :fail

call :wait_rpc
if errorlevel 1 goto :fail

echo.
echo  [4/6] Deploying contracts to Anvil...
"%PY%" "%ROOT%\contracts\script\deploy.py"
if errorlevel 1 (
  echo  deploy failed
  goto :fail
)
"%PY%" "%ROOT%\scripts\sync_deploy_env.py" 31337

echo.
echo  [5/6] Starting API at %API_URL%
start "OverUnder API" /D "%ROOT%\backend" cmd /k "%PY%" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

echo  [6/6] Starting web at %WEB_URL%
start "OverUnder Web" /D "%ROOT%\web" cmd /k npm run dev

echo.
echo  Stack is up. Keep the Anvil / API / Web windows open.
echo    Web   %WEB_URL%
echo    API   %API_URL%/health
echo    RPC   %RPC_URL%
echo  Stop with scripts\stop_stack.cmd
echo.
exit /b 0

:ensure_python
echo  [1/6] Python 3.12 venv + deps
if exist "%PY%" (
  echo  using existing venv
  exit /b 0
)
where uv >nul 2>&1
if errorlevel 1 (
  echo  uv is not on PATH. Install uv or create contracts\.venv with Python 3.12.
  exit /b 1
)
uv venv "%ROOT%\contracts\.venv" --python 3.12
if errorlevel 1 exit /b 1
uv pip install --python "%PY%" -r "%ROOT%\contracts\requirements.txt" -r "%ROOT%\backend\requirements.txt" -r "%ROOT%\oracles\requirements.txt"
if errorlevel 1 (
  echo  pip install failed
  exit /b 1
)
exit /b 0

:ensure_node
echo  [2/6] Web npm packages
where npm >nul 2>&1
if errorlevel 1 (
  echo  npm is not on PATH. Install Node.js.
  exit /b 1
)
if exist "%ROOT%\web\node_modules\" exit /b 0
pushd "%ROOT%\web"
call npm install
set "ERR=!errorlevel!"
popd
exit /b !ERR!

:start_anvil
echo  [3/6] Anvil on :8545
call :port_open 8545
if not errorlevel 1 (
  echo  already listening on 8545 — reusing it
  exit /b 0
)

where docker >nul 2>&1
if not errorlevel 1 (
  echo  starting Anvil via docker compose
  docker compose -f "%ROOT%\infra\docker-compose.yml" up -d anvil
  if not errorlevel 1 exit /b 0
  echo  docker compose failed, trying local anvil...
)

set "ANVIL="
where anvil >nul 2>&1
if not errorlevel 1 set "ANVIL=anvil"
if not defined ANVIL if exist "%USERPROFILE%\.foundry\bin\anvil.exe" set "ANVIL=%USERPROFILE%\.foundry\bin\anvil.exe"
if not defined ANVIL (
  echo  No Anvil. Install Foundry ^(anvil^) or start Docker Desktop and retry.
  exit /b 1
)
start "OverUnder Anvil" cmd /k "%ANVIL%" --host 127.0.0.1 --chain-id 31337 --block-time 1
exit /b 0

:wait_rpc
echo  waiting for %RPC_URL% ...
"%PY%" "%ROOT%\scripts\wait_rpc.py" "%RPC_URL%"
if errorlevel 1 (
  echo  Anvil did not become ready on %RPC_URL%
  exit /b 1
)
exit /b 0

:port_open
netstat -an | findstr /R /C:":%~1 .*LISTENING" >nul 2>&1
exit /b %errorlevel%

:fail
echo.
echo  Stack failed to start. See messages above.
pause
exit /b 1
