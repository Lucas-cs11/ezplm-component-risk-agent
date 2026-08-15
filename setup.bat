@echo off
chcp 65001 >nul
title eZmanbo 环境安装器
echo ============================================
echo      eZmanbo — 电子元器件智能选型系统
echo      环境安装与配置向导
echo ============================================
echo.

:: ── 检测 Python ────────────────────────────────────
echo [1/4] 检测 Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ 未检测到 Python，请先安装 Python 3.9+
    echo    下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)
for /f "tokens=2" %%i in ('python --version 2^>^&1') do set pyver=%%i
echo ✅ Python %pyver%

:: ── 检测 Node.js ───────────────────────────────────
echo [2/4] 检测 Node.js...
node --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ 未检测到 Node.js，请先安装 Node.js 18+
    echo    下载地址: https://nodejs.org/
    pause
    exit /b 1
)
for /f "tokens=1" %%i in ('node --version') do set nodever=%%i
echo ✅ Node.js %nodever%

:: ── 安装 Python 依赖 ───────────────────────────────
echo [3/4] 安装 Python 依赖...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo ⚠️ pip 安装失败，尝试使用 pip3...
    pip3 install -r requirements.txt
)
echo ✅ Python 依赖安装完成

:: ── 安装前端依赖 ───────────────────────────────────
echo [4/4] 安装前端依赖...
cd frontend\web
if not exist node_modules (
    call npm install
) else (
    echo ✅ 前端依赖已存在，跳过
)
cd ..\..

:: ── 配置向导 ──────────────────────────────────────
echo.
echo ============================================
echo  安装完成！启动配置向导...
echo ============================================
echo.
echo 启动后端...
start "eZmanbo Backend" /B python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
echo 等待后端启动...
timeout /t 5 /nobreak >nul

echo 启动前端...
start "eZmanbo Frontend" /B cmd /c "cd frontend\web && npx next dev -p 3000"

echo.
echo ✅ 系统已启动！
echo    前端地址: http://localhost:3000
echo    首次使用请先完成 /setup 配置向导
echo.
echo 按任意键退出（服务将在后台继续运行）
pause >nul
