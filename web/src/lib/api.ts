export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, headers, ...rest } = init;
  const res = await fetch(path, {
    credentials: "same-origin",
    ...rest,
    headers: { ...(json !== undefined ? { "Content-Type": "application/json" } : {}), ...headers },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  if (!res.ok) {
    let msg = `Lỗi ${res.status}`;
    try {
      const d = await res.json();
      if (typeof d.detail === "string") msg = d.detail;
      else if (Array.isArray(d.detail)) msg = "Dữ liệu gửi lên không hợp lệ.";
    } catch {
      /* không phải JSON */
    }
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------- kiểu dữ liệu
export type Me = { username: string; name: string; role: string; permissions: string[] };

export type AlertSummary = {
  id: number;
  level: "red" | "yellow" | "green";
  status: string;
  status_label: string;
  code: string;
  ga: string;
  form: string;
  created_at: string | null;
  summary: string;
  reasons: string[];
  demo: boolean;
  patient_open?: number; // số cảnh báo chưa đóng của cùng thai phụ (danh sách)
};

export type Sibling = { id: number; level: string; status: string; form: string; reasons: string[]; created_at: string | null };
export type DownReview = {
  review_id: number; alert_id: number; code: string; reasons: string[]; final_level: string; final_label: string;
  note: string; handled_at: string | null; by: string;
};

export type WhyItem = { level: string; source: string; title: string; detail: string; basis?: string; basis_codes?: string[] };

export type Overview = {
  today: string;
  tomorrow: string;
  shift: string;
  metrics: { appointments_tomorrow: number; forms_today: number; responses_24h: number; open_alerts: number; open_red: number };
  steps: { key: string; label: string; done: boolean; to: string; pending?: number | null }[];
  latest_alerts: (Pick<AlertSummary, "id" | "level" | "status" | "status_label" | "code" | "summary" | "reasons" | "created_at">)[];
  llm?: { provider: string; model: string };
};

export type AlertList = {
  items: AlertSummary[];
  counts: { red: number; yellow: number; awaiting_doctor: number; downgrade_reviews: number };
  digest_times: string[];
  final_levels: Record<string, string>;
  decisions: Record<string, string>;
};

export type BpSeries = {
  rows: { t: string; sys: number; dia: number | null; ewma: number | null }[];
  yellow: number;
  red: number;
  upper: number | null;
  sigma_k: number;
};
export type GlucoseSeries = {
  rows: ({ t: string } & Record<string, number | string>)[];
  labels: Record<string, string>;
  fasting_threshold: number;
};

export type AlertDetail = AlertSummary & {
  decisions: Record<string, string>;
  doctor_decision: { status: string; decision: string | null; label: string; note: string; at: string | null } | null;
  siblings: Sibling[];
  why: WhyItem[];
  ai_escalated: boolean;
  layers: {
    rule: { level: string; reasons: string[]; implausible: string[] };
    trend: { level: string; items: { kind: string; reason: string }[] };
    ai: {
      level: string;
      confidence: number | null;
      needs_human: boolean;
      skipped?: boolean;
      findings: { name: string; present: boolean; temporality: string; experiencer: string; evidence: string }[];
      model: string | null;
      prompt_version: string | null;
      rules_version: string;
    };
  };
  answers: { key: string; text: string; value: unknown }[];
  free_text: string;
  note: string;
  final_level: string | null;
  phone_masked: string;
  submitted_at: string | null;
  charts: { bp: BpSeries | null; glucose: GlucoseSeries | null };
};
