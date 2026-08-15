"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";

const FIELD_CONFIG: Record<string, {
  label: string; placeholder: string; unit: string; type: "number" | "text" | "select";
  options?: { value: string; label: string }[];
}> = {
  input_voltage_nominal_v: { label: "输入电压", placeholder: "如 12、5、3.7", unit: "V", type: "number" },
  output_voltage_v:        { label: "输出电压", placeholder: "如 3.3、5", unit: "V", type: "number" },
  output_current_a:        { label: "输出电流", placeholder: "如 2、0.5（500mA）", unit: "A", type: "number" },
  temperature_min_c:       { label: "最低工作温度", placeholder: "如 -40、0", unit: "°C", type: "number" },
  temperature_max_c:       { label: "最高工作温度", placeholder: "如 85、125", unit: "°C", type: "number" },
  grade: {
    label: "应用等级", placeholder: "", unit: "", type: "select",
    options: [
      { value: "commercial",  label: "商业级（0~70°C）" },
      { value: "industrial",  label: "工业级（−40~85°C）" },
      { value: "automotive",  label: "车规级 AEC-Q100" },
    ],
  },
};

interface Props {
  missingFields: string[];
  accumulated: Record<string, unknown>;
  onSubmit: (message: string) => void;
}

export function ParameterForm({ missingFields, accumulated, onSubmit }: Props) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [submitted, setSubmitted] = useState(false);

  const validFields = missingFields.filter(f => FIELD_CONFIG[f]);
  if (validFields.length === 0 || submitted) return null;

  const handleSubmit = () => {
    const parts: string[] = [];
    for (const field of validFields) {
      const v = values[field]?.trim();
      if (!v) continue;
      const cfg = FIELD_CONFIG[field];
      if (cfg.type === "select") {
        const opt = cfg.options?.find(o => o.value === v);
        parts.push(`${cfg.label}：${opt?.label ?? v}`);
      } else {
        parts.push(`${cfg.label}：${v}${cfg.unit}`);
      }
    }
    if (parts.length === 0) return;
    setSubmitted(true);
    onSubmit(parts.join("，"));
  };

  return (
    <div className="mt-3 border border-blue-200 rounded-xl bg-blue-50 overflow-hidden">
      <div className="px-4 py-2 bg-blue-100 border-b border-blue-200 flex items-center gap-2">
        <div className="w-1.5 h-1.5 rounded-full bg-blue-500" />
        <span className="text-xs font-semibold text-blue-800">填写参数</span>
        <span className="text-2xs text-blue-500 ml-1">可跳过不确定项，直接提交已知值</span>
      </div>
      <div className="p-4 grid grid-cols-1 gap-3">
        {validFields.map(field => {
          const cfg = FIELD_CONFIG[field];
          const alreadyKnown = accumulated[field] != null;
          return (
            <div key={field} className={cn("flex items-center gap-3", alreadyKnown && "opacity-50")}>
              <label className="text-xs font-medium text-gray-700 w-24 shrink-0">
                {cfg.label}
                {alreadyKnown && <span className="ml-1 text-2xs text-green-600">✓已知</span>}
              </label>
              {cfg.type === "select" ? (
                <select
                  value={values[field] ?? ""}
                  onChange={e => setValues(v => ({ ...v, [field]: e.target.value }))}
                  disabled={alreadyKnown}
                  className="flex-1 h-8 px-2 text-xs border border-gray-200 rounded-lg bg-white focus:border-blue-400 focus:outline-none"
                >
                  <option value="">请选择…</option>
                  {cfg.options?.map(o => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
              ) : (
                <div className="flex-1 flex items-center border border-gray-200 rounded-lg bg-white overflow-hidden focus-within:border-blue-400">
                  <input
                    type="number"
                    step="any"
                    placeholder={cfg.placeholder}
                    value={values[field] ?? ""}
                    onChange={e => setValues(v => ({ ...v, [field]: e.target.value }))}
                    disabled={alreadyKnown}
                    className="flex-1 h-8 px-3 text-xs bg-transparent focus:outline-none"
                  />
                  {cfg.unit && (
                    <span className="px-2 text-xs text-gray-400 bg-gray-50 border-l border-gray-200 h-full flex items-center">
                      {cfg.unit}
                    </span>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
      <div className="px-4 pb-4 flex justify-end gap-2">
        <button onClick={() => setSubmitted(true)}
          className="text-xs px-3 py-1.5 border border-gray-200 rounded-lg text-gray-500 hover:bg-gray-50">
          跳过
        </button>
        <button onClick={handleSubmit}
          className="text-xs px-4 py-1.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-medium transition-colors">
          确认提交
        </button>
      </div>
    </div>
  );
}
