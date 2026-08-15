import { create } from "zustand";

function uid() {
  return Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
}

export interface WFNodeData {
  label: string;
  color: string;
  iconName: string;
  description: string;
  params?: Record<string, string | number>;
}

export interface WFNode {
  id: string;
  type: "pipeline";
  position: { x: number; y: number };
  data: WFNodeData;
}

export interface WFEdge {
  id: string;
  source: string;
  target: string;
  animated?: boolean;
  type?: string;
  style?: Record<string, unknown>;
}

export interface Workflow {
  id: string;
  name: string;
  nodes: WFNode[];
  edges: WFEdge[];
  demoPrompt?: string;
  createdAt: number;
  updatedAt: number;
}

interface WorkflowStore {
  workflows: Workflow[];
  activeId: string | null;
  activeWorkflow: () => Workflow | null;
  createWorkflow: (name?: string, nodes?: WFNode[], edges?: WFEdge[]) => string;
  deleteWorkflow: (id: string) => void;
  setActive: (id: string) => void;
  updateGraph: (id: string, nodes: WFNode[], edges: WFEdge[]) => void;
  renameWorkflow: (id: string, name: string) => void;
}

const STORAGE_KEY = "ezmanbo_workflows";

export const DEFAULT_WF_NODES: WFNode[] = [
  { id: "input",    type: "pipeline", position: { x: 40,   y: 180 }, data: { label: "用户需求输入",       iconName: "MessageSquare", color: "bg-slate-600",  description: "接收自然语言选型需求" } },
  { id: "parse",    type: "pipeline", position: { x: 260,  y: 80  }, data: { label: "Stage 1 约束解析",   iconName: "Brain",         color: "bg-blue-600",   description: "LLM 提取结构化参数",  params: { 模型: "claude-sonnet-5" } } },
  { id: "search",   type: "pipeline", position: { x: 480,  y: 80  }, data: { label: "Stage 2 eZ-PLM 检索",iconName: "Search",        color: "bg-teal-600",   description: "多前缀关键词并发检索", params: { 并发: 8, TTL: "24h" } } },
  { id: "score",    type: "pipeline", position: { x: 700,  y: 80  }, data: { label: "Stage 3 多维评分",   iconName: "BarChart2",     color: "bg-purple-600", description: "D1–D7 七维规则评分",  params: { 维度: 7 } } },
  { id: "evidence", type: "pipeline", position: { x: 920,  y: 20  }, data: { label: "Stage 4 证据链",     iconName: "FileText",      color: "bg-green-600",  description: "E1/E2/E3 来源标注" } },
  { id: "risk",     type: "pipeline", position: { x: 920,  y: 180 }, data: { label: "Stage 5 风险评估",   iconName: "Shield",        color: "bg-amber-600",  description: "G1–G6 安全约束门禁" } },
  { id: "critic",   type: "pipeline", position: { x: 700,  y: 280 }, data: { label: "Critic 自省验证",    iconName: "CheckCircle",   color: "bg-orange-600", description: "LangGraph 批评节点" } },
  { id: "dual",     type: "pipeline", position: { x: 700,  y: 400 }, data: { label: "双模型验证",         iconName: "AlertTriangle", color: "bg-red-600",    description: "双模型一致性验证", params: { 模型A: "claude", 模型B: "deepseek" } } },
  { id: "report",   type: "pipeline", position: { x: 1140, y: 120 }, data: { label: "Stage 6 报告生成",   iconName: "Cpu",           color: "bg-teal-700",   description: "BOM + 风险报告输出" } },
];

export const DEFAULT_WF_EDGES: WFEdge[] = [
  { id: "e1", source: "input",    target: "parse",    animated: true },
  { id: "e2", source: "parse",    target: "search",   animated: true },
  { id: "e3", source: "search",   target: "score",    animated: true },
  { id: "e4", source: "score",    target: "evidence" },
  { id: "e5", source: "score",    target: "risk" },
  { id: "e6", source: "score",    target: "critic" },
  { id: "e7", source: "critic",   target: "dual" },
  { id: "e8", source: "evidence", target: "report" },
  { id: "e9", source: "risk",     target: "report" },
  { id: "e10", source: "dual",   target: "report" },
];

const DEFAULT_WORKFLOW: Workflow = {
  id: "default",
  name: "eZmanbo 选型流水线",
  nodes: DEFAULT_WF_NODES,
  edges: DEFAULT_WF_EDGES,
  demoPrompt: "我需要选一个DC-DC Buck降压器件，输入12V，输出5V，最大电流2A，工业级，工作温度-40°C到85°C，优先高效率",
  createdAt: Date.now(),
  updatedAt: Date.now(),
};

