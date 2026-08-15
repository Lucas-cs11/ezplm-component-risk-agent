"use client";

import { useChatStore } from "@/store/chat";
import { useAuthStore } from "@/store/authStore";
import { X, Download, FileSpreadsheet, FileText, Package, Award, ShieldCheck, AlertTriangle, GripHorizontal } from "lucide-react";
import { cn } from "@/lib/utils";
import { useState, useRef, useEffect } from "react";
import type { ScoredPart } from "@/types";

function DimBar({ dim, value }: { dim: string; value: number }) {
  const pct = Math.min(100, Math.round((value / 20) * 100));
  const color = value >= 16 ? "bg-green-500" : value >= 10 ? "bg-teal-500" : "bg-amber-400";
  return (
    <div className="flex items-center gap-2 py-0.5">
      <span className="text-[10px] text-slate-500 w-7 shrink-0 font-mono font-bold">{dim}</span>
      <div className="flex-1 h-1.5 bg-slate-100 rounded-full overflow-hidden">
        <div className={cn("h-full rounded-full transition-all", color)} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[10px] font-mono text-slate-500 w-8 text-right">{(value ?? 0).toFixed(1)}</span>
    </div>
  );
}

export function ResultModal() {
  const { selectedPartModal, setSelectedPartModal, activeSession } = useChatStore();
  const { token } = useAuthStore();
  const session = activeSession();
  const modalRef = useRef<HTMLDivElement>(null);
  const dragHandleRef = useRef<HTMLDivElement>(null);
  const resizeHandleRef = useRef<HTMLDivElement>(null);

  const [position, setPosition] = useState({ x: 0, y: 0 });
  const [size, setSize] = useState({ width: 520, height: 600 });
  const [isDragging, setIsDragging] = useState(false);
  const [isResizing, setIsResizing] = useState(false);
  const [dragOffset, setDragOffset] = useState({ x: 0, y: 0 });

  if (!selectedPartModal) return null;

  const part = selectedPartModal as ScoredPart & { part?: Record<string, unknown> };
  const pn = (part.part?.part_number ?? "") as string;
  const mfr = (part.part?.manufacturer ?? "") as string;
  const pkg = (part.part?.package ?? "") as string;
  const grade = (part.part as Record<string,unknown>)?.grade as string ?? "";
  const lifecycle = (part.part?.lifecycle_status ?? "Active") as string;
  const dimScores: Record<string, number> = ((part.score as unknown) as Record<string,unknown>)?.dim_scores as Record<string,number> ?? {};
  const totalScore = part.score?.total_score ?? 0;
  const isRec = part.recommendation_level === "recommended";

  const getAuthBearer = (): Record<string, string> =>
    token ? { Authorization: `Bearer ${token}` } : {};

  const handleExport = async (endpoint: string, filename: string, method = "GET") => {
    if (!session?.id) return;
    const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "";
    const resp = await fetch(`${API_BASE}${endpoint}?session_id=${session.id}`, { method, headers: getAuthBearer() });
    if (!resp.ok) return;
    const blob = await resp.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = filename; a.click();
    URL.revokeObjectURL(a.href);
  };

  // Dragging handler
  useEffect(() => {
    if (!isDragging || !modalRef.current) return;

    const handleMouseMove = (e: MouseEvent) => {
      setPosition({
        x: Math.max(-window.innerWidth / 3, Math.min(window.innerWidth / 3, e.clientX - dragOffset.x)),
        y: Math.max(0, Math.min(window.innerHeight - 100, e.clientY - dragOffset.y)),
      });
    };

    const handleMouseUp = () => setIsDragging(false);

    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);
    return () => {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    };
  }, [isDragging, dragOffset]);

  // Resizing handler
  useEffect(() => {
    if (!isResizing || !modalRef.current) return;

    const handleMouseMove = (e: MouseEvent) => {
      const newWidth = Math.max(360, Math.min(800, e.clientX - modalRef.current!.getBoundingClientRect().left + dragOffset.x));
      const newHeight = Math.max(400, Math.min(900, e.clientY - modalRef.current!.getBoundingClientRect().top + dragOffset.y));
      setSize({ width: newWidth, height: newHeight });
    };

    const handleMouseUp = () => setIsResizing(false);

    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);
    return () => {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    };
  }, [isResizing, dragOffset]);

  const handleDragStart = (e: React.MouseEvent) => {
    if (!modalRef.current) return;
    const rect = modalRef.current.getBoundingClientRect();
    setDragOffset({ x: e.clientX - rect.left, y: e.clientY - rect.top });
    setIsDragging(true);
  };

  const handleResizeStart = (e: React.MouseEvent) => {
    if (!modalRef.current) return;
    const rect = modalRef.current.getBoundingClientRect();
    setDragOffset({ x: e.clientX - rect.right, y: e.clientY - rect.bottom });
    setIsResizing(true);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-slate-900/50 backdrop-blur-sm" onClick={() => setSelectedPartModal(null)} />

      {/* Modal card - now with position/size and selection box effect */}
      <div
        ref={modalRef}
        className="relative bg-white rounded-2xl shadow-2xl shadow-black/30 border-2 border-blue-400 overflow-hidden animate-fade-in"
        style={{
          width: `${size.width}px`,
          height: `${size.height}px`,
          transform: `translate(${position.x}px, ${position.y}px)`,
          boxShadow: '0 0 0 1px rgba(59, 130, 246, 0.5), 0 20px 40px rgba(0,0,0,0.2)'
        }}
      >
        {/* Header - draggable */}
        <div
          ref={dragHandleRef}
          onMouseDown={handleDragStart}
          className="flex items-start justify-between p-4 pb-3 border-b border-slate-200 bg-gradient-to-r from-blue-50 to-teal-50 cursor-move hover:from-blue-100 hover:to-teal-100 transition-colors select-none"
        >
          <div className="flex items-center gap-2 flex-1 min-w-0 pr-4">
            <GripHorizontal className="w-4 h-4 text-slate-400 shrink-0" />
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-0.5 flex-wrap">
                {isRec && (
                  <span className="text-[10px] bg-teal-100 text-teal-700 px-2 py-0.5 rounded-full font-bold border border-teal-300">
                    首选推荐
                  </span>
                )}
                <span className="text-[10px] text-slate-500 font-mono">
                  {lifecycle === "Active"
                    ? <span className="text-green-600 font-bold">● Active</span>
                    : <span className="text-amber-600 font-bold">● {lifecycle}</span>}
                </span>
              </div>
              <h2 className="text-lg font-black text-slate-900 font-mono truncate">{pn}</h2>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <div className="text-right">
              <div className="text-2xl font-black text-teal-600">{totalScore.toFixed(1)}</div>
              <div className="text-[9px] text-slate-400 font-medium">/100</div>
            </div>
            <button onClick={() => setSelectedPartModal(null)}
              className="p-1.5 text-slate-400 hover:text-slate-700 hover:bg-white/80 rounded-lg transition-all">
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="overflow-y-auto p-4 space-y-4" style={{ maxHeight: `${size.height - 180}px` }}>
          {/* Manufacturer */}
          {mfr && <p className="text-xs text-slate-600 font-medium">{mfr}</p>}

          {/* Tags */}
          {(grade || pkg) && (
            <div className="flex gap-2 flex-wrap">
              {grade && <span className="text-xs border-2 border-slate-300 bg-slate-50 px-2.5 py-1 rounded-lg text-slate-700 font-medium hover:border-slate-400 transition-colors">{grade}</span>}
              {pkg   && <span className="text-xs border-2 border-slate-300 bg-slate-50 px-2.5 py-1 rounded-lg font-mono text-slate-700 hover:border-slate-400 transition-colors">{pkg}</span>}
            </div>
          )}

          {/* Dim scores */}
          {Object.keys(dimScores).length > 0 && (
            <div>
              <p className="text-[10px] font-bold text-slate-600 uppercase tracking-wider mb-2.5">七维评分</p>
              <div className="space-y-1 bg-slate-50 p-3 rounded-lg">
                {Object.entries(dimScores).map(([k, v]) => <DimBar key={k} dim={k} value={v} />)}
              </div>
            </div>
          )}

          {/* Export actions */}
          <div>
            <p className="text-[10px] font-bold text-slate-600 uppercase tracking-wider mb-2.5">导出报告</p>
            <div className="grid grid-cols-3 gap-2">
              <button onClick={() => handleExport("/export/bom", "BOM.xlsx", "POST")}
                className="flex flex-col items-center gap-1 p-2.5 bg-green-50 hover:bg-green-100 border-2 border-green-300 text-green-700 rounded-lg transition-all hover:shadow-md">
                <FileSpreadsheet className="w-4 h-4" />
                <span className="text-[9px] font-bold">BOM</span>
              </button>
              <button onClick={() => handleExport("/report/risk", `风险评估_${pn}.md`)}
                className="flex flex-col items-center gap-1 p-2.5 bg-amber-50 hover:bg-amber-100 border-2 border-amber-300 text-amber-700 rounded-lg transition-all hover:shadow-md">
                <FileText className="w-4 h-4" />
                <span className="text-[9px] font-bold">风险</span>
              </button>
              <button onClick={() => handleExport("/export/decision-package", `决策包_${pn}.xlsx`, "POST")}
                className="flex flex-col items-center gap-1 p-2.5 bg-blue-50 hover:bg-blue-100 border-2 border-blue-300 text-blue-700 rounded-lg transition-all hover:shadow-md">
                <Package className="w-4 h-4" />
                <span className="text-[9px] font-bold">决策包</span>
              </button>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="absolute bottom-0 left-0 right-0 px-4 py-3 border-t border-slate-200 bg-white flex gap-2">
          <button onClick={() => setSelectedPartModal(null)}
            className="flex-1 h-9 border-2 border-slate-300 text-slate-700 hover:bg-slate-50 text-sm font-semibold rounded-lg transition-all">
            关闭
          </button>
          <button
            onClick={() => {
              useChatStore.getState().setSelectedPart(pn);
              setSelectedPartModal(null);
            }}
            className="flex-1 h-9 bg-gradient-to-r from-teal-500 to-teal-600 hover:from-teal-600 hover:to-teal-700 text-white text-sm font-extrabold rounded-lg transition-all shadow-md hover:shadow-lg flex items-center justify-center gap-2">
            <Award className="w-4 h-4" /> 选用
          </button>
        </div>

        {/* Resize handle */}
        <div
          onMouseDown={handleResizeStart}
          className="absolute bottom-0 right-0 w-5 h-5 bg-gradient-to-tl from-teal-400 to-transparent cursor-nwse-resize hover:from-teal-500 transition-colors"
          title="拖动调整大小"
        />
      </div>
    </div>
  );
}
