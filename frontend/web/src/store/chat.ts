import { create } from "zustand";
import type { Session, ChatMessage } from "@/types";
import type { ScoredPart } from "@/types";
import { v4like } from "./id";

export { v4like as generateId } from "./id";

const STORAGE_KEY = "ezmanbo_sessions";
const MAX_SESSIONS = 50;

export type HealthStatus = "connected" | "disconnected" | "checking";
export type ThinkingDepth = "fast" | "standard" | "deep";

export interface ToolCallEvent {
  id: string;
  type: string;
  label: string;
  ts: number;
  data?: Record<string, unknown>;
}

interface ChatStore {
  sessions: Session[];
  activeSessionId: string | null;
  activeSession: () => Session | undefined;
  healthStatus: HealthStatus;
  setHealthStatus: (status: HealthStatus) => void;
  selectedPartNumber: string | null;
  setSelectedPart: (partNumber: string | null) => void;
  pendingInput: string | null;
  setPendingInput: (input: string | null) => void;
  // Thinking depth
  thinkingDepth: ThinkingDepth;
  setThinkingDepth: (d: ThinkingDepth) => void;
  // Tool call event log (cleared on new send)
  toolCallEvents: ToolCallEvent[];
  pushToolCallEvent: (evt: Omit<ToolCallEvent, "id" | "ts">) => void;
  clearToolCallEvents: () => void;
  // Selected part for result modal
  selectedPartModal: ScoredPart | null;
  setSelectedPartModal: (p: ScoredPart | null) => void;

  createSession: () => string;
  switchSession: (id: string) => void;
  deleteSession: (id: string) => void;

  addMessage: (msg: ChatMessage) => void;
  updateMessage: (id: string, patch: Partial<ChatMessage>) => void;
  setStreaming: (id: string, streaming: boolean) => void;
  setSessionTitle: (id: string, title: string) => void;
  syncSessionsFromBackend: () => Promise<void>;
}

// ── Persistence ────────────────────────────────────────────
function loadSessions(): Session[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch { return []; }
}

function saveSessions(sessions: Session[]) {
  if (typeof window === "undefined") return;
  try {
    const trimmed = sessions.slice(0, MAX_SESSIONS);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(trimmed));
  } catch { /* quota exceeded */ }
}

function createEmptySession(): Session {
  return {
    id: v4like(),
    title: "新的选型",
    messages: [],
    createdAt: Date.now(),
    updatedAt: Date.now(),
  };
}

export const useChatStore = create<ChatStore>()((set, get) => ({
  sessions: loadSessions(),
  activeSessionId: null,
  healthStatus: "checking" as HealthStatus,
  selectedPartNumber: null,
  pendingInput: null,
  thinkingDepth: "standard" as ThinkingDepth,
  toolCallEvents: [],
  selectedPartModal: null,

  activeSession: () => {
    const { sessions, activeSessionId } = get();
    return sessions.find((s) => s.id === activeSessionId);
  },

  setHealthStatus: (status) => set({ healthStatus: status }),
  setSelectedPart: (partNumber) => set({ selectedPartNumber: partNumber }),
  setPendingInput: (input) => set({ pendingInput: input }),
  setThinkingDepth: (d) => set({ thinkingDepth: d }),
  pushToolCallEvent: (evt) => set((s) => ({
    toolCallEvents: [...s.toolCallEvents, { ...evt, id: v4like(), ts: Date.now() }].slice(-100),
  })),
  clearToolCallEvents: () => set({ toolCallEvents: [] }),
  setSelectedPartModal: (p) => set({ selectedPartModal: p }),

  createSession: () => {
    const session = createEmptySession();
    set((s) => {
      const updated = { sessions: [session, ...s.sessions], activeSessionId: session.id };
      saveSessions(updated.sessions);
      return updated;
    });
    return session.id;
  },

  switchSession: (id) => set({ activeSessionId: id }),

  deleteSession: (id) => {
    set((s) => {
      const filtered = s.sessions.filter((ss) => ss.id !== id);
      const updated = {
        sessions: filtered,
        activeSessionId: s.activeSessionId === id ? (filtered[0]?.id ?? null) : s.activeSessionId,
      };
      saveSessions(updated.sessions);
      return updated;
    });
  },

  addMessage: (msg) =>
    set((s) => {
      const updated = {
        sessions: s.sessions.map((ss) => {
          if (ss.id !== s.activeSessionId) return ss;
          // 第一句话不设为标题，统一用"新的选型"
          const title = ss.messages.length === 0 ? "新的选型" : ss.title;
          return { ...ss, messages: [...ss.messages, msg], title, updatedAt: Date.now() };
        }),
      };
      saveSessions(updated.sessions);
      return updated;
    }),

  setSessionTitle: (id: string, title: string) =>
    set((s) => {
      const updated = {
        sessions: s.sessions.map((ss) =>
          ss.id === id ? { ...ss, title, updatedAt: Date.now() } : ss
        ),
      };
      saveSessions(updated.sessions);
      return updated;
    }),

  updateMessage: (id, patch) =>
    set((s) => {
      const updated = {
        sessions: s.sessions.map((ss) => {
          if (ss.id !== s.activeSessionId) return ss;
          return {
            ...ss,
            messages: ss.messages.map((m) => (m.id === id ? { ...m, ...patch } : m)),
            updatedAt: Date.now(),
          };
        }),
      };
      saveSessions(updated.sessions);
      return updated;
    }),

  setStreaming: (id, v) =>
    set((s) => ({
      sessions: s.sessions.map((ss) => {
        if (ss.id !== s.activeSessionId) return ss;
        return {
          ...ss,
          messages: ss.messages.map((m) => (m.id === id ? { ...m, isStreaming: v } : m)),
        };
      }),
    })),

  // ── 后端会话同步 ──────────────────────────────────────
  syncSessionsFromBackend: async () => {
    const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "";
    try {
      const { useAuthStore } = await import("@/store/authStore");
      const token = useAuthStore.getState().token;
      const resp = await fetch(`${API_BASE}/agent/sessions`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!resp.ok) return;
      const data = await resp.json();
      const backendSessions: Array<{ id: string; title: string; message_count: number }> = data.sessions || [];

      set((s) => {
        const localMap = new Map(s.sessions.map((ls) => [ls.id, ls]));
        const existingIds = new Set(s.sessions.map((ls) => ls.id));
        let changed = false;
        const mergedSessions = [...s.sessions];

        for (const bs of backendSessions) {
          if (existingIds.has(bs.id)) {
            // 已有本地会话 → 仅更新元数据（标题、时间戳），不覆盖消息
            const idx = mergedSessions.findIndex((ls) => ls.id === bs.id);
            if (idx >= 0) {
              const local = mergedSessions[idx];
              // 仅当本地标题为默认且后端有实质标题时更新
              if (local.title === "新的选型" && bs.title && bs.title !== "新的对话") {
                mergedSessions[idx] = { ...local, title: bs.title, updatedAt: Date.now() };
                changed = true;
              }
            }
          } else {
            // 后端有但本地没有 → 创建占位 session（消息为空）
            const placeholder: Session = {
              id: bs.id,
              title: bs.title || "新的选型",
              messages: [],
              createdAt: Date.now(),
              updatedAt: Date.now(),
            };
            mergedSessions.unshift(placeholder);
            existingIds.add(bs.id);
            changed = true;
          }
        }

        if (changed) {
          saveSessions(mergedSessions);
          return { sessions: mergedSessions };
        }
        return {};
      });
    } catch {
      // 静默失败，后端不可用不影响本地体验
    }
  },
}));