const LDO_WORKFLOW: Workflow = {
  id: "preset_ldo",
  name: "LDO 线性稳压选型",
  demoPrompt: "我需要一个LDO稳压器，输入5V，输出3.3V，最大输出电流500mA，工业级（-40°C到85°C），优先SOT-23封装，需要低噪声、高PSRR",
  createdAt: Date.now(),
  updatedAt: Date.now(),
  nodes: [
    { id: "ldo_in",     type: "pipeline", position: { x: 40,  y: 120 }, data: { label: "选型需求输入",   iconName: "MessageSquare", color: "bg-slate-600",  description: "LDO规格自然语言描述" } },
    { id: "ldo_parse",  type: "pipeline", position: { x: 280, y: 120 }, data: { label: "参数结构化解析", iconName: "Brain",         color: "bg-blue-600",   description: "提取Vin/Vout/Iout/温度", params: { 模型: "claude-sonnet-5" } } },
    { id: "ldo_search", type: "pipeline", position: { x: 520, y: 120 }, data: { label: "eZPLM 数据检索", iconName: "Search",        color: "bg-teal-600",   description: "并发搜索LDO候选器件",   params: { 并发: 8 } } },
    { id: "ldo_score",  type: "pipeline", position: { x: 760, y: 120 }, data: { label: "多维评分",       iconName: "BarChart2",     color: "bg-purple-600", description: "D1-D7七维规则评分",     params: { 维度: 7 } } },
    { id: "ldo_risk",   type: "pipeline", position: { x: 760, y: 260 }, data: { label: "约束门禁验证",   iconName: "Shield",        color: "bg-amber-600",  description: "G1-G6安全约束检查" } },
    { id: "ldo_report", type: "pipeline", position: { x: 1000,y: 180 }, data: { label: "报告生成",       iconName: "FileText",      color: "bg-green-600",  description: "输出BOM与选型报告" } },
  ],
  edges: [
    { id: "l1", source: "ldo_in",     target: "ldo_parse",  animated: true },
    { id: "l2", source: "ldo_parse",  target: "ldo_search", animated: true },
    { id: "l3", source: "ldo_search", target: "ldo_score",  animated: true },
    { id: "l4", source: "ldo_score",  target: "ldo_risk" },
    { id: "l5", source: "ldo_score",  target: "ldo_report" },
    { id: "l6", source: "ldo_risk",   target: "ldo_report" },
  ],
};

const AUTOMOTIVE_WORKFLOW: Workflow = {
  id: "preset_auto",
  name: "车规 AEC-Q100 选型",
  demoPrompt: "车载BCM模块电源设计，需要AEC-Q100 Grade 1认证LDO，输入12V（±20%浮动），输出5V/500mA，工作温度-40°C到125°C，必须满足汽车级可靠性要求",
  createdAt: Date.now(),
  updatedAt: Date.now(),
  nodes: [
    { id: "av_in",     type: "pipeline", position: { x: 40,  y: 160 }, data: { label: "车规需求输入",   iconName: "MessageSquare", color: "bg-slate-600",  description: "含AEC-Q100约束描述" } },
    { id: "av_parse",  type: "pipeline", position: { x: 280, y: 80  }, data: { label: "车规约束解析",   iconName: "Brain",         color: "bg-blue-600",   description: "提取温度/认证级别/电气参数", params: { 模型: "claude-sonnet-5" } } },
    { id: "av_search", type: "pipeline", position: { x: 520, y: 80  }, data: { label: "eZPLM 检索",     iconName: "Search",        color: "bg-teal-600",   description: "过滤AEC-Q100候选器件" } },
    { id: "av_cert",   type: "pipeline", position: { x: 520, y: 240 }, data: { label: "认证等级验证",   iconName: "Shield",        color: "bg-amber-600",  description: "Grade 0/1/2/3 门禁" } },
    { id: "av_score",  type: "pipeline", position: { x: 760, y: 160 }, data: { label: "多维评分",       iconName: "BarChart2",     color: "bg-purple-600", description: "D1-D7，温度维度加权" } },
    { id: "av_dual",   type: "pipeline", position: { x: 760, y: 300 }, data: { label: "双模型交叉验证", iconName: "AlertTriangle", color: "bg-red-600",    description: "高风险场景双模型一致性校验", params: { 模型A: "claude", 模型B: "deepseek" } } },
    { id: "av_report", type: "pipeline", position: { x: 1000,y: 200 }, data: { label: "报告生成",       iconName: "FileText",      color: "bg-green-600",  description: "含车规合规性说明" } },
  ],
  edges: [
    { id: "a1", source: "av_in",     target: "av_parse",  animated: true },
    { id: "a2", source: "av_parse",  target: "av_search", animated: true },
    { id: "a3", source: "av_parse",  target: "av_cert" },
    { id: "a4", source: "av_search", target: "av_score",  animated: true },
    { id: "a5", source: "av_cert",   target: "av_score" },
    { id: "a6", source: "av_score",  target: "av_dual" },
    { id: "a7", source: "av_score",  target: "av_report" },
    { id: "a8", source: "av_dual",   target: "av_report" },
  ],
};

