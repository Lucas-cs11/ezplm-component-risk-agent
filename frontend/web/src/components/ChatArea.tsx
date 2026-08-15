"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { useChatStore, generateId } from "@/store/chat";
import { MessageBubble } from "@/components/MessageBubble";
import { PdfReportViewer } from "@/components/PdfReportViewer";
import { ParameterForm } from "@/components/ParameterForm";
import { Send, Loader2, Slash, PanelLeftOpen, PanelLeftClose, PanelRightOpen, PanelRightClose, Zap } from "lucide-react";
import { estimateConversationTokens, COMPACT_THRESHOLD } from "@/lib/tokenBudget";
import { buildSelectionContext, cn } from "@/lib/utils";
import { getApiHeaders, getAuthBearer } from "@/lib/api";
import { useAuthStore } from "@/store/authStore";
import type { AnalysisReport } from "@/types";

interface SSEStage { stage: string; status: string; total?: number; }
interface SSEScore { index: number; total: number; part_number: string; total_score: number; recommendation_level?: string; }

/* ── Slash 命令注册 ─────────────────────────────── */
const SLASH_COMMANDS: Record<string, { desc: string; action: (ctx: CmdCtx) => void }> = {
  clear: {
    desc: "清空当前对话",
    action: ({ clearMessages }) => clearMessages(),
  },
  new: {
    desc: "创建新对话",
    action: ({ createSession }) => createSession(),
  },
  compact: {
    desc: "压缩对话生成摘要",
    action: ({ compact }) => compact(),
  },
  export: {
    desc: "下载 BOM Excel",
    action: () => {
      const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "";
      fetch(`${API_BASE}/export/bom`, { method: "POST", headers: getAuthBearer() })
        .then(r => r.blob())
        .then(blob => {
          const url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = url; a.download = "BOM.xlsx"; a.click();
          URL.revokeObjectURL(url);
        })
        .catch(() => {});
    },
  },
  risk: {
    desc: "查看风险评估报告",
    action: ({ showReport }) => showReport("risk"),
  },

  schematic: {
    desc: "显示应用电路图",
    action: ({ toggleSchematic }) => toggleSchematic(),
  },
  save: {
    desc: "导出对话为 Markdown",
    action: ({ exportChat }) => exportChat(),
  },
  replace: {
    desc: "查询替代器件 /replace <型号>",
    action: ({ replacePart }) => replacePart(),
  },
};
type CmdCtx = {
  clearMessages: () => void;
  createSession: () => void;
  compact: () => void;
  showReport: (t: "bom" | "risk") => void;
  toggleSchematic: () => void;
  exportChat: () => void;
  replacePart: () => void;
};

/* ── 阶段映射（含百分比估算）────────────────────── */
const STAGE_INFO: Record<string, { label: string; pct: number }> = {
  parse:    { label: "解析需求", pct: 10 },
  search:   { label: "检索器件库", pct: 25 },
  enrich:   { label: "富化器件详情", pct: 38 },
  score:    { label: "评分中", pct: 55 },
  analysis: { label: "证据+风险分析", pct: 72 },
  evidence: { label: "构建证据链", pct: 75 },
  risk:     { label: "风险评估", pct: 90 },
  report:   { label: "生成报告", pct: 98 },
};

/* ══════════════════════════════════════════════════════════════ */

