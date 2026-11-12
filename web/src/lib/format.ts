import dayjs from "dayjs";

export const fmtDateTime = (s: string | null | undefined) => (s ? dayjs(s).format("DD/MM HH:mm") : "—");
export const fmtDate = (s: string | null | undefined) => (s ? dayjs(s).format("DD/MM/YYYY") : "—");
export const fmtShort = (s: string) => dayjs(s).format("DD/MM");

export function relTime(s: string | null | undefined): string {
  if (!s) return "";
  const m = dayjs().diff(dayjs(s), "minute");
  if (m < 1) return "vừa xong";
  if (m < 60) return `${m} phút trước`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} giờ trước`;
  const d = Math.floor(h / 24);
  return d < 7 ? `${d} ngày trước` : dayjs(s).format("DD/MM");
}

export const VI_WEEKDAY = ["Chủ nhật", "Thứ hai", "Thứ ba", "Thứ tư", "Thứ năm", "Thứ sáu", "Thứ bảy"];
export const longToday = () => {
  const d = dayjs();
  return `${VI_WEEKDAY[d.day()]}, ${d.format("DD/MM/YYYY")}`;
};
