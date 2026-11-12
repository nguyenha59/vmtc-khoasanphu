/**
 * Lý do cảnh báo → nhãn ngắn, dễ đọc cho điều dưỡng.
 *
 * Máy chủ trả lý do dạng kỹ thuật, ví dụ:
 *   "Xu hướng: EWMA 139 vượt nền 120 + 2σ (128)", "AI: Rối loạn thị giác", "Huyết áp 154/91 ≥ 140/90",
 *   "Đường huyết lúc đói 6.6 ≥ 5.3 mmol/L", "3 lần đo liên tiếp tăng, lần cuối 150 ≥ nền + 8".
 * Danh sách chỉ cần biết "về cái gì"; chi tiết số liệu nằm ở trang chi tiết cảnh báo.
 */
export type ReasonTag = { text: string; source: "rule" | "trend" | "ai" };

const TREND = /^xu hướng:|lần đo liên tiếp tăng|ewma/i;
const BP = /^huyết áp\s+(\d+)\s*\/\s*(\d+)/i;
const GLU = /^đường huyết\s+(lúc đói|sau ăn 1 giờ|sau ăn 2 giờ)\s+([\d.,]+)/i;

function normalize(raw: string): ReasonTag {
  const r = raw.trim();
  if (TREND.test(r)) return { text: "Huyết áp tăng dần", source: "trend" };
  if (/^ai:\s*/i.test(r)) return { text: r.replace(/^ai:\s*/i, ""), source: "ai" };
  const bp = r.match(BP);
  if (bp) return { text: `Huyết áp ${bp[1]}/${bp[2]}`, source: "rule" };
  const glu = r.match(GLU);
  if (glu) return { text: `Đường huyết ${glu[1].replace("lúc ", "")} ${glu[2]}`, source: "rule" };
  return { text: r.replace(/\s*[≥>]=?\s*[\d/.,]+(\s*mmol\/L)?$/i, ""), source: "rule" };
}

/** Bỏ trùng (không phân biệt hoa thường); lý do quy tắc và AI trùng nhau thì giữ một, ưu tiên quy tắc. */
export function reasonTags(reasons: string[]): ReasonTag[] {
  const out: ReasonTag[] = [];
  for (const raw of reasons) {
    const t = normalize(raw);
    if (!t.text) continue;
    const key = t.text.toLowerCase();
    const i = out.findIndex((o) => o.text.toLowerCase() === key);
    if (i === -1) out.push(t);
    else if (out[i].source === "ai" && t.source !== "ai") out[i] = t;
  }
  return out;
}
