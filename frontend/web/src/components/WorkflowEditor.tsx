"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  ReactFlow, ReactFlowProvider, addEdge, useNodesState, useEdgesState,
  Controls, Background, BackgroundVariant, Handle, Position, useReactFlow,
  type Connection, type NodeTypes,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { cn } from "@/lib/utils";
import {
  MessageSquare, Search, BarChart2, Shield, FileText, Brain,
  CheckCircle, AlertTriangle, Cpu, Radio, Zap, GitMerge, Filter,
  X, Play, Save, Plus, Trash2, Pencil, Check, Sparkles, ChevronRight,
  Loader2, PanelLeftClose, PanelLeftOpen,
} from "lucide-react";
import { useWorkflowStore, type WFNode, type WFEdge } from "@/store/workflowStore";
import { useAuthStore } from "@/store/authStore";

// ── Icon map (string → component) ────────────────────────────────────
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const ICON_MAP: Record<string, React.ElementType> = {
  MessageSquare, Search, BarChart2, Shield, FileText, Brain,
  CheckCircle, AlertTriangle, Cpu, Radio, Zap, GitMerge, Filter,
};

// ── Node catalog (palette) ────────────────────────────────────────────
const NODE_CATALOG = [
  { iconName: "MessageSquare", label: "需求输入",   color: "bg-slate-600",  description: "接收自然语言选型需求" },
  { iconName: "Brain",         label: "AI 推理",    color: "bg-blue-600",   description: "LLM 提取结构化参数" },
  { iconName: "Search",        label: "数据检索",   color: "bg-teal-600",   description: "多前缀关键词并发检索" },
  { iconName: "BarChart2",     label: "多维评分",   color: "bg-purple-600", description: "D1–D7 七维规则评分" },
  { iconName: "FileText",      label: "证据链",     color: "bg-green-600",  description: "E1/E2/E3 来源标注" },
  { iconName: "Shield",        label: "约束检查",   color: "bg-amber-600",  description: "G1–G6 安全约束门禁" },
  { iconName: "CheckCircle",   label: "验证节点",   color: "bg-orange-600", description: "LangGraph 批评节点" },
  { iconName: "AlertTriangle", label: "双模型验证", color: "bg-red-600",    description: "双模型一致性验证" },
  { iconName: "Cpu",           label: "报告生成",   color: "bg-teal-700",   description: "BOM + 风险报告输出" },
  { iconName: "Zap",           label: "触发器",     color: "bg-yellow-600", description: "工作流触发条件" },
  { iconName: "GitMerge",      label: "合并节点",   color: "bg-indigo-600", description: "合并多条数据流" },
  { iconName: "Filter",        label: "过滤器",     color: "bg-pink-600",   description: "条件过滤与路由" },
];

// ── PipelineNode ──────────────────────────────────────────────────────
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function PipelineNode({ data, selected }: { data: any; selected?: boolean }) {
  const Icon = (ICON_MAP[data.iconName as string] ?? Brain) as React.ElementType;
  return (
    <div className={cn(
      "min-w-[160px] rounded-2xl border-2 bg-white shadow-lg transition-all cursor-pointer select-none",
      selected ? "border-teal-500 shadow-teal-200 shadow-xl" : "border-slate-200 hover:border-slate-300"
    )}>
      <Handle type="target" position={Position.Left} className="!w-3 !h-3 !bg-slate-300 !border-2 !border-white" />
      <div className={cn("px-3 py-2 rounded-t-xl flex items-center gap-2", data.color as string)}>
        <Icon className="w-4 h-4 text-white shrink-0" />
        <span className="text-xs font-extrabold text-white truncate">{data.label as string}</span>
      </div>
      <div className="px-3 py-2">
        <p className="text-[10px] text-slate-500 leading-relaxed">{data.description as string}</p>
        {data.params && Object.entries(data.params as Record<string, unknown>).map(([k, v]) => (
          <div key={k} className="flex items-center justify-between mt-1">
            <span className="text-[9px] text-slate-400 uppercase tracking-wide">{k}</span>
            <span className="text-[10px] font-mono font-bold text-slate-700">{String(v)}</span>
          </div>
        ))}
      </div>
      <Handle type="source" position={Position.Right} className="!w-3 !h-3 !bg-teal-400 !border-2 !border-white" />
    </div>
  );
}

