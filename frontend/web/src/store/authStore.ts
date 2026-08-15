import { create } from "zustand";

export interface AuthUser {
  id: number;
  username: string;
  email: string;
  is_admin: boolean;
  is_guest?: boolean;
  dual_model_enabled?: boolean;
}

export const GUEST_MESSAGE_LIMIT = 5;

interface AuthStore {
  token: string | null;
  user: AuthUser | null;
  guestMessageCount: number;
  login: (token: string, user: AuthUser) => void;
  logout: () => void;
  setUser: (user: AuthUser) => void;
  isAuthenticated: () => boolean;
  getAuthHeaders: () => Record<string, string>;
  incrementGuestCount: () => void;
  canSendAsGuest: () => boolean;
  remainingGuestMessages: () => number;
}

function loadToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("ezmanbo_token");
}

function loadUser(): AuthUser | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem("ezmanbo_user");
    return raw ? JSON.parse(raw) : null;
  } catch { return null; }
}

function loadGuestCount(): number {
  if (typeof window === "undefined") return 0;
  const raw = localStorage.getItem("ezmanbo_guest_count");
  return raw ? parseInt(raw, 10) : 0;
}

export const useAuthStore = create<AuthStore>()((set, get) => ({
  token: loadToken(),
  user: loadUser(),
  guestMessageCount: loadGuestCount(),

  login: (token, user) => {
    localStorage.setItem("ezmanbo_token", token);
    localStorage.setItem("ezmanbo_user", JSON.stringify(user));
    if (!user.is_guest) localStorage.removeItem("ezmanbo_guest_count");
    set({ token, user, guestMessageCount: 0 });
  },

  setUser: (user) => {
    localStorage.setItem("ezmanbo_user", JSON.stringify(user));
    set({ user });
  },

  logout: () => {
    localStorage.removeItem("ezmanbo_token");
    localStorage.removeItem("ezmanbo_user");
    localStorage.removeItem("ezmanbo_guest_count");
    set({ token: null, user: null, guestMessageCount: 0 });
  },

  isAuthenticated: () => !!get().token,

  getAuthHeaders: (): Record<string, string> => {
    const token = get().token;
    return token ? { Authorization: `Bearer ${token}` } : {};
  },

  incrementGuestCount: () => {
    const next = get().guestMessageCount + 1;
    localStorage.setItem("ezmanbo_guest_count", String(next));
    set({ guestMessageCount: next });
  },

  canSendAsGuest: () => {
    const { user, guestMessageCount } = get();
    if (!user?.is_guest) return true;
    return guestMessageCount < GUEST_MESSAGE_LIMIT;
  },

  remainingGuestMessages: () => {
    const { user, guestMessageCount } = get();
    if (!user?.is_guest) return Infinity;
    return Math.max(0, GUEST_MESSAGE_LIMIT - guestMessageCount);
  },
}));
