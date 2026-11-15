import {
  Alert, Badge, Box, Button, Collapse, Divider, Grid, Group, Paper, SimpleGrid, Skeleton, Stack, Switch, Text,
  ThemeIcon, Tooltip, UnstyledButton,
} from "@mantine/core";
import {
  IconAlertTriangle, IconCalendarEvent, IconChevronDown, IconCircleCheck, IconClipboardList, IconDeviceMobile,
  IconFileDescription, IconFilterOff, IconInfoCircle, IconShieldCheck, IconSparkles, IconStethoscope,
} from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "../lib/api";
import { fail, ok } from "../lib/notify";

type Evidence = { source: string; source_vi: string; text: string };
type Rejected = { code: string; text: string; blocking: string[]; details: string[] };
type Item = {
  code: string; name: string; kind: "module" | "pack"; flags: string[]; questions: string[]; rejected: Rejected[];
  confirmed: boolean; active_now: boolean; schedule: string; window: string | null;
};
type PreviewQ = {
  code: string; text: string; added: boolean; group: number; group_label: string; reasons: string[]; score: number;
  mandatory: boolean;
};
export type FormPlanData = {
  flags: { flag: string; name: string; evidence: Evidence[] }[];
  items: Item[]; pending_flags: string[]; stale_confirmed: string[]; confirmed: string[]; insulin: boolean;
  /** Có căn cứ bác sĩ ghi, sẽ tự áp dụng – hiện sẵn đã tick; điều dưỡng bỏ tick = gỡ. */
  auto_pending?: string[];
  question_text: Record<string, string>; can_confirm: boolean; rules_version: string;
  preview: { form: string; name: string; questions: PreviewQ[] } | null;
  ai_input: { mask_ok: boolean; n_questions: number; fields: Record<string, unknown> } | null;
};

const ISSUE_VI: Record<string, string> = {
  NO_TRIAGE: "quy tắc không phân mức được câu trả lời",
  REVERSED: "trả lời “Có” lại là tốt – dễ nhầm",
  JARGON: "có thuật ngữ chuyên môn",
  VAGUE: "từ chỉ mức độ không có mốc đo",
};
const SOURCE: Record<string, { color: string; label: string }> = {
  icd10: { color: "grape", label: "Chẩn đoán ICD-10" },
  risk: { color: "brand", label: "Nhóm nguy cơ hồ sơ" },
  history: { color: "teal", label: "Tiền sử" },
  ai_note: { color: "orange", label: "AI đọc ghi chú" },
};
const DOCTOR_SOURCES = new Set(["icd10", "risk"]);

type Status = { label: string; color: string; icon: ReactNode };

/** Lựa chọn ban đầu = đang áp dụng + bộ câu có căn cứ bác sĩ sắp tự áp dụng. */
const baseOf = (d: FormPlanData): string[] => [...new Set([...d.confirmed, ...(d.auto_pending ?? [])])];

