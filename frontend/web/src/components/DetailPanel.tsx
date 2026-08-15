"use client";

import { useChatStore } from "@/store/chat";
import { useAuthStore, GUEST_MESSAGE_LIMIT } from "@/store/authStore";
import { useState } from "react";
import { Cpu, BarChart2, Zap, ChevronRight, Star } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ScoredPart } from "@/types";

type TabId = "overview" | "parts" | "risks" | "evidence";
type SortKey = "total" | "param" | "supply" | "risk" | "quality";
type FilterKey = "all" | "active" | "domestic";

const TABS: { id: TabId; label: string }[] = [
  { id: "overview", label: "概览" },
  { id: "parts",    label: "器件" },
  { id: "risks",    label: "风险" },
  { id: "evidence", label: "证据" },
];

const SORT_TABS: { id: SortKey; label: string }[] = [
  { id: "total",   label: "综合" },
  { id: "param",   label: "参数" },
  { id: "supply",  label: "供应" },
  { id: "risk",    label: "安全" },
  { id: "quality", label: "质量" },
];

function ScoreRing({ score }: { score: number }) {
  const pct = Math.min(100, Math.round(score));
  const color = score >= 75 ? "text-ez-green" : score >= 55 ? "text-ez-accent" : "text-ez-amber";
  return (
    <div className={cn("text-center tabular-nums leading-none", color)}>
      <div className="text-xl font-black">{Math.round(score)}</div>
      <div className="text-2xs text-ez-text-label font-normal mt-0.5">/100</div>
    </div>
  );
}

function MiniBar({ label, value, max = 20 }: { label: string; value: number; max?: number }) {
  const pct = Math.min(100, Math.round((value / max) * 100));
  const color = pct >= 75 ? "bg-ez-green" : pct >= 50 ? "bg-ez-accent" : "bg-ez-amber";
  return (
    <div className="flex items-center gap-2 py-0.5">
      <span className="w-5 text-2xs text-ez-text-label font-mono shrink-0">{label}</span>
      <div className="flex-1 h-1 bg-ez-bg-surface rounded-full overflow-hidden">
        <div className={cn("h-full rounded-full", color)} style={{ width: `${pct}%` }} />
      </div>
      <span className="w-6 text-right text-2xs font-mono text-ez-text-muted">{value.toFixed(0)}</span>
    </div>
  );
}

function PartCard({ part, rank, onSelect }: { part: ScoredPart; rank: number; onSelect: () => void }) {
  const isRec = part.recommendation_level === "recommended";
  const pn = part.part?.part_number ?? "";
  const mfr = part.part?.manufacturer ?? "";
  const pkg = part.part?.package ?? "";
  const lifecycle = part.part?.lifecycle_status ?? "Active";
  const domestic = part.part?.is_domestic === true;
  const dimScores: Record<string, number> = (part.score as any)?.dim_scores ?? {};
  const totalScore = part.score?.total_score ?? 0;
  const isActive = lifecycle === "Active";

  return (
    <div className={cn(
      "rounded-xl border mb-2 overflow-hidden transition-all",
      isRec
        ? "border-blue-200 shadow-sm"
        : "border-ez-border hover:border-ez-border-hi hover:shadow-sm"
    )}>
      {isRec && (
        <div className="h-0.5 bg-gradient-to-r from-ez-accent to-blue-400" />
      )}
      <div className={cn("p-3", isRec ? "bg-blue-50/40" : "bg-white")}>
        <div className="flex items-start gap-3">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className="text-2xs text-ez-text-label font-mono">#{rank}</span>
              <span className={cn("text-xs font-bold font-mono truncate", isRec ? "text-ez-accent" : "text-ez-text")}>
                {pn}
              </span>
              {isRec && (
                <span className="inline-flex items-center gap-0.5 text-2xs bg-ez-accent text-white px-1.5 py-0.5 rounded font-semibold shrink-0">
                  <Star className="w-2 h-2" /> 首选
                </span>
              )}
              {domestic && (
                <span className="text-2xs bg-green-50 border border-green-200 text-ez-green px-1.5 py-0.5 rounded shrink-0">国产</span>
              )}
            </div>
            {mfr && <p className="text-2xs text-ez-text-muted mt-0.5 truncate">{mfr}</p>}
            <div className="flex items-center gap-1 mt-1.5 flex-wrap">
              {pkg && (
                <span className="text-2xs border border-ez-border px-1.5 py-0.5 rounded font-mono text-ez-text-label">{pkg}</span>
              )}
              <span className={cn(
                "text-2xs px-1.5 py-0.5 rounded border",
                isActive ? "border-green-200 text-ez-green bg-green-50" : "border-amber-200 text-ez-amber bg-amber-50"
              )}>{lifecycle}</span>
            </div>
          </div>
          <ScoreRing score={totalScore} />
        </div>

        {Object.keys(dimScores).length > 0 && (
          <div className="mt-2 pt-2 border-t border-ez-border/60 space-y-0">
            {Object.entries(dimScores).slice(0, 5).map(([k, v]) => (
              <MiniBar key={k} label={k} value={v} />
            ))}
          </div>
        )}

        <button onClick={onSelect}
          className={cn(
            "mt-2.5 w-full h-7 text-2xs font-semibold rounded-lg transition-colors flex items-center justify-center gap-1",
            isRec
              ? "bg-ez-accent hover:bg-ez-accent-hi text-white"
              : "bg-ez-bg-surface hover:bg-ez-bg-hover border border-ez-border text-ez-text-muted hover:text-ez-text"
          )}>
          查看详情 &amp; 选用 <ChevronRight className="w-3 h-3" />
        </button>
      </div>
    </div>
  );
}

