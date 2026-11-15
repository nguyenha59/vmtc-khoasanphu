import {
  Alert, Badge, Box, Button, Grid, Group, List, Paper, SimpleGrid, Skeleton, Stack, Tabs, Text, TextInput, ThemeIcon, Title,
} from "@mantine/core";
import {
  IconActivity, IconChartLine, IconFileText, IconEdit, IconHeartbeat, IconPrinter, IconScale, IconSearch, IconShieldCheck,
  IconShieldX, IconNotes, IconTrendingUp,
} from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { DataTable } from "../components/DataTable";
import { LevelBadge, PageHeader, SectionTitle, StatCard } from "../components/ui";
import { VitalsCharts } from "../components/VitalsChart";
import { VisitHistory, type Visit } from "../components/VisitHistory";
import { PatientList } from "../components/PatientList";
import { ObstetricSummary, ProgramBadges, ProgramEditor, type ObHistory, type Program } from "../components/PatientRecord";
import { FormPlanPanel } from "../components/FormPlan";
import { api, type BpSeries, type GlucoseSeries } from "../lib/api";
import { reasonTags } from "../lib/reasons";
import { useCan } from "../lib/auth";
import { fmtDate, fmtDateTime } from "../lib/format";
import { fail, ok } from "../lib/notify";

type ProfileData = {
  patient_id: number; code: string; stage: string; phone_masked: string; risk_groups: string[]; insulin: boolean;
  consent: { status: string; label: string; at: string | null }; last_visit: string | null; next_appt: string | null;
  delivery_date: string | null;
  metrics: { bp_mean: number | null; bp_n: number; bp_max: string | null; weight: number | null; reply_rate: number | null; answered: number; sent: number };
  summary_lines: string[];
  responses: { at: string; form: string; level: string; summary: string }[];
  alerts: { id: number; at: string | null; level: string; form: string; reasons: string[]; status: string; final_level: string; note: string }[];
  visits: Visit[];
  program: Program;
  history: ObHistory | null;
  charts: { bp: BpSeries | null; glucose: GlucoseSeries | null };
};

export function Profile() {
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const [input, setInput] = useState(q);
  useEffect(() => setInput(q), [q]); // tìm từ thanh trên khi đang ở trang này
  const recent = useQuery({ queryKey: ["profile-recent"], queryFn: () => api<{ codes: string[] }>("/api/profile/recent") });
  const prof = useQuery({ queryKey: ["profile", q], queryFn: () => api<ProfileData>(`/api/profile?q=${encodeURIComponent(q)}`),
    enabled: !!q, retry: false });

  const go = (v: string) => { setInput(v); setParams(v ? { q: v } : {}); };

  return (
    <>
      <PageHeader title="Hồ sơ theo dõi" subtitle="Tóm tắt giữa hai lần khám, chỉ số, nhật ký và đồng thuận"
        actions={
          <form onSubmit={(e) => { e.preventDefault(); go(input.trim()); }}>
            <Group gap="xs">
              <TextInput placeholder="Mã R#### hoặc PID" value={input} onChange={(e) => setInput(e.currentTarget.value)}
                leftSection={<IconSearch size={16} />} w={240} aria-label="Mã thai phụ" />
              <Button type="submit">Mở hồ sơ</Button>
            </Group>
          </form>
        } />

      {!q && (
        <Stack gap="lg">
          {!!recent.data?.codes.length && (
            <Group gap="xs" wrap="wrap">
              <Text size="sm" c="dimmed">Có câu trả lời form gần đây:</Text>
              {recent.data.codes.map((c) => <Button key={c} variant="light" size="xs" radius="xl" onClick={() => go(c)}>{c}</Button>)}
            </Group>
          )}
          <PatientList onOpen={go} />
        </Stack>
      )}
      {q && prof.isLoading && <Stack><Skeleton h={110} radius="lg" /><Skeleton h={120} radius="lg" /><Skeleton h={300} radius="lg" /></Stack>}
      {q && prof.isError && <Alert color="danger" title="Không mở được hồ sơ">{(prof.error as Error).message}</Alert>}
      {prof.data && <ProfileView d={prof.data} />}
    </>
  );
}

