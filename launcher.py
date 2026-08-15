#!/usr/bin/env python3
"""
eZmanbo 桌面启动器
功能：一键启动/停止前后端，状态监控，配置检查
"""

import os
import sys
import subprocess
import time
import threading
import json
import tkinter as tk
from tkinter import ttk, scrolledtext
from pathlib import Path
from urllib.request import urlopen, Request

ROOT = Path(__file__).parent
BACKEND_PORT = 8000
FRONTEND_PORT = 3000

# 检测运行模式：优先使用打包好的二进制，回退到 python 源码
_BACKEND_BINARY = ROOT / "ezmanbo-server"
if _BACKEND_BINARY.exists():
    BACKEND_CMD = [str(_BACKEND_BINARY)]
else:
    BACKEND_CMD = [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", str(BACKEND_PORT)]


class LauncherApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("eZmanbo 控制面板")
        self.root.geometry("700x500")
        self.root.configure(bg="#f8f9fa")

        self.backend_proc = None
        self.frontend_proc = None
        self.monitor_active = True

        self._build_ui()
        self._start_monitor()

    def _build_ui(self):
        # Header
        header = tk.Frame(self.root, bg="#1a1a2e", height=60)
        header.pack(fill="x")
        tk.Label(header, text="eZmanbo 控制系统", fg="white", bg="#1a1a2e",
                 font=("Helvetica", 18, "bold")).pack(expand=True)

        # Status indicators
        status_frame = tk.Frame(self.root, bg="#f8f9fa", pady=15)
        status_frame.pack(fill="x")

        self.status_labels = {}
        for i, (key, label) in enumerate([
            ("backend", "后端服务"),
            ("frontend", "前端服务"),
            ("llm", "LLM 连接"),
            ("ezplm", "eZ-PLM 连接"),
        ]):
            f = tk.Frame(status_frame, bg="white", relief="solid", bd=1, padx=12, pady=8)
            f.pack(side="left", padx=6, expand=True, fill="x")
            tk.Label(f, text=label, font=("Helvetica", 9), fg="#666").pack()
            lbl = tk.Label(f, text="● 检测中", font=("Helvetica", 11, "bold"), fg="#999")
            lbl.pack()
            self.status_labels[key] = lbl

        # Controls
        btn_frame = tk.Frame(self.root, bg="#f8f9fa", pady=10)
        btn_frame.pack(fill="x")

        self.start_btn = tk.Button(btn_frame, text="🚀 启动全部",
            command=self._start_all, bg="#0d6efd", fg="white",
            font=("Helvetica", 12, "bold"), padx=20, pady=6, bd=0)
        self.start_btn.pack(side="left", padx=10, expand=True)

        self.stop_btn = tk.Button(btn_frame, text="⏹ 停止全部",
            command=self._stop_all, bg="#dc3545", fg="white",
            font=("Helvetica", 12, "bold"), padx=20, pady=6, bd=0, state="disabled")
        self.stop_btn.pack(side="left", padx=10, expand=True)

        tk.Button(btn_frame, text="🌐 打开浏览器",
            command=self._open_browser, bg="#198754", fg="white",
            font=("Helvetica", 12), padx=20, pady=6, bd=0).pack(side="left", padx=10, expand=True)

        # Log output
        log_frame = tk.Frame(self.root, bg="#f8f9fa")
        log_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        tk.Label(log_frame, text="运行日志", font=("Helvetica", 10, "bold"),
                 fg="#333", bg="#f8f9fa").pack(anchor="w")
        self.log = scrolledtext.ScrolledText(log_frame, bg="#1a1a2e", fg="#0f0",
            font=("Courier", 9), height=12, bd=0, padx=8, pady=8)
        self.log.pack(fill="both", expand=True)

    def _log(self, msg):
        import datetime
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.log.insert("end", f"[{ts}] {msg}\n")
        self.log.see("end")
        self.root.update()

    def _start_backend(self):
        self._log("正在启动后端服务...")
        cwd = str(ROOT) if not _BACKEND_BINARY.exists() else str(ROOT / "dist")
        self.backend_proc = subprocess.Popen(
            BACKEND_CMD + ["--port", str(BACKEND_PORT)],
            cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        self._log(f"后端 PID: {self.backend_proc.pid}")

    def _start_frontend(self):
        self._log("正在启动前端...")
        frontend_dir = ROOT / "frontend" / "web"
        if (frontend_dir / "node_modules").exists():
            self.frontend_proc = subprocess.Popen(
                ["npx", "next", "dev", "-p", str(FRONTEND_PORT)],
                cwd=str(frontend_dir), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            self._log(f"前端 PID: {self.frontend_proc.pid}")
        else:
            self._log("⚠️ node_modules 不存在，跳过前端启动")

    def _start_all(self):
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self._start_backend()
        self._start_frontend()

    def _stop_all(self):
        self._log("正在停止服务...")
        for proc, name in [(self.backend_proc, "后端"), (self.frontend_proc, "前端")]:
            if proc:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                    self._log(f"{name} 已停止")
                except:
                    proc.kill()
                    self._log(f"{name} 已强制停止")
        self.backend_proc = None
        self.frontend_proc = None
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")

    def _open_browser(self):
        import webbrowser
        webbrowser.open(f"http://localhost:{FRONTEND_PORT}")

    def _check_backend(self):
        try:
            r = urlopen(f"http://localhost:{BACKEND_PORT}/health", timeout=3)
            return r.status == 200
        except:
            return False

    def _check_llm(self):
        try:
            r = urlopen(f"http://localhost:{BACKEND_PORT}/api/setup/status", timeout=3)
            data = json.loads(r.read())
            return data.get("llm", {}).get("configured", False)
        except:
            return False

    def _check_ezplm(self):
        try:
            r = urlopen(f"http://localhost:{BACKEND_PORT}/api/setup/status", timeout=3)
            data = json.loads(r.read())
            return data.get("ezplm", {}).get("configured", False)
        except:
            return False

    def _start_monitor(self):
        def loop():
            while self.monitor_active:
                try:
                    backend_ok = self._check_backend()
                    frontend_ok = bool(self.frontend_proc and self.frontend_proc.poll() is None)
                    llm_ok = self._check_llm() if backend_ok else False
                    ezplm_ok = self._check_ezplm() if backend_ok else False

                    self.root.after(0, lambda: self.status_labels["backend"].config(
                        text="● 运行中", fg="#198754" if backend_ok else "#dc3545"))
                    self.root.after(0, lambda: self.status_labels["frontend"].config(
                        text="● 运行中", fg="#198754" if frontend_ok else "#dc3545"))
                    self.root.after(0, lambda: self.status_labels["llm"].config(
                        text="● 已配置" if llm_ok else "● 未配置", fg="#198754" if llm_ok else "#ffc107"))
                    self.root.after(0, lambda: self.status_labels["ezplm"].config(
                        text="● 已配置" if ezplm_ok else "● 未配置", fg="#198754" if ezplm_ok else "#ffc107"))
                except:
                    pass
                time.sleep(3)
        threading.Thread(target=loop, daemon=True).start()

    def _check_onboarding(self):
        """启动后检测是否需要跳转 Onboarding 向导。"""
        try:
            time.sleep(3)  # 等待后端就绪
            r = urlopen(f"http://localhost:{BACKEND_PORT}/api/setup/status", timeout=5)
            data = json.loads(r.read())
            if not data.get("onboarding_done"):
                self._log("🔄 检测到首次使用，打开配置向导...")
                import webbrowser
                webbrowser.open(f"http://localhost:{FRONTEND_PORT}/setup")
                self._log("🌐 配置向导已打开")
        except:
            pass

    def _start_all(self):
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self._start_backend()
        self._start_frontend()
        # 启动后检测 Onboarding
        threading.Thread(target=self._check_onboarding, daemon=True).start()

    def _on_close(self):
        self.monitor_active = False
        self._stop_all()
        self.root.destroy()


if __name__ == "__main__":
    LauncherApp().run()
