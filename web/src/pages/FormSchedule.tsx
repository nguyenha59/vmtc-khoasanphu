import {
  Alert, Badge, Button, Checkbox, Grid, Group, Paper, Progress, SimpleGrid, Skeleton, Stack, Tabs, Text, ThemeIcon,
} from "@mantine/core";
import {
  IconActivityHeartbeat, IconBabyCarriage, IconBan, IconBell, IconCalendarRepeat, IconDownload, IconDroplet, IconInfoCircle,
  IconPhoneCall, IconSend, IconSparkles,
} from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { DataTable, type Column } from "../components/DataTable";
import { PageHeader, SectionTitle, StatCard } from "../components/ui";
import { api } from "../lib/api";
import { useCan } from "../lib/auth";
import { fmtShort } from "../lib/format";
import { fail, ok } from "../lib/notify";

type Decision = { code: string; ga: string; form: string | null; kind: string; reason: string; skip_reason: string;
  gap_days: number | null; last_visit: string | null; next_appt: string | null };
type Data = {
  today: string; rules_version: string;
  policy: { min_gap_days: number; skip_if_visit_within_days: number; stop_routine_from_ga_weeks: number; resend_block_days: number;
    postpartum_days: number[]; reminder_hours: number };
  counts: { routine: number; m_tha: number; m_dtd: number; postpartum: number; skip: number; proposals: number };
  send: Decision[]; skip: Decision[];
  proposals: { patient_id: number; code: string; flags: string[]; modules: string[]; current: string[] }[];
  reminders_due: number; call_list: { code: string; forms: string[] }[];
};

const gapCol: Column<Decision> = { key: "gap_days", label: "Khoảng trống", width: 160, render: (d) => (
  <Group gap={8} wrap="nowrap"><Progress value={Math.min(100, ((d.gap_days ?? 0) / 56) * 100)} w={70} size="sm" color="brand" />
    <Text size="xs" c="dimmed">{d.gap_days ?? 0} ngày</Text></Group>) };
const baseCols: Column<Decision>[] = [
  { key: "code", label: "Mã", render: (d) => <Text fw={600} size="sm">{d.code}</Text> },
  { key: "ga", label: "Tuần thai" },
  { key: "last_visit", label: "Khám gần nhất", render: (d) => (d.last_visit ? fmtShort(d.last_visit) : "—") },
  { key: "next_appt", label: "Hẹn tới", render: (d) => (d.next_appt ? fmtShort(d.next_appt) : "—") },
  gapCol,
  { key: "form", label: "Form", render: (d) => (d.form ? <Badge variant="light">{d.form}</Badge> : "—") },
];

