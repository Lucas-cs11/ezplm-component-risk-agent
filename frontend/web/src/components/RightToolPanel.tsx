"use client";

import React, { useState } from "react";
import { useChatStore } from "@/store/chat";
import { useAuthStore, GUEST_MESSAGE_LIMIT } from "@/store/authStore";
import { DetailPanel } from "@/components/DetailPanel";
import {
  Cpu, Download, Brain, Terminal, SlidersHorizontal,
  FileSpreadsheet, FileText, Package, Zap, ChevronRight,
  X, Clock, Check, AlertCircle, Info
} from "lucide-react";
import { cn } from "@/lib/utils";

type PanelId = "results" | "export" | "thinking" | "log" | "params";

const PANELS: { id: PanelId; icon: React.ElementType; label: string }[] = [
  { id: "results",  icon: Cpu,              label: "器件结果" },
  { id: "export",   icon: Download,         label: "导出报告" },
  { id: "thinking", icon: Brain,            label: "思考深度" },
  { id: "log",      icon: Terminal,         label: "调用日志" },
  { id: "params",   icon: SlidersHorizontal,label: "参数配置" },
];

const DEPTH_OPTIONS = [
  { id: "fast"    as const, label: "快速", desc: "直接检索，跳过深度推理",    color: "text-green-600",  bg: "bg-green-50 border-green-200" },
  { id: "standard" as const,label: "标准", desc: "平衡速度与质量（推荐）",    color: "text-teal-600",   bg: "bg-teal-50 border-teal-200" },
  { id: "deep"    as const, label: "深度", desc: "全面分析，启用Critic评审",  color: "text-purple-600", bg: "bg-purple-50 border-purple-200" },
];