export function ChatArea({ leftOpen, rightOpen, onToggleLeft, onToggleRight, onToggleMobileMenu }: {
  leftOpen: boolean; rightOpen: boolean;
  onToggleLeft: () => void; onToggleRight: () => void;
  onToggleMobileMenu?: () => void;
}) {
  const { activeSession, addMessage, updateMessage, setStreaming, createSession, setSessionTitle, healthStatus, setHealthStatus, pendingInput, setPendingInput, thinkingDepth, pushToolCallEvent, clearToolCallEvents } = useChatStore();
  const { canSendAsGuest, incrementGuestCount, user: authUser } = useAuthStore();
  const session = activeSession();
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [stage, setStage] = useState("");
  const [progress, setProgress] = useState({ current: 0, total: 0, pct: 0 });
  const [showCmdMenu, setShowCmdMenu] = useState(false);
  const [compactResult, setCompactResult] = useState<string | null>(null);
  const [activeReport, setActiveReport] = useState<"bom" | "risk" | null>(null);
  const [currentIntent, setCurrentIntent] = useState<"selection" | "chat" | "adjustment" | "clarify" | null>(null);
  const [showThinking, setShowThinking] = useState(false);
  const [accumulatedInput, setAccumulatedInput] = useState("");  // 跨轮累积的约束文本
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const streamingMsgId = useRef<string | null>(null);
  const inputTextRef = useRef("");  // 同步最新输入值，供 slash command 读取参数

  // A report in the session is the source of truth for follow-up selection,
  // independent of the classifier label used for the latest message.
  const hasActiveSelection = session?.messages.some(
    (m) => m.role === "assistant" && Boolean(
      m.report && ((m.report.candidates?.length ?? 0) > 0 || (m.report.recommended_parts?.length ?? 0) > 0)
    )
  ) ?? false;

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [session?.messages]);

  // Watch pendingInput from DetailPanel "选择此器件" button
  useEffect(() => {
    if (!pendingInput || loading) return;
    const text = pendingInput;
    setPendingInput(null);
    handleSend(text);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingInput]);

  // ── 后端连接健康检查（30s 轮询）──────────────────
  useEffect(() => {
    const checkHealth = async () => {
      const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "";
      setHealthStatus("checking");
      try {
        const resp = await fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(5000) });
        setHealthStatus(resp.ok ? "connected" : "disconnected");
      } catch {
        setHealthStatus("disconnected");
      }
    };
    checkHealth();
    const interval = setInterval(checkHealth, 30000);
    return () => clearInterval(interval);
  }, [setHealthStatus]);

  /* ── 快速短语 ────────────────────────────────── */
  const handleQuickPhrase = (phrase: string) => setInput(phrase);

  /* ── Slash 命令 ────────────────────────────────── */
  const handleCmd = useCallback((cmd: string) => {
    const ctx: CmdCtx = {
      clearMessages: () => {
        if (session) {
          session.messages.forEach((m) => {
            updateMessage(m.id, { content: "", report: undefined });
          });
        }
        // Actually remove messages: use store actions
        useChatStore.setState((s) => ({
          sessions: s.sessions.map((ss) =>
            ss.id === s.activeSessionId ? { ...ss, messages: [] } : ss
          ),
        }));
      },
      createSession,
      compact: async () => {
        if (!session || session.messages.length < 2) return;
        const summary = session.messages.map((m) =>
          `[${m.role}]: ${m.content.slice(0, 200)}`
        ).join("\n");
        const aid = generateId();
        addMessage({ id: aid, role: "assistant", content: "", timestamp: Date.now() });
        const _API = process.env.NEXT_PUBLIC_API_BASE || "";
        try {
          const resp = await fetch(`${_API}/agent/chat`, {
            method: "POST",
            headers: getApiHeaders(),
            body: JSON.stringify({
              user_input: `请将以下对话历史压缩为一段简洁摘要（不超过200字），保留关键器件型号和参数：\n${summary}`,
              session_id: session?.id,
            }),
          });
          const data = await resp.json();
          const text = data.response || data.output || JSON.stringify(data);
          updateMessage(aid, { content: `**对话摘要**\n\n${text}` });
          setCompactResult(text);
        } catch {
          updateMessage(aid, { content: "压缩失败" });
        }
      },
      showReport: (t) => setActiveReport(t),
      toggleSchematic: () => setActiveReport(null),
      replacePart: async () => {
        const fullCmd = inputTextRef.current.trim();
        const mpn = fullCmd.replace(/^\/replace\s+/i, "").trim();
        if (!mpn) return;
        const API = process.env.NEXT_PUBLIC_API_BASE || "";
        const aid = generateId();
        addMessage({ id: aid, role: "assistant", content: `正在查询 \`${mpn}\` 的替代器件...`, timestamp: Date.now(), isStreaming: true });
        try {
          const resp = await fetch(`${API}/replacement`, {
            method: "POST",
            headers: getApiHeaders(),
            body: JSON.stringify({ original_part_number: mpn }),
          });
          if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            updateMessage(aid, { content: `> 替代料查询失败: ${err.detail || resp.statusText}`, isStreaming: false });
            return;
          }
          const data = await resp.json();
          const lines: string[] = [
            `**替代料查询: \`${mpn}\`**`,
            "",
          ];
          const orig = data.original_part;
          if (orig) {
            const oPn = orig.part_number || "?";
            const oMfr = orig.manufacturer || "未知厂商";
            const oCat = orig.category || "";
            lines.push(`**原器件**: \`${oPn}\` — ${oMfr}${oCat ? " | " + oCat : ""}`);
            if (orig.description) lines.push(`> ${(orig.description as string).slice(0, 150)}`);
            lines.push("");
          }
          const candidates = data.replacement_candidates || [];
          lines.push(`**兼容性**: ${data.compatibility_level || "?"}`);
          lines.push("");
          if (candidates.length === 0) {
            lines.push("> 未找到替代器件。");
          } else {
            lines.push(`共 **${candidates.length}** 款替代候选：`);
            lines.push("");
            candidates.forEach((c: any, i: number) => {
              const p = c.part || {};
              const s = c.score || {};
              const pn = p.part_number || "?";
              const mfr = p.manufacturer || "—";
              const score = Math.round(s.total_score || 0);
              const level = c.recommendation_level || "—";
              lines.push(`**#${i + 1}** \`${pn}\` — ${mfr} | 综合 **${score}** 分 | ${level}`);
            });
          }
          lines.push("");
          if (data.comparison_summary) {
            lines.push(`> ${data.comparison_summary}`);
          }
          updateMessage(aid, { content: lines.join("\n"), isStreaming: false });
        } catch (err: unknown) {
          const msg = err instanceof Error ? err.message : "Unknown";
          updateMessage(aid, { content: `> 替代料查询失败: ${msg}`, isStreaming: false });
        }
      },
      exportChat: () => {
        if (!session || session.messages.length === 0) return;
        const title = session.messages.find(m => m.role === "user")?.content?.slice(0, 60) || "对话记录";
        const date = new Date().toISOString().slice(0, 10);
        const lines: string[] = [
          `# ${title}`,
          "",
          `> 导出时间：${new Date().toLocaleString("zh-CN")}`,
          `> 生成工具：eZmanbo / eZ-PLM Agent`,
          "",
        ];
        for (const m of session.messages) {
          if (m.role === "user") {
            lines.push(`> ${m.content}`);
            lines.push("");
          } else {
            if (m.report) {
              lines.push(`*[选型完成 · ${m.report.recommended_parts?.length || 0} 款推荐 · ${m.report.risks?.overall_risk_level?.toUpperCase() || "?"} 风险]*`);
              lines.push("");
            }
            lines.push(m.content);
            lines.push("");
            lines.push("---");
            lines.push("");
          }
        }
        const md = lines.join("\n");
        const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `eZmanbo_${date}_${title.slice(0, 20).replace(/[/\\?%*:|"<>]/g, "_")}.md`;
        a.click();
        URL.revokeObjectURL(url);
      },
    };
    if (SLASH_COMMANDS[cmd]) {
      SLASH_COMMANDS[cmd].action(ctx);
    }
    setInput("");
    setShowCmdMenu(false);
  }, [session, addMessage, updateMessage, createSession]);

  /* ── 检测 / 命令 (onKeyDown 确保即时响应) ─────── */
  const handleKeyDownIntercept = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // 先处理 slash 菜单的键盘导航
    if (showCmdMenu) {
      if (e.key === "Escape") {
        e.preventDefault();
        setShowCmdMenu(false);
        return;
      }
      if (e.key === "Enter" && !e.shiftKey) {
        // 如果有筛选结果，选择第一个
        const cmds = Object.entries(SLASH_COMMANDS).filter(
          ([k]) => k.startsWith(input.slice(1).split(" ")[0].toLowerCase())
        );
        if (cmds.length > 0) {
          e.preventDefault();
          handleCmd(cmds[0][0]);
          return;
        }
      }
    }

    // Enter 发送
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleInputChange = (val: string) => {
    setInput(val);
    inputTextRef.current = val;
    setShowCmdMenu(val.startsWith("/"));
  };

  /* ── SSE 流式接收器（共享） ────────────────────── */
  const consumeSelectionStream = async (aid: string, resp: Response, userInput: string = "") => {
    const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "";
    const reader = resp.body?.getReader();
    if (!reader) throw new Error("No stream");
    const decoder = new TextDecoder();
    let buffer = "", fullText = "", thinkingText = "", thinkingDone = false;
    let lastReadAt = Date.now();
    const STREAM_TIMEOUT = 300000;
    const report: Partial<AnalysisReport> = {};

    try {
    while (true) {
      if (Date.now() - lastReadAt > STREAM_TIMEOUT) throw new Error("Stream timeout");
      const readPromise = reader.read();
      const timeoutPromise = new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error("Read timeout")), 60000)
      );
      const { done, value } = await Promise.race([readPromise, timeoutPromise]);
      if (done) break;
      lastReadAt = Date.now();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      let ce = "";
      for (const line of lines) {
        if (line.startsWith("event:")) { ce = line.slice(6).trim(); continue; }
        if (!line.startsWith("data:")) continue;
        try {
          const d = JSON.parse(line.slice(5));
          if (ce === "thinking_delta") {
            thinkingText += (d.text || "") + "\n";
            updateMessage(aid, { thinking: thinkingText });
          } else if (ce === "stage") {
            const s = d as SSEStage;
            setStage(s.stage);
            const info = STAGE_INFO[s.stage] || { label: s.stage, pct: 50 };
            if (s.total) setProgress({ current: 0, total: s.total, pct: info.pct });
            else setProgress((p) => ({ ...p, pct: info.pct }));
          } else if (ce === "score_update") {
            const su = d as SSEScore;
            const pct = su.total > 0 ? Math.round(25 + (su.index / su.total) * 35) : 55;
            setProgress({ current: su.index, total: su.total, pct });
            const line = `- \`${su.part_number}\` — **${su.total_score}** _${su.recommendation_level || ""}_\n`;
            fullText += line;
            // 首条正文到来时标记思考完成
            if (!thinkingDone && thinkingText) {
              thinkingDone = true;
              updateMessage(aid, { content: fullText, thinkingDone: true });
            } else {
              updateMessage(aid, { content: fullText });
            }
          } else if (ce === "text_delta") {
            if (!thinkingDone && thinkingText) {
              thinkingDone = true;
              updateMessage(aid, { thinkingDone: true });
            }
            fullText += (d.text || "") + "\n";
            updateMessage(aid, { content: fullText });
          } else if (ce === "parse_done") {
            if (d.constraints) report.constraints = d.constraints;
          } else if (ce === "risk_done") {
            report.risks = d;
          } else if (ce === "evidence_done") {
            report.evidence_count = d.evidence_count;
            report.avg_confidence = d.avg_confidence;
            if (d.evidence_items) report.evidence_items = d.evidence_items;
          } else if (ce === "done") {
            report.elapsed_s = d.elapsed_s;
            report.request_id = d.request_id;
            if (d.summary) report.summary = d.summary;
            if (d.recommended_parts) report.recommended_parts = d.recommended_parts;
            if (d.candidates)        report.candidates        = d.candidates;
            // 提取选型标题（≤10字）
            if (report.constraints) {
              const c = report.constraints;
              const parts: string[] = [];
              if (c.topology) parts.push(c.topology === "buck" ? "Buck" : c.topology === "boost" ? "Boost" : c.topology === "ldo" ? "LDO" : c.topology);
              if (c.output_voltage_v) parts.push(`${c.output_voltage_v}V`);
              if (c.output_current_a) parts.push(`${c.output_current_a}A`);
              const title = parts.join(" ") + " 选型";
              if (title.length <= 12 && session) {
                setSessionTitle(session.id, title);
              }
            }
          } else if (ce === "error") {
            const errMsg = d.message || d.detail || "服务器内部错误";
            updateMessage(aid, { content: `> ⚠️ 分析失败：${errMsg}`, isStreaming: false });
            setStreaming(aid, false);
            return;
          }
        } catch { /* skip */ }
        ce = "";
      }
    }
    updateMessage(aid, { content: fullText, report: report as AnalysisReport, isStreaming: false, thinkingDone: true });

    // ── 选型完成后将上下文注入后端 Agent 会话 ────────
    if (session && (report as AnalysisReport).constraints) {
      const _API = process.env.NEXT_PUBLIC_API_BASE || "";
      fetch(`${_API}/agent/init_session`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: session.id,
          context_type: "selection_context",
          context: buildSelectionContext(report as AnalysisReport),
        }),
      }).catch(() => {});
    }
    } catch (_streamErr) {
      // 流式失败 → 降级到非流式 /analyze
      if (!fullText && userInput) {
        try {
          const fbResp = await fetch(`${API_BASE}/analyze`, {
            method: "POST",
            headers: getApiHeaders(),
            body: JSON.stringify({ user_input: userInput, thinking_depth: "default", session_id: useChatStore.getState().activeSessionId }),
          });
          if (fbResp.ok) {
            const fbData = await fbResp.json();
            fullText = "（非流式恢复）\n\n" + JSON.stringify(fbData, null, 2).slice(0, 2000);
            updateMessage(aid, { content: fullText, report: fbData, isStreaming: false, thinkingDone: true });
            return;
          }
        } catch { /* 降级也失败 */ }
      }
      const retained = fullText ? fullText + "\n\n> ⚠️ 连接中断，请点击重新发送" : "> ⚠️ 连接中断，已尝试非流式恢复，请点击重新发送";
      updateMessage(aid, { content: retained, report: Object.keys(report).length > 0 ? (report as AnalysisReport) : undefined, isStreaming: false, thinkingDone: true });
      setProgress((p) => ({ ...p, pct: 0 }));
    }
  };

  const consumeChatStream = async (aid: string, resp: Response, userInput: string = "") => {
    const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "";
    const reader = resp.body?.getReader();
    if (!reader) throw new Error("No stream");
    const decoder = new TextDecoder();
    let buffer = "", fullText = "", thinkingText = "";
    let lastReadAt = Date.now();
    const STREAM_TIMEOUT = 300000; // 300s 无数据则超时（思考模式可能较慢）
    try {
    while (true) {
      // 流式读取超时检测
      if (Date.now() - lastReadAt > STREAM_TIMEOUT) {
        throw new Error("Stream timeout");
      }
      const readPromise = reader.read();
      const timeoutPromise = new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error("Read timeout")), 120000) // 120s/块（思考模式放宽）
      );
      const { done, value } = await Promise.race([readPromise, timeoutPromise]);
      if (done) break;
      lastReadAt = Date.now();
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      let ce = "";
      for (const line of lines) {
        if (line.startsWith("event:")) { ce = line.slice(6).trim(); continue; }
        if (!line.startsWith("data:")) continue;
        try {
          const d = JSON.parse(line.slice(5));
          // Push non-noisy events to the tool call log
          if (!["text_delta", "thinking_delta"].includes(ce)) {
            const labels: Record<string, string> = {
              start: "流水线启动", intent: `意图: ${(d as any)?.intent ?? ""}`,
              parse_done: "约束解析完成", score_update: `评分: ${(d as any)?.part_number ?? ""}`,
              evidence_done: "证据生成完成", risk_done: "风险评估完成",
              clarify_fields: "需补充参数", done: `完成 (${(d as any)?.elapsed_s?.toFixed(1) ?? ""}s)`,
              error: `错误: ${(d as any)?.message ?? ""}`,
            };
            if (labels[ce]) pushToolCallEvent({ type: ce, label: labels[ce] });
          }
          if (ce === "thinking_delta") {
            thinkingText += (d.text || "") + "\n";
            updateMessage(aid, { thinking: thinkingText });
          } else if (ce === "text_delta" || ce === "start") {
            if (ce === "text_delta" && thinkingText && !fullText) {
              // 首条正文到来 → 标记思考完成
              updateMessage(aid, { thinkingDone: true });
            }
            fullText += (d.text || "") + "\n";
            updateMessage(aid, { content: fullText });
          } else if (ce === "done") {
            if (d.text) fullText = d.text;
          } else if (ce === "error") {
            const errMsg = d.message || d.detail || "服务器内部错误";
            updateMessage(aid, { content: `> ⚠️ ${errMsg}`, isStreaming: false });
            setStreaming(aid, false);
            return;
          }
        } catch { /* skip */ }
        ce = "";
      }
    }
    updateMessage(aid, { content: fullText || "已处理", isStreaming: false, thinkingDone: true });
    } catch (_streamErr) {
      // 流式失败 → 降级到非流式请求
      if (!fullText && userInput) {
        try {
          const fallbackResp = await fetch(`${API_BASE}/agent/chat`, {
            method: "POST",
            headers: getApiHeaders(),
            body: JSON.stringify({ user_input: userInput, session_id: useChatStore.getState().activeSessionId }),
          });
          if (fallbackResp.ok) {
            const fbData = await fallbackResp.json();
            const fbText = fbData.response || fbData.output || JSON.stringify(fbData);
            updateMessage(aid, { content: fbText, isStreaming: false, thinkingDone: true });
            return;
          }
        } catch { /* 降级也失败 */ }
      }
      const retained = fullText ? fullText + "\n\n> ⚠️ 连接中断，请点击重新发送" : "> ⚠️ 连接中断，已尝试非流式恢复，请点击重新发送";
      updateMessage(aid, { content: retained, isStreaming: false, thinkingDone: true });
    }
  };

  /* ── 统一流式消费器（对应后端 /chat/stream）──────────────── */
  const consumeUnifiedStream = async (aid: string, resp: Response, userInput: string = "") => {
    const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "";
    const reader = resp.body?.getReader();
    if (!reader) throw new Error("No stream");
    const decoder = new TextDecoder();
    let buffer = "", fullText = "", thinkingText = "", thinkingDone = false;
    let lastReadAt = Date.now();
    let detectedIntent = "chat";
    const STREAM_TIMEOUT = 300000;
    const report: Partial<AnalysisReport> = {};

    try {
      while (true) {
        if (Date.now() - lastReadAt > STREAM_TIMEOUT) throw new Error("Stream timeout");
        const { done, value } = await Promise.race([
          reader.read(),
          new Promise<never>((_, rej) => setTimeout(() => rej(new Error("Read timeout")), 120000)),
        ]);
        if (done) break;
        lastReadAt = Date.now();
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        let ce = "";
        for (const line of lines) {
          if (line.startsWith("event:")) { ce = line.slice(6).trim(); continue; }
          if (!line.startsWith("data:")) continue;
          try {
            const d = JSON.parse(line.slice(5));
            // Push pipeline events to 调用日志
            if (!["text_delta", "thinking_delta", "stage", "score_update"].includes(ce)) {
              const evtLabels: Record<string, string> = {
                intent: `意图: ${(d as any)?.intent ?? ""}`,
                parse_done: "约束解析完成",
                evidence_done: "证据生成完成",
                risk_done: "风险评估完成",
                clarify_fields: "需补充参数",
                lifecycle_alert: "生命周期预警",
                done: `完成 (${(d as any)?.elapsed_s?.toFixed(1) ?? ""}s)`,
                error: `错误: ${(d as any)?.message ?? ""}`,
              };
              if (evtLabels[ce]) pushToolCallEvent({ type: ce, label: evtLabels[ce] });
            }
            if (ce === "intent") {
              detectedIntent = d.intent || "chat";
              setCurrentIntent(detectedIntent as "selection" | "chat" | "adjustment" | "clarify");
              if (detectedIntent === "clarify") {
                setAccumulatedInput(prev => prev ? `${prev}; ${userInput}` : userInput);
              } else if (detectedIntent === "selection" || detectedIntent === "adjustment") {
                setAccumulatedInput("");
              }
            } else if (ce === "thinking_delta") {
              thinkingText += (d.text || "") + "\n";
              updateMessage(aid, { thinking: thinkingText });
            } else if (ce === "stage") {
              const s = d as SSEStage;
              setStage(s.stage);
              const info = STAGE_INFO[s.stage] || { label: s.stage, pct: 50 };
              if (s.total) setProgress({ current: 0, total: s.total, pct: info.pct });
              else setProgress(p => ({ ...p, pct: info.pct }));
            } else if (ce === "score_update") {
              const su = d as SSEScore;
              const pct = su.total > 0 ? Math.round(25 + (su.index / su.total) * 35) : 55;
              setProgress({ current: su.index, total: su.total, pct });
              // 不将评分条目追加到正文，仅更新进度条；分析文本由 text_delta 提供
              if (!thinkingDone && thinkingText) { thinkingDone = true; updateMessage(aid, { thinkingDone: true }); }
            } else if (ce === "text_delta") {
              if (!thinkingDone && thinkingText) { thinkingDone = true; updateMessage(aid, { thinkingDone: true }); }
              fullText += (d.text || "") + "\n";
              updateMessage(aid, { content: fullText });
            } else if (ce === "parse_done") {
              if (d.constraints) report.constraints = d.constraints;
            } else if (ce === "risk_done") {
              report.risks = d;
            } else if (ce === "evidence_done") {
              report.evidence_count = d.evidence_count;
              report.avg_confidence = d.avg_confidence;
              if (d.evidence_items) report.evidence_items = d.evidence_items;
            } else if (ce === "clarify_fields") {
              // Store missing fields so MessageBubble can render ParameterForm
              if (d.missing_p0?.length) {
                updateMessage(aid, { missing_fields: d.missing_p0, accumulated_constraints: d.accumulated ?? {} });
              }
            } else if (ce === "lifecycle_alert" && d.alerts?.length) {
              report.lifecycle_alerts = d.alerts;
              if (d.has_high) {
                const highParts = d.alerts.filter((a: any) => a.severity === "HIGH").map((a: any) => a.part_number).join("、");
                fullText += `\n> ⚠️ **生命周期告警**：${highParts} 已停产，系统已自动查询替代料\n\n`;
                updateMessage(aid, { content: fullText });
              }
            } else if (ce === "reference_designs" && d.designs?.length) {
              report.reference_designs = d.designs;
            } else if (ce === "agent_activity") {
              // 显示多智能体活动状态到思考面板
              const done = (d.status || "").includes("完成") || (d.status || "").includes("找到");
              thinkingText += `${done ? "✓" : "⟳"} [${d.agent}] ${d.status}\n`;
              updateMessage(aid, { thinking: thinkingText });
              if (d.phase) {
                const info = STAGE_INFO[d.phase];
                if (info) setProgress(p => ({ ...p, pct: info.pct }));
              }
            } else if (ce === "done") {
              report.elapsed_s = d.elapsed_s;
              report.request_id = d.request_id;
              if (d.summary) report.summary = d.summary;
              if (d.recommended_parts) report.recommended_parts = d.recommended_parts;
              if (d.candidates)        report.candidates        = d.candidates;
              if (d.intent === "selection_choice" && d.selected_part) {
                const pn = d.selected_part as string;
                useChatStore.getState().setSelectedPart(pn);
                fetch(`${API_BASE}/select-part`, {
                  method: "POST", headers: getApiHeaders(),
                  body: JSON.stringify({ session_id: useChatStore.getState().activeSessionId, part_number: pn }),
                }).catch(() => {});
                fullText = `✅ 已选定 **${pn}**\n\n您可以通过下方按钮导出 BOM / 风险评估报告。`;
                updateMessage(aid, { content: fullText, isStreaming: false });
                setStreaming(aid, false); setCurrentIntent(null); setLoading(false);
                streamingMsgId.current = null;
                return;
              }
              if (report.constraints) {
                const c = report.constraints;
                const parts: string[] = [];
                if (c.topology) parts.push(c.topology === "buck" ? "Buck" : c.topology === "boost" ? "Boost" : c.topology === "ldo" ? "LDO" : c.topology);
                if (c.output_voltage_v) parts.push(`${c.output_voltage_v}V`);
                if (c.output_current_a) parts.push(`${c.output_current_a}A`);
                const title = parts.join(" ") + " 选型";
                if (title.length <= 12 && session) setSessionTitle(session.id, title);
              }
            } else if (ce === "error") {
              updateMessage(aid, { content: `> ⚠️ ${d.message || "服务器内部错误"}`, isStreaming: false });
              setStreaming(aid, false); return;
            }
          } catch { /* skip */ }
          ce = "";
        }
      }
      // Finalize from the data contract, not the classifier label. Classifier
      // drift must not discard a valid selection report returned by the backend.
      const hasStructuredReport = Boolean(
        report.constraints ||
        report.risks ||
        (report.candidates?.length ?? 0) > 0 ||
        (report.recommended_parts?.length ?? 0) > 0 ||
        (report.evidence_items?.length ?? 0) > 0 ||
        (report.evidence_count ?? 0) > 0
      );
      updateMessage(aid, {
        content: fullText || "已处理",
        report: hasStructuredReport ? (report as AnalysisReport) : undefined,
        isStreaming: false,
        thinkingDone: true,
      });
      if (hasStructuredReport && session && (report as AnalysisReport).constraints) {
        fetch(`${API_BASE}/agent/init_session`, {
          method: "POST", headers: getApiHeaders(),
          body: JSON.stringify({
            session_id: session.id,
            context_type: "selection_context",
            context: buildSelectionContext(report as AnalysisReport),
          }),
        }).catch(() => {});
      }
    } catch (_err) {
      if (!fullText && userInput) {
        try {
          const fb = await fetch(`${API_BASE}/agent/chat`, {
            method: "POST", headers: getApiHeaders(),
            body: JSON.stringify({ user_input: userInput, session_id: useChatStore.getState().activeSessionId }),
          });
          if (fb.ok) {
            const fbd = await fb.json();
            updateMessage(aid, { content: fbd.response || fbd.output || "已处理", isStreaming: false, thinkingDone: true });
            return;
          }
        } catch { /* ignore */ }
      }
      updateMessage(aid, {
        content: fullText ? fullText + "\n\n> ⚠️ 连接中断" : "> ⚠️ 连接中断，请重试",
        report: Object.keys(report).length > 0 ? (report as AnalysisReport) : undefined,
        isStreaming: false, thinkingDone: true,
      });
    }
  };

  /* ── 发送消息 + 意图分流 ──────────────────────── */
  const handleSend = useCallback(async (overrideText?: string) => {
    const text = (overrideText ?? input).trim();
    if (!text || loading) return;
    // Guest quota check
    if (!canSendAsGuest()) {
      addMessage({ id: generateId(), role: "assistant", content: "游客试用已达到 5 次上限，请注册账号（待开放）或联系管理员获取访问权限。", timestamp: Date.now() });
      return;
    }
    if (authUser?.is_guest) incrementGuestCount();
    setInput("");
    setLoading(true);
    setStage("");
    setProgress({ current: 0, total: 0, pct: 0 });
    setActiveReport(null);

    // Slash command?
    if (text.startsWith("/")) {
      const cmd = text.slice(1).split(" ")[0].toLowerCase();
      if (SLASH_COMMANDS[cmd]) {
        handleCmd(cmd);
        setLoading(false);
        return;
      }
    }

    // ── Auto-compact: token 预算检查 ──────────────────
    const msgs = (session?.messages || []).map(m => ({ role: m.role, content: m.content }));
    const estimatedTokens = estimateConversationTokens(msgs);
    if (estimatedTokens > COMPACT_THRESHOLD && session) {
      // 自动压缩：调用 LLM 摘要
      const compactId = generateId();
      addMessage({ id: compactId, role: "assistant", content: "对话较长，自动压缩上下文中...", timestamp: Date.now(), isStreaming: false });
      const _COMPACT_API = process.env.NEXT_PUBLIC_API_BASE || "";
      try {
        const compactResp = await fetch(`${_COMPACT_API}/agent/chat`, {
          method: "POST",
          headers: getApiHeaders(),
          body: JSON.stringify({
            user_input: `总结以下对话的关键信息（器件型号、参数、约束），不超过150字：\n${msgs.slice(-10).map(m => `[${m.role}]: ${m.content.slice(0, 200)}`).join("\n")}`,
            session_id: useChatStore.getState().activeSessionId,
          }),
        });
        const compactData = await compactResp.json();
        const compactText = compactData.response || compactData.output || "";
        // 清理旧消息，仅保留压缩摘要 + 最近 2 轮
        if (session.messages.length > 6) {
          const recentMsgs = session.messages.slice(-4);
          useChatStore.setState((s) => ({
            sessions: s.sessions.map((ss) =>
              ss.id === s.activeSessionId
                ? { ...ss, messages: [{ id: compactId, role: "assistant", content: `上下文已压缩：\n${compactText}`, timestamp: Date.now() }, ...recentMsgs] }
                : ss
            ),
          }));
        }
      } catch { /* non-critical */ }
    }

    const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "";
    const aid = generateId();
    const uid = generateId();
    streamingMsgId.current = aid;
    addMessage({ id: uid, role: "user", content: text, timestamp: Date.now() });
    addMessage({ id: aid, role: "assistant", content: "", timestamp: Date.now(), isStreaming: true });
    setStreaming(aid, true);
    clearToolCallEvents();

    try {
      // ── 单一统一流式端点，意图分类在服务端完成 ────────────────
      const resp = await fetch(`${API_BASE}/chat/stream`, {
        method: "POST",
        headers: getApiHeaders(),
        body: JSON.stringify({
          user_input: text,
          thinking_depth: thinkingDepth,
          session_id: useChatStore.getState().activeSessionId,
          accumulated_input: accumulatedInput || null,
          has_active_selection: hasActiveSelection,
        }),
      });
      await consumeUnifiedStream(aid, resp, text);
      setStreaming(aid, false);

      // ── 语义选择检测（LLM 兜底，非关键路径）──────────────────
      if (session) {
        const _API = process.env.NEXT_PUBLIC_API_BASE || "";
        fetch(`${_API}/interpret-selection`, {
          method: "POST",
          headers: getApiHeaders(),
          body: JSON.stringify({ session_id: session.id, user_input: text }),
        })
          .then(r => r.json())
          .then(data => {
            if (data.selected) {
              fetch(`${_API}/select-part`, {
                method: "POST",
                headers: getApiHeaders(),
                body: JSON.stringify({ session_id: session.id, part_number: data.selected }),
              }).catch(() => {});
            }
          })
          .catch(() => {});
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      updateMessage(aid, { content: `> 连接失败: ${msg}`, isStreaming: false });
      setStreaming(aid, false);
    }
    setLoading(false);
    setStage("");
    setCurrentIntent(null);
    streamingMsgId.current = null;
  }, [input, loading, session, accumulatedInput, setAccumulatedInput, addMessage, updateMessage, setStreaming, setSessionTitle, handleCmd, hasActiveSelection]);

  return (
    <main className="flex-1 flex flex-col min-w-0 bg-ez-bg">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-3 h-10 md:h-9 border-b border-ez-border bg-ez-bg-header flex-shrink-0 gap-2">
        <div className="flex items-center gap-2">
          <button onClick={onToggleLeft} className="btn-tool focus-ring" title={leftOpen ? "收起侧栏" : "展开侧栏"}>
            {leftOpen ? <PanelLeftClose className="w-3.5 h-3.5" /> : <PanelLeftOpen className="w-3.5 h-3.5" />}
          </button>
          {(() => {
            const msgs = (session?.messages || []).map(m => ({ role: m.role, content: m.content }));
            const tokens = estimateConversationTokens(msgs);
            return (
              <span className={`text-2xs font-mono hidden sm:inline ${tokens > COMPACT_THRESHOLD ? 'text-ez-amber' : 'text-ez-text-dim'}`}>
                ~{tokens}t
              </span>
            );
          })()}
        </div>
        <div className="flex items-center gap-1.5">
          <button onClick={onToggleRight} className="btn-tool focus-ring hidden lg:inline-flex" title={rightOpen ? "收起面板" : "展开面板"}>
            {rightOpen ? <PanelRightClose className="w-3.5 h-3.5" /> : <PanelRightOpen className="w-3.5 h-3.5" />}
          </button>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
        {session?.messages.map((m) => (
          <div key={m.id}>
            <MessageBubble message={m} progress={m.id === streamingMsgId.current ? progress : undefined} />
            {m.report && !m.isStreaming && m.id === session.messages.filter((x) => x.role === "assistant" && x.report).pop()?.id && (() => {
              const isSelected = useChatStore.getState().selectedPartNumber;
              return (
              <div className="ml-11 mt-2 animate-fade-in">
                {isSelected && (
                  <div className="p-3 border border-ez-border-hi bg-ez-bg-panel animate-fade-in">
                    <p className="text-xs text-ez-text font-medium mb-1.5 font-mono">
                      ✓ SELECTED: <span className="text-ez-accent">{isSelected}</span>
                    </p>
                    <p className="text-2xs text-ez-text-muted mb-3 uppercase tracking-wide">Export reports?</p>
                    <div className="flex gap-2">
                      <button
                        onClick={() => {
                          const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "";
                          fetch(`${API_BASE}/export/bom?session_id=${session.id}`, { method: "POST", headers: getAuthBearer() })
                            .then(r => r.blob())
                            .then(blob => { const url = URL.createObjectURL(blob); const a = document.createElement("a"); a.href = url; a.download = "BOM.xlsx"; a.click(); URL.revokeObjectURL(url); })
                            .catch(() => {});
                        }}
                        className="btn-tool text-2xs uppercase tracking-wider h-7 px-3 text-ez-green border-ez-green/40 hover:bg-ez-green/10"
                      >
                        导出 BOM（xlsx）
                      </button>
                      <button
                        onClick={() => {
                          const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "";
                          fetch(`${API_BASE}/report/risk?session_id=${session.id}`, { headers: getAuthBearer() })
                            .then(r => r.json())
                            .then(data => {
                              const blob = new Blob([data.content || data], { type: "text/markdown" });
                              const url = URL.createObjectURL(blob); const a = document.createElement("a");
                              a.href = url; a.download = `风险评估_${isSelected}.md`; a.click();
                              URL.revokeObjectURL(url);
                            })
                            .catch(() => {});
                        }}
                        className="text-xs px-3 py-1.5 bg-white text-gray-700 border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors"
                      >
                        导出风险评估
                      </button>
                      <button
                        onClick={() => {
                          const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "";
                          fetch(`${API_BASE}/export/decision-package?session_id=${session.id}`, { method: "POST", headers: getAuthBearer() })
                            .then(r => r.blob())
                            .then(blob => { const url = URL.createObjectURL(blob); const a = document.createElement("a"); a.href = url; a.download = `选型决策包_${isSelected}.xlsx`; a.click(); URL.revokeObjectURL(url); })
                            .catch(() => {});
                        }}
                        className="text-xs px-3 py-1.5 bg-brand-50 text-brand-700 border border-brand-200 rounded-lg hover:bg-brand-100 transition-colors font-medium"
                      >
                        导出决策包（IATF）
                      </button>
                      <button
                        onClick={() => useChatStore.getState().setSelectedPart(null)}
                        className="btn-tool text-2xs uppercase tracking-wider h-7 px-3 text-ez-text-muted"
                      >
                        取消
                      </button>
                    </div>
                  </div>
                )}
              </div>
              );
            })()}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="border-t border-ez-border p-3 bg-ez-bg-panel relative flex-shrink-0">
        {/* Slash command menu */}
        {showCmdMenu && (
          <div className="absolute bottom-full left-0 right-0 mx-3 mb-1 py-1 bg-ez-bg-panel border border-ez-border-hi shadow-xl z-50 animate-fade-in">
            {Object.entries(SLASH_COMMANDS)
              .filter(([k]) => k.startsWith(input.slice(1).split(" ")[0].toLowerCase()))
              .map(([k, v]) => (
                <button key={k} onClick={() => handleCmd(k)} className="w-full flex items-center gap-3 px-3 h-8 hover:bg-ez-bg-hover transition-colors">
                  <Slash className="w-3 h-3 text-ez-accent" />
                  <span className="font-mono text-xs text-ez-accent">/{k}</span>
                  <span className="text-ez-text-muted text-2xs ml-auto">{v.desc}</span>
                </button>
              ))}
          </div>
        )}

        {/* Text input */}
        <div className="max-w-3xl mx-auto w-full">
          <div className={cn(
            "bg-white rounded-2xl border border-ez-border shadow-sm overflow-hidden transition-all duration-150",
            loading ? "opacity-70" : "hover:shadow focus-within:shadow focus-within:border-ez-border-hi"
          )}>
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => handleInputChange(e.target.value)}
              onKeyDown={handleKeyDownIntercept}
              placeholder="描述选型需求，如「12V转5V，2A，工业级」…"
              rows={3}
              className="w-full resize-none bg-transparent px-4 pt-4 pb-2 text-sm text-ez-text placeholder:text-ez-text-dim focus:outline-none leading-relaxed"
              disabled={loading}
            />
            <div className="flex items-center justify-between px-4 pb-3 pt-0.5">
              <span className="text-2xs text-ez-text-label hidden md:block">Enter 发送 · Shift+Enter 换行 · / 命令</span>
              <button
                onClick={() => handleSend()}
                disabled={loading || !input.trim()}
                className={cn(
                  "ml-auto w-8 h-8 rounded-xl flex items-center justify-center transition-all",
                  input.trim() && !loading
                    ? "bg-ez-accent hover:bg-ez-accent-hi text-white shadow-sm"
                    : "bg-ez-bg-surface text-ez-text-dim cursor-not-allowed"
                )}
              >
                {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />}
              </button>
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}
