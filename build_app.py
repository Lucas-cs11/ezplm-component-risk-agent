"""
eZmanbo 应用打包脚本（PyInstaller）
用法: pip install pyinstaller && python build_app.py
"""

import os
import sys
import shutil
from pathlib import Path

ROOT = Path(__file__).parent

def build_backend():
    """打包后端 FastAPI 为单文件二进制。"""
    print("=== 打包后端 ===")
    entry = ROOT / "app" / "main.py"
    # 使用 PyInstaller 打包 uvicorn + app
    cmd = (
        f"pyinstaller --onefile --name eZmanboServer "
        f"--add-data '{ROOT}/memory:memory' "
        f"--add-data '{ROOT}/.env.example:.' "
        f"--hidden-import uvicorn "
        f"--hidden-import uvicorn.logging "
        f"--hidden-import uvicorn.loops.auto "
        f"--hidden-import uvicorn.protocols.http.auto "
        f"--hidden-import uvicorn.protocols.websockets.auto "
        f"--hidden-import langchain_openai "
        f"--hidden-import langchain_core "
        f"--hidden-import chromadb "
        f"--hidden-import sentence_transformers "
        f"--collect-submodules app "
        f"{entry}"
    )
    os.system(cmd)
    print("后端打包完成")


def build_launcher():
    """打包桌面启动器（简单的 Tkinter GUI）。"""
    print("=== 打包启动器 ===")
    launcher = ROOT / "launcher.py"
    if not launcher.exists():
        print("启动器脚本不存在，跳过")
        return
    cmd = (
        f"pyinstaller --onefile --name eZmanbo "
        f"--add-data '{ROOT}/dist/eZmanboServer:.' "
        f"{launcher}"
    )
    os.system(cmd)
    print("启动器打包完成")


def clean():
    """清理构建产物。"""
    for d in ["build", "__pycache__"]:
        shutil.rmtree(ROOT / d, ignore_errors=True)
    for f in ROOT.glob("*.spec"):
        f.unlink()


if __name__ == "__main__":
    build_backend()
    clean()
    print("\n✅ 打包完成！产物在 dist/ 目录")
    print("   dist/eZmanboServer — 后端服务（直接运行）")