/** Bộ câu hỏi cá nhân hóa: hệ thống nhận tình trạng từ hồ sơ + AI, câu qua cổng chất lượng, điều dưỡng xác nhận. */
export function FormPlanPanel({ patientId }: { patientId: number }) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["form-plan", patientId],
    queryFn: () => api<FormPlanData>(`/api/patients/${patientId}/form-plan`),
  });
  const [sel, setSel] = useState<string[]>([]);
  const [insulin, setInsulin] = useState(false);
  useEffect(() => {
    if (data) { setSel(baseOf(data)); setInsulin(data.insulin); }
  }, [data]);
  const save = useMutation({
    mutationFn: () => api<FormPlanData>(`/api/patients/${patientId}/form-plan`, { method: "PUT", json: { codes: sel, insulin,
        declined: (data?.auto_pending ?? []).filter((c) => !sel.includes(c)) } }),
    onSuccess: (d) => {
      qc.setQueryData(["form-plan", patientId], d);
      qc.invalidateQueries({ queryKey: ["profile"] });
      ok("Đã xác nhận bộ câu hỏi cho thai phụ.");
    },
    onError: (e) => fail(e),
  });
  const doctorFlags = useMemo(
    () => new Set((data?.flags ?? []).filter((f) => f.evidence.some((e) => DOCTOR_SOURCES.has(e.source))).map((f) => f.flag)),
    [data],
  );
  if (isLoading || !data) return <Skeleton h={320} radius="md" />;

  const flagName = Object.fromEntries(data.flags.map((f) => [f.flag, f.name]));
  const base = baseOf(data);
  const autoPending = new Set(data.auto_pending ?? []);
  const added = sel.filter((c) => !base.includes(c));
  const removed = base.filter((c) => !sel.includes(c));
  const insulinChanged = sel.includes("M-DTD") && insulin !== data.insulin;
  const dirty = added.length + removed.length > 0 || insulinChanged;
  const pending = data.items.filter((i) => !i.confirmed && !autoPending.has(i.code) && i.questions.length > 0).length;

  const statusOf = (it: Item): Status => {
    if (it.confirmed && it.kind === "pack" && it.flags.some((f) => doctorFlags.has(f)))
      return { label: "Tự áp dụng", color: "brand", icon: <IconShieldCheck size={12} /> };
    if (autoPending.has(it.code))
      return { label: "Tự áp dụng (bác sĩ ghi)", color: "brand", icon: <IconShieldCheck size={12} /> };
    if (it.confirmed) return { label: "Đã xác nhận", color: "ok", icon: <IconCircleCheck size={12} /> };
    return { label: "Chờ xác nhận", color: "warn", icon: <IconAlertTriangle size={12} /> };
  };

  return (
    <Stack gap="md">
      {/* ---------------- tóm tắt */}
      <Paper withBorder radius="md" p={0} style={{ overflow: "hidden" }}>
        <SimpleGrid cols={{ base: 2, md: 4 }} spacing={0}>
          <Kpi label="Tình trạng nhận ra" value={data.flags.length}
            hint={data.pending_flags.length ? `${data.pending_flags.length} chưa có bộ câu đạt chuẩn` : "từ hồ sơ và AI"} />
          <Kpi label="Bộ câu đang áp dụng" value={data.confirmed.length}
            hint={data.confirmed.length
              ? data.confirmed.map((c) => data.items.find((i) => i.code === c)?.name ?? c).join(" · ")
              : "chỉ form theo tuần thai"} />
          <Kpi label="Chờ xác nhận" value={pending} hint={pending ? "điều dưỡng xem bên dưới" : "không có"}
            tone={pending ? "warn" : undefined} />
          <Kpi label="Form theo mốc kế tiếp" value={data.preview ? data.preview.form : "—"}
            hint={data.preview ? `${data.preview.questions.length} câu, xếp theo thai phụ` : "ngoài cửa sổ tuần thai"} last />
        </SimpleGrid>
      </Paper>

      <Grid gutter="md" align="flex-start">
        {/* ---------------- cột trái: căn cứ + bộ câu */}
        <Grid.Col span={{ base: 12, lg: 7 }}>
          <Stack gap="md">
            <Paper withBorder radius="md" p="md">
              <Header icon={<IconSparkles size={16} />} title="Căn cứ từ hồ sơ"
                sub="Bác sĩ ghi (chẩn đoán, nhóm nguy cơ) → tự áp dụng · AI / tiền sử → chờ xác nhận" />
              {data.flags.length === 0 ? (
                <Text size="sm" c="dimmed" mt="sm">
                  Chưa thấy nhóm nguy cơ nào trong chẩn đoán, tiền sử hay ghi chú lịch hẹn. Thai phụ chỉ nhận form theo tuần thai.
                </Text>
              ) : (
                <Stack gap={0} mt="sm">
                  {data.flags.map((f, i) => (
                    <Box key={f.flag}>
                      {i > 0 && <Divider my={8} color="ink.1" />}
                      <Group justify="space-between" align="center" wrap="wrap" gap={6}>
                        <Text fw={600} size="sm">{f.name}</Text>
                        <Group gap={4} justify="flex-end" wrap="wrap">
                          {f.evidence.map((e, j) => (
                            <Tooltip key={j} label={e.text} multiline maw={360} withArrow>
                              <Badge variant="light" radius="sm" size="sm" color={SOURCE[e.source]?.color ?? "gray"}
                                style={{ cursor: "help" }}>
                                {SOURCE[e.source]?.label ?? e.source_vi}
                              </Badge>
                            </Tooltip>
                          ))}
                        </Group>
                      </Group>
                    </Box>
                  ))}
                </Stack>
              )}
              {data.pending_flags.length > 0 && (
                <Alert mt="md" variant="light" color="warn" radius="md" p="sm" icon={<IconAlertTriangle size={16} />}>
                  <Text size="sm">
                    <b>{data.pending_flags.map((f) => flagName[f] ?? f).join(", ")}</b>: chưa có bộ câu hỏi đạt chất lượng – chờ bác sĩ
                    duyệt ngân hàng câu hỏi. Hệ thống không gửi câu chưa đạt.
                  </Text>
                </Alert>
              )}
            </Paper>

            <Paper withBorder radius="md" p="md">
              <Header icon={<IconClipboardList size={16} />} title="Bộ câu hỏi theo tình trạng"
                sub={`Mọi câu đã qua cổng chất lượng · rules ${data.rules_version}`} />
              {data.items.length === 0 ? (
                <Text size="sm" c="dimmed" mt="sm">Không có bộ câu nào cần thêm.</Text>
              ) : (
                <Stack gap="sm" mt="sm">
                  {data.items.map((it) => (
                    <PackCard key={it.code} it={it} on={sel.includes(it.code)} status={statusOf(it)} flagName={flagName}
                      questionText={data.question_text} canEdit={data.can_confirm}
                      onToggle={(v) => setSel(v ? [...sel, it.code] : sel.filter((c) => c !== it.code))}
                      insulin={it.code === "M-DTD" ? { value: insulin, set: setInsulin } : undefined} />
                  ))}
                </Stack>
              )}
              {data.stale_confirmed.length > 0 && (
                <Text size="xs" c="warn.8" mt="sm">
                  {data.stale_confirmed.join(", ")}: đã áp dụng nhưng hồ sơ hiện không còn căn cứ – xem lại có cần giữ không.
                </Text>
              )}
              {!data.can_confirm && <Text size="xs" c="dimmed" mt="sm">Điều dưỡng xác nhận bộ câu hỏi; bác sĩ xem.</Text>}
            </Paper>

            {data.ai_input && <AiInputCard input={data.ai_input} />}
          </Stack>
        </Grid.Col>

        {/* ---------------- cột phải: xem trước form */}
        <Grid.Col span={{ base: 12, lg: 5 }}>
          <Box style={{ position: "sticky", top: 16 }}>
            <FormPreview preview={data.preview} />
          </Box>
        </Grid.Col>
      </Grid>

      {/* ---------------- thanh lưu: chỉ hiện khi có thay đổi */}
      {dirty && data.can_confirm && (
        <Paper shadow="md" radius="md" p="sm" withBorder
          style={{ position: "sticky", bottom: 12, zIndex: 5, borderColor: "var(--mantine-color-brand-4)" }}>
          <Group justify="space-between" wrap="wrap" gap="sm">
            <Text size="sm">
              <b>Chưa lưu:</b>{" "}
              {[added.length ? `bật ${added.join(", ")}` : null,
                removed.length ? `tắt ${removed.join(", ")}${removed.some((c) => autoPending.has(c)) ? " (không tự áp dụng lại)" : ""}` : null,
                insulinChanged ? "đổi insulin" : null].filter(Boolean).join(" · ")}
            </Text>
            <Group gap="xs">
              <Button variant="default" onClick={() => { setSel(base); setInsulin(data.insulin); }}>Hoàn tác</Button>
              <Button loading={save.isPending} onClick={() => save.mutate()}>Xác nhận bộ câu hỏi</Button>
            </Group>
          </Group>
        </Paper>
      )}
    </Stack>
  );
}

