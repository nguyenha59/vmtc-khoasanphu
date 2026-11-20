import {
  Alert, Anchor, Badge, Box, Button, Checkbox, Code, Divider, Drawer, FileInput, Grid, Group, Paper, Select, SimpleGrid,
  Skeleton, Stack, Switch, Tabs, Text, TextInput, ThemeIcon, Title,
} from "@mantine/core";
import {
  IconAlertTriangle, IconCalendar, IconCheck, IconDownload, IconFileSpreadsheet, IconGitMerge, IconListCheck, IconLock,
  IconPhoneCall, IconRobot, IconSend, IconUpload, IconUsers,
} from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import dayjs from "dayjs";
import { useState } from "react";
import { DataTable } from "../components/DataTable";
import { PageHeader, SectionTitle, StatCard } from "../components/ui";
import { api } from "../lib/api";
import { useCan } from "../lib/auth";
import { fail, ok, postForm } from "../lib/notify";

type Reason = { code: string; label: string };
type Plan = {
  plan_id: number; code: string; full_name: string; phone_masked: string; nationality: string; start_time: string;
  services: string[]; arrival_time: string | null; arrival_label: string; method: string; method_label: string;
  status: string; status_label: string; reasons: Reason[]; merged: boolean;
  visit_types: string[]; planned_services: PlannedService[]; basis: string[]; fasting: boolean; maybe_services: string[];
};
type PlannedService = { code: string; name: string; why: string; source: string; conditional?: boolean };
const WHY_VI: Record<string, string> = {
  note: "ghi chú", visit_type: "loại khám", ga: "tuần thai", ai: "AI", service_column: "cột Dịch vụ", safety: "an toàn – nghi chuyển dạ",
};
type PlanList = {
  date: string; waiting_label: string; items: Plan[]; counts: { total: number; need: number; auto: number; merged: number };
  invalid_phones: string[]; exports: string[]; samples: string[]; llm: { provider: string; model: string };
};
type PlanDetail = Plan & {
  pid: string; merged_from: number[]; conflict_detail: string; masked_note: string; rule_applied: string; notes: string[];
  warnings: string[]; ai: unknown; ai_summary: string[]; confidence: number | null; model: string | null; prompt_version: string | null;
  ga_inconsistent: boolean; default_time: string; ai_status: string | null; ai_status_label: string;
};
type Issue = { code: string; message: string; row_no: number | null; fatal: boolean };
type UploadResult = { ok: boolean; report: Issue[]; warning: string | null; message?: string };

const STATUS_COLOR: Record<string, string> = { needs_review: "warn", auto: "brand", confirmed: "ok" };

function MethodBadge({ p }: { p: Plan }) {
  return <Badge variant="light" color={p.method === "ai" ? "grape" : p.method === "manual" ? "blue" : "gray"}>{p.method_label}</Badge>;
}

/** Một lịch hẹn dạng thẻ (điện thoại): giờ đến nổi bật bên phải, lý do cần xác nhận ở dưới. */
function PlanCard({ p }: { p: Plan }) {
  return (
    <Stack gap={6}>
      <Group justify="space-between" wrap="nowrap" align="flex-start">
        <Box style={{ minWidth: 0 }}>
          <Group gap={6} wrap="nowrap">
            <Text fw={700} size="sm">{p.code}</Text>
            <Text size="sm" truncate>{p.full_name}</Text>
          </Group>
          <Text size="xs" c="dimmed" ff="monospace">{p.phone_masked}</Text>
        </Box>
        <Box ta="right" style={{ flex: "none" }}>
          <Text size="xs" c="dimmed">Hẹn {p.start_time} → đến</Text>
          <Text fw={700} fz="lg" lh={1.1}>{p.arrival_label}</Text>
        </Box>
      </Group>
      {p.visit_types.length > 0 && <Text size="xs" fw={600}>{p.visit_types.join(" · ")}</Text>}
      <Text size="xs" c="dimmed" lineClamp={2}>{p.services.join(", ")}</Text>
      <Group gap={6} wrap="wrap">
        <Badge color={STATUS_COLOR[p.status]}>{p.status_label}</Badge>
        <MethodBadge p={p} />
      </Group>
      {p.reasons.length > 0 && <Text size="xs" c="warn.8">{p.reasons.map((r) => r.label).join(" · ")}</Text>}
    </Stack>
  );
}

