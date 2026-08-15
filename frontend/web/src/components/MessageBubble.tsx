"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Sparkles, User, ChevronDown } from "lucide-react";
import { cn, formatElapsed } from "@/lib/utils";
import { ParameterForm } from "@/components/ParameterForm";
import { useChatStore } from "@/store/chat";
import type { ChatMessage } from "@/types";

interface ProgInfo { current: number; total: number; pct: number; }

function SparkleLoader() {
  return (
    <div className="flex items-center gap-1.5 py-2 px-1">
      {[0, 160, 320].map((delay) => (
        <div key={delay}
          className="w-2 h-2 rounded-full bg-teal-500"
          style={{ animation: "pulseDot 1.4s ease-in-out infinite", animationDelay: `${delay}ms` }}
        />
      ))}
    </div>
  );
}

function ThinkingFade({ content, isStreaming }: { content: string; isStreaming?: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const lines = content.trim().split("\n").filter(Boolean);
  if (!lines.length) return null;
  return (
    <div className="mb-3">
      <button onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1.5 text-slate-400 hover:text-slate-600 mb-1 transition-colors">
        <div className={cn("w-1.5 h-1.5 rounded-full bg-teal-400/60", isStreaming && "animate-pulse")} />
        <span className="text-2xs font-medium uppercase tracking-widest">
          {isStreaming ? "推理中…" : `推理过程（${lines.length} 步）`}
        </span>
        <ChevronDown className={cn("w-2.5 h-2.5 transition-transform", expanded && "rotate-180")} />
      </button>
      {!expanded && isStreaming && lines.length > 0 && (
        <p className="text-2xs text-ez-text-label/60 font-mono italic pl-3 truncate border-l border-ez-text-dim/20 think-line">
          {lines[lines.length - 1]}
        </p>
      )}
      {expanded && (
        <div className="pl-3 border-l border-ez-text-dim/20 max-h-28 overflow-y-auto space-y-0.5">
          {lines.map((line, i) => (
            <p key={i} className="text-2xs text-ez-text-label/70 font-mono italic leading-relaxed think-line">{line}</p>
          ))}
        </div>
      )}
    </div>
  );
}

function ParameterFormInline({ message }: { message: ChatMessage }) {
  const { setPendingInput } = useChatStore();
  return (
    <ParameterForm
      missingFields={message.missing_fields ?? []}
      accumulated={(message.accumulated_constraints ?? {}) as Record<string, unknown>}
      onSubmit={(text) => setPendingInput(text)}
    />
  );
}

export function MessageBubble({ message, progress }: { message: ChatMessage; progress?: ProgInfo }) {
  const isUser = message.role === "user";

  return (
    <div className={cn("flex gap-2.5", isUser ? "flex-row-reverse msg-user" : "flex-row msg-assistant")}>
      <div className="w-7 h-7 rounded-xl border border-slate-100 bg-white shadow-sm flex items-center justify-center shrink-0 mt-0.5">
        {isUser ? <User className="w-3.5 h-3.5 text-slate-500" /> : <Sparkles className="w-3.5 h-3.5 text-teal-600" />}
      </div>

      <div className={cn("flex flex-col", isUser ? "items-end max-w-[72%]" : "flex-1 min-w-0")}>
        {isUser ? (
          <div className="bg-white border border-slate-200 shadow-sm rounded-2xl rounded-tr-sm px-4 py-2.5 text-sm text-slate-800 leading-relaxed">
            <p className="whitespace-pre-wrap">{message.content}</p>
          </div>
        ) : (
          <div className="w-full">
            {message.thinking && (
              <ThinkingFade content={message.thinking} isStreaming={message.isStreaming && !message.thinkingDone} />
            )}
            {!message.content && message.isStreaming && (
              <>
                {progress && (
                  <div className="mb-2">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-2xs font-mono text-slate-400 uppercase tracking-wide">
                        {progress.pct < 15 ? "解析需求" : progress.pct < 30 ? "检索器件" :
                         progress.pct < 60 ? `多维评分 ${progress.current}/${progress.total}` :
                         progress.pct < 80 ? "生成证据" : progress.pct < 95 ? "风险评估" : "生成报告"}
                      </span>
                      <span className="text-2xs font-mono text-teal-600">{progress.pct}%</span>
                    </div>
                    <div className="stage-track"><div className="stage-fill" style={{ width: `${progress.pct}%` }} /></div>
                  </div>
                )}
                {!message.thinking && <SparkleLoader />}
              </>
            )}
            {message.content && (
              <div>
                {progress && message.isStreaming && (
                  <div className="mb-2">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-2xs font-mono text-slate-400 uppercase tracking-wide">
                        {progress.pct < 60 ? `多维评分 ${progress.current}/${progress.total}` :
                         progress.pct < 80 ? "生成证据" : progress.pct < 95 ? "风险评估" : "生成报告"}
                      </span>
                      <span className="text-2xs font-mono text-teal-600">{progress.pct}%</span>
                    </div>
                    <div className="stage-track"><div className="stage-fill" style={{ width: `${progress.pct}%` }} /></div>
                  </div>
                )}
                <div className={cn("markdown-body", message.isStreaming && !message.report && "typing-cursor")}>
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
                </div>
                {!message.isStreaming && message.missing_fields?.length && (
                  <ParameterFormInline message={message} />
                )}
                {!message.isStreaming && message.report && (
                  <div className="mt-2 flex items-center gap-2 text-2xs text-slate-400">
                    <span className="font-mono">{(message.report.candidates?.length || message.report.recommended_parts?.length || 0)} 个候选器件</span>
                    <span className="text-slate-200">·</span>
                    <span className="font-mono">{message.report.evidence_count || 0} 条证据</span>
                    <span className="text-slate-200">·</span>
                    <span className="font-mono">{formatElapsed(message.report.elapsed_s || 0)}</span>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
