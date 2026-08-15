@echo off
REM eZmanbo 比赛展示一键启动脚本 (Windows)
cd /d "%~dp0"

title eZmanbo 智能元器件选型系统

echo ================================================
echo   eZmanbo 智能元器件选型系统 — 比赛展示版
echo ================================================
echo.

where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [错误] 未检测到 Python
    pause & exit /b 1
)

where node >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [错误] 未检测到 Node.js
    pause & exit /b 1
)

echo [OK] 环境检查通过

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000 "') do taskkill /f /pid %%a 2>nul
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":3000 "') do taskkill /f /pid %%a 2>nul

echo [启动] 后端服务...
start /B python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 > "%TEMP%\ezmanbo_backend.log" 2>&1

:wait_backend
timeout /t 2 /nobreak >nul
curl -s http://localhost:8000/health >nul 2>nul
if %ERRORLEVEL% neq 0 goto wait_backend
echo [OK] 后端就绪

echo [启动] 前端服务...
cd frontend\web
start /B npx next dev -p 3000 > "%TEMP%\ezmanbo_frontend.log" 2>&1
cd /d "%~dp0"

:wait_frontend
timeout /t 3 /nobreak >nul
curl -s http://localhost:3000 >nul 2>nul
if %ERRORLEVEL% neq 0 goto wait_frontend
echo [OK] 前端就绪

echo.
echo ================================================
echo   系统已就绪！
echo   前端: http://localhost:3000
echo   后端: http://localhost:8000
echo ================================================

start http://localhost:3000

echo 按任意键停止所有服务...
pause >nul
taskkill /f /im python.exe 2>nul
taskkill /f /im node.exe 2>nul
echo 服务已停止
pause
