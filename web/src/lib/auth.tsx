import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, type Me } from "./api";

export const ROLE_LABELS: Record<string, string> = { nurse: "Điều dưỡng", doctor: "Bác sĩ", admin: "Quản trị" };

export function useMe() {
  return useQuery<Me | null>({
    queryKey: ["me"],
    queryFn: async () => {
      try {
        return await api<Me>("/api/auth/me");
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null;
        throw e;
      }
    },
    staleTime: 60_000,
    retry: false,
    // Trang form thai phụ không đăng nhập – máy chủ công khai không có /api/auth.
    enabled: window.location.pathname !== "/form",
  });
}

export function useCan() {
  const { data } = useMe();
  return (action: string) => !!data?.permissions.includes(action);
}

export function useLogout() {
  const qc = useQueryClient();
  return async () => {
    await api("/api/auth/logout", { method: "POST" });
    qc.clear();
    qc.setQueryData(["me"], null);
  };
}