function ProfileView({ d }: { d: ProfileData }) {
  const can = useCan();
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const [editProgram, setEditProgram] = useState(false);
  const m = d.metrics;
  const refresh = () => qc.invalidateQueries({ queryKey: ["profile"] });
  const grant = useMutation({
    mutationFn: () => api(`/api/profile/${d.patient_id}/consent/grant`, { method: "POST", json: { note } }),
    onSuccess: () => { ok("Đã ghi nhận đồng ý."); refresh(); }, onError: (e) => fail(e),
  });
  const withdraw = useMutation({
    mutationFn: () => api<{ cancelled: number }>(`/api/profile/${d.patient_id}/consent/withdraw`, { method: "POST", json: { note } }),
    onSuccess: (r) => { ok(`Đã rút đồng ý; hủy ${r.cancelled} lịch gửi chưa xuất, vô hiệu đường dẫn form chưa dùng.`); refresh(); },
    onError: (e) => fail(e),
  });
  const granted = d.consent.status === "granted";
  const [delivery, setDelivery] = useState(d.delivery_date ?? "");
  const now = new Date(); // ngày theo giờ máy (không dùng UTC – 0h–7h sáng giờ VN sẽ lệch 1 ngày)
  const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  const saveDelivery = useMutation({
    mutationFn: () => api(`/api/profile/${d.patient_id}/delivery`, { method: "POST", json: { delivery_date: delivery } }),
    onSuccess: () => { ok("Đã ghi ngày sinh – chuyển sang form sau sinh S1–S4.", "Đã lưu"); refresh(); },
    onError: (e) => fail(e),
  });

  return (
    <Stack gap="lg">
      <Paper p="lg">
        <Group justify="space-between" wrap="wrap" gap="md">
          <Group gap="md" wrap="nowrap">
            <ThemeIcon size={56} radius="xl" variant="light"><IconHeartbeat size={28} /></ThemeIcon>
            <Box>
              <Group gap="sm"><Title order={2}>{d.code}</Title>
                <Badge color={granted ? "ok" : d.consent.status === "withdrawn" ? "danger" : "gray"}
                  leftSection={granted ? <IconShieldCheck size={12} /> : <IconShieldX size={12} />}>{d.consent.label}</Badge></Group>
              <Text size="sm" c="dimmed">{d.stage} · SĐT <Text span ff="monospace">{d.phone_masked}</Text></Text>
              <Group gap={6} mt={6} wrap="wrap">
                <ProgramBadges p={d.program} />
                {d.risk_groups.length ? d.risk_groups.map((g) => <Badge key={g} variant="outline" color="grape">{g}</Badge>)
                  : <Text size="xs" c="dimmed">Không thuộc nhóm theo dõi</Text>}
                {d.insulin && <Badge variant="outline" color="blue">insulin</Badge>}
              </Group>
            </Box>
          </Group>
          <Stack gap={4} align="flex-end">
            <Text size="sm">Khám gần nhất <b>{fmtDate(d.last_visit)}</b> · Hẹn tới <b>{fmtDate(d.next_appt)}</b></Text>
            <Group gap="xs">
              {can("manage_patient") && (
                <Button variant="default" leftSection={<IconEdit size={16} />} onClick={() => setEditProgram(true)}>Chương trình</Button>
              )}
              <Button component="a" href={`/api/profile/${d.patient_id}/print`} variant="default" leftSection={<IconPrinter size={16} />}>
                In tóm tắt</Button>
            </Group>
          </Stack>
        </Group>
      </Paper>

      {d.program.override_monitoring && d.program.override_reason && (
        <Alert color="grape" variant="light" p="sm" title="Bác sĩ chỉ định theo dõi từ xa">
          <Text size="sm">{d.program.override_reason}</Text>
        </Alert>
      )}
      {editProgram && <ProgramEditor patientId={d.patient_id} program={d.program} opened onClose={() => setEditProgram(false)} />}

      <ObstetricSummary h={d.history} />

      <SimpleGrid cols={{ base: 2, lg: 4 }}>
        <StatCard label="HA tâm thu trung bình" value={m.bp_mean ?? "—"} hint={`${m.bp_n} lần đo từ lần khám trước`} icon={<IconActivity size={22} />} color="blue" />
        <StatCard label="HA cao nhất" value={m.bp_max ?? "—"} hint="mmHg" icon={<IconTrendingUp size={22} />} color="danger" />
        <StatCard label="Cân nặng" value={m.weight !== null ? `${m.weight} kg` : "—"} icon={<IconScale size={22} />} color="grape" />
        <StatCard label="Tỷ lệ trả lời" value={m.reply_rate !== null ? `${Math.round(m.reply_rate * 100)}%` : "—"}
          hint={`${m.answered}/${m.sent} form`} icon={<IconFileText size={22} />} />
      </SimpleGrid>

      <Grid gutter="lg">
        <Grid.Col span={{ base: 12, md: 5 }}>
          <Paper p="lg" h="100%">
            <SectionTitle icon={<IconNotes size={18} />}>Tóm tắt giữa hai lần khám</SectionTitle>
            <List spacing="xs" size="sm">{d.summary_lines.map((l, i) => <List.Item key={i}>{l}</List.Item>)}</List>
          </Paper>
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 7 }}>
          <Paper p="lg" h="100%">
            <SectionTitle icon={<IconChartLine size={18} />}>Chỉ số 90 ngày</SectionTitle>
            <VitalsCharts charts={d.charts} />
          </Paper>
        </Grid.Col>
      </Grid>

      <Paper p={0} style={{ overflow: "hidden" }}>
        <Tabs defaultValue="visits" px="md" pt="sm">
          <Tabs.List>
            <Tabs.Tab value="visits" rightSection={<Badge size="sm" variant="light">{d.visits.length}</Badge>}>Lịch sử khám</Tabs.Tab>
            <Tabs.Tab value="forms" rightSection={<Badge size="sm" variant="light">{d.responses.length}</Badge>}>Nhật ký form</Tabs.Tab>
            <Tabs.Tab value="alerts" rightSection={<Badge size="sm" variant="light">{d.alerts.length}</Badge>}>Nhật ký cảnh báo</Tabs.Tab>
            <Tabs.Tab value="formplan">Bộ câu hỏi</Tabs.Tab>
            <Tabs.Tab value="consent">Đồng thuận</Tabs.Tab>
            <Tabs.Tab value="delivery" rightSection={d.delivery_date ? <Badge size="sm" variant="light" color="ok">đã sinh</Badge> : null}>
              Sau sinh</Tabs.Tab>
          </Tabs.List>
          <Tabs.Panel value="visits" py="md">
            <VisitHistory visits={d.visits} />
          </Tabs.Panel>
          <Tabs.Panel value="forms" py="md">
            <DataTable rows={d.responses} empty="Chưa có câu trả lời." columns={[
              { key: "at", label: "Thời điểm", render: (r) => fmtDateTime(r.at), width: 120 },
              { key: "form", label: "Form", width: 90, render: (r) => <Badge variant="light">{r.form}</Badge> },
              { key: "level", label: "Mức", width: 100, render: (r) => <LevelBadge level={r.level} size="sm" /> },
              { key: "summary", label: "Tóm tắt", render: (r) => <Text size="sm" lineClamp={2}>{r.summary}</Text> },
            ]} />
          </Tabs.Panel>
          <Tabs.Panel value="alerts" py="md">
            <DataTable rows={d.alerts} empty="Chưa có cảnh báo." columns={[
              { key: "at", label: "Thời điểm", render: (r) => <Text component={Link} to={`/canh-bao/${r.id}`} size="sm" c="brand.7">{fmtDateTime(r.at)}</Text> },
              { key: "level", label: "Mức", width: 90, render: (r) => <LevelBadge level={r.level} size="sm" /> },
              { key: "reasons", label: "Lý do", render: (r) => <Text size="sm" lineClamp={2}>{reasonTags(r.reasons).map((t) => t.text).join(" · ") || "—"}</Text> },
              { key: "status", label: "Trạng thái", width: 110 }, { key: "final_level", label: "Mức sau gọi", width: 110 },
              { key: "note", label: "Xử trí", render: (r) => <Text size="sm" lineClamp={3}>{r.note || "—"}</Text> },
            ]} />
          </Tabs.Panel>
          <Tabs.Panel value="formplan" py="md">
            <FormPlanPanel patientId={d.patient_id} />
          </Tabs.Panel>
          <Tabs.Panel value="consent" p="md">
            <Stack gap="md" maw={520}>
              <Text size="sm"><b>Trạng thái:</b> {d.consent.label}{d.consent.at ? ` · ${fmtDateTime(d.consent.at)}` : ""}</Text>
              <TextInput label="Ghi chú (người ghi nhận / hình thức)" value={note} onChange={(e) => setNote(e.currentTarget.value)} />
              <Group>
                <Button color="ok" disabled={!can("manage_consent") || granted} loading={grant.isPending} onClick={() => grant.mutate()}>
                  Ghi nhận đồng ý</Button>
                <Button color="danger" variant="light" disabled={!can("manage_consent") || !granted} loading={withdraw.isPending}
                  onClick={() => withdraw.mutate()}>Rút đồng ý (dừng gửi ngay)</Button>
              </Group>
            </Stack>
          </Tabs.Panel>
          <Tabs.Panel value="delivery" p="md">
            <Stack gap="md" maw={520}>
              <Text size="sm" c="dimmed">
                Ghi ngày sinh khi thai phụ đã sinh: hệ thống dừng form thai kỳ và gửi form sau sinh S1–S4
                (ngày 3, 7, 14, 42; S3–S4 có EPDS). Quên xuất SMS đúng ngày vẫn được gửi bù vài ngày.
              </Text>
              <Group align="flex-end">
                <TextInput type="date" label="Ngày sinh" value={delivery} max={today}
                  onChange={(e) => setDelivery(e.currentTarget.value)} w={200} />
                <Button disabled={!can("record_delivery") || !delivery || delivery === d.delivery_date}
                  loading={saveDelivery.isPending} onClick={() => saveDelivery.mutate()}>Lưu ngày sinh</Button>
              </Group>
              {d.delivery_date && <Text size="sm"><b>Đã ghi:</b> {fmtDate(d.delivery_date)}</Text>}
            </Stack>
          </Tabs.Panel>
        </Tabs>
      </Paper>
    </Stack>
  );
}
