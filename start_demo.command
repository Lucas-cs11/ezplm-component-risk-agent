#!/bin/bash
# eZmanbo 比赛展示一键启动脚本 (macOS)
# 双击此文件即可启动前后端并打开浏览器

# 获取脚本所在目录
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "================================================"
echo "  eZmanbo 智能元器件选型系统 — 比赛展示版"
echo "================================================"
echo ""

# 检查 Python 环境
PYTHON=$(which python3)
if [ -z "$PYTHON" ]; then
    echo "❌ 未检测到 Python3，请先安装 Python"
    exit 1
fi

# 检查 Node.js 环境
NODE=$(which node)
if [ -z "$NODE" ]; then
    echo "❌ 未检测到 Node.js，请先安装 Node.js"
    exit 1
fi

echo "✅ 环境检查通过"
echo ""

# 清理上次运行状态
lsof -ti :8000 2>/dev/null | xargs kill -9 2>/dev/null
lsof -ti :3000 2>/dev/null | xargs kill -9 2>/dev/null

# 启动后端
echo "🚀 启动后端服务..."
cd "$DIR"
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 > /tmp/ezmanbo_backend.log 2>&1 &
BACKEND_PID=$!
echo "   后端 PID: $BACKEND_PID"

# 等待后端就绪
for i in $(seq 1 30); do
    sleep 1
    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
        echo "✅ 后端就绪"
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "❌ 后端启动超时"
        kill $BACKEND_PID 2>/dev/null
        exit 1
    fi
done

# 启动前端
echo "🚀 启动前端服务..."
cd "$DIR/frontend/web"
npx next dev -p 3000 > /tmp/ezmanbo_frontend.log 2>&1 &
FRONTEND_PID=$!
echo "   前端 PID: $FRONTEND_PID"

# 等待前端就绪（首次启动需编译约 2-3 分钟）
echo "   ⏳ 首次启动需要编译前端模块，约需 2-3 分钟，请稍候..."
FRONTEND_READY=0
for i in $(seq 1 60); do
    sleep 5
    if curl -s --max-time 5 http://localhost:3000 > /dev/null 2>&1; then
        echo "✅ 前端就绪"
        FRONTEND_READY=1
        break
    fi
    echo "   编译中... (${i}/60，已等待 $((i * 5))s)"
done
if [ "$FRONTEND_READY" -eq 0 ]; then
    echo "⚠️ 前端编译超时，请检查日志: /tmp/ezmanbo_frontend.log"
fi

# 打开浏览器
echo ""
echo "================================================"
echo "  系统已就绪，正在打开浏览器..."
echo "  前端: http://localhost:3000"
echo "  后端: http://localhost:8000"
echo "================================================"
echo ""
echo "  按 Ctrl+C 停止所有服务"

open http://localhost:3000

# 等待退出信号
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; echo '服务已停止'; exit 0" INT TERM
wait
