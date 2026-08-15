"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useChatStore } from "@/store/chat";
import { useAuthStore } from "@/store/authStore";
import {
  Plus,
  Trash2,
  MessageSquare,
  LogOut,
  Settings,
  User,
  LayoutDashboard,
  Cpu,
  History,
  X,
  Radio,
  Menu,
  Workflow
} from "lucide-react";
import { cn, buildSelectionContext } from "@/lib/utils";

interface SidebarProps {
  currentTab: "chat" | "dashboard" | "workflow";
  onTabChange: (tab: "chat" | "dashboard" | "workflow") => void;
  onCloseMobile?: () => void;
}

export function Sidebar({ currentTab, onTabChange, onCloseMobile }: SidebarProps) {
  const { 
    sessions, 
    activeSessionId, 
    createSession, 
    switchSession, 
    deleteSession, 
    syncSessionsFromBackend,
    healthStatus
  } = useChatStore();
  const { user, logout } = useAuthStore();
  const router = useRouter();
  
  const [showHistory, setShowHistory] = useState(true);

  useEffect(() => { 
    syncSessionsFromBackend(); 
  }, [syncSessionsFromBackend]);

  const handleCreateSession = () => {
    const prevActive = useChatStore.getState().activeSession();
    const lastReport = prevActive?.messages
      .filter((m) => m.role === "assistant" && m.report).pop()?.report;
    const newId = createSession();
    
    // Switch to Chat tab automatically
    onTabChange("chat");
    
    if (lastReport?.constraints) {
      const token = useAuthStore.getState().token;
      fetch(`/agent/init_session`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: JSON.stringify({ session_id: newId, context_type: "selection_context", context: buildSelectionContext(lastReport) }),
      }).catch(() => {});
    }

    if (onCloseMobile) onCloseMobile();
  };

  const handleSwitchSession = (id: string) => {
    switchSession(id);
    onTabChange("chat");
    if (onCloseMobile) onCloseMobile();
  };

  return (
    <aside className="w-[260px] h-full flex flex-col bg-white border-r border-slate-100/80 shrink-0 overflow-hidden relative">
      
      {/* ── Brand Logo & Status ────────────────────────── */}
      <div className="p-5 border-b border-slate-50 flex items-center justify-between">
        <div className="flex flex-col">
          <span className="avyal-gradient-text font-premium-display font-black text-lg tracking-tight">
            eZmanbo
          </span>
          <span className="text-[10px] text-slate-400 font-medium tracking-wider mt-0.5">
            智能选型与评估平台
          </span>
        </div>

        <div className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-slate-50 border border-slate-100">
          <div className={cn(
            "w-1.5 h-1.5 rounded-full animate-pulse",
            healthStatus === "connected" ? "bg-emerald-500" :
            healthStatus === "checking" ? "bg-amber-400" : "bg-slate-350"
          )} />
          <span className="text-[9px] font-bold text-slate-500">
            {healthStatus === "connected" ? "在线" :
             healthStatus === "checking" ? "同步" : "离线"}
          </span>
        </div>

        {/* Mobile close button */}
        {onCloseMobile && (
          <button onClick={onCloseMobile} className="p-1 text-slate-400 hover:text-slate-600 md:hidden">
            <X className="w-4 h-4" />
          </button>
        )}
      </div>

      {/* ── Main Navigation List ──────────────────────── */}
      <div className="px-3 py-4 space-y-1">
        
        {/* Nav 1: Smart Chat Area */}
        <button
          onClick={() => { onTabChange("chat"); if (onCloseMobile) onCloseMobile(); }}
          className={cn(
            "w-full flex items-center gap-3 px-3.5 h-10 rounded-xl transition-all font-semibold text-xs",
            currentTab === "chat"
              ? "bg-teal-50 text-teal-700 shadow-sm shadow-teal-600/5"
              : "text-slate-500 hover:bg-slate-50 hover:text-slate-800"
          )}
        >
          <Cpu className={cn("w-4 h-4", currentTab === "chat" ? "text-teal-600" : "text-slate-400")} />
          <span>智能器件选型</span>
        </button>

        {/* Nav 2: Analytics Dashboard */}
        <button
          onClick={() => { onTabChange("dashboard"); if (onCloseMobile) onCloseMobile(); }}
          className={cn(
            "w-full flex items-center gap-3 px-3.5 h-10 rounded-xl transition-all font-semibold text-xs",
            currentTab === "dashboard"
              ? "bg-teal-50 text-teal-700 shadow-sm shadow-teal-600/5"
              : "text-slate-500 hover:bg-slate-50 hover:text-slate-800"
          )}
        >
          <LayoutDashboard className={cn("w-4 h-4", currentTab === "dashboard" ? "text-teal-600" : "text-slate-400")} />
          <span>智能数据中心</span>
        </button>

        {/* Nav 3: Workflow Editor */}
        <button
          onClick={() => { onTabChange("workflow"); if (onCloseMobile) onCloseMobile(); }}
          className={cn(
            "w-full flex items-center gap-3 px-3.5 h-10 rounded-xl transition-all font-semibold text-xs",
            currentTab === "workflow"
              ? "bg-teal-50 text-teal-700 shadow-sm shadow-teal-600/5"
              : "text-slate-500 hover:bg-slate-50 hover:text-slate-800"
          )}
        >
          <Radio className={cn("w-4 h-4", currentTab === "workflow" ? "text-teal-600" : "text-slate-400")} />
          <span>工作流编排</span>
        </button>

      </div>

      {/* ── Collapsible Active Sessions Group ──────────── */}
      <div className="flex-1 overflow-y-auto min-h-0 px-3 pb-4">
        <div className="mt-4">
          <div className="flex items-center justify-between px-2.5 mb-2">
            <span className="text-[10px] font-extrabold text-slate-450 uppercase tracking-widest flex items-center gap-1.5">
              <History className="w-3 h-3 text-slate-400" />
              最近选型会话
            </span>
            <button 
              onClick={handleCreateSession} 
              className="p-1 rounded-lg bg-teal-50 text-teal-600 hover:bg-teal-100 transition-colors"
              title="新建会话"
            >
              <Plus className="w-3 h-3" />
            </button>
          </div>

          <div className="space-y-1 max-h-[300px] md:max-h-none overflow-y-auto pr-0.5">
            {sessions.length === 0 ? (
              <p className="px-3 py-6 text-center text-slate-400 text-2xs tracking-wide">暂无选型记录</p>
            ) : (
              sessions.map((s, idx) => (
                <div 
                  key={s.id}
                  className={cn(
                    "w-full flex items-center gap-2.5 px-3.5 h-9 rounded-xl transition-all group relative cursor-pointer",
                    s.id === activeSessionId && currentTab === "chat"
                      ? "bg-slate-50 text-slate-850 border border-slate-100"
                      : "text-slate-500 hover:bg-slate-50/60 hover:text-slate-800"
                  )}
                  onClick={() => handleSwitchSession(s.id)}
                >
                  <span className="text-[10px] font-mono font-bold text-slate-400 shrink-0 w-4">
                    {String(idx + 1).padStart(2, "0")}
                  </span>
                  <MessageSquare className="w-3.5 h-3.5 shrink-0 opacity-45 text-slate-450" />
                  <div className="flex flex-col min-w-0 flex-1">
                    <span className="truncate text-xs font-medium">{s.title || "新的对话"}</span>
                    <span className="text-[9px] text-slate-400 font-mono">{(() => { const d = new Date(s.createdAt || Date.now()); return `${d.getFullYear()}年${d.getMonth()+1}月${d.getDate()}日 ${String(d.getHours()).padStart(2,"0")}:${String(d.getMinutes()).padStart(2,"0")}`; })()}</span>
                  </div>
                  
                  <button
                    onClick={(e) => { 
                      e.stopPropagation(); 
                      if (sessions.length > 1) deleteSession(s.id); 
                    }}
                    className="opacity-0 group-hover:opacity-100 p-1 text-slate-400 hover:text-rose-600 hover:bg-rose-50 rounded-md transition-all absolute right-2 bg-slate-50 md:bg-transparent"
                  >
                    <Trash2 className="w-3 h-3" />
                  </button>
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      {/* ── User Profile Bottom Section ────────────────── */}
      <div className="p-4 border-t border-slate-50 bg-slate-50/40">
        
        {/* Floating card layout */}
        <div className="p-3 bg-white border border-slate-100 shadow-sm rounded-2xl flex items-center gap-3">
          <div className="w-8 h-8 rounded-xl bg-teal-50 border border-teal-100 flex items-center justify-center text-teal-600 font-extrabold text-sm shrink-0">
            {(user?.username || "U").slice(0, 1).toUpperCase()}
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-xs font-bold text-slate-800 truncate leading-none mb-1">
              {user?.username || "研究员"}
            </p>
            <span className="text-[9px] bg-slate-100 text-slate-500 font-bold px-1.5 py-0.5 rounded-md uppercase tracking-wider">
              {user?.is_admin ? "管理员" : "工程师"}
            </span>
          </div>
        </div>

        {/* Setting & Logout actions */}
        <div className="flex gap-2 mt-3">
          {user?.is_admin && (
            <button 
              onClick={() => router.push("/setup")}
              className="flex-1 flex items-center justify-center gap-1.5 h-8 text-[10px] font-extrabold text-slate-500 bg-white border border-slate-200/60 hover:text-slate-800 hover:bg-slate-50 rounded-xl transition-all"
            >
              <Settings className="w-3.5 h-3.5" /> 设置
            </button>
          )}
          <button 
            onClick={() => { logout(); router.push("/login"); }}
            className="flex-1 flex items-center justify-center gap-1.5 h-8 text-[10px] font-extrabold text-slate-500 bg-white border border-slate-200/60 hover:text-rose-600 hover:border-rose-100 hover:bg-rose-50 rounded-xl transition-all"
          >
            <LogOut className="w-3.5 h-3.5" /> 退出登录
          </button>
        </div>

      </div>

    </aside>
  );
}