function Kpi({ label, value, hint, tone, last }: { label: string; value: ReactNode; hint: string; tone?: string; last?: boolean }) {
  return (
    <Box p="md" style={{ borderRight: last ? undefined : "1px solid var(--mantine-color-ink-2)" }}>
      <Text size="xs" c="dimmed" fw={600} tt="uppercase" lh={1.2}>{label}</Text>
      <Text fz={24} fw={700} lh={1.2} mt={6} c={tone ? `${tone}.7` : "ink.9"} style={{ fontVariantNumeric: "tabular-nums" }}>
        {value}
      </Text>
      <Text size="xs" c="dimmed" mt={2} lineClamp={1}>{hint}</Text>
    </Box>
  );
}

function Header({ icon, title, sub }: { icon: ReactNode; title: string; sub?: string }) {
  return (
    <Group gap="sm" wrap="nowrap" align="flex-start">
      <ThemeIcon variant="light" radius="md" size={30}>{icon}</ThemeIcon>
      <div>
        <Text fw={700} lh={1.3}>{title}</Text>
        {sub && <Text size="xs" c="dimmed">{sub}</Text>}
      </div>
    </Group>
  );
}

function PackCard({ it, on, status, flagName, questionText, canEdit, onToggle, insulin }: {
  it: Item; on: boolean; status: Status; flagName: Record<string, string>; questionText: Record<string, string>;
  canEdit: boolean; onToggle: (v: boolean) => void; insulin?: { value: boolean; set: (v: boolean) => void };
}) {
  const [open, setOpen] = useState(false);
  const empty = it.questions.length === 0;
  return (
    <Paper withBorder radius="md" p="sm"
      style={{ borderColor: on ? "var(--mantine-color-brand-3)" : undefined, background: on ? "var(--mantine-color-brand-0)" : undefined }}>
      <Group justify="space-between" align="flex-start" wrap="nowrap" gap="sm">
        <Group gap="sm" align="flex-start" wrap="nowrap" style={{ minWidth: 0 }}>
          <ThemeIcon variant="light" color={it.kind === "module" ? "grape" : "brand"} radius="md" size={34}>
            {it.kind === "module" ? <IconStethoscope size={18} /> : <IconFileDescription size={18} />}
          </ThemeIcon>
          <div style={{ minWidth: 0 }}>
            <Group gap={6} wrap="wrap">
              <Text fw={600}>{it.name}</Text>
              <Badge variant="light" radius="sm" size="sm" color={status.color} leftSection={status.icon}>{status.label}</Badge>
              {!it.active_now && !empty && <Badge variant="outline" radius="sm" size="sm" color="gray">Ngoài tuần thai</Badge>}
            </Group>
            <Text size="xs" c="dimmed" mt={2}>
              {it.flags.length ? `Vì: ${it.flags.map((f) => flagName[f] ?? f).join(", ")}` : "Đã áp dụng trước đây"}
            </Text>
            <Stack gap={2} mt={4}>
              <Meta icon={<IconCalendarEvent size={13} />}>{it.schedule}</Meta>
              {it.window && <Meta icon={<IconDeviceMobile size={13} />}>Tuần thai {it.window}</Meta>}
            </Stack>
          </div>
        </Group>
        <Tooltip withArrow label={empty ? "Không còn câu đạt chất lượng" : on ? "Đang áp dụng – tắt để bỏ" : "Bật để áp dụng"}>
          <div><Switch checked={on} disabled={!canEdit || empty} onChange={(e) => onToggle(e.currentTarget.checked)} size="md" /></div>
        </Tooltip>
      </Group>

      <UnstyledButton mt={8} onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <Group gap={4}>
          <Text size="xs" fw={600} c="brand.7">
            {open ? "Ẩn" : "Xem"} {it.questions.length} câu{it.rejected.length ? ` · ${it.rejected.length} câu bị loại vì chất lượng` : ""}
          </Text>
          <IconChevronDown size={14} color="var(--mantine-color-brand-7)"
            style={{ transform: open ? "rotate(180deg)" : undefined, transition: "transform 150ms" }} />
        </Group>
      </UnstyledButton>
      <Collapse in={open}>
        <Stack gap={4} mt={6} pl={4}>
          {it.questions.map((q, i) => (
            <Group key={q} gap={8} wrap="nowrap" align="flex-start">
              <Text size="xs" c="dimmed" w={16} ta="right" mt={2}>{i + 1}.</Text>
              <Text size="sm">{questionText[q] ?? q}</Text>
            </Group>
          ))}
          {it.rejected.map((r) => (
            <Group key={r.code} gap={8} wrap="nowrap" align="flex-start">
              <Box mt={2} style={{ display: "flex" }}><IconFilterOff size={14} color="var(--mantine-color-ink-4)" /></Box>
              <Text size="xs" c="dimmed">
                <Text span td="line-through" inherit>{r.text}</Text> – {r.blocking.map((b) => ISSUE_VI[b] ?? b).join("; ")}
              </Text>
            </Group>
          ))}
        </Stack>
      </Collapse>
      {insulin && on && (
        <Switch mt="sm" size="xs" label="Đang dùng insulin (báo cáo 2 lần/tuần)" checked={insulin.value}
          disabled={!canEdit} onChange={(e) => insulin.set(e.currentTarget.checked)} />
      )}
    </Paper>
  );
}