const nodeTypes: NodeTypes = { pipeline: PipelineNode };

// ── WorkflowEditor (outer) ────────────────────────────────────────────
export function WorkflowEditor({ onClose, onRun }: { onClose?: () => void; onRun?: (prompt: string) => void }) {
  const { workflows, activeId, activeWorkflow, createWorkflow, deleteWorkflow,
          setActive, renameWorkflow } = useWorkflowStore();
  const { token } = useAuthStore();
  const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "";

  const [savedMsg, setSavedMsg] = useState(false);
  const [listOpen, setListOpen] = useState(true);
  const [aiOpen, setAiOpen] = useState(false);
  const [aiInput, setAiInput] = useState("");
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState("");
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameVal, setRenameVal] = useState("");
  const wf = activeWorkflow();

  const handleSaved = () => { setSavedMsg(true); setTimeout(() => setSavedMsg(false), 2000); };

  const handleAiGenerate = async () => {
    if (!aiInput.trim()) return;
    setAiLoading(true); setAiError("");
    try {
      const resp = await fetch(`${API_BASE}/workflow/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ description: aiInput }),
      });
      if (!resp.ok) { setAiError("生成失败，请重试"); return; }
      const data = await resp.json();
      const newNodes: WFNode[] = (data.nodes || []).map((n: { id: string; iconName: string; label: string; color: string; description: string; params?: Record<string,string|number>; x?: number; y?: number }) => ({
        id: n.id, type: "pipeline" as const,
        position: { x: n.x ?? 0, y: n.y ?? 0 },
        data: { iconName: n.iconName, label: n.label, color: n.color, description: n.description, params: n.params },
      }));
      const newEdges: WFEdge[] = (data.edges || []).map((e: { source: string; target: string }, i: number) => ({
        id: `ae${i}`, source: e.source, target: e.target, animated: true,
      }));
      const id = createWorkflow(data.name || aiInput.slice(0, 20), newNodes, newEdges);
      setActive(id); setAiInput(""); setAiOpen(false);
    } catch { setAiError("网络错误，请重试"); }
    finally { setAiLoading(false); }
  };

  return (
    <div className="flex flex-col h-full w-full bg-[#f8f9fa]">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2.5 bg-white border-b border-slate-100 shrink-0 gap-3">
        <div className="flex items-center gap-2 min-w-0">
          <button onClick={() => setListOpen(v => !v)} className="p-1 text-slate-400 hover:text-slate-700 rounded-lg hover:bg-slate-100">
            {listOpen ? <PanelLeftClose className="w-4 h-4" /> : <PanelLeftOpen className="w-4 h-4" />}
          </button>
          <h2 className="text-sm font-extrabold text-slate-800 truncate">{wf?.name ?? "工作流编排"}</h2>
          {savedMsg && <span className="text-[10px] text-teal-600 font-bold shrink-0">已保存 ✓</span>}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button onClick={() => setAiOpen(v => !v)}
            className={cn("flex items-center gap-1.5 px-3 h-7 text-xs font-bold rounded-xl border transition-colors",
              aiOpen ? "bg-violet-100 text-violet-700 border-violet-200" : "bg-white text-slate-600 border-slate-200 hover:bg-violet-50 hover:text-violet-600")}>
            <Sparkles className="w-3.5 h-3.5" /> AI 生成
          </button>
          <button onClick={() => createWorkflow()}
            className="flex items-center gap-1 px-2.5 h-7 bg-white text-slate-600 border border-slate-200 text-xs font-semibold rounded-xl hover:bg-slate-50">
            <Plus className="w-3.5 h-3.5" /> 新建
          </button>
          <button
            onClick={() => {
              if (wf?.demoPrompt && onRun) { onRun(wf.demoPrompt); }
              else if (onRun) { onRun(""); }
            }}
            disabled={!wf?.demoPrompt || !onRun}
            className="flex items-center gap-1 px-2.5 h-7 bg-teal-50 text-teal-700 border border-teal-200 text-xs font-bold rounded-xl hover:bg-teal-100 disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
            <Play className="w-3.5 h-3.5" /> 运行
          </button>
          {onClose && <button onClick={onClose} className="p-1.5 text-slate-400 hover:text-slate-700 hover:bg-slate-100 rounded-lg"><X className="w-4 h-4" /></button>}
        </div>
      </div>

      {/* AI generation panel */}
      {aiOpen && (
        <div className="shrink-0 bg-violet-50 border-b border-violet-100 px-4 py-3 flex gap-2 items-start">
          <Sparkles className="w-4 h-4 text-violet-500 mt-2 shrink-0" />
          <div className="flex-1 space-y-2">
            <textarea value={aiInput} onChange={e => setAiInput(e.target.value)} rows={2}
              placeholder="描述你的工作流，例如：先解析用户需求，检索数据库，再进行多维评分，最后生成报告…"
              className="w-full text-xs bg-white border border-violet-200 rounded-xl px-3 py-2 resize-none focus:outline-none focus:border-violet-400" />
            {aiError && <p className="text-[10px] text-red-500">{aiError}</p>}
            <div className="flex gap-2">
              <button onClick={handleAiGenerate} disabled={aiLoading || !aiInput.trim()}
                className="flex items-center gap-1.5 px-3 h-7 bg-violet-600 text-white text-xs font-bold rounded-xl hover:bg-violet-700 disabled:opacity-50 transition-colors">
                {aiLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Sparkles className="w-3.5 h-3.5" />}
                {aiLoading ? "生成中…" : "生成工作流"}
              </button>
              <button onClick={() => setAiOpen(false)} className="px-3 h-7 text-xs text-slate-500 hover:text-slate-700 rounded-xl hover:bg-slate-100">取消</button>
            </div>
          </div>
        </div>
      )}

      {/* Body */}
      <div className="flex-1 flex min-h-0">
        {/* Workflow list */}
        {listOpen && (
          <div className="w-44 shrink-0 bg-white border-r border-slate-100 flex flex-col overflow-hidden">
            <p className="text-[9px] font-bold text-slate-400 uppercase tracking-widest px-3 pt-3 pb-1">工作流列表</p>
            <div className="flex-1 overflow-y-auto">
              {workflows.map(w => (
                <div key={w.id} onClick={() => setActive(w.id)}
                  className={cn("group flex items-center gap-1.5 px-2.5 py-2 cursor-pointer transition-colors",
                    w.id === activeId ? "bg-teal-50 border-l-2 border-teal-500" : "hover:bg-slate-50 border-l-2 border-transparent")}>
                  {renaming === w.id ? (
                    <input autoFocus value={renameVal} onChange={e => setRenameVal(e.target.value)}
                      onBlur={() => { renameWorkflow(w.id, renameVal || w.name); setRenaming(null); }}
                      onKeyDown={e => { if (e.key === "Enter") { renameWorkflow(w.id, renameVal || w.name); setRenaming(null); } }}
                      className="flex-1 text-[10px] bg-white border border-teal-300 rounded px-1 py-0.5 focus:outline-none min-w-0" />
                  ) : (
                    <span className="flex-1 text-[10px] font-medium text-slate-700 truncate">{w.name}</span>
                  )}
                  <div className="hidden group-hover:flex items-center gap-0.5">
                    <button onClick={e => { e.stopPropagation(); setRenaming(w.id); setRenameVal(w.name); }}
                      className="p-0.5 text-slate-400 hover:text-slate-700 rounded"><Pencil className="w-2.5 h-2.5" /></button>
                    {workflows.length > 1 && (
                      <button onClick={e => { e.stopPropagation(); deleteWorkflow(w.id); }}
                        className="p-0.5 text-slate-400 hover:text-red-500 rounded"><Trash2 className="w-2.5 h-2.5" /></button>
                    )}
                  </div>
                </div>
              ))}
            </div>
            {/* Node palette */}
            <div className="border-t border-slate-100 px-2 pt-2 pb-3">
              <p className="text-[9px] font-bold text-slate-400 uppercase tracking-widest mb-1.5 px-1">节点类型 (拖入画布)</p>
              {NODE_CATALOG.map(item => {
                const Icon = ICON_MAP[item.iconName] ?? Brain;
                return (
                  <div key={item.iconName} draggable
                    onDragStart={e => { e.dataTransfer.setData("application/nodedata", JSON.stringify({ iconName: item.iconName, label: item.label, color: item.color, description: item.description })); e.dataTransfer.effectAllowed = "move"; }}
                    className="flex items-center gap-1.5 px-2 py-1.5 rounded-lg hover:bg-slate-50 cursor-grab active:cursor-grabbing transition-colors">
                    <Icon className={cn("w-3 h-3 shrink-0", item.color.replace("bg-", "text-"))} />
                    <span className="text-[10px] text-slate-600 truncate">{item.label}</span>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Canvas */}
        {wf && (
          <ReactFlowProvider>
            <FlowCanvas key={wf.id} initialNodes={wf.nodes} initialEdges={wf.edges}
              workflowId={wf.id} onSaved={handleSaved} />
          </ReactFlowProvider>
        )}
      </div>
    </div>
  );
}

// ── FlowCanvas (inner, uses useReactFlow) ─────────────────────────────
interface FlowCanvasProps {
  initialNodes: WFNode[];
  initialEdges: WFEdge[];
  workflowId: string;
  onSaved: () => void;
}

function FlowCanvas({ initialNodes, initialEdges, workflowId, onSaved }: FlowCanvasProps) {
  const { screenToFlowPosition } = useReactFlow();
  const { updateGraph } = useWorkflowStore();
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes as any);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);

  // Re-init when workflow switches
  useEffect(() => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    setNodes(initialNodes as any);
    setEdges(initialEdges);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflowId]);

  const onConnect = useCallback(
    (params: Connection) => setEdges(eds => addEdge({ ...params, animated: true }, eds)),
    [setEdges]
  );

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  }, []);

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    const raw = e.dataTransfer.getData("application/nodedata");
    if (!raw) return;
    try {
      const nodeData = JSON.parse(raw);
      const position = screenToFlowPosition({ x: e.clientX, y: e.clientY });
      const newNode: WFNode = {
        id: `node_${Date.now()}`,
        type: "pipeline",
        position,
        data: nodeData,
      };
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      setNodes(nds => [...nds, newNode as any]);
    } catch { /* ignore */ }
  }, [screenToFlowPosition, setNodes]);

  const handleSave = () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    updateGraph(workflowId, nodes as any, edges as any);
    onSaved();
  };

  return (
    <div className="flex-1 relative flex flex-col">
      <div className="absolute top-2 right-2 z-10">
        <button onClick={handleSave}
          className="flex items-center gap-1.5 px-3 h-7 bg-teal-500 text-white text-xs font-bold rounded-xl hover:bg-teal-600 shadow transition-colors">
          <Save className="w-3 h-3" /> 保存
        </button>
      </div>
      <ReactFlow
        nodes={nodes} edges={edges}
        onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
        onConnect={onConnect} nodeTypes={nodeTypes}
        onDragOver={onDragOver} onDrop={onDrop}
        fitView fitViewOptions={{ padding: 0.2 }}
        defaultEdgeOptions={{ type: "smoothstep", style: { stroke: "#cbd5e1", strokeWidth: 2 } }}
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#e2e8f0" />
        <Controls />
      </ReactFlow>
      <div className="flex items-center gap-2 px-4 py-1.5 bg-white border-t border-slate-100 shrink-0">
        <div className="w-2 h-2 rounded-full bg-teal-500 animate-pulse" />
        <span className="text-[10px] text-slate-500">{nodes.length} 节点 · {edges.length} 边</span>
      </div>
    </div>
  );
}
