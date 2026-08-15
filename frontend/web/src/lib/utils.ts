import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatElapsed(seconds: number): string {
  if (seconds < 1) return `${(seconds * 1000).toFixed(0)}ms`;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${s.toFixed(0)}s`;
}

export const RISK_COLORS: Record<string, string> = {
  high:   "text-risk-high bg-red-50 border-risk-high",
  medium: "text-risk-medium bg-orange-50 border-risk-medium",
  low:    "text-risk-low bg-green-50 border-risk-low",
};

export const RISK_LABELS: Record<string, string> = {
  high:   "HIGH",
  medium: "MEDIUM",
  low:    "LOW",
};

export const LLM_MODEL = "eZmanbo AI";

// ── 选型上下文构建 ────────────────────────────────────────────

/** 从 AnalysisReport 提取精炼的选型上下文文本，供 init_session 注入后端 */
export function buildSelectionContext(report: {
  constraints: {
    topology?: string;
    input_voltage_nominal_v?: number;
    output_voltage_v?: number;
    output_current_a?: number;
    temperature_min_c?: number;
    temperature_max_c?: number;
    grade?: string;
  };
  recommended_parts?: Array<{
    part: { part_number: string; manufacturer?: string };
    score: { total_score: number };
  }>;
}): string {
  const c = report.constraints || {};
  const lines: string[] = [];

  if (c.topology) lines.push(`拓扑: ${c.topology}`);
  if (c.input_voltage_nominal_v !== undefined) lines.push(`输入: ${c.input_voltage_nominal_v}V`);
  if (c.output_voltage_v !== undefined) lines.push(`输出: ${c.output_voltage_v}V`);
  if (c.output_current_a !== undefined) lines.push(`电流: ${c.output_current_a}A`);
  if (c.temperature_min_c !== undefined && c.temperature_max_c !== undefined) {
    lines.push(`温度: ${c.temperature_min_c}~${c.temperature_max_c}°C`);
  }
  if (c.grade) lines.push(`等级: ${c.grade}`);

  const top = (report.recommended_parts || []).slice(0, 3);
  if (top.length > 0) {
    lines.push("推荐器件:");
    top.forEach((sp, i) => {
      const pn = sp.part.part_number || "?";
      const mfr = sp.part.manufacturer || "?";
      const score = Math.round(sp.score.total_score || 0);
      lines.push(`  #${i + 1}: ${pn} (${mfr}) - ${score}分`);
    });
  }

  return lines.join("\n");
}