function Meta({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <Group gap={6} wrap="nowrap" align="flex-start">
      <Box c="dimmed" mt={2} style={{ display: "flex", flex: "0 0 auto" }}>{icon}</Box>
      <Text size="xs" c="dimmed" style={{ flex: 1, minWidth: 0 }}>{children}</Text>
    </Group>
  );
}

function FormPreview({ preview }: { preview: FormPlanData["preview"] }) {
  if (!preview) {
    return (
      <Paper withBorder radius="md" p="md">
        <Header icon={<IconDeviceMobile size={16} />} title="Xem trước form" />
        <Text size="sm" c="dimmed" mt="sm">
          Thai phụ đang ngoài cửa sổ tuần thai của form theo mốc (hoặc đã sinh) – chưa có form kế tiếp để xem trước.
        </Text>
      </Paper>
    );
  }
  return (
    <Paper withBorder radius="lg" p={0} style={{ overflow: "hidden" }}>
      <Box px="md" py="sm" style={{ background: "var(--mantine-color-brand-7)", color: "white" }}>
        <Text size="xs" fw={600} tt="uppercase" style={{ opacity: 0.85 }}>Xem trước · thai phụ sẽ thấy</Text>
        <Text fw={700}>{preview.name}</Text>
        <Text size="xs" style={{ opacity: 0.85 }}>{preview.questions.length} câu · xếp theo tình trạng của chị</Text>
      </Box>
      <Stack gap={0} p="md">
        {preview.questions.map((q, i) => {
          const head = i === 0 || preview.questions[i - 1].group_label !== q.group_label;
          return (
            <Box key={q.code}>
              {head && (
                <Text size="xs" fw={700} c={q.group <= 2 ? "brand.8" : "ink.6"} tt="uppercase" mt={i ? "md" : 0} mb={4}>
                  {q.group_label}
                </Text>
              )}
              <Group gap={8} wrap="nowrap" align="flex-start" py={6} style={{ borderBottom: "1px solid var(--mantine-color-ink-1)" }}>
                <Text size="sm" c="dimmed" w={20} ta="right" style={{ fontVariantNumeric: "tabular-nums" }}>{i + 1}</Text>
                <Text size="sm" style={{ flex: 1 }}>
                  {q.text}{" "}
                  {q.added && <Badge size="xs" variant="light" radius="sm" color="brand">theo tình trạng</Badge>}
                </Text>
                <Tooltip label={q.reasons.join(" · ")} multiline maw={300} withArrow position="left">
                  <Box c="ink.4" style={{ display: "flex", cursor: "help" }} aria-label="Lý do chọn câu"><IconInfoCircle size={16} /></Box>
                </Tooltip>
              </Group>
            </Box>
          );
        })}
      </Stack>
      <Box px="md" py="sm" style={{ background: "var(--mantine-color-ink-0)", borderTop: "1px solid var(--mantine-color-ink-2)" }}>
        <Text size="xs" c="dimmed">
          Xem trước theo điểm quy tắc. Khi tạo đường dẫn, AI chấm điểm thêm – chỉ được thêm hoặc đẩy câu lên, không bao giờ bỏ câu bắt buộc.
        </Text>
      </Box>
    </Paper>
  );
}

