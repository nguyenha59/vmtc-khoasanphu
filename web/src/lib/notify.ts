import { notifications } from "@mantine/notifications";

export const ok = (message: string, title?: string) => notifications.show({ color: "ok", title, message });
export const fail = (e: unknown, title = "Có lỗi") =>
  notifications.show({ color: "danger", title, message: e instanceof Error ? e.message : String(e) });

/** Gửi multipart/form-data (tải file). */
export async function postForm<T>(path: string, form: FormData): Promise<T> {
  const res = await fetch(path, { method: "POST", body: form, credentials: "same-origin" });
  const d = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof d.detail === "string" ? d.detail : `Lỗi ${res.status}`);
  return d as T;
}