export function Appointments() {
  const qc = useQueryClient();
  const can = useCan();
  const [d, setD] = useState(dayjs().add(1, "day").format("YYYY-MM-DD"));
  const [file, setFile] = useState<File | null>(null);
  const [sample, setSample] = useState<string | null>(null);
  const [useAi, setUseAi] = useState(true);
  const [result, setResult] = useState<UploadResult | null>(null);
  const [tab, setTab] = useState<string | null>("need");
  const [openId, setOpenId] = useState<number | null>(null);

  const q = useQuery({ queryKey: ["appointments", d], queryFn: () => api<PlanList>(`/api/appointments?d=${d}`) });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["appointments"] }); qc.invalidateQueries({ queryKey: ["overview"] }); };

  const upload = useMutation({
    mutationFn: () => {
      const f = new FormData();
      f.append("d", d);
      f.append("use_ai", String(useAi));
      if (file) f.append("file", file);
      else if (sample) f.append("sample", sample);
      return postForm<UploadResult>("/api/appointments/upload", f);
    },
    onSuccess: (r) => { setResult(r); if (r.ok) { ok(r.message ?? "Đã xử lý."); refresh(); setTab("need"); } },
    onError: (e) => fail(e, "Không xử lý được file"),
  });
  const exp = useMutation({
    mutationFn: () => api<{ files: { name: string; rows: number }[] }>(`/api/appointments/export?d=${d}`, { method: "POST" }),
    onSuccess: (r) => { ok(r.files.map((f) => `${f.name}: ${f.rows} tin`).join(" · "), "Đã xuất file WorldSMS"); refresh(); },
    onError: (e) => fail(e, "Không xuất được"),
  });

  const data = q.data;
  const items = data?.items ?? [];
  const lists: Record<string, Plan[]> = {
    need: items.filter((p) => p.status === "needs_review"),
    auto: items.filter((p) => p.status !== "needs_review"),
    merged: items.filter((p) => p.merged),
  };
  const fatal = result?.report.filter((r) => r.fatal) ?? [];
  const rowIssues = result?.report.filter((r) => !r.fatal) ?? [];

  return (
    <>
      <PageHeader title="Lịch hẹn" subtitle="Tải file HIS → kiểm tra → tính giờ đến → xác nhận → xuất tin nhắn WorldSMS"
        actions={<TextInput type="date" value={d} onChange={(e) => e.currentTarget.value && setD(e.currentTarget.value)}
          leftSection={<IconCalendar size={16} />} aria-label="Ngày hẹn" w={190} />} />

      <Paper p="lg" mb="lg">
        <SectionTitle icon={<IconUpload size={18} />}>Tải file lịch hẹn từ HIS</SectionTitle>
        <Grid align="flex-end">
          <Grid.Col span={{ base: 12, md: 5 }}>
            <FileInput label="File Excel xuất từ HIS" placeholder="Chọn file .xlsx" accept=".xlsx,.csv" clearable value={file}
              onChange={(f) => { setFile(f); if (f) setSample(null); }} leftSection={<IconFileSpreadsheet size={16} />} />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 4 }}>
            <Select label="…hoặc file giả mẫu" placeholder="Chọn file mẫu" data={data?.samples ?? []} value={sample} clearable
              onChange={(v) => { setSample(v); if (v) setFile(null); }} />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 3 }}>
            <Button fullWidth leftSection={<IconListCheck size={18} />} loading={upload.isPending}
              disabled={!(file || sample) || !can("upload_his")} onClick={() => upload.mutate()}>Kiểm tra & xử lý</Button>
          </Grid.Col>
        </Grid>
        <Switch mt="md" checked={useAi} onChange={(e) => setUseAi(e.currentTarget.checked)}
          label="Dùng AI đọc ghi chú" description={data ? `Mô hình: ${data.llm.provider} – ${data.llm.model}` : undefined} />
        {upload.isPending && <Text size="sm" c="dimmed" mt="sm">Đang kiểm tra file và tính giờ đến… (AI có thể mất vài phút)</Text>}
        {result?.warning && <Alert mt="md" color="warn" icon={<IconAlertTriangle size={18} />}>{result.warning}</Alert>}
        {result && !result.ok && (
          <Alert mt="md" color="danger" title="File có lỗi nghiêm trọng – KHÔNG xử lý tiếp" icon={<IconAlertTriangle size={18} />}>
            <Stack gap={4}>{fatal.map((r, i) => <Text key={i} size="sm"><b>{r.code}</b> – {r.message}</Text>)}</Stack>
          </Alert>
        )}
        {result?.ok && rowIssues.length > 0 && (
          <Alert mt="md" color="warn" variant="light" title={`Kết quả kiểm tra: ${rowIssues.length} cảnh báo dòng`}>
            <Stack gap={2}>{rowIssues.slice(0, 8).map((r, i) => <Text key={i} size="sm">Dòng {r.row_no}: <b>{r.code}</b> – {r.message}</Text>)}
              {rowIssues.length > 8 && <Text size="xs" c="dimmed">… và {rowIssues.length - 8} cảnh báo khác</Text>}</Stack>
          </Alert>
        )}
        {result?.ok && rowIssues.length === 0 && <Alert mt="md" color="ok" icon={<IconCheck size={18} />}>Kiểm tra file: đạt, không có cảnh báo.</Alert>}
      </Paper>

      {q.isLoading ? <Skeleton h={300} radius="lg" /> : !items.length ? (
        <Paper p="xl"><Text c="dimmed" ta="center">Chưa có lịch hẹn cho ngày {dayjs(d).format("DD/MM/YYYY")}. Tải file HIS ở trên.</Text></Paper>
      ) : (
        <>
          <SimpleGrid cols={{ base: 2, lg: 4 }} mb="lg">
            <StatCard label="Tổng lịch hẹn" value={data!.counts.total} icon={<IconUsers size={22} />} />
            <StatCard label="Cần xác nhận" value={data!.counts.need} icon={<IconAlertTriangle size={22} />} color="warn" />
            <StatCard label="Tự điền / đã xác nhận" value={data!.counts.auto}
              hint={`${Math.round((data!.counts.auto / data!.counts.total) * 100)}%`} icon={<IconCheck size={22} />} color="ok" />
            <StatCard label="Đã gộp" value={data!.counts.merged} icon={<IconGitMerge size={22} />} color="grape" />
          </SimpleGrid>

          <Paper p={0} mb="lg" style={{ overflow: "hidden" }}>
            <Tabs value={tab} onChange={setTab} px="md" pt="sm">
              <Tabs.List>
                <Tabs.Tab value="need" rightSection={<Badge size="sm" color="warn">{lists.need.length}</Badge>}>Cần xác nhận</Tabs.Tab>
                <Tabs.Tab value="auto" rightSection={<Badge size="sm" color="ok">{lists.auto.length}</Badge>}>Tự điền xong</Tabs.Tab>
                <Tabs.Tab value="merged" rightSection={<Badge size="sm" color="grape">{lists.merged.length}</Badge>}>Đã gộp</Tabs.Tab>
              </Tabs.List>
            </Tabs>
            <Box p="md">
              <DataTable rows={lists[tab ?? "need"]} rowKey={(p) => p.plan_id} activeKey={openId} onRowClick={(p) => setOpenId(p.plan_id)}
                minWidth={980} card={(p) => <PlanCard p={p} />}
                columns={[
                  { key: "code", label: "Mã", nowrap: true, render: (p) => <Text fw={600} size="sm">{p.code}</Text> },
                  { key: "full_name", label: "Khách", width: 170 },
                  { key: "phone_masked", label: "SĐT", nowrap: true, render: (p) => <Text ff="monospace" size="sm">{p.phone_masked}</Text> },
                  { key: "start_time", label: "Giờ hẹn", nowrap: true },
                  { key: "services", label: "Loại khám · dịch vụ", width: 240, render: (p) => (
                    <Box>
                      {p.visit_types.length > 0 && <Text size="sm" fw={600} lineClamp={1}>{p.visit_types.join(" · ")}
                        {p.fasting && <Badge ml={6} size="xs" color="warn" variant="light">Nhịn ăn</Badge>}</Text>}
                      <Text size="xs" c="dimmed" lineClamp={2}>{p.services.join(", ")}</Text>
                    </Box>) },
                  { key: "arrival_label", label: "Giờ đến", nowrap: true, render: (p) => <Text fw={700} size="sm">{p.arrival_label}</Text> },
                  { key: "method_label", label: "Cách tính", nowrap: true, width: 120, render: (p) => <MethodBadge p={p} /> },
                  { key: "status_label", label: "Trạng thái", nowrap: true, width: 150, render: (p) => <Badge color={STATUS_COLOR[p.status]}>{p.status_label}</Badge> },
                  { key: "reasons", label: "Lý do", width: 200, render: (p) => <Text size="xs" c="dimmed" lineClamp={2}>{p.reasons.map((r) => r.label).join(", ") || "—"}</Text> },
                ]} />
            </Box>
          </Paper>

          <Paper p="lg" mb="lg">
            <SectionTitle icon={<IconFileSpreadsheet size={18} />}>Bảng giờ đến (Excel)</SectionTitle>
            <Group justify="space-between" wrap="wrap">
              <Text size="sm" c="dimmed">File lịch OH kèm loại khám, dịch vụ dự kiến, giờ đến, cách tính và căn cứ (nguồn) – cho lễ tân / điều dưỡng.</Text>
              <Group gap="xs">
                <Button component="a" href={`/api/appointments/arrivals-xlsx?d=${d}&mode=full`} variant="light"
                  disabled={!can("export_worldsms")} leftSection={<IconDownload size={16} />}>Bản 1: tiếp đón 15' + dịch vụ</Button>
                <Button component="a" href={`/api/appointments/arrivals-xlsx?d=${d}&mode=services_only`} variant="light"
                  disabled={!can("export_worldsms")} leftSection={<IconDownload size={16} />}>Bản 2: chỉ thời gian dịch vụ</Button>
              </Group>
            </Group>
          </Paper>

          <Paper p="lg" style={data!.counts.need ? undefined : { borderColor: "var(--mantine-color-brand-3)" }}>
            <SectionTitle icon={<IconSend size={18} />}>Xuất file WorldSMS</SectionTitle>
            {data!.invalid_phones.length > 0 && (
              <Alert color="warn" mb="md" icon={<IconAlertTriangle size={18} />}>
                {data!.invalid_phones.length} khách có số điện thoại lỗi sẽ không có trong file – cần sửa trên HIS: {data!.invalid_phones.join(", ")}
              </Alert>
            )}
            <Group justify="space-between" wrap="wrap">
              {data!.counts.need ? (
                <Group gap="xs"><ThemeIcon color="warn" variant="light"><IconLock size={16} /></ThemeIcon>
                  <Text size="sm">Còn <b>{data!.counts.need}</b> ca “Cần xác nhận” – xác nhận hết để mở khóa xuất file.</Text></Group>
              ) : <Text size="sm" c="dimmed">Đã xác nhận hết. Xuất 2 file: khách Việt Nam và khách nước ngoài.</Text>}
              <Button leftSection={<IconFileSpreadsheet size={18} />} disabled={!!data!.counts.need || !can("export_worldsms")}
                loading={exp.isPending} onClick={() => exp.mutate()}>Xuất 2 file</Button>
            </Group>
            {data!.exports.length > 0 && (
              <Stack mt="md" gap="xs">
                {[["Bản 1: tiếp đón 15' + dịch vụ", false], ["Bản 2: chỉ thời gian dịch vụ", true]].map(([label, b2]) => {
                  const files = data!.exports.filter((n) => n.includes("_ban2_") === b2);
                  return files.length > 0 && (
                    <Group key={String(label)} gap="sm">
                      <Text size="sm" fw={600} w={210}>{label}</Text>
                      {files.map((n) => (
                        <Button key={n} component="a" href={`/api/appointments/export/${encodeURIComponent(n)}`} variant="light"
                          leftSection={<IconDownload size={16} />} size="xs">{n}</Button>
                      ))}
                    </Group>
                  );
                })}
              </Stack>
            )}
          </Paper>
        </>
      )}

      <PlanDrawer id={openId} d={d} waiting={data?.waiting_label ?? "Khám chờ"} onClose={() => setOpenId(null)} onDone={refresh} />
    </>
  );
}