export function FormSchedule() {
  const qc = useQueryClient();
  const can = useCan();
  const { data, isLoading } = useQuery({ queryKey: ["form-schedule"], queryFn: () => api<Data>("/api/form-schedule") });
  const [files, setFiles] = useState<string[]>([]);
  const [insulin, setInsulin] = useState<Record<number, boolean>>({});
  const refresh = () => { qc.invalidateQueries({ queryKey: ["form-schedule"] }); qc.invalidateQueries({ queryKey: ["overview"] }); };

  const exp = useMutation({
    mutationFn: () => api<{ channel: string; ok: number; blocked: number; file: string | null }>("/api/form-schedule/export", { method: "POST" }),
    onSuccess: (r) => { ok(`Kênh ${r.channel}: ${r.ok} tin; bị chặn ${r.blocked}.`, "Đã lưu lịch & xuất SMS"); if (r.file) setFiles((f) => [...f, r.file!]); refresh(); },
    onError: (e) => fail(e),
  });
  const remind = useMutation({
    mutationFn: () => api<{ ok: number; file: string | null }>("/api/form-schedule/reminders", { method: "POST" }),
    onSuccess: (r) => { ok(`Đã tạo ${r.ok} tin nhắc.`); if (r.file) setFiles((f) => [...f, r.file!]); refresh(); },
    onError: (e) => fail(e),
  });
  const confirmMod = useMutation({
    mutationFn: (pid: number) => api(`/api/form-schedule/proposals/${pid}/confirm`, { method: "POST", json: { insulin: !!insulin[pid] } }),
    onSuccess: () => { ok("Đã xác nhận module."); refresh(); }, onError: (e) => fail(e),
  });

  if (isLoading || !data) return <Stack><Skeleton h={40} w={300} /><Skeleton h={120} radius="lg" /><Skeleton h={400} radius="lg" /></Stack>;
  const P = data.policy;
  const c = data.counts;

  return (
    <>
      <PageHeader title="Lịch gửi form" subtitle={`Hôm nay ${fmtShort(data.today)} · quy tắc ${data.rules_version}`}
        actions={<Button leftSection={<IconSend size={18} />} loading={exp.isPending} disabled={!data.send.length || !can("export_form_sms")}
          onClick={() => exp.mutate()}>Lưu lịch & xuất SMS ({data.send.length})</Button>} />

      <Alert variant="light" color="brand" icon={<IconInfoCircle size={18} />} mb="lg" radius="lg">
        <Text size="sm">Chỉ thai phụ <b>đã đồng thuận</b>. Form định kỳ khi khoảng trống đến lần hẹn tới &gt; {P.min_gap_days} ngày;
          không gửi nếu hẹn trong {P.skip_if_visit_within_days} ngày, tuần thai ≥ {P.stop_routine_from_ga_weeks} (trừ module), đã gửi trong {P.resend_block_days} ngày
          hoặc đã trả lời. M-THA hằng ngày, M-ĐTĐ mỗi 3 ngày; sau sinh S1–S4 ngày {P.postpartum_days.join(", ")}.</Text>
      </Alert>

      {files.length > 0 && (
        <Group mb="lg" gap="sm">{files.map((n) => (
          <Button key={n} component="a" href={`/api/form-schedule/file/${encodeURIComponent(n)}`} variant="light" size="xs"
            leftSection={<IconDownload size={16} />}>{n}</Button>))}</Group>
      )}

      <SimpleGrid cols={{ base: 2, sm: 3, xl: 6 }} mb="lg">
        <StatCard label="Định kỳ F1–F6" value={c.routine} icon={<IconCalendarRepeat size={22} />} />
        <StatCard label="Module M-THA" value={c.m_tha} icon={<IconActivityHeartbeat size={22} />} color="danger" />
        <StatCard label="Module M-ĐTĐ" value={c.m_dtd} icon={<IconDroplet size={22} />} color="blue" />
        <StatCard label="Sau sinh S1–S4" value={c.postpartum} icon={<IconBabyCarriage size={22} />} color="grape" />
        <StatCard label="Không gửi" value={c.skip} icon={<IconBan size={22} />} color="gray" />
        <StatCard label="Đề xuất module" value={c.proposals} icon={<IconSparkles size={22} />} color="warn" />
      </SimpleGrid>

      <Paper p={0} mb="lg" style={{ overflow: "hidden" }}>
        <Tabs defaultValue="send" px="md" pt="sm">
          <Tabs.List>
            <Tabs.Tab value="send" rightSection={<Badge size="sm">{data.send.length}</Badge>}>Gửi hôm nay</Tabs.Tab>
            <Tabs.Tab value="skip" rightSection={<Badge size="sm" color="gray">{data.skip.length}</Badge>}>Không gửi</Tabs.Tab>
          </Tabs.List>
          <Tabs.Panel value="send" py="md">
            <DataTable rows={data.send} columns={[...baseCols, { key: "reason", label: "Lý do", render: (d) => <Text size="xs" c="dimmed">{d.reason}</Text> }]} />
          </Tabs.Panel>
          <Tabs.Panel value="skip" py="md">
            <DataTable rows={data.skip} columns={[...baseCols, { key: "skip_reason", label: "Không gửi vì", render: (d) => <Text size="xs" c="dimmed">{d.skip_reason}</Text> }]} />
          </Tabs.Panel>
        </Tabs>
      </Paper>

      <Grid gutter="lg">
        <Grid.Col span={{ base: 12, md: 6 }}>
          <Paper p="lg" h="100%">
            <SectionTitle icon={<IconSparkles size={18} />}>Đề xuất module bệnh lý (từ AI đọc ghi chú)</SectionTitle>
            {!data.proposals.length ? <Text size="sm" c="dimmed">Không có đề xuất mới.</Text> : (
              <Stack gap="sm">
                {data.proposals.map((p) => (
                  <Paper key={p.patient_id} p="sm" withBorder shadow="none" radius="md">
                    <Group justify="space-between" wrap="wrap" gap="xs">
                      <div>
                        <Text fw={700} size="sm">{p.code}</Text>
                        <Text size="xs" c="dimmed">Nguy cơ: {p.flags.join(", ")} → đề xuất <b>{p.modules.join(", ")}</b></Text>
                      </div>
                      <Group gap="sm">
                        {p.modules.includes("M-DTD") && <Checkbox size="xs" label="Dùng insulin" checked={!!insulin[p.patient_id]}
                          onChange={(e) => setInsulin((s) => ({ ...s, [p.patient_id]: e.currentTarget.checked }))} />}
                        <Button size="xs" loading={confirmMod.isPending && confirmMod.variables === p.patient_id}
                          onClick={() => confirmMod.mutate(p.patient_id)}>Xác nhận</Button>
                      </Group>
                    </Group>
                  </Paper>
                ))}
              </Stack>
            )}
          </Paper>
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 6 }}>
          <Paper p="lg" h="100%">
            <SectionTitle icon={<IconBell size={18} />}>Chưa trả lời</SectionTitle>
            <Group justify="space-between" mb="lg" wrap="wrap">
              <Text size="sm"><b>{data.reminders_due}</b> đường dẫn chưa trả lời sau {P.reminder_hours} giờ (nhắc 1 lần).</Text>
              <Button size="sm" variant="light" loading={remind.isPending} disabled={!data.reminders_due || !can("export_form_sms")}
                onClick={() => remind.mutate()}>Gửi nhắc</Button>
            </Group>
            <Group gap={6} mb="xs"><ThemeIcon size={22} variant="light" color="danger"><IconPhoneCall size={14} /></ThemeIcon>
              <Text size="sm" fw={600}>Cần gọi (module bỏ 2 lần liên tiếp)</Text></Group>
            {data.call_list.length ? (
              <Stack gap={4}>{data.call_list.map((x) => <Text key={x.code} size="sm"><b>{x.code}</b> – bỏ {x.forms.join(", ")}</Text>)}</Stack>
            ) : <Text size="sm" c="dimmed">Không có.</Text>}
          </Paper>
        </Grid.Col>
      </Grid>
    </>
  );
}