const REPLACEMENT_WORKFLOW: Workflow = {
  id: "preset_replace",
  name: "国产替代料搜索",
  demoPrompt: "需要替代TPS54360，寻找国产替代方案，Buck降压拓扑，48V宽压输入，输出3.3V/3.6A，要求国产品牌优先，价格实惠，可在工业环境使用",
  createdAt: Date.now(),
  updatedAt: Date.now(),
  nodes: [
    { id: "rp_in",      type: "pipeline", position: { x: 40,  y: 140 }, data: { label: "目标型号输入",   iconName: "MessageSquare", color: "bg-slate-600",  description: "输入待替代器件型号" } },
    { id: "rp_extract", type: "pipeline", position: { x: 280, y: 80  }, data: { label: "规格参数提取",   iconName: "Brain",         color: "bg-blue-600",   description: "从型号提取电气规格" } },
    { id: "rp_search",  type: "pipeline", position: { x: 520, y: 80  }, data: { label: "国产替代检索",   iconName: "Search",        color: "bg-teal-600",   description: "按国产优先策略检索", params: { 策略: "domestic_first" } } },
    { id: "rp_compat",  type: "pipeline", position: { x: 520, y: 240 }, data: { label: "引脚兼容性评估", iconName: "Filter",        color: "bg-pink-600",   description: "封装/引脚Drop-in评估" } },
    { id: "rp_cost",    type: "pipeline", position: { x: 760, y: 160 }, data: { label: "成本对比",       iconName: "BarChart2",     color: "bg-purple-600", description: "原料vs替代料价格分析" } },
    { id: "rp_report",  type: "pipeline", position: { x: 1000,y: 160 }, data: { label: "替代方案报告",   iconName: "FileText",      color: "bg-green-600",  description: "输出替代料BOM清单" } },
  ],
  edges: [
    { id: "r1", source: "rp_in",      target: "rp_extract", animated: true },
    { id: "r2", source: "rp_extract", target: "rp_search",  animated: true },
    { id: "r3", source: "rp_extract", target: "rp_compat" },
    { id: "r4", source: "rp_search",  target: "rp_cost",    animated: true },
    { id: "r5", source: "rp_compat",  target: "rp_cost" },
    { id: "r6", source: "rp_cost",    target: "rp_report" },
  ],
};

function load(): Workflow[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch { return []; }
}

function save(workflows: Workflow[]) {
  if (typeof window === "undefined") return;
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(workflows)); } catch { /* quota */ }
}

const _initial = load();
const initialWorkflows = _initial.length > 0 ? _initial : [DEFAULT_WORKFLOW, LDO_WORKFLOW, AUTOMOTIVE_WORKFLOW, REPLACEMENT_WORKFLOW];

export const useWorkflowStore = create<WorkflowStore>((set, get) => ({
  workflows: initialWorkflows,
  activeId: initialWorkflows[0].id,

  activeWorkflow: () => {
    const { workflows, activeId } = get();
    return workflows.find(w => w.id === activeId) ?? null;
  },

  createWorkflow: (name = "新工作流", nodes = [], edges = []) => {
    const id = uid();
    const wf: Workflow = { id, name, nodes, edges, createdAt: Date.now(), updatedAt: Date.now() };
    set(s => { const u = [...s.workflows, wf]; save(u); return { workflows: u, activeId: id }; });
    return id;
  },

  deleteWorkflow: (id) => set(s => {
    let updated = s.workflows.filter(w => w.id !== id);
    if (updated.length === 0) { updated = [{ ...DEFAULT_WORKFLOW, id: uid() }]; }
    save(updated);
    return { workflows: updated, activeId: s.activeId === id ? updated[0].id : s.activeId };
  }),

  setActive: (id) => set({ activeId: id }),

  updateGraph: (id, nodes, edges) => set(s => {
    const u = s.workflows.map(w => w.id === id ? { ...w, nodes, edges, updatedAt: Date.now() } : w);
    save(u);
    return { workflows: u };
  }),

  renameWorkflow: (id, name) => set(s => {
    const u = s.workflows.map(w => w.id === id ? { ...w, name } : w);
    save(u);
    return { workflows: u };
  }),
}));