function PlanDrawer({ id, d, waiting, onClose, onDone }: { id: number | null; d: string; waiting: string; onClose: () => void; onDone: () => void }) {
  const can = useCan();
  const q = useQuery({ queryKey: ["plan", id, d], queryFn: () => api<PlanDetail>(`/api/appointments/plan/${id}?d=${d}`), enabled: id !== null });
  const p = q.data;
  const [time, setTime] = useState<string | null>(null);
  const [keepWaiting, setKeepWaiting] = useState(true);
  const t = time ?? p?.default_time ?? "";

  const confirm = useMutation({
    mutationFn: () => api(`/api/appointments/plan/${id}/confirm`, { method: "POST",
      json: { d, arrival_time: p?.method === "waiting" && keepWaiting ? null : t } }),
    onSuccess: () => { ok("Đã xác nhận giờ đến."); onDone(); setTime(null); onClose(); },
    onError: (e) => fail(e, "Không xác nhận được"),
  });
  const doctor = useMutation({
    mutationFn: () => api(`/api/appointments/plan/${id}/called-doctor?d=${d}`, { method: "POST" }),
    onSuccess: () => ok("Đã ghi nhận vào nhật ký."), onError: (e) => fail(e),
  });

  return (
    <Drawer opened={id !== null} onClose={() => { setTime(null); onClose(); }} position="right" size="lg" padding="xl"
      title={p ? <Group gap="sm"><Title order={3}>{p.code}</Title><Badge color={STATUS_COLOR[p.status]}>{p.status_label}</Badge></Group> : "Chi tiết"}>
      {!p ? <Stack><Skeleton h={80} /><Skeleton h={160} /></Stack> : (
        <Stack gap="lg">
          <Paper p="md" bg="ink.0" withBorder={false} shadow="none">
            <Text fw={600}>{p.full_name}</Text>
            <Text size="sm" c="dimmed">SĐT <Text span ff="monospace">{p.phone_masked}</Text> · {p.nationality} · PID {p.pid}</Text>
            <Text size="sm" mt={6}>Giờ hẹn <b>{p.start_time}</b> · {p.services.join(", ")}</Text>
            {p.merged_from.length > 1 && <Text size="xs" c="grape.7" mt={4}>Đã gộp từ dòng Excel {p.merged_from.join(", ")}. {p.conflict_detail}</Text>}
          </Paper>

          {p.reasons.length > 0 && (
            <Alert color="warn" variant="light" title="Lý do cần xác nhận" icon={<IconAlertTriangle size={18} />}>
              <Stack gap={2}>{p.reasons.map((r) => <Text key={r.code} size="sm"><Code>{r.code}</Code> {r.label}</Text>)}</Stack>
            </Alert>
          )}

          <Box>
            <Text size="sm" fw={600} mb={6}>Ghi chú đã che</Text>
            <Code block>{p.masked_note || "(không có ghi chú)"}</Code>
            <Text size="sm" mt="sm"><b>Quy tắc áp dụng:</b> {p.rule_applied || "—"}</Text>
            {p.notes.map((n, i) => <Text key={i} size="xs" c="dimmed">• {n}</Text>)}
            {p.warnings.length > 0 && <Text size="xs" c="warn.8" mt={4}>Cảnh báo dòng: {p.warnings.join(", ")}</Text>}
          </Box>

          {p.planned_services.length > 0 && (
            <Box>
              <Text size="sm" fw={600} mb={6}>Loại khám và dịch vụ dự kiến (suy ra từ ghi chú)</Text>
              <Text size="sm">{p.visit_types.join(" · ")}{p.fasting && <Badge ml="xs" color="warn" variant="light">Cần nhịn ăn</Badge>}</Text>
              <Stack gap={2} mt={4}>
                {p.planned_services.filter((x) => !x.conditional).map((x) => (
                  <Text key={x.code} size="xs">• {x.name} <Text span c="dimmed">({WHY_VI[x.why] ?? x.why})</Text></Text>
                ))}
              </Stack>
              {p.maybe_services.length > 0 && (
                <Text size="xs" c="warn.8" mt={6}>Có thể cần (nếu chưa làm – hỏi khách khi gọi): {p.maybe_services.join(", ")}</Text>
              )}
              {p.basis.length > 0 && (
                <Text size="xs" c="dimmed" mt={6}>Căn cứ: {p.basis.join("; ")}</Text>
              )}
            </Box>
          )}

          <Box>
            <Group gap={6} mb={6}><IconRobot size={16} /><Text size="sm" fw={600}>AI đọc ra</Text></Group>
            {p.ai ? (
              <>
                <Stack gap={2}>{(p.ai_summary ?? []).map((t, i) => <Text key={i} size="sm">• {t}</Text>)}</Stack>
                <Text size="xs" c="dimmed" mt={4}>Mức chắc chắn của AI: {p.confidence !== null ? `${Math.round(p.confidence * 100)}%` : "—"}</Text>
                {p.ga_inconsistent && <Alert mt="xs" color="warn" variant="light">Tuần thai AI đọc lệch &gt; 2 tuần so với lần khám trước – không dùng để chọn form.</Alert>}
              </>
            ) : <Text size="sm" c={p.ai_status === "failed" || p.ai_status === "mask_failed" ? "warn.8" : "dimmed"}>
                {p.ai_status_label || "Không gọi AI."}</Text>}
          </Box>

          <Divider />
          <Stack gap="sm">
            <Text fw={700}>Xác nhận giờ đến</Text>
            {p.method === "waiting" && (
              <Checkbox checked={keepWaiting} onChange={(e) => setKeepWaiting(e.currentTarget.checked)}
                label={`Giữ là “${waiting}” (không gửi giờ đến)`} />
            )}
            {!(p.method === "waiting" && keepWaiting) && (
              <TextInput type="time" label="Giờ đến" description={`Phải trước hoặc bằng giờ hẹn ${p.start_time}`} value={t}
                onChange={(e) => setTime(e.currentTarget.value)} step={300} w={200} />
            )}
            <Group mt="xs">
              <Button leftSection={<IconCheck size={18} />} loading={confirm.isPending} disabled={!can("confirm_arrival")}
                onClick={() => confirm.mutate()}>Xác nhận</Button>
              <Button variant="default" leftSection={<IconPhoneCall size={18} />} loading={doctor.isPending}
                disabled={!can("confirm_arrival")} onClick={() => doctor.mutate()}>Đã gọi bác sĩ</Button>
            </Group>
            <Anchor size="xs" c="dimmed" component="span">Mọi lần xem / xác nhận đều ghi nhật ký.</Anchor>
          </Stack>
        </Stack>
      )}
    </Drawer>
  );
}