const AI_FIELD_VI: Record<string, string> = {
  ga_weeks: "Tuần thai", conditions: "Tình trạng", previous_flagged: "Lần trước trả lời Có / Không chắc",
  recent_concepts: "Triệu chứng tự viết gần đây", measurements: "Số đo gần nhất (đã phân loại)",
  medications: "Nhóm thuốc", obstetric_history: "Tiền sử sản khoa", days_since_last_visit: "Từ lần khám gần nhất",
  days_to_next_visit: "Tới lần hẹn kế tiếp",
};

function aiValue(key: string, v: unknown): string {
  if (v === null || v === undefined) return "chưa rõ";
  if (Array.isArray(v)) {
    if (v.length === 0) return "không có";
    return v.map((x) => (typeof x === "object" && x ? `${(x as { text?: string }).text ?? ""} → ${(x as { answer?: string }).answer ?? ""}` : String(x))).join(" · ");
  }
  if (key.startsWith("days_")) return `${v} ngày`;
  if (key === "ga_weeks") return `${v} tuần`;
  return String(v);
}

/** Đúng phần thông tin thai phụ gửi AI chọn câu (đã qua che dữ liệu) – không tên, SĐT, mã hồ sơ, ngày, ghi chú gốc. */
function AiInputCard({ input }: { input: NonNullable<FormPlanData["ai_input"]> }) {
  const [open, setOpen] = useState(false);
  return (
    <Paper withBorder radius="md" p="md">
      <UnstyledButton onClick={() => setOpen((o) => !o)} w="100%">
        <Group justify="space-between" wrap="nowrap">
          <Header icon={<IconShieldCheck size={16} />} title="Thông tin gửi AI (đã ẩn danh)"
            sub={`Không gửi tên, số điện thoại, mã hồ sơ, ngày tháng, ghi chú gốc · kèm ${input.n_questions} câu trong kho`} />
          <IconChevronDown size={16} style={{ transform: open ? "rotate(180deg)" : undefined, transition: "transform 150ms", flexShrink: 0 }} />
        </Group>
      </UnstyledButton>
      <Collapse in={open}>
        {!input.mask_ok ? (
          <Alert mt="sm" color="warn" variant="light" radius="md" p="sm">Che dữ liệu chưa sạch – hệ thống sẽ không gửi AI, chỉ dùng quy tắc.</Alert>
        ) : (
          <Stack gap={6} mt="sm">
            {Object.entries(input.fields).map(([k, v]) => (
              <Group key={k} gap="xs" align="flex-start" wrap="nowrap">
                <Text size="xs" c="dimmed" w={170} style={{ flexShrink: 0 }}>{AI_FIELD_VI[k] ?? k}</Text>
                <Text size="sm">{aiValue(k, v)}</Text>
              </Group>
            ))}
          </Stack>
        )}
      </Collapse>
    </Paper>
  );
}
