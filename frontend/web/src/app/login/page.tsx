"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/store/authStore";
import { Lock, User, ArrowRight, Cpu, Shield } from "lucide-react";

export default function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const { login } = useAuthStore();
  const router = useRouter();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const resp = await fetch("/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const data = await resp.json();
      if (!resp.ok) { setError(data.detail || "认证失败，请检查账号密码"); return; }
      login(data.token, data.user);
      router.replace("/");
    } catch { setError("连接失败，请检查网络"); }
    finally { setLoading(false); }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-[#022c22] via-[#044f3f] to-[#0f172a] flex items-center justify-center p-4 relative overflow-hidden">
      {/* Dot-grid ambient overlay */}
      <div className="absolute inset-0 bg-dot-grid opacity-25 pointer-events-none" />
      {/* Glow blob */}
      <div className="absolute top-1/3 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[700px] h-[500px] bg-teal-500/8 rounded-full blur-3xl pointer-events-none" />

      <div className="relative w-full max-w-[420px]">
        {/* Brand header */}
        <div className="text-center mb-10">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-white/10 border border-white/15 backdrop-blur-sm mb-5 shadow-xl">
            <Cpu className="w-8 h-8 text-teal-400" />
          </div>
          <h1 className="text-4xl font-black text-white tracking-tight mb-2 font-premium-display">
            eZmanbo
          </h1>
          <p className="text-sm text-white/45 font-medium tracking-widest uppercase">
            智能元器件选型与评估平台
          </p>
        </div>

        {/* Glass login card */}
        <div className="bg-white/96 backdrop-blur-xl rounded-3xl shadow-2xl shadow-black/40 border border-white/20 overflow-hidden">
          <div className="px-8 pt-8 pb-4">
            <h2 className="text-xl font-extrabold text-slate-800 mb-1">账号登录</h2>
            <p className="text-xs text-slate-400 font-medium">使用您的企业账号访问智能选型平台</p>
          </div>

          <form onSubmit={handleSubmit} className="px-8 pb-8 space-y-4">
            <div>
              <label className="block text-xs font-bold text-slate-500 mb-2 tracking-wide">用户名</label>
              <div className="relative">
                <User className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                <input
                  type="text"
                  value={username}
                  onChange={e => setUsername(e.target.value)}
                  className="w-full h-11 bg-slate-50 border border-slate-200 rounded-xl pl-10 pr-4 text-sm text-slate-800 placeholder:text-slate-400 focus:outline-none focus:border-teal-400 focus:bg-white transition-all"
                  placeholder="请输入用户名"
                  required autoFocus
                />
              </div>
            </div>

            <div>
              <label className="block text-xs font-bold text-slate-500 mb-2 tracking-wide">密码</label>
              <div className="relative">
                <Lock className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                <input
                  type="password"
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  className="w-full h-11 bg-slate-50 border border-slate-200 rounded-xl pl-10 pr-4 text-sm text-slate-800 placeholder:text-slate-400 focus:outline-none focus:border-teal-400 focus:bg-white transition-all"
                  placeholder="请输入登录密码"
                  required
                />
              </div>
            </div>

            {error && (
              <div className="flex items-center gap-2.5 p-3 bg-red-50 border border-red-100 rounded-xl">
                <div className="w-1.5 h-1.5 rounded-full bg-red-500 shrink-0" />
                <span className="text-xs font-medium text-red-700">{error}</span>
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full h-12 bg-gradient-to-r from-teal-600 to-teal-500 hover:from-teal-500 hover:to-teal-400 text-white text-sm font-extrabold rounded-xl flex items-center justify-center gap-2 transition-all shadow-lg shadow-teal-600/25 disabled:opacity-60 mt-2"
            >
              {loading ? (
                <span className="flex items-center gap-2">
                  <div className="w-4 h-4 border-2 border-white/40 border-t-white rounded-full animate-spin" />
                  验证中…
                </span>
              ) : (
                <>登录平台 <ArrowRight className="w-4 h-4" /></>
              )}
            </button>

            <div className="flex items-center justify-between pt-1">
              <span className="text-xs text-slate-400">暂无账号？</span>
              <a href="/register" className="text-xs font-bold text-teal-600 hover:text-teal-700 transition-colors flex items-center gap-1">
                申请内测资格 <ArrowRight className="w-3 h-3" />
              </a>
            </div>
          </form>
        </div>

        <div className="flex items-center justify-center gap-2 mt-6">
          <Shield className="w-3 h-3 text-white/20" />
          <p className="text-white/20 text-xs font-medium">eZmanbo v2.5 · 内测阶段 · 仅限授权用户访问</p>
        </div>
      </div>
    </div>
  );
}
