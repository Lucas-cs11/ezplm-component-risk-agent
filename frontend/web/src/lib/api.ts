import { useAuthStore } from "@/store/authStore";

export const API = process.env.NEXT_PUBLIC_API_BASE ?? "";

export function getApiHeaders(): Record<string, string> {
  const token = useAuthStore.getState().token;
  return {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

export function getAuthBearer(): Record<string, string> {
  const token = useAuthStore.getState().token;
  return token ? { Authorization: `Bearer ${token}` } : {};
}
