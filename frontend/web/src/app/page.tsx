"use client";

import { useEffect, useRef, useState } from "react";
import { useChatStore } from "@/store/chat";
import { Sidebar } from "@/components/Sidebar";
import { RightToolPanel } from "@/components/RightToolPanel";
import { ResultModal } from "@/components/ResultModal";
import { DashboardPanel } from "@/components/DashboardPanel";
import { WorkflowEditor } from "@/components/WorkflowEditor";
import { ChatArea } from "@/components/ChatArea";
import { useAuthStore } from "@/store/authStore";
import { Activity, Menu } from "lucide-react";
import { cn } from "@/lib/utils";

export default function Home() {
  const { sessions, createSession, activeSession, healthStatus, setPendingInput } = useChatStore();
  const { user } = useAuthStore();
  const initialized = useRef(false);
  
  // Layout views & responsiveness states
  const [currentTab, setCurrentTab] = useState<"chat" | "dashboard" | "workflow">("chat");
  const [leftOpen, setLeftOpen] = useState(true);
  const [rightOpen, setRightOpen] = useState(true);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);

  useEffect(() => {
    if (!initialized.current && sessions.length === 0) {
      createSession();
      initialized.current = true;
    }
  }, [sessions.length, createSession]);

  const session = activeSession();
  const msgCount = session?.messages?.length ?? 0;

  return (
    <div className="w-screen h-screen overflow-hidden bg-gradient-to-tr from-[#022c22] via-[#044f3f] to-[#0f172a] md:p-4 flex items-center justify-center font-sans">
      
      {/* ── Main Application Glass Panel Container ──────────────── */}
      <div className="w-full h-full bg-[#f8f9fa] md:rounded-[24px] shadow-[0_24px_50px_rgba(0,0,0,0.3)] border border-white/10 overflow-hidden flex flex-col md:flex-row relative">
        
        {/* ── Desktop Sidebar: Side-by-side layout ── */}
        <div className="hidden md:block h-full shrink-0">
          {leftOpen && (
            <Sidebar 
              currentTab={currentTab} 
              onTabChange={(tab) => setCurrentTab(tab)} 
            />
          )}
        </div>

        {/* ── Mobile Sidebar Drawer Slide-Out ── */}
        {mobileSidebarOpen && (
          <div className="md:hidden fixed inset-0 z-50 flex">
            {/* Dark blur backdrop */}
            <div 
              className="fixed inset-0 bg-slate-900/40 backdrop-blur-sm transition-opacity"
              onClick={() => setMobileSidebarOpen(false)}
            />
            {/* Drawer container */}
            <div className="relative flex-1 flex flex-col max-w-[280px] w-full bg-white h-full animate-slide-up shadow-2xl">
              <Sidebar 
                currentTab={currentTab} 
                onTabChange={(tab) => {
                  setCurrentTab(tab);
                  setMobileSidebarOpen(false);
                }} 
                onCloseMobile={() => setMobileSidebarOpen(false)}
              />
            </div>
          </div>
        )}

        {/* ── Main Working Canvas ────────────────────────── */}
        <div className="flex-1 flex flex-col min-w-0 h-full relative">
          
          {/* Mobile top navigation header bar */}
          <header className="md:hidden flex items-center justify-between h-12 px-4 bg-white border-b border-slate-100 flex-shrink-0 select-none">
            <button 
              onClick={() => setMobileSidebarOpen(true)}
              className="p-1 text-slate-500 hover:text-slate-800 focus:outline-none"
              title="打开菜单"
            >
              <Menu className="w-5 h-5" />
            </button>
            <div className="flex flex-col items-center">
              <span className="text-sm font-black avyal-gradient-text tracking-wide leading-none">eZmanbo</span>
              <span className="text-[9px] text-slate-400 mt-0.5 font-semibold">智能选型</span>
            </div>
            <div className="flex items-center gap-1.5">
              <div className={cn("w-1.5 h-1.5 rounded-full animate-pulse",
                healthStatus === "connected" ? "bg-emerald-500" : "bg-amber-400"
              )} />
              <span className="text-[10px] font-bold text-slate-500 uppercase">
                {healthStatus === "connected" ? "在线" : "连接中…"}
              </span>
            </div>
          </header>

          {/* Conditional layout render based on currentTab */}
          <div className="flex-1 flex min-h-0 overflow-hidden relative">
            {currentTab === "chat" ? (
              <>
                <ChatArea
                  leftOpen={leftOpen}
                  rightOpen={rightOpen}
                  onToggleLeft={() => setLeftOpen(v => !v)}
                  onToggleRight={() => setRightOpen(v => !v)}
                  onToggleMobileMenu={() => setMobileSidebarOpen(v => !v)}
                />

                {/* Desktop Right Tool Panel */}
                <div className="hidden lg:flex h-full shrink-0">
                  <RightToolPanel sessionId={session?.id} />
                </div>
              </>
            ) : currentTab === "dashboard" ? (
              <DashboardPanel onViewChat={() => setCurrentTab("chat")} />
            ) : (
              <WorkflowEditor
                onClose={() => setCurrentTab("chat")}
                onRun={(prompt) => {
                  if (prompt) setPendingInput(prompt);
                  setCurrentTab("chat");
                }}
              />
            )}
          </div>

          {/* ── Status Bar Strip (Only shown on Desktop md+) ── */}
          <footer className="hidden md:flex items-center h-[24px] px-5 bg-white border-t border-slate-50 flex-shrink-0 select-none gap-4">
            <span className="text-slate-400 text-[10px] font-semibold">用户: <span className="text-slate-600 font-bold">{user?.username ?? "—"}</span></span>
            <span className="text-slate-200 text-2xs">|</span>
            <span className="text-slate-400 text-[10px] font-semibold">本轮消息数: <span className="text-slate-600 font-mono font-bold">{msgCount}</span></span>
            <span className="text-slate-200 text-2xs">|</span>
            <span className="text-slate-400 text-[10px] font-semibold">会话索引: <span className="text-slate-600 font-mono font-bold">{session?.id?.slice(-8) ?? "—"}</span></span>
            <div className="flex-1" />
            <Activity className="w-3 h-3 text-teal-500 animate-pulse" />
            <span className="text-slate-450 text-[10px] font-bold tracking-tight">eZmanbo v2.5 · 电子选型决策 Agent 系统</span>
          </footer>

        </div>

      </div>

      {/* Result Modal - global overlay */}
      <ResultModal />

    </div>
  );
}