function ExportPanel({ sessionId }: { sessionId?: string }) {
  const { selectedPartNumber } = useChatStore();
  const { token } = useAuthStore();
  const getAuthBearer = (): Record<string, string> =>
    token ? { Authorization: `Bearer ${token}` } : {};

  const handleExport = async (endpoint: string, filename: string, method = "GET") => {
    const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "";
    const url = `${API_BASE}${endpoint}?session_id=${sessionId}`;
    try {
      const resp = await fetch(url, { method, headers: getAuthBearer() });
      const contentType = resp.headers.get("content-type") || "";

      // /report/* 端点返回 JSON { content, type }，而非可直接下载的文件流
      if (contentType.includes("application/json")) {
        const data = await resp.json();
        if (!resp.ok) {
          alert(data?.detail || "导出失败，请稍后重试");
          return;
        }
        const blob = new Blob([data.content ?? ""], { type: "text/markdown;charset=utf-8" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = filename;
        a.click();
        URL.revokeObjectURL(a.href);
        return;
      }

      if (!resp.ok) {
        alert("导出失败，请稍后重试");
        return;
      }
      const blob = await resp.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = filename;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch {
      alert("导出请求失败，请检查网络连接");
    }
  };

  const exports = [
    { icon: FileSpreadsheet, label: "导出 BOM 清单", sub: ".xlsx 格式", action: () => handleExport("/export/bom", "BOM.xlsx", "POST"), color: "text-green-600 bg-green-50 border-green-200" },
    { icon: FileText,        label: "风险评估报告",  sub: ".md 格式",   action: () => handleExport("/report/risk", `风险评估_${selectedPartNumber || "报告"}.md`), color: "text-amber-600 bg-amber-50 border-amber-200" },
    { icon: Package,         label: "IATF 决策包",   sub: ".xlsx 完整包",action: () => handleExport("/export/decision-package", `选型决策包_${selectedPartNumber || "报告"}.xlsx`, "POST"), color: "text-blue-600 bg-blue-50 border-blue-200" },
  ];

  if (!sessionId) {
    return <div className="p-5 text-center text-slate-400 text-xs">发起选型会话后可导出报告</div>;
  }

  return (
    <div className="p-4 space-y-3">
      <p className="text-xs font-bold text-slate-600 mb-4">选择导出格式</p>
      {exports.map((e, i) => (
        <button key={i} onClick={e.action}
          className={cn("w-full flex items-center gap-3 p-3 rounded-xl border transition-all hover:shadow-sm text-left", e.color)}>
          <e.icon className="w-5 h-5 shrink-0" />
          <div className="flex-1 min-w-0">
            <p className="text-xs font-bold">{e.label}</p>
            <p className="text-[10px] opacity-70">{e.sub}</p>
          </div>
          <ChevronRight className="w-3.5 h-3.5 opacity-50" />
        </button>
      ))}
    </div>
  );
}

function ThinkingPanel() {
  const { thinkingDepth, setThinkingDepth } = useChatStore();
  return (
    <div className="p-4 space-y-3">
      <p className="text-xs font-bold text-slate-600 mb-4">选择推理深度</p>
      {DEPTH_OPTIONS.map(d => (
        <button key={d.id} onClick={() => setThinkingDepth(d.id)}
          className={cn("w-full flex items-start gap-3 p-3.5 rounded-xl border-2 transition-all text-left",
            thinkingDepth === d.id ? `${d.bg} border-current` : "bg-white border-slate-100 hover:border-slate-200"
          )}>
          <div className={cn("w-2 h-2 rounded-full mt-1 shrink-0",
            thinkingDepth === d.id ? d.color.replace("text-","bg-") : "bg-slate-300")} />
          <div className="flex-1">
            <p className={cn("text-xs font-extrabold", thinkingDepth === d.id ? d.color : "text-slate-700")}>{d.label}</p>
            <p className="text-[10px] text-slate-500 mt-0.5">{d.desc}</p>
          </div>
          {thinkingDepth === d.id && <Check className={cn("w-3.5 h-3.5 shrink-0 mt-0.5", d.color)} />}
        </button>
      ))}
    </div>
  );
}

function LogPanel() {
  const { toolCallEvents, clearToolCallEvents } = useChatStore();
  const icons: Record<string, React.ElementType> = {
    start: Info, intent: Info, parse_done: Check,
    score_update: Cpu, evidence_done: Check, risk_done: AlertCircle,
    done: Check, clarify_fields: Info, error: AlertCircle,
  };
  const colors: Record<string, string> = {
    start: "text-slate-400", intent: "text-blue-500", parse_done: "text-teal-600",
    score_update: "text-blue-500", evidence_done: "text-green-500", risk_done: "text-amber-500",
    done: "text-green-600", clarify_fields: "text-purple-500", error: "text-red-500",
  };
  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100">
        <p className="text-xs font-bold text-slate-600">实时调用日志</p>
        <button onClick={clearToolCallEvents} className="text-[10px] text-slate-400 hover:text-slate-600 font-medium">清空</button>
      </div>
      <div className="flex-1 overflow-y-auto p-3 space-y-1 font-mono text-[10px]">
        {toolCallEvents.length === 0 ? (
          <div className="py-8 text-center text-slate-400 text-xs">
            <Terminal className="w-8 h-8 mx-auto mb-2 opacity-20" />
            <p>暂无调用记录</p>
          </div>
        ) : toolCallEvents.map(evt => {
          const Icon = icons[evt.type] ?? Info;
          const color = colors[evt.type] ?? "text-slate-400";
          const time = new Date(evt.ts).toTimeString().slice(0,8);
          return (
            <div key={evt.id} className="flex items-start gap-2 py-1 border-b border-slate-50">
              <Icon className={cn("w-3 h-3 mt-0.5 shrink-0", color)} />
              <div className="flex-1 min-w-0">
                <span className={cn("font-bold", color)}>{evt.type}</span>
                <span className="text-slate-400 ml-1">{evt.label}</span>
              </div>
              <span className="text-slate-300 shrink-0">{time}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ParamsPanel() {
  const [config, setConfig] = useState<any>(null);
  const [verifierModel, setVerifierModel] = useState("");
  const [verifierApiKey, setVerifierApiKey] = useState("");
  const [verifierBaseUrl, setVerifierBaseUrl] = useState("");
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; msg: string } | null>(null);
  const [verifierConfigured, setVerifierConfigured] = useState(false);
  const [dualModelToggling, setDualModelToggling] = useState(false);

  const { token } = useAuthStore();
  const { user, setUser } = useAuthStore();
  const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "";
  const isAdmin = user?.is_admin === true;
  const isGuest = user?.is_guest === true;
  const dualEnabled = user?.dual_model_enabled === true;

  React.useEffect(() => {
    const loadData = async () => {
      try {
        // All users: check verifier status
        const sv = await fetch(`${API_BASE}/admin/verifier-status`, {
          headers: { Authorization: `Bearer ${token}` }
        });
        if (sv.ok) {
          const d = await sv.json();
          setVerifierConfigured(d.configured);
        }
        // Admin only: load full config
        if (isAdmin) {
          const resp = await fetch(`${API_BASE}/admin/config`, {
            headers: { Authorization: `Bearer ${token}` }
          });
          if (resp.ok) {
            const data = await resp.json();
            setConfig(data);
            setVerifierModel(data.verifier_model || "");
            setVerifierApiKey(data.verifier_api_key_masked || "");
            setVerifierBaseUrl(data.verifier_base_url || "");
          }
        }
      } catch (e) {
        console.error("加载配置失败:", e);
      }
    };
    if (token) loadData();
  }, [token, API_BASE, isAdmin]);

  const handleTestVerifier = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const resp = await fetch(`${API_BASE}/admin/config/test-verifier`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({
          verifier_model: verifierModel,
          verifier_api_key: verifierApiKey || null,
          verifier_base_url: verifierBaseUrl || null,
        }),
      });
      const data = await resp.json();
      if (resp.ok) {
        setTestResult({ ok: true, msg: `连接成功：${data.response || "OK"}` });
      } else {
        setTestResult({ ok: false, msg: data.detail || "连接失败" });
      }
    } catch (e: any) {
      setTestResult({ ok: false, msg: `请求错误: ${e.message}` });
    } finally {
      setTesting(false);
    }
  };

  const handleSaveVerifier = async () => {
    setSaving(true);
    try {
      const resp = await fetch(`${API_BASE}/admin/config`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({
          verifier_model: verifierModel || null,
          verifier_api_key: verifierApiKey || null,
          verifier_base_url: verifierBaseUrl || null,
        }),
      });
      if (resp.ok) {
        setVerifierConfigured(!!verifierModel.trim());
        setTestResult(null);
      } else {
        alert("保存失败");
      }
    } catch (e) {
      alert("保存配置失败");
    } finally {
      setSaving(false);
    }
  };

  const handleToggleDualModel = async () => {
    if (isGuest) return;
    setDualModelToggling(true);
    try {
      const newVal = !dualEnabled;
      const resp = await fetch(`${API_BASE}/auth/me/dual-model`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ enabled: newVal }),
      });
      if (resp.ok) {
        const data = await resp.json();
        if (user) setUser({ ...user, dual_model_enabled: data.dual_model_enabled });
      } else {
        const err = await resp.json();
        alert(err.detail || "操作失败");
      }
    } catch (e) {
      alert("请求失败");
    } finally {
      setDualModelToggling(false);
    }
  };

  return (
    <div className="p-4 space-y-4">
      <p className="text-xs font-bold text-slate-600 mb-2">数据参数配置</p>
      {[
        { label: "候选器件上限", key: "max_candidates", def: "8",  unit: "个" },
        { label: "证据检索数量", key: "evidence_k",     def: "5",  unit: "条" },
        { label: "最低综合评分", key: "min_score",      def: "40", unit: "分" },
      ].map(p => (
        <div key={p.key}>
          <label className="block text-[10px] font-bold text-slate-500 mb-1.5 uppercase tracking-wide">{p.label}</label>
          <div className="flex items-center gap-2">
            <input type="number" defaultValue={p.def}
              className="flex-1 h-9 bg-slate-50 border border-slate-200 rounded-xl px-3 text-xs text-slate-800 focus:outline-none focus:border-teal-400 transition-colors" />
            <span className="text-[10px] text-slate-400 shrink-0">{p.unit}</span>
          </div>
        </div>
      ))}
      <div className="pt-3 border-t border-slate-100 flex items-center justify-between">
        <div>
          <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wide">优先国产器件</p>
          <p className="text-[9px] text-slate-400 mt-0.5">国内品牌排序靠前</p>
        </div>
        <div className="w-10 h-5 bg-teal-500 rounded-full flex items-center px-0.5">
          <div className="w-4 h-4 bg-white rounded-full shadow ml-auto" />
        </div>
      </div>

      {/* ── 双模型验证开关（所有已登录用户可见）────── */}
      {!isGuest && (
        <div className="pt-4 border-t border-slate-100">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wide">双模型验证</p>
              <p className="text-[9px] text-slate-400 mt-0.5">
                {verifierConfigured ? "开启后选型结果经双模型独立对比" : "管理员尚未配置验证模型"}
              </p>
            </div>
            <button
              onClick={handleToggleDualModel}
              disabled={!verifierConfigured || dualModelToggling}
              className={cn(
                "w-10 h-5 rounded-full flex items-center px-0.5 transition-colors",
                dualEnabled && verifierConfigured ? "bg-teal-500" : "bg-slate-200",
                (!verifierConfigured || dualModelToggling) && "opacity-50 cursor-not-allowed"
              )}
            >
              <div className={cn("w-4 h-4 bg-white rounded-full shadow transition-transform", dualEnabled && verifierConfigured ? "ml-auto" : "")} />
            </button>
          </div>
          {dualEnabled && verifierConfigured && (
            <p className="text-[9px] text-teal-600 font-bold mt-1.5">已开启 · 下次选型将自动触发双模型验证</p>
          )}
        </div>
      )}

      {/* ── 验证模型配置（仅管理员可见）────── */}
      {isAdmin && (
        <div className="pt-4 border-t border-slate-100 space-y-3">
          <p className="text-xs font-bold text-slate-600">验证模型配置 <span className="text-[9px] text-amber-500 font-normal ml-1">管理员</span></p>
          <div>
            <label className="block text-[10px] font-bold text-slate-500 mb-1.5 uppercase tracking-wide">验证模型</label>
            <input type="text" value={verifierModel} onChange={(e) => { setVerifierModel(e.target.value); setTestResult(null); }} placeholder="例：claude-opus-5"
              className="w-full h-9 bg-slate-50 border border-slate-200 rounded-xl px-3 text-xs text-slate-800 focus:outline-none focus:border-teal-400 transition-colors" />
          </div>
          <div>
            <label className="block text-[10px] font-bold text-slate-500 mb-1.5 uppercase tracking-wide">API Key（可选）</label>
            <input type="password" value={verifierApiKey} onChange={(e) => { setVerifierApiKey(e.target.value); setTestResult(null); }} placeholder="留空则使用主模型密钥"
              className="w-full h-9 bg-slate-50 border border-slate-200 rounded-xl px-3 text-xs text-slate-800 focus:outline-none focus:border-teal-400 transition-colors" />
          </div>
          <div>
            <label className="block text-[10px] font-bold text-slate-500 mb-1.5 uppercase tracking-wide">Base URL（可选）</label>
            <input type="text" value={verifierBaseUrl} onChange={(e) => { setVerifierBaseUrl(e.target.value); setTestResult(null); }} placeholder="留空则使用主模型URL"
              className="w-full h-9 bg-slate-50 border border-slate-200 rounded-xl px-3 text-xs text-slate-800 focus:outline-none focus:border-teal-400 transition-colors" />
          </div>
          {testResult && (
            <p className={cn("text-[10px] px-2 py-1 rounded-lg", testResult.ok ? "text-teal-700 bg-teal-50" : "text-red-700 bg-red-50")}>
              {testResult.msg}
            </p>
          )}
          <div className="flex gap-2">
            <button onClick={handleTestVerifier} disabled={testing || !verifierModel.trim()}
              className="flex-1 h-8 bg-slate-100 text-slate-700 rounded-xl text-xs font-bold hover:bg-slate-200 disabled:bg-slate-50 disabled:text-slate-300 transition-colors">
              {testing ? "测试中…" : "测试连接"}
            </button>
            <button onClick={handleSaveVerifier} disabled={saving || !testResult?.ok}
              className="flex-1 h-8 bg-teal-500 text-white rounded-xl text-xs font-bold hover:bg-teal-600 disabled:bg-slate-300 transition-colors">
              {saving ? "保存中…" : "保存配置"}
            </button>
          </div>
          <p className="text-[9px] text-slate-400">需先通过测试连接，才能保存配置</p>
        </div>
      )}
    </div>
  );
}