export function DetailPanel() {
  const { activeSession, setPendingInput, setSelectedPartModal } = useChatStore();
  const { user, remainingGuestMessages } = useAuthStore();
  const session = activeSession();
  const lastReport = session?.messages.filter(m => m.role === "assistant" && m.report).pop()?.report;
  const [tab, setTab] = useState<TabId>("overview");
  const [sortKey, setSortKey] = useState<SortKey>("total");
  const [filter, setFilter] = useState<FilterKey>("all");
  const isGuest = user?.is_guest === true;
  const remaining = remainingGuestMessages?.() ?? 0;

  const allParts: ScoredPart[] = (() => {
    const cand = lastReport?.candidates ?? [];
    const rec  = lastReport?.recommended_parts ?? [];
    return cand.length > 0 ? cand : rec;
  })();

  const filtered = allParts.filter(p => {
    if (filter === "active") return (p.part?.lifecycle_status ?? "Active") === "Active";
    if (filter === "domestic") return p.part?.is_domestic === true;
    return true;
  });

  const sorted = [...filtered].sort((a, b) => {
    const ds = (p: ScoredPart) => (p.score as any)?.dim_scores ?? {};
    if (sortKey === "param")   return (ds(b).D1 ?? 0) - (ds(a).D1 ?? 0);
    if (sortKey === "supply")  return (ds(b).D3 ?? 0) - (ds(a).D3 ?? 0);
    if (sortKey === "risk")    return (ds(b).D6 ?? 0) - (ds(a).D6 ?? 0);
    if (sortKey === "quality") return (ds(b).D7 ?? 0) - (ds(a).D7 ?? 0);
    return (b.score?.total_score ?? 0) - (a.score?.total_score ?? 0);
  });

  if (!lastReport) {
    return (
      <aside className="w-full lg:w-[360px] h-full flex flex-col bg-ez-bg-panel border-l border-ez-border shrink-0 overflow-hidden">
        <div className="panel-header"><BarChart2 className="w-3.5 h-3.5 text-ez-accent" /><span>属性面板</span></div>
        {isGuest && (
          <div className="px-3 py-2 border-b border-ez-border bg-amber-50/60 flex items-center gap-2">
            <Zap className="w-3 h-3 text-ez-amber shrink-0" />
            <span className="text-2xs text-ez-text-muted flex-1">试用额度</span>
            <span className={cn("text-xs font-mono font-bold", remaining <= 1 ? "text-ez-red" : remaining <= 2 ? "text-ez-amber" : "text-ez-green")}>
              {remaining}/{GUEST_MESSAGE_LIMIT}
            </span>
          </div>
        )}
        <div className="flex-1 flex flex-col items-center justify-center gap-3 px-6 text-center">
          <div className="w-12 h-12 rounded-2xl bg-ez-bg-surface flex items-center justify-center">
            <Cpu className="w-6 h-6 text-ez-text-dim" />
          </div>
          <div>
            <p className="text-sm font-semibold text-ez-text-muted">暂无分析结果</p>
            <p className="text-2xs text-ez-text-label mt-1">发送选型需求后，器件评分和风险<br />分析将在此处显示</p>
          </div>
        </div>
      </aside>
    );
  }

  const { risks, evidence_count, avg_confidence, elapsed_s, constraints, evidence_items = [] } = lastReport;
  const level = risks?.overall_risk_level ?? "low";
  const riskColor = level === "high" || level === "critical" ? "text-ez-red bg-red-50 border-red-200"
    : level === "medium" || level === "low_medium" ? "text-ez-amber bg-amber-50 border-amber-200"
    : "text-ez-green bg-green-50 border-green-200";

  return (
    <aside className="w-full lg:w-[360px] h-full flex flex-col bg-ez-bg-panel border-l border-ez-border shrink-0 overflow-hidden">
      <div className="panel-header"><BarChart2 className="w-3.5 h-3.5 text-ez-accent" /><span>属性面板</span></div>

      {isGuest && (
        <div className="px-3 py-1.5 border-b border-ez-border bg-amber-50/60 flex items-center gap-2">
          <Zap className="w-3 h-3 text-ez-amber shrink-0" />
          <span className="text-2xs text-ez-text-muted flex-1">试用额度</span>
          <span className={cn("text-xs font-mono font-bold", remaining <= 1 ? "text-ez-red" : remaining <= 2 ? "text-ez-amber" : "text-ez-green")}>
            {remaining}/{GUEST_MESSAGE_LIMIT}
          </span>
        </div>
      )}

      {/* Tabs */}
      <div className="flex border-b border-ez-border bg-white shrink-0 px-1">
        {TABS.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)}
            className={cn(
              "flex-1 flex items-center justify-center gap-1 py-2.5 text-2xs font-semibold transition-colors border-b-2 -mb-px",
              tab === t.id
                ? "border-ez-accent text-ez-accent"
                : "border-transparent text-ez-text-muted hover:text-ez-text"
            )}>
            {t.label}
            {t.id === "parts" && allParts.length > 0 && (
              <span className={cn(
                "text-2xs px-1.5 rounded-full font-bold",
                tab === t.id ? "bg-ez-accent text-white" : "bg-ez-bg-surface text-ez-text-muted"
              )}>{allParts.length}</span>
            )}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto min-h-0">

        {/* OVERVIEW TAB */}
        {tab === "overview" && (
          <div className="p-3 space-y-2.5">
            {/* Risk + quick stats */}
            <div className="bg-white rounded-xl border border-ez-border shadow-sm overflow-hidden">
              <div className={cn("flex items-center gap-2 px-3 py-2 border-b border-ez-border")}>
                <span className={cn("text-2xs font-bold px-2 py-0.5 rounded-full border uppercase tracking-wide", riskColor)}>
                  {level.replace("_", " ")}
                </span>
                <span className="text-2xs text-ez-text-label ml-auto">综合风险等级</span>
              </div>
              <div className="divide-y divide-ez-border/60">
                <div className="prop-row"><span className="prop-label">候选器件</span><span className="prop-value font-semibold text-ez-text">{allParts.length} 个</span></div>
                <div className="prop-row"><span className="prop-label">证据条数</span><span className="prop-value">{evidence_count ?? 0}</span></div>
                <div className="prop-row"><span className="prop-label">平均置信度</span><span className="prop-value">{avg_confidence ? `${(avg_confidence * 100).toFixed(0)}%` : "—"}</span></div>
                {elapsed_s && <div className="prop-row"><span className="prop-label">分析耗时</span><span className="prop-value">{elapsed_s.toFixed(2)}s</span></div>}
              </div>
            </div>

            {constraints && (
              <div className="bg-white rounded-xl border border-ez-border shadow-sm overflow-hidden">
                <div className="px-3 py-2 border-b border-ez-border">
                  <span className="text-2xs font-bold text-ez-text-muted uppercase tracking-wide">设计约束</span>
                </div>
                <div className="divide-y divide-ez-border/60">
                  {(constraints as any).input_voltage_nominal_v != null && <div className="prop-row"><span className="prop-label">输入电压</span><span className="prop-value">{(constraints as any).input_voltage_nominal_v}V</span></div>}
                  {(constraints as any).output_voltage_v != null && <div className="prop-row"><span className="prop-label">输出电压</span><span className="prop-value">{(constraints as any).output_voltage_v}V</span></div>}
                  {(constraints as any).output_current_a != null && <div className="prop-row"><span className="prop-label">输出电流</span><span className="prop-value">{(constraints as any).output_current_a}A</span></div>}
                  {(constraints as any).topology && <div className="prop-row"><span className="prop-label">拓扑</span><span className="prop-value capitalize">{(constraints as any).topology}</span></div>}
                  {(constraints as any).category && <div className="prop-row"><span className="prop-label">类别</span><span className="prop-value">{(constraints as any).category}</span></div>}
                  {(constraints as any).grade && <div className="prop-row"><span className="prop-label">等级</span><span className="prop-value">{(constraints as any).grade}</span></div>}
                </div>
              </div>
            )}
          </div>
        )}

        {/* PARTS TAB */}
        {tab === "parts" && (
          <div className="flex flex-col h-full">
            <div className="px-3 pt-2.5 pb-2 border-b border-ez-border bg-white shrink-0 space-y-2">
              {/* Sort */}
              <div className="flex gap-1">
                {SORT_TABS.map(s => (
                  <button key={s.id} onClick={() => setSortKey(s.id)}
                    className={cn(
                      "flex-1 text-2xs py-1 rounded-lg border font-medium transition-colors",
                      sortKey === s.id
                        ? "bg-ez-accent text-white border-ez-accent"
                        : "border-ez-border text-ez-text-muted hover:border-ez-accent hover:text-ez-accent"
                    )}>{s.label}</button>
                ))}
              </div>
              {/* Filter */}
              <div className="flex items-center gap-1.5">
                {(["all","active","domestic"] as FilterKey[]).map(f => (
                  <button key={f} onClick={() => setFilter(f)}
                    className={cn(
                      "text-2xs px-2.5 py-1 rounded-lg border font-medium transition-colors",
                      filter === f
                        ? "bg-ez-accent/10 border-ez-accent text-ez-accent"
                        : "border-ez-border text-ez-text-muted hover:border-ez-accent"
                    )}>
                    {f === "all" ? "全部" : f === "active" ? "仅 Active" : "仅国产"}
                  </button>
                ))}
                <span className="ml-auto text-2xs text-ez-text-label">{sorted.length} 个</span>
              </div>
            </div>
            <div className="flex-1 overflow-y-auto p-3">
              {sorted.length === 0
                ? (
                  <div className="flex flex-col items-center justify-center py-10 text-center">
                    <Cpu className="w-8 h-8 text-ez-text-dim mb-2" />
                    <p className="text-xs text-ez-text-muted font-medium">无符合筛选条件的器件</p>
                    <p className="text-2xs text-ez-text-label mt-1">尝试切换筛选条件</p>
                  </div>
                )
                : sorted.map((part, i) => (
                    <PartCard key={(part as any).part?.part_number ?? i} part={part} rank={i + 1}
                      onSelect={() => setSelectedPartModal(part)} />
                  ))}
            </div>
          </div>
        )}

        {/* RISKS TAB */}
        {tab === "risks" && (
          <div className="p-3 space-y-2">
            {risks?.risk_items?.length
              ? risks.risk_items.map((r: any, i: number) => {
                  const sc = r.severity === "high" || r.severity === "critical"
                    ? "border-red-200 bg-red-50/50" : r.severity === "medium"
                    ? "border-amber-200 bg-amber-50/50" : "border-green-200 bg-green-50/50";
                  return (
                    <div key={i} className={cn("border rounded-xl p-3 shadow-sm", sc)}>
                      <div className="flex items-start gap-2 mb-1">
                        <span className={cn("risk-badge shrink-0 mt-0.5", r.severity)}>{r.severity}</span>
                        <span className="text-xs font-semibold text-ez-text leading-tight">{r.title ?? r.description?.slice(0, 60)}</span>
                      </div>
                      {r.description && <p className="text-2xs text-ez-text-muted leading-relaxed">{r.description}</p>}
                      {r.mitigation && <p className="text-2xs text-ez-green mt-1.5 font-medium">建议：{r.mitigation}</p>}
                    </div>
                  );
                })
              : (
                <div className="flex flex-col items-center justify-center py-10">
                  <p className="text-xs text-ez-text-muted">暂无风险数据</p>
                </div>
              )
            }
          </div>
        )}

        {/* EVIDENCE TAB */}
        {tab === "evidence" && (
          <div className="p-3 space-y-1.5">
            {evidence_items?.length
              ? evidence_items.map((e: any, i: number) => (
                  <div key={i} className="border border-ez-border rounded-xl p-2.5 bg-white shadow-sm">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-2xs font-mono text-ez-accent font-bold">{e.part_number}</span>
                      <span className={cn("ml-auto text-2xs font-bold px-1.5 py-0.5 rounded-full border",
                        e.confidence >= 0.8 ? "text-ez-green border-green-200 bg-green-50"
                          : e.confidence >= 0.5 ? "text-ez-amber border-amber-200 bg-amber-50"
                          : "text-ez-red border-red-200 bg-red-50"
                      )}>{Math.round((e.confidence ?? 0) * 100)}%</span>
                    </div>
                    <p className="text-2xs text-ez-text-muted leading-relaxed">{e.claim}</p>
                  </div>
                ))
              : (
                <div className="flex flex-col items-center justify-center py-10">
                  <p className="text-xs text-ez-text-muted">暂无证据数据</p>
                </div>
              )
            }
          </div>
        )}

      </div>
    </aside>
  );
}
