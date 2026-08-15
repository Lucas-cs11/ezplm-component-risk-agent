"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Image from "next/image";
import { useAuthStore } from "@/store/authStore";

type Tab = "llm" | "ezplm" | "users" | "rag";

const PROVIDERS: Record<string, { name: string; base_url: string; models: string[]; default_model: string }> = {
  manbou:   { name: "Manbou API（推荐）", base_url: "https://www.manbouapi.com/v1",  models: ["claude-sonnet-5", "claude-opus-5", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"], default_model: "claude-sonnet-5" },
  deepseek: { name: "DeepSeek 官方",      base_url: "https://api.deepseek.com/v1",   models: ["deepseek-chat", "deepseek-reasoner"],                                               default_model: "deepseek-chat" },
  openai:   { name: "OpenAI",             base_url: "https://api.openai.com/v1",     models: ["gpt-4o", "gpt-4o-mini"],              default_model: "gpt-4o-mini" },
  custom:   { name: "自定义",              base_url: "",                              models: [],                                     default_model: "" },
};

interface User { id: number; username: string; email: string; is_admin: boolean; is_active: boolean; }

export default function SetupPage() {
  const { user, getAuthHeaders } = useAuthStore();
  const router = useRouter();
  const [tab, setTab] = useState<Tab>("llm");

  // LLM state
  const [provider, setProvider] = useState("manbou");
  const [llmKey, setLlmKey] = useState("");
  const [baseUrl, setBaseUrl] = useState(PROVIDERS.manbou.base_url);
  const [model, setModel] = useState(PROVIDERS.manbou.default_model);
  const [customModel, setCustomModel] = useState("");

  // ezPLM state
  const [ezplmKey, setEzplmKey] = useState("");

  // UI state
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ type: "ok" | "err"; text: string } | null>(null);
  const [users, setUsers] = useState<User[]>([]);

  // RAG 知识库 state
  const [ragCount, setRagCount] = useState<number | null>(null);
  const [ragDocs, setRagDocs] = useState<{id:string;title:string;category:string;preview:string}[]>([]);
  const [ragTitle, setRagTitle] = useState("");
  const [ragCategory, setRagCategory] = useState("general");
  const [ragContent, setRagContent] = useState("");
  const [ragUploading, setRagUploading] = useState(false);

  const headers = { "Content-Type": "application/json", ...getAuthHeaders() };

  useEffect(() => {
    if (!user?.is_admin) { router.replace("/"); return; }
    fetch("/admin/config", { headers: getAuthHeaders() })
      .then((r) => r.json())
      .then((d) => {
        if (d.llm_provider) setProvider(d.llm_provider);
        if (d.llm_base_url) setBaseUrl(d.llm_base_url);
        if (d.llm_model)    setModel(d.llm_model);
      })
      .catch(() => {});
  }, [user, router]);

  useEffect(() => {
    if (tab === "users") {
      fetch("/admin/users", { headers: getAuthHeaders() })
        .then((r) => r.json())
        .then((d) => setUsers(d.users || []))
        .catch(() => {});
    }
    if (tab === "rag") {
      fetch("/admin/rag/status", { headers: getAuthHeaders() }).then(r => r.json()).then(d => setRagCount(d.count ?? 0)).catch(() => {});
      fetch("/admin/rag/docs",   { headers: getAuthHeaders() }).then(r => r.json()).then(d => setRagDocs(d.docs || [])).catch(() => {});
    }
  }, [tab]);

  useEffect(() => {
    const p = PROVIDERS[provider];
    if (p) {
      setBaseUrl(p.base_url);
      if (p.models.length > 0) setModel(p.models[0]);
      else setModel("");
    }
  }, [provider]);

  const flash = (type: "ok" | "err", text: string) => {
    setMsg({ type, text });
    setTimeout(() => setMsg(null), 3000);
  };

  const saveLlm = async () => {
    setSaving(true);
    const finalModel = model === "custom" ? customModel : model;
    const body: Record<string, string> = { llm_provider: provider, llm_base_url: baseUrl, llm_model: finalModel };
    if (llmKey) body.llm_api_key = llmKey;
    const r = await fetch("/admin/config", { method: "POST", headers, body: JSON.stringify(body) });
    flash(r.ok ? "ok" : "err", r.ok ? "LLM 配置已保存" : "保存失败");
    setSaving(false);
  };

  const saveEzplm = async () => {
    if (!ezplmKey) return;
    setSaving(true);
    const r = await fetch("/admin/config", {
      method: "POST",
      headers,
      body: JSON.stringify({ ezplm_api_key: ezplmKey }),
    });
    flash(r.ok ? "ok" : "err", r.ok ? "eZ-PLM 配置已保存" : "保存失败");
    setSaving(false);
  };

  const toggleUser = async (uid: number) => {
    const r = await fetch(`/admin/users/${uid}/toggle`, { method: "POST", headers: getAuthHeaders() });
    if (r.ok) {
      const d = await r.json();
      setUsers((prev) => prev.map((u) => (u.id === uid ? { ...u, is_active: d.is_active } : u)));
    }
  };

  const ragUpload = async () => {
    if (!ragContent.trim()) return;
    setRagUploading(true);
    try {
      const r = await fetch("/admin/rag/upload", {
        method: "POST", headers,
        body: JSON.stringify({ title: ragTitle || "未命名文档", content: ragContent, category: ragCategory }),
      });
      const d = await r.json();
      if (r.ok) {
        setRagCount(d.count); setRagContent(""); setRagTitle("");
        flash("ok", `已上传，知识库共 ${d.count} 条片段`);
        fetch("/admin/rag/docs", { headers: getAuthHeaders() }).then(rr => rr.json()).then(dd => setRagDocs(dd.docs || []));
      } else flash("err", "上传失败：" + (d.detail || "未知错误"));
    } catch { flash("err", "网络错误"); }
    finally { setRagUploading(false); }
  };

  const ragClear = async () => {
    if (!confirm("确定清空全部知识库？此操作不可逆。")) return;
    const r = await fetch("/admin/rag/clear", { method: "DELETE", headers });
    if (r.ok) { setRagCount(0); setRagDocs([]); flash("ok", "知识库已清空"); }
  };

  const TABS: { id: Tab; label: string }[] = [
    { id: "llm", label: "LLM 配置" },
    { id: "ezplm", label: "eZ-PLM" },
    { id: "users", label: "用户管理" },
    { id: "rag", label: "知识库" },
  ];

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-2xl mx-auto py-10 px-4">
        <div className="flex items-center justify-between mb-8">
          <div className="flex items-center gap-3">
            <Image src="/logo.svg" alt="eZmanbo" width={100} height={50} className="h-auto" />
            <div>
              <h1 className="text-lg font-semibold text-gray-800">系统设置</h1>
              <p className="text-xs text-gray-400">仅管理员可访问</p>
            </div>
          </div>
          <button onClick={() => router.push("/")}
            className="text-sm text-gray-500 hover:text-gray-700 px-3 py-1.5 rounded-lg hover:bg-gray-100 transition-colors">
            ← 返回
          </button>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 mb-6 bg-gray-100 p-1 rounded-xl">
          {TABS.map((t) => (
            <button key={t.id} onClick={() => setTab(t.id)}
              className={`flex-1 py-2 text-sm font-medium rounded-lg transition-colors ${
                tab === t.id ? "bg-white text-gray-800 shadow-sm" : "text-gray-500 hover:text-gray-700"
              }`}>
              {t.label}
            </button>
          ))}
        </div>

        {msg && (
          <div className={`mb-4 p-3 rounded-lg text-sm ${
            msg.type === "ok" ? "bg-green-50 text-green-700 border border-green-200" : "bg-red-50 text-red-600 border border-red-200"
          }`}>
            {msg.text}
          </div>
        )}

        <div className="bg-white rounded-2xl border border-gray-200 p-6">
          {tab === "llm" && (
            <div className="space-y-5">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">LLM 提供商</label>
                <div className="grid grid-cols-2 gap-2">
                  {Object.entries(PROVIDERS).map(([id, p]) => (
                    <button key={id} onClick={() => setProvider(id)}
                      className={`p-3 rounded-xl border-2 text-sm font-medium transition-colors text-left ${
                        provider === id ? "border-blue-500 bg-blue-50 text-blue-700" : "border-gray-200 text-gray-600 hover:border-gray-300"
                      }`}>
                      {p.name}
                    </button>
                  ))}
                </div>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">API Key</label>
                <input type="password" value={llmKey} onChange={(e) => setLlmKey(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
                  placeholder="留空保留现有 Key" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Base URL</label>
                <input type="text" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">模型</label>
                <select value={model} onChange={(e) => setModel(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white">
                  {(PROVIDERS[provider]?.models || []).map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                  <option value="custom">其他（手动输入）</option>
                </select>
                {model === "custom" && (
                  <input type="text" value={customModel} onChange={(e) => setCustomModel(e.target.value)}
                    className="w-full mt-2 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    placeholder="模型名称，如 deepseek-chat" />
                )}
              </div>
              <button onClick={saveLlm} disabled={saving}
                className="w-full py-2.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors">
                {saving ? "保存中…" : "保存 LLM 配置"}
              </button>
            </div>
          )}

          {tab === "ezplm" && (
            <div className="space-y-5">
              <p className="text-sm text-gray-500">服务地址固定为 <code className="bg-gray-100 px-1 rounded">https://www.ezplm.cn</code>，只需配置 API Key。</p>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">eZ-PLM API Key</label>
                <input type="password" value={ezplmKey} onChange={(e) => setEzplmKey(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
                  placeholder="epk_..." />
              </div>
              <button onClick={saveEzplm} disabled={!ezplmKey || saving}
                className="w-full py-2.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors">
                {saving ? "保存中…" : "保存 eZ-PLM 配置"}
              </button>
            </div>
          )}

          {tab === "users" && (
            <div>
              <h3 className="text-sm font-medium text-gray-700 mb-4">已注册用户（{users.length}）</h3>
              <div className="space-y-2">
                {users.map((u) => (
                  <div key={u.id} className="flex items-center gap-3 p-3 bg-gray-50 rounded-xl">
                    <div className="w-8 h-8 rounded-full bg-blue-100 flex items-center justify-center text-sm font-semibold text-blue-600">
                      {u.username[0].toUpperCase()}
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-gray-800 truncate">{u.username}</p>
                      <p className="text-xs text-gray-400 truncate">{u.email} {u.is_admin && "· 管理员"}</p>
                    </div>
                    <button onClick={() => toggleUser(u.id)} disabled={u.is_admin}
                      className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors ${
                        u.is_active
                          ? "bg-red-50 text-red-600 hover:bg-red-100"
                          : "bg-green-50 text-green-600 hover:bg-green-100"
                      } disabled:opacity-40`}>
                      {u.is_active ? "禁用" : "启用"}
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {tab === "rag" && (
            <div className="space-y-5">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="text-sm font-medium text-gray-700">工程知识库</h3>
                  <p className="text-xs text-gray-400 mt-0.5">
                    {ragCount === null ? "加载中…" : `已索引 ${ragCount} 条文档片段`}
                  </p>
                </div>
                <button onClick={ragClear}
                  className="text-xs text-red-500 hover:text-red-700 px-2 py-1 rounded border border-red-200 hover:bg-red-50 transition-colors">
                  清空全部
                </button>
              </div>

              <div className="space-y-3 border border-dashed border-gray-200 rounded-xl p-4">
                <p className="text-xs font-medium text-gray-600">上传新文档</p>
                <input value={ragTitle} onChange={e => setRagTitle(e.target.value)}
                  placeholder="文档标题（如：TPS54360 应用笔记）"
                  className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
                <select value={ragCategory} onChange={e => setRagCategory(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500">
                  <option value="general">通用知识</option>
                  <option value="buck_design">降压设计规范</option>
                  <option value="ldo_design">线性稳压设计</option>
                  <option value="thermal">热管理</option>
                  <option value="emc">EMC 规范</option>
                  <option value="automotive">车规标准</option>
                </select>
                <textarea value={ragContent} onChange={e => setRagContent(e.target.value)} rows={6}
                  placeholder="粘贴文档内容（应用笔记、设计规范、工程经验总结…）"
                  className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm font-mono resize-y focus:outline-none focus:ring-2 focus:ring-blue-500" />
                <button onClick={ragUpload} disabled={!ragContent.trim() || ragUploading}
                  className="w-full py-2.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors">
                  {ragUploading ? "上传中…" : "上传到知识库"}
                </button>
              </div>

              {ragDocs.length > 0 && (
                <div>
                  <p className="text-xs font-medium text-gray-600 mb-2">已索引文档（{ragDocs.length} 条）</p>
                  <div className="space-y-1.5 max-h-52 overflow-y-auto pr-1">
                    {ragDocs.map(doc => (
                      <div key={doc.id} className="flex items-start gap-2 p-2.5 bg-gray-50 rounded-lg text-xs">
                        <div className="flex-1 min-w-0">
                          <p className="font-medium text-gray-700 truncate">{doc.title}</p>
                          <p className="text-gray-400 mt-0.5 truncate">{doc.preview}</p>
                        </div>
                        {doc.category && (
                          <span className="shrink-0 px-1.5 py-0.5 bg-blue-50 text-blue-600 rounded text-[10px]">
                            {doc.category}
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