export function RightToolPanel({ sessionId }: { sessionId?: string }) {
  const [activePanel, setActivePanel] = useState<PanelId | null>(null);
  const { toolCallEvents } = useChatStore();
  const { user, remainingGuestMessages } = useAuthStore();
  const isGuest = user?.is_guest === true;
  const remaining = remainingGuestMessages?.() ?? 0;

  const toggle = (id: PanelId) => setActivePanel(p => p === id ? null : id);

  return (
    <div className="flex h-full shrink-0">
      {/* Expanded panel content */}
      {activePanel && (
        <div className="w-[290px] h-full bg-white border-l border-slate-100 flex flex-col overflow-hidden animate-fade-in">
          <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100 shrink-0">
            <span className="text-xs font-extrabold text-slate-700">
              {PANELS.find(p => p.id === activePanel)?.label}
            </span>
            <button onClick={() => setActivePanel(null)}
              className="p-1 text-slate-400 hover:text-slate-700 hover:bg-slate-100 rounded-lg transition-colors">
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
          <div className="flex-1 overflow-y-auto min-h-0">
            {activePanel === "results"  && <DetailPanel />}
            {activePanel === "export"   && <ExportPanel sessionId={sessionId} />}
            {activePanel === "thinking" && <ThinkingPanel />}
            {activePanel === "log"      && <LogPanel />}
            {activePanel === "params"   && <ParamsPanel />}
          </div>
        </div>
      )}

      {/* VS Code-style activity bar */}
      <div className="w-12 h-full bg-white border-l border-slate-100 flex flex-col items-center py-4 gap-1 shrink-0">
        {isGuest && (
          <div className="mb-3 flex flex-col items-center gap-0.5">
            <Zap className={cn("w-4 h-4", remaining <= 1 ? "text-red-500" : "text-amber-500")} />
            <span className={cn("text-[9px] font-bold font-mono", remaining <= 1 ? "text-red-500" : "text-amber-500")}>
              {remaining}
            </span>
          </div>
        )}
        {PANELS.map(p => {
          const isActive = activePanel === p.id;
          const hasBadge = p.id === "log" && toolCallEvents.length > 0;
          return (
            <button key={p.id} onClick={() => toggle(p.id)} title={p.label}
              className={cn(
                "relative w-9 h-9 rounded-xl flex items-center justify-center transition-all",
                isActive
                  ? "bg-teal-50 text-teal-600 shadow-sm"
                  : "text-slate-400 hover:bg-slate-50 hover:text-slate-700"
              )}>
              <p.icon className="w-4 h-4" />
              {hasBadge && (
                <span className="absolute top-1.5 right-1.5 w-1.5 h-1.5 rounded-full bg-teal-500" />
              )}
            </button>
          );
        })}
        <div className="flex-1" />
        <Clock className="w-3.5 h-3.5 text-slate-200" />
      </div>
    </div>
  );
}

