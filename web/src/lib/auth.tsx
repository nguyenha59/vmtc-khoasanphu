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

/**
 * Đăng xuất: thu hồi phiên trên máy chủ, rồi về màn đăng nhập NGAY và xóa dữ liệu bệnh nhân đang lưu tạm trên trình duyệt.
 *
 * Lỗi cũ: gọi qc.clear() trước setQueryData(["me"], null) → clear() gỡ luôn truy vấn "me" mà giao diện đang theo dõi,
 * nên giao diện vẫn giữ người dùng cũ (màn hình còn dữ liệu) cho tới khi tải lại trang.
 */
export function useLogout() {
  const qc = useQueryClient();
  return async () => {
    let serverOk = true;
    try {
      await api("/api/auth/logout", { method: "POST" });
    } catch {
      serverOk = false; // mất mạng / máy chủ lỗi: vẫn rời phiên trên máy này, báo để người dùng biết
    }
    await qc.cancelQueries();
    qc.setQueryData(["me"], null); // giao diện chuyển ngay sang màn đăng nhập
    qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "me" }); // bỏ dữ liệu bệnh nhân còn trong bộ nhớ đệm
    if (!serverOk) {
      const { notifications } = await import("@mantine/notifications");
      notifications.show({
        color: "warn", title: "Đã rời phiên trên máy này",
        message: "Không liên lạc được máy chủ để hủy phiên – hãy đóng trình duyệt nếu dùng máy chung.",
      });
    }
  };
}
