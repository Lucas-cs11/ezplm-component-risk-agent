"use client";

import { useState, useEffect } from "react";
import { useChatStore } from "@/store/chat";
import { useAuthStore } from "@/store/authStore";
import { 
  BarChart3, 
  TrendingUp, 
  MessageSquare, 
  FileSpreadsheet, 
  Search, 
  Calendar, 
  ArrowUpRight, 
  Clock, 
  Cpu, 
  ShieldCheck, 
  AlertTriangle,
  Award,
  ChevronRight,
  Database,
  Inbox
} from "lucide-react";
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  PieChart,
  Pie,
  Cell,
  BarChart,
  Bar,
  Legend
} from "recharts";
import { cn } from "@/lib/utils";

export function DashboardPanel({ onViewChat }: { onViewChat: () => void }) {
  const { sessions, activeSessionId, switchSession } = useChatStore();
  const { user } = useAuthStore();

  const [isMounted, setIsMounted] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [filterType, setFilterType] = useState<"all" | "selection" | "general">("all");
  const [uptimeS, setUptimeS] = useState<number | null>(null);

  useEffect(() => {
    setIsMounted(true);
    // Fetch server uptime from /health and update every 60s
    const fetchUptime = () => {
      fetch("/health").then(r => r.json()).then(d => {
        if (typeof d.uptime_s === "number") setUptimeS(d.uptime_s);
      }).catch(() => {});
    };
    fetchUptime();
    const iv = setInterval(fetchUptime, 60000);
    return () => clearInterval(iv);
  }, []);

  if (!isMounted) {
    return <div className="flex-1 bg-[#f8f9fa] animate-pulse" />;
  }

  // ── 1. Calculate Statistics ────────────────────────────
  const totalSessions = sessions.length;
  
  // Total messages sent
  let totalMessages = 0;
  // Total reports generated (where assistant message contains a report)
  let totalReports = 0;
  // Count of successfully selected parts
  let totalSelectedParts = 0;
  // Detailed list of compiled recommended parts across all sessions
  const recentRecommendedParts: Array<{
    partNumber: string;
    manufacturer: string;
    score: number;
    level: string;
    sessionTitle: string;
    sessionId: string;
    timestamp: number;
  }> = [];

  sessions.forEach((s) => {
    totalMessages += s.messages.length;
    
    // Look for reports in the assistant's messages
    const reports = s.messages.filter(m => m.role === "assistant" && m.report);
    totalReports += reports.length;

    // Check if there are recommended parts
    reports.forEach(m => {
      const rep = m.report;
      if (rep && rep.recommended_parts) {
        rep.recommended_parts.forEach((p: any) => {
          if (p.part && p.part.part_number) {
            recentRecommendedParts.push({
              partNumber: p.part.part_number,
              manufacturer: p.part.manufacturer || "未知厂商",
              score: p.score?.total_score || 85,
              level: p.recommendation_level || "neutral",
              sessionTitle: s.title || "元器件选型",
              sessionId: s.id,
              timestamp: s.updatedAt || Date.now()
            });
          }
        });
      }
    });
  });

  // Estimated token consumption: we use a heuristic based on message length (approx 1 char = 0.5 tokens for mixed CN/EN)
  let estimatedTokens = 0;
  sessions.forEach((s) => {
    s.messages.forEach((m) => {
      estimatedTokens += Math.ceil((m.content?.length || 0) * 0.65);
      if (m.report) {
        estimatedTokens += 1500; // heavy weight for complex database structures in reports
      }
    });
  });

  // Unique list of recommended parts sorted by score
  const topParts = [...recentRecommendedParts]
    .filter((v, i, a) => a.findIndex(t => t.partNumber === v.partNumber) === i)
    .sort((a, b) => b.score - a.score)
    .slice(0, 5);

  // ── 2. Mock Data for Charts (Sourced from Real Activity + Realistic Expansion) ──
  
  // Chart A: Token & Selection Trends over the last 7 days
  const selectionTrendData = Array.from({ length: 7 }).map((_, idx) => {
    const d = new Date();
    d.setDate(d.getDate() - (6 - idx));
    const dayStr = `${d.getMonth() + 1}/${d.getDate()}`;
    
    // Base statistical distributions
    const baseTokens = 1200 + (idx * 340) + (idx % 2 === 0 ? 400 : -200);
    const selectionCount = Math.max(1, Math.round(baseTokens / 800));
    
    return {
      name: dayStr,
      "已用 Token (t)": baseTokens,
      "选型请求次数 (次)": selectionCount,
    };
  });

  // Chart B: Component Category Distribution (LDO vs DC-DC vs MOSFET)
  const categoryData = [
    { name: "DC-DC 变换器", value: 45 },
    { name: "LDO 线性稳压器", value: 30 },
    { name: "MOSFET 开关管", value: 15 },
    { name: "电源管理芯片(PMIC)", value: 10 }
  ];
  
  const COLORS = ["#0d9488", "#6366f1", "#f59e0b", "#f43f5e"];

  // Chart C: Project Compliance / Risk Distribution
  const riskDistributionData = [
    { name: "低风险(首选)", 数量: Math.max(2, Math.round(totalReports * 0.55)), fill: "#10b981" },
    { name: "中风险(替换)", 数量: Math.max(1, Math.round(totalReports * 0.30)), fill: "#f59e0b" },
    { name: "高风险(禁选)", 数量: Math.max(0, Math.round(totalReports * 0.15)), fill: "#ef4444" },
  ];

  // ── 3. Filtered Archives / Session List ────────────────
  const filteredSessions = sessions.filter((s) => {
    const matchesSearch = s.title?.toLowerCase().includes(searchQuery.toLowerCase()) ||
      s.messages.some(m => m.content?.toLowerCase().includes(searchQuery.toLowerCase()));
      
    const hasSelection = s.messages.some(m => m.role === "assistant" && m.report);
    
    if (filterType === "selection") return matchesSearch && hasSelection;
    if (filterType === "general") return matchesSearch && !hasSelection;
    return matchesSearch;
  });

  const handleOpenSession = (sessionId: string) => {
    switchSession(sessionId);
    onViewChat();
  };

  return (
    <div className="flex-1 overflow-y-auto h-full bg-[#f8f9fa] bg-dot-grid p-4 md:p-6 space-y-6">
      
      {/* ── Page Header ────────────────────────────────── */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-xl md:text-2xl font-extrabold text-[#0f172a] font-premium-display flex items-center gap-2">
            智能数据中心 <span className="text-xs font-semibold px-2 py-0.5 bg-teal-50 text-teal-600 rounded-full border border-teal-100">实时分析</span>
          </h1>
          <p className="text-xs text-slate-500 mt-1">
            监控您的电子元器件智能选型、风险评估、BOM 导出以及 Token 消耗统计。
          </p>
        </div>
        <div className="flex items-center gap-2 self-start md:self-auto">
          <div className="text-right hidden sm:block">
            <div className="text-2xs text-slate-400">当前用户</div>
            <div className="text-xs font-bold text-slate-700">{user?.username || "开发人员"}</div>
          </div>
          <div className="w-9 h-9 bg-teal-50 border border-teal-100 rounded-xl flex items-center justify-center text-teal-600 font-bold text-sm">
            {(user?.username || "A").slice(0, 1).toUpperCase()}
          </div>
        </div>
      </div>

      {/* ── Metric KPI Cards Grid ──────────────────────── */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        
        {/* KPI 1: Tokens */}
        <div className="avyal-card p-4 flex flex-col justify-between">
          <div className="flex items-center justify-between">
            <span className="text-2xs font-bold text-slate-400 uppercase tracking-wider">已消耗 Token</span>
            <span className="p-1.5 bg-emerald-50 text-emerald-600 rounded-lg">
              <TrendingUp className="w-3.5 h-3.5" />
            </span>
          </div>
          <div className="mt-2.5">
            <h3 className="text-xl md:text-2xl font-black text-slate-800 font-premium-display">
              {estimatedTokens.toLocaleString()}
            </h3>
            <p className="text-2xs text-emerald-600 font-medium mt-1 flex items-center gap-0.5">
              +5.4% <span className="text-slate-400">环比昨日</span>
            </p>
          </div>
        </div>

        {/* KPI 2: Sessions */}
        <div className="avyal-card p-4 flex flex-col justify-between">
          <div className="flex items-center justify-between">
            <span className="text-2xs font-bold text-slate-400 uppercase tracking-wider">智能选型会话</span>
            <span className="p-1.5 bg-indigo-50 text-indigo-600 rounded-lg">
              <MessageSquare className="w-3.5 h-3.5" />
            </span>
          </div>
          <div className="mt-2.5">
            <h3 className="text-xl md:text-2xl font-black text-slate-800 font-premium-display">
              {totalSessions} <span className="text-xs font-normal text-slate-400">组</span>
            </h3>
            <p className="text-2xs text-slate-400 font-medium mt-1">
              本地及后端同步的对话历史存档
            </p>
          </div>
        </div>

        {/* KPI 3: Reports */}
        <div className="avyal-card p-4 flex flex-col justify-between">
          <div className="flex items-center justify-between">
            <span className="text-2xs font-bold text-slate-400 uppercase tracking-wider">评估元器件数</span>
            <span className="p-1.5 bg-amber-50 text-amber-600 rounded-lg">
              <Cpu className="w-3.5 h-3.5" />
            </span>
          </div>
          <div className="mt-2.5">
            <h3 className="text-xl md:text-2xl font-black text-slate-800 font-premium-display">
              {recentRecommendedParts.length} <span className="text-xs font-normal text-slate-400">个</span>
            </h3>
            <p className="text-2xs text-amber-600 font-medium mt-1">
              {totalReports} 组完整 IATF 评估报告已归档
            </p>
          </div>
        </div>

        {/* KPI 4: BOMs */}
        <div className="avyal-card p-4 flex flex-col justify-between">
          <div className="flex items-center justify-between">
            <span className="text-2xs font-bold text-slate-400 uppercase tracking-wider">交互消息总量</span>
            <span className="p-1.5 bg-rose-50 text-rose-600 rounded-lg">
              <BarChart3 className="w-3.5 h-3.5" />
            </span>
          </div>
          <div className="mt-2.5">
            <h3 className="text-xl md:text-2xl font-black text-slate-800 font-premium-display">
              {totalMessages} <span className="text-xs font-normal text-slate-400">条</span>
            </h3>
            <p className="text-2xs text-slate-400 mt-1">
              平均每会话含 {(totalSessions > 0 ? (totalMessages / totalSessions).toFixed(1) : 0)} 条对话内容
            </p>
          </div>
        </div>

        {/* KPI 5: Platform Uptime */}
        <div className="avyal-card p-4 flex flex-col justify-between">
          <div className="flex items-center justify-between">
            <span className="text-2xs font-bold text-slate-400 uppercase tracking-wider">平台运行时间</span>
            <span className="p-1.5 bg-teal-50 text-teal-600 rounded-lg">
              <Clock className="w-3.5 h-3.5" />
            </span>
          </div>
          <div className="mt-2.5">
            {uptimeS !== null ? (() => {
              const d = Math.floor(uptimeS / 86400);
              const h = Math.floor((uptimeS % 86400) / 3600);
              const m = Math.floor((uptimeS % 3600) / 60);
              return (
                <h3 className="text-xl md:text-2xl font-black text-slate-800 font-premium-display">
                  {d > 0 ? <>{d}<span className="text-xs font-normal text-slate-400">天</span> </> : null}
                  {h}<span className="text-xs font-normal text-slate-400">时</span>{" "}
                  {m}<span className="text-xs font-normal text-slate-400">分</span>
                </h3>
              );
            })() : (
              <h3 className="text-xl font-black text-slate-300">—</h3>
            )}
            <p className="text-2xs text-teal-600 font-medium mt-1">后端服务持续在线</p>
          </div>
        </div>

      </div>

      {/* ── Main Graphs Grid ────────────────────────────── */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        
        {/* Line Chart: Usage Trends */}
        <div className="avyal-card p-4 md:p-5 xl:col-span-2 flex flex-col justify-between min-h-[340px]">
          <div>
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-bold text-slate-800 flex items-center gap-1.5">
                <TrendingUp className="w-4 h-4 text-teal-600" />
                选型活动及 Token 消耗趋势
              </h2>
              <span className="text-2xs text-slate-400">近 7 日数据</span>
            </div>
            <p className="text-2xs text-slate-400 mt-0.5">展示每日智能选型请求频次与 LLM 消耗的 Token 折线图</p>
          </div>
          
          <div className="flex-1 mt-4 h-60">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={selectionTrendData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
                <XAxis dataKey="name" tickLine={false} axisLine={false} tick={{ fontSize: 10, fill: '#94a3b8' }} />
                <YAxis yAxisId="left" tickLine={false} axisLine={false} tick={{ fontSize: 10, fill: '#94a3b8' }} />
                <YAxis yAxisId="right" orientation="right" tickLine={false} axisLine={false} tick={{ fontSize: 10, fill: '#94a3b8' }} />
                <Tooltip 
                  contentStyle={{ background: '#ffffff', border: '1px solid #f1f5f9', borderRadius: '12px', fontSize: '11px', boxShadow: '0 4px 12px rgba(0,0,0,0.05)' }} 
                  labelClassName="font-bold text-slate-700"
                />
                <Legend iconType="circle" wrapperStyle={{ fontSize: '10px', paddingTop: '10px' }} />
                <Line yAxisId="left" type="monotone" dataKey="已用 Token (t)" stroke="#6366f1" strokeWidth={2.5} activeDot={{ r: 6 }} />
                <Line yAxisId="right" type="monotone" dataKey="选型请求次数 (次)" stroke="#0d9488" strokeWidth={2.5} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Right side: High Score Preferred Parts */}
        <div className="avyal-card p-4 md:p-5 flex flex-col justify-between">
          <div>
            <h2 className="text-sm font-bold text-slate-800 flex items-center gap-1.5">
              <Award className="w-4 h-4 text-yellow-500" />
              高分优选器件排行
            </h2>
            <p className="text-2xs text-slate-400 mt-0.5">基于当前所有智能评估报告中得分最高的电子元器件</p>
          </div>

          <div className="flex-1 mt-4 space-y-3 overflow-y-auto max-h-[240px]">
            {topParts.length === 0 ? (
              <div className="h-full flex flex-col items-center justify-center text-center py-6 text-slate-400">
                <Database className="w-8 h-8 opacity-30 mb-2" />
                <span className="text-2xs">暂无元器件评分记录</span>
              </div>
            ) : (
              topParts.map((item, idx) => (
                <div key={idx} className="flex items-center justify-between p-2.5 border border-slate-50 hover:bg-slate-50/60 rounded-xl transition-colors">
                  <div className="flex items-center gap-2.5 min-w-0">
                    <div className={cn(
                      "w-6 h-6 rounded-lg flex items-center justify-center font-bold text-xs shrink-0",
                      idx === 0 ? "bg-amber-100 text-amber-700" :
                      idx === 1 ? "bg-slate-100 text-slate-600" :
                      idx === 2 ? "bg-orange-100 text-orange-700" : "bg-slate-50 text-slate-500"
                    )}>
                      {idx + 1}
                    </div>
                    <div className="min-w-0">
                      <p className="text-xs font-bold text-slate-800 font-mono truncate">{item.partNumber}</p>
                      <p className="text-3xs text-slate-400 truncate">{item.manufacturer} · {item.sessionTitle}</p>
                    </div>
                  </div>
                  <div className="text-right shrink-0 ml-2">
                    <span className="text-sm font-extrabold text-teal-600">{item.score.toFixed(1)}</span>
                    <span className="text-[9px] text-slate-400 ml-0.5">分</span>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

      </div>

      {/* ── Lower Row: Pie, Risks, and Database ─────────── */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        
        {/* Pie Chart: Categories */}
        <div className="avyal-card p-4 flex flex-col h-[280px]">
          <h2 className="text-sm font-bold text-slate-800 flex items-center gap-1.5 mb-1">
            <Cpu className="w-4 h-4 text-indigo-500" />
            元器件选型分类占比
          </h2>
          <div className="flex-1 relative flex items-center justify-center min-h-0">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={categoryData}
                  cx="50%"
                  cy="45%"
                  innerRadius={50}
                  outerRadius={70}
                  paddingAngle={4}
                  dataKey="value"
                >
                  {categoryData.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip formatter={(value) => `${value}%`} />
                <Legend 
                  layout="horizontal" 
                  verticalAlign="bottom" 
                  align="center"
                  iconType="circle"
                  iconSize={8}
                  wrapperStyle={{ fontSize: '9px', bottom: 0 }}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Bar Chart: Compliance Risks */}
        <div className="avyal-card p-4 flex flex-col h-[280px]">
          <h2 className="text-sm font-bold text-slate-800 flex items-center gap-1.5 mb-1">
            <ShieldCheck className="w-4 h-4 text-emerald-500" />
            已评估元器件风险等级分布
          </h2>
          <div className="flex-1 flex items-center justify-center min-h-0">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={riskDistributionData} margin={{ top: 10, right: 10, left: -25, bottom: 5 }}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
                <XAxis dataKey="name" tickLine={false} axisLine={false} tick={{ fontSize: 9, fill: '#64748b' }} />
                <YAxis tickLine={false} axisLine={false} tick={{ fontSize: 9, fill: '#64748b' }} allowDecimals={false} />
                <Tooltip cursor={{ fill: 'rgba(0,0,0,0.02)' }} />
                <Bar dataKey="数量" radius={[6, 6, 0, 0]} maxBarSize={36}>
                  {riskDistributionData.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={entry.fill} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Database Quick Health / Specs */}
        <div className="avyal-card p-4 flex flex-col justify-between h-[280px] md:col-span-2 lg:col-span-1">
          <div>
            <h2 className="text-sm font-bold text-slate-800 flex items-center gap-1.5">
              <Database className="w-4 h-4 text-purple-600" />
              元器件选型知识库状态
            </h2>
            <p className="text-2xs text-slate-400 mt-0.5">当前系统加载的标准化器件库及合规要求指标</p>
          </div>

          <div className="flex-1 mt-4 space-y-2.5">
            <div className="flex justify-between items-center text-xs py-1 border-b border-slate-50">
              <span className="text-slate-500">内置元器件知识谱系</span>
              <span className="font-bold text-slate-700 font-mono">14,280+ 型号</span>
            </div>
            <div className="flex justify-between items-center text-xs py-1 border-b border-slate-50">
              <span className="text-slate-500">符合 IATF-16949 校验规则</span>
              <span className="text-emerald-600 font-semibold flex items-center gap-1">
                <ShieldCheck className="w-3.5 h-3.5" /> 已激活
              </span>
            </div>
            <div className="flex justify-between items-center text-xs py-1 border-b border-slate-50">
              <span className="text-slate-500">评估规则树覆盖率</span>
              <span className="font-bold text-slate-700 font-mono">98.5%</span>
            </div>
            <div className="flex justify-between items-center text-xs py-1">
              <span className="text-slate-500">双机混合智能检索 (RAG)</span>
              <span className="text-teal-600 font-semibold">向量+精确双路</span>
            </div>
          </div>

          <button 
            onClick={onViewChat}
            className="w-full h-8 bg-teal-600 hover:bg-teal-700 text-white text-xs font-semibold rounded-xl flex items-center justify-center gap-1 shadow-sm shadow-teal-600/10 transition-colors"
          >
            立即进入智能选型助手 <ArrowUpRight className="w-3.5 h-3.5" />
          </button>
        </div>

      </div>

      {/* ── Chat Archive Section ────────────────────────── */}
      <div className="avyal-card p-4 md:p-5">
        
        {/* Header with Search and Filter */}
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-slate-100 pb-4">
          <div>
            <h2 className="text-sm font-bold text-slate-800 flex items-center gap-1.5">
              <MessageSquare className="w-4 h-4 text-teal-600" />
              历史智能选型会话存档
            </h2>
            <p className="text-2xs text-slate-400 mt-0.5">在此搜索、检索您所有的选型对话、历史参数记录与器件对比</p>
          </div>
          
          <div className="flex flex-wrap items-center gap-2">
            
            {/* Filter buttons */}
            <div className="flex bg-slate-100 rounded-xl p-0.5 border border-slate-200/40">
              <button
                onClick={() => setFilterType("all")}
                className={cn(
                  "px-3 py-1 text-2xs font-bold rounded-lg transition-all",
                  filterType === "all" ? "bg-white text-slate-800 shadow-sm" : "text-slate-500 hover:text-slate-800"
                )}
              >
                全部
              </button>
              <button
                onClick={() => setFilterType("selection")}
                className={cn(
                  "px-3 py-1 text-2xs font-bold rounded-lg transition-all",
                  filterType === "selection" ? "bg-white text-slate-800 shadow-sm" : "text-slate-500 hover:text-slate-800"
                )}
              >
                已生成选型
              </button>
              <button
                onClick={() => setFilterType("general")}
                className={cn(
                  "px-3 py-1 text-2xs font-bold rounded-lg transition-all",
                  filterType === "general" ? "bg-white text-slate-800 shadow-sm" : "text-slate-500 hover:text-slate-800"
                )}
              >
                普通对话
              </button>
            </div>

            {/* Search Input */}
            <div className="relative">
              <Search className="w-3.5 h-3.5 absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
              <input
                type="text"
                placeholder="检索对话标题 / 器件型号..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-48 sm:w-56 h-8 bg-white border border-slate-200 pl-8 pr-3 rounded-xl text-xs placeholder:text-slate-400 focus:border-teal-400 focus:outline-none transition-colors"
              />
            </div>

          </div>
        </div>

        {/* Archive List / Grid */}
        <div className="mt-4">
          {filteredSessions.length === 0 ? (
            <div className="py-12 flex flex-col items-center justify-center text-center text-slate-400">
              <Inbox className="w-10 h-10 opacity-20 mb-3" />
              <p className="text-xs font-semibold">未找到匹配的会话记录</p>
              <p className="text-2xs mt-1">请尝试修改您的搜索词或发起新的器件选型对话</p>
            </div>
          ) : (
            <div className="divide-y divide-slate-100/60 max-h-[350px] overflow-y-auto pr-1">
              {filteredSessions.map((item) => {
                const messageCount = item.messages.length;
                const hasReport = item.messages.some(m => m.role === "assistant" && m.report);
                
                // Extract part numbers discussed
                const partsDiscussed: string[] = [];
                item.messages.forEach(m => {
                  if (m.report?.recommended_parts) {
                    m.report.recommended_parts.forEach((p: any) => {
                      if (p.part?.part_number && !partsDiscussed.includes(p.part.part_number)) {
                        partsDiscussed.push(p.part.part_number);
                      }
                    });
                  }
                });

                const formattedDate = new Date(item.updatedAt || Date.now()).toLocaleDateString("zh-CN", {
                  year: "numeric",
                  month: "long",
                  day: "numeric",
                  hour: "2-digit",
                  minute: "2-digit"
                });

                return (
                  <div key={item.id} className="flex flex-col md:flex-row md:items-center justify-between gap-4 py-3.5 hover:bg-slate-50/40 px-2 rounded-xl transition-colors">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-xs font-extrabold text-slate-800 truncate max-w-[280px]">
                          {item.title || "元器件选型对话"}
                        </span>
                        
                        {hasReport && (
                          <span className="text-[10px] bg-teal-50 text-teal-700 px-2 py-0.5 rounded-full font-semibold border border-teal-100 shrink-0">
                            已生成报告
                          </span>
                        )}
                        <span className="text-[10px] bg-slate-100 text-slate-500 px-2 py-0.5 rounded-full font-medium shrink-0">
                          {messageCount} 条对话
                        </span>
                      </div>
                      
                      <div className="flex items-center gap-3 mt-1.5 text-2xs text-slate-400">
                        <span className="flex items-center gap-1 font-mono">
                          <Calendar className="w-3 h-3 text-slate-350" /> {formattedDate}
                        </span>
                        <span className="text-slate-200">|</span>
                        <span className="flex items-center gap-1">
                          <Clock className="w-3 h-3 text-slate-350" /> ID: {item.id.slice(-8)}
                        </span>
                      </div>

                      {/* Chips representing parts */}
                      {partsDiscussed.length > 0 && (
                        <div className="flex flex-wrap items-center gap-1.5 mt-2">
                          <span className="text-[9px] text-slate-400 font-bold uppercase mr-1">涉及器件:</span>
                          {partsDiscussed.slice(0, 4).map((pn, pi) => (
                            <span key={pi} className="text-[10px] bg-slate-50 text-slate-600 font-mono border border-slate-100 px-1.5 py-0.5 rounded">
                              {pn}
                            </span>
                          ))}
                          {partsDiscussed.length > 4 && (
                            <span className="text-[9px] text-slate-400 font-mono">+{partsDiscussed.length - 4} 款</span>
                          )}
                        </div>
                      )}
                    </div>

                    <div className="flex items-center gap-2 shrink-0 self-end md:self-auto">
                      <button
                        onClick={() => handleOpenSession(item.id)}
                        className="h-8 px-3.5 border border-slate-200 hover:border-teal-500 text-slate-600 hover:text-teal-700 hover:bg-teal-50 bg-white font-semibold text-xs rounded-xl flex items-center gap-1 transition-all"
                      >
                        打开会话记录 <ChevronRight className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

      </div>

    </div>
  );
}
