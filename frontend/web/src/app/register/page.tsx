"use client";

import { Lock } from "lucide-react";

export default function RegisterPage() {
  return (
    <div className="h-screen flex items-center justify-center bg-ez-bg">
      <div className="w-[340px]">
        <div className="text-center mb-8">
          <div className="text-ez-accent font-mono font-bold text-2xl tracking-[0.25em] mb-1">eZmanbo</div>
          <div className="text-ez-text-label text-2xs tracking-[0.15em] font-medium">智能元器件选型平台</div>
        </div>
        <div className="bg-ez-bg-panel border border-ez-border rounded-lg overflow-hidden">
          <div className="panel-header"><Lock className="w-3 h-3" /><span>用户注册</span></div>
          <div className="p-8 text-center space-y-4">
            <div className="w-14 h-14 mx-auto rounded-full bg-amber-50 border border-amber-200 flex items-center justify-center">
              <Lock className="w-6 h-6 text-amber-500" />
            </div>
            <div>
              <p className="text-ez-text font-semibold text-sm">内测阶段</p>
              <p className="text-ez-text-muted text-xs mt-1.5 leading-relaxed">注册通道尚未开通，感谢您的关注</p>
              <p className="text-ez-text-label text-2xs mt-2 leading-relaxed">如需访问权限，请联系系统管理员</p>
            </div>
          </div>
        </div>
        <div className="mt-4 flex justify-center">
          <a href="/login" className="text-ez-accent text-xs hover:text-ez-accent-hi transition-colors">← 返回登录</a>
        </div>
      </div>
    </div>
  );
}
