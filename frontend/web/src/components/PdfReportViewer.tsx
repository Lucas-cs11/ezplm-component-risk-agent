"use client";

import { useState, useEffect } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Download, Maximize2, Minimize2, Loader2, FileText } from "lucide-react";
import { getAuthBearer } from "@/lib/api";

interface Props {
  reportType: "bom" | "risk";
}

const LABELS: Record<string, string> = {
  bom: "BOM 元器件选型清单",
  risk: "供应链与工程风险评估报告",
};

export function PdfReportViewer({ reportType }: Props) {
  const [md, setMd] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "";
    fetch(`${API_BASE}/report/${reportType}`, { headers: getAuthBearer() })
      .then((r) => r.json())
      .then((d) => {
        if (!cancelled) {
          if (d.content) setMd(d.content);
          else setError(d.detail || "未知错误");
        }
      })
      .catch((e) => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [reportType]);

  const handlePrintPDF = () => {
    const printWindow = window.open("", "_blank");
    if (!printWindow) return;
    const html = `<!DOCTYPE html><html><head><meta charset="utf-8"><title>${LABELS[reportType]}</title>
<style>body{font-family:"PingFang SC","Microsoft YaHei",sans-serif;max-width:800px;margin:40px auto;padding:20px;line-height:1.8;color:#333}
h1{font-size:22px;border-bottom:2px solid #1a237e;padding-bottom:8px}
h2{font-size:17px;color:#1a237e;margin-top:24px}
table{width:100%;border-collapse:collapse;margin:12px 0}
th{background:#e8eaf6;text-align:left;padding:6px 8px;font-size:12px}
td{padding:6px 8px;border-bottom:1px solid #eee;font-size:12px}
code{background:#f5f5f5;padding:1px 4px;border-radius:3px;font-size:11px}
strong{color:#1a237e}
p{font-size:13px}
@media print{body{margin:0;padding:20px}}</style></head>
<body>${md || ""}</body></html>`;
    printWindow.document.write(html);
    printWindow.document.close();
    setTimeout(() => printWindow.print(), 500);
  };

  return (
    <div className="mt-3 rounded-xl border border-gray-200 bg-white overflow-hidden shadow-sm animate-slide-up">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-4 py-2 bg-gray-50 border-b border-gray-100">
        <div className="flex items-center gap-2">
          <FileText className="w-4 h-4 text-brand-600" />
          <span className="text-xs font-semibold text-gray-700">{LABELS[reportType]}</span>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={handlePrintPDF}
            disabled={!md}
            className="p-1.5 hover:bg-gray-200 rounded transition-colors"
            title="下载 PDF"
          >
            <Download className="w-4 h-4 text-gray-500" />
          </button>
          <button
            onClick={() => setExpanded(!expanded)}
            className="p-1.5 hover:bg-gray-200 rounded transition-colors"
          >
            {expanded ? <Minimize2 className="w-4 h-4 text-gray-500" /> : <Maximize2 className="w-4 h-4 text-gray-500" />}
          </button>
        </div>
      </div>

      {/* Content */}
      <div className={`bg-white overflow-y-auto transition-all ${expanded ? "max-h-[600px] p-6" : "max-h-[400px] p-4"}`}>
        {loading ? (
          <div className="flex items-center justify-center py-8 text-gray-400">
            <Loader2 className="w-5 h-5 animate-spin mr-2" />
            <span className="text-xs">加载报告中...</span>
          </div>
        ) : error ? (
          <p className="text-xs text-red-500 py-4">{error}</p>
        ) : md ? (
          <div className="markdown-body text-xs leading-relaxed document-preview">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{md}</ReactMarkdown>
          </div>
        ) : null}
      </div>

      {/* Footer */}
      <div className="px-4 py-2 bg-gray-50 border-t border-gray-100 flex items-center justify-between">
        <span className="text-[10px] text-gray-400">
          eZ-PLM Agent · Markdown · {LABELS[reportType]}
        </span>
        <button
          onClick={handlePrintPDF}
          disabled={!md}
          className="text-[10px] text-brand-600 hover:text-brand-800 font-medium"
        >
          打印 / 导出 PDF
        </button>
      </div>
    </div>
  );
}
