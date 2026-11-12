import {
  Accordion, Alert, Badge, Button, Group, Paper, SimpleGrid, Skeleton, Stack, Table, Text,
} from "@mantine/core";
import {
  IconAlertOctagon, IconCheck, IconFlask, IconPhoneCheck, IconPlayerPlay, IconRobot, IconSchool, IconShieldCheck,
  IconTargetArrow, IconUsersGroup, IconX,
} from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DataTable, RecordsTable } from "../components/DataTable";
import { PageHeader, SectionTitle, StatCard } from "../components/ui";
import { api } from "../lib/api";
import { useCan } from "../lib/auth";
import { fmtDateTime } from "../lib/format";
import { fail, ok } from "../lib/notify";

type Records = { columns: string[]; rows: Record<string, unknown>[] };
type Card = { dataset: string; label: string; value: string; target: string; passed: boolean | null; blocking: boolean; run: string };
type Data = {
  cards: { red_sensitivity: number | null; arrival_agreement: number | null; arrival_reviewed: number; alert_agreement: number | null;
    transfer_rate: number | null; arrival_total: number; redteam_sensitivity: number | null; redteam_n: number; redteam_false_alarm: number | null };
  scorecard: Card[]; blocking_failed: boolean;
  notes: { ts: string; model: string; prompt: string; auto_rate: number | null; acc_auto: number | null; ga_acc: number | null } | null;
  forms: { rules_only: number | null; rules_plus_ai: number | null; extra_red: number; confusion: Record<string, Record<string, number>>;
    by_group: Record<string, number>; missed_red: string[] } | null;
  history: Records; weekly: Records; errors: Record<string, unknown>[];
  feedback: Feedback;
};
type Counts = { n: number; handled: number; keep: number; down: number; false_alarm: number;
  false_alarm_rate: number | null; downgrade_rate: number | null };
type Feedback = {
  overall: Counts; ai_escalated: Counts; review: string[];
  reasons: (Counts & { reason: string; levels: string[]; needs_review: boolean })[];
  by_rules_version: (Counts & { rules_version: string })[];
  config: { min_handled: number; review_false_alarm_rate: number; review_downgrade_rate: number };
};

function FeedbackSection({ fb }: { fb: Feedback }) {
  const o = fb.overall;
  return (
    <Paper p="lg" mb="lg">
      <SectionTitle icon={<IconSchool size={18} />}>Học từ kết quả gọi</SectionTitle>
      <Text size="sm" c="dimmed" mb="md">
        Mỗi lần điều dưỡng chọn “Giữ đỏ / Hạ vàng / Báo nhầm” là một nhãn đúng. Lý do nào hay bị báo nhầm sẽ được đưa lên
        để bác sĩ xem lại danh mục từ ngữ hoặc ngưỡng – hệ thống không tự sửa quy tắc.
      </Text>
      <SimpleGrid cols={{ base: 2, md: 4 }} mb="md">
        <StatCard label="Cảnh báo đã gọi" value={`${o.handled}/${o.n}`} hint="có chọn mức sau khi gọi" icon={<IconPhoneCheck size={22} />} />
        <StatCard label="Giữ mức" value={String(o.keep)} hint="hệ thống đúng" icon={<IconCheck size={22} />} color="ok" />
        <StatCard label="Báo nhầm" value={pct(o.false_alarm_rate)} hint={`${o.false_alarm} ca · hạ vàng ${o.down}`} icon={<IconX size={22} />} color="danger" />
        <StatCard label="AI nâng mức" value={String(fb.ai_escalated.n)} hint={`báo nhầm ${pct(fb.ai_escalated.false_alarm_rate)} trong số đã gọi`} icon={<IconRobot size={22} />} color="grape" />
      </SimpleGrid>
      {fb.review.length > 0 ? (
        <Alert color="warn" mb="md" icon={<IconAlertOctagon size={18} />} title="Cần bác sĩ xem lại">
          {fb.review.join(" · ")} – báo nhầm ≥ {pct(fb.config.review_false_alarm_rate)} hoặc hạ mức ≥ {pct(fb.config.review_downgrade_rate)}
          {" "}(tính khi có ít nhất {fb.config.min_handled} ca đã gọi).
        </Alert>
      ) : (
        <Text size="sm" c="dimmed" mb="md">Chưa có lý do nào cần xem lại (cần ít nhất {fb.config.min_handled} ca đã gọi cho mỗi lý do).</Text>
      )}
      <DataTable rows={fb.reasons} pageSize={15} empty="Chưa có cảnh báo." columns={[
        { key: "reason", label: "Cảnh báo về", render: (r) => <Group gap={6} wrap="nowrap"><Text size="sm" fw={600}>{r.reason}</Text>
          {r.needs_review && <Badge size="xs" color="warn">xem lại</Badge>}</Group> },
        { key: "n", label: "Số cảnh báo", align: "right" },
        { key: "handled", label: "Đã gọi", align: "right" },
        { key: "keep", label: "Giữ mức", align: "right" },
        { key: "down", label: "Hạ vàng", align: "right" },
        { key: "false_alarm", label: "Báo nhầm", align: "right" },
        { key: "false_alarm_rate", label: "Tỷ lệ báo nhầm", align: "right",
          render: (r) => <Text size="sm" fw={600} c={r.needs_review ? "danger.7" : undefined}>{pct(r.false_alarm_rate)}</Text> },
      ]} />
    </Paper>
  );
}

const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)}%`);
const LV = ["green", "yellow", "red"];
const LV_VI: Record<string, string> = { green: "Xanh", yellow: "Vàng", red: "Đỏ" };

export function Quality() {
  const can = useCan();
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["quality"], queryFn: () => api<Data>("/api/quality") });
  const run = useMutation({
    mutationFn: (ds: string) => api(`/api/quality/run/${ds}`, { method: "POST" }),
    onSuccess: (_r, ds) => { ok(`Đã chạy bộ ${ds}.`); qc.invalidateQueries({ queryKey: ["quality"] }); },
    onError: (e) => fail(e, "Không chạy được"),
  });

  if (isLoading || !data) return <Stack><Skeleton h={40} w={260} /><Skeleton h={120} radius="lg" /><Skeleton h={360} radius="lg" /></Stack>;
  const c = data.cards;

  return (
    <>
      <PageHeader title="Chất lượng AI" subtitle="Độ nhạy ca đỏ phải luôn 100% – chỉ tiêu bắt buộc không đạt thì không đổi phiên bản"
        actions={can("run_eval") && (["notes", "forms", "noshow"] as const).map((ds) => (
          <Button key={ds} variant="default" size="sm" leftSection={<IconPlayerPlay size={14} />}
            loading={run.isPending && run.variables === ds} disabled={run.isPending} onClick={() => run.mutate(ds)}>Chạy {ds}</Button>
        ))} />
      {run.isPending && <Alert mb="lg" color="brand" variant="light">Đang chạy bộ kiểm thử – với Ollama có thể mất nhiều phút.</Alert>}

      <SimpleGrid cols={{ base: 2, lg: 4 }} mb="lg">
        <StatCard label="Độ nhạy ca đỏ" value={pct(c.red_sensitivity)} hint="Chỉ tiêu 100%"
          icon={c.red_sensitivity === 1 ? <IconShieldCheck size={22} /> : <IconAlertOctagon size={22} />}
          color={c.red_sensitivity === 1 ? "ok" : c.red_sensitivity === null ? "gray" : "danger"} emphasis={c.red_sensitivity !== null && c.red_sensitivity < 1} />
        <StatCard label="AI khớp người" value={pct(c.arrival_agreement)} hint={`${c.arrival_reviewed} giờ đến · cảnh báo ${pct(c.alert_agreement)}`} icon={<IconUsersGroup size={22} />} />
        <StatCard label="Chuyển người" value={pct(c.transfer_rate)} hint={`trên ${c.arrival_total} lịch hẹn`} icon={<IconTargetArrow size={22} />} color="warn" />
        <StatCard label="Red-team" value={pct(c.redteam_sensitivity)} hint={`${c.redteam_n} ca · báo nhầm ${pct(c.redteam_false_alarm)}`} icon={<IconFlask size={22} />} color="grape" />
      </SimpleGrid>

      <Paper p="lg" mb="lg">
        <SectionTitle icon={<IconTargetArrow size={18} />}>Bảng chỉ tiêu (lần chạy mới nhất)</SectionTitle>
        {data.blocking_failed && <Alert color="danger" mb="md" icon={<IconAlertOctagon size={18} />}>
          Có chỉ tiêu BẮT BUỘC không đạt – không chuyển phiên bản prompt / mô hình / quy tắc.</Alert>}
        <DataTable rows={data.scorecard} pageSize={40} empty="Chưa có lần chạy đánh giá có bảng chỉ tiêu. Chạy: python scripts/run_eval.py --dataset forms" columns={[
          { key: "dataset", label: "Bộ", render: (r) => <Badge variant="light" color="gray">{r.dataset}</Badge> },
          { key: "label", label: "Chỉ tiêu", render: (r) => <Group gap={6} wrap="nowrap"><Text size="sm">{r.label}</Text>
            {r.blocking && <Badge size="xs" color="danger" variant="outline">bắt buộc</Badge>}</Group> },
          { key: "value", label: "Kết quả", render: (r) => <Text fw={700} size="sm">{r.value}</Text> },
          { key: "target", label: "Cần" },
          { key: "passed", label: "Đạt", align: "center", render: (r) => r.passed === null ? "—" : r.passed
            ? <IconCheck size={18} color="var(--mantine-color-ok-7)" /> : <IconX size={18} color="var(--mantine-color-danger-7)" /> },
          { key: "run", label: "Lần chạy", render: (r) => <Text size="xs" c="dimmed">{r.run}</Text> },
        ]} />
        <Stack gap={4} mt="md">
          {data.notes && <Text size="xs" c="dimmed">Ghi chú ({fmtDateTime(data.notes.ts)}, {data.notes.model}, {data.notes.prompt}): tự điền {pct(data.notes.auto_rate)} · đúng trong phần tự điền {pct(data.notes.acc_auto)} · tuần thai đúng {pct(data.notes.ga_acc)}</Text>}
          {data.forms && <Text size="xs" c="dimmed">Form: quy tắc {pct(data.forms.rules_only)} → quy tắc + AI {pct(data.forms.rules_plus_ai)}; AI bắt thêm {data.forms.extra_red} ca đỏ.</Text>}
        </Stack>
      </Paper>

      <FeedbackSection fb={data.feedback} />

      <Accordion variant="contained" radius="lg" multiple defaultValue={["history"]} styles={{ item: { background: "#fff" } }}>
        {data.forms && (
          <Accordion.Item value="confusion">
            <Accordion.Control>Ma trận nhầm lẫn (quy tắc + AI) và theo nhóm</Accordion.Control>
            <Accordion.Panel>
              <SimpleGrid cols={{ base: 1, md: 2 }}>
                <Table withTableBorder withColumnBorders ta="center" fz="sm">
                  <Table.Thead><Table.Tr><Table.Th>Đáp án \ Hệ thống</Table.Th>{LV.map((l) => <Table.Th key={l} ta="center">{LV_VI[l]}</Table.Th>)}</Table.Tr></Table.Thead>
                  <Table.Tbody>{LV.map((a) => (
                    <Table.Tr key={a}><Table.Td fw={600}>{LV_VI[a]}</Table.Td>{LV.map((b) => {
                      const v = data.forms!.confusion[a]?.[b] ?? 0;
                      const bad = LV.indexOf(b) < LV.indexOf(a) && v > 0;
                      return <Table.Td key={b} bg={a === b ? "ok.0" : bad ? "danger.1" : v ? "warn.0" : undefined} fw={a === b ? 700 : 400}>{v}</Table.Td>;
                    })}</Table.Tr>))}</Table.Tbody>
                </Table>
                <Stack gap={4}>{Object.entries(data.forms.by_group).map(([g, v]) => (
                  <Group key={g} justify="space-between"><Text size="sm">{g}</Text><Badge color={v >= 1 ? "ok" : v >= 0.8 ? "warn" : "danger"}>{pct(v)}</Badge></Group>))}</Stack>
              </SimpleGrid>
              {data.forms.missed_red.length > 0 && <Alert color="danger" mt="md">Red-team bỏ sót: {data.forms.missed_red.join("; ")}</Alert>}
            </Accordion.Panel>
          </Accordion.Item>
        )}
        <Accordion.Item value="history">
          <Accordion.Control>Theo tuần và phiên bản</Accordion.Control>
          <Accordion.Panel><RecordsTable data={data.history} pageSize={15} /></Accordion.Panel>
        </Accordion.Item>
        {data.weekly.rows.length > 0 && (
          <Accordion.Item value="weekly">
            <Accordion.Control>Giờ đến: AI / quy tắc so với người xác nhận</Accordion.Control>
            <Accordion.Panel><RecordsTable data={data.weekly} /></Accordion.Panel>
          </Accordion.Item>
        )}
        {data.feedback.by_rules_version.length > 0 && (
          <Accordion.Item value="fb-version">
            <Accordion.Control>Kết quả gọi theo phiên bản quy tắc (so trước / sau khi đổi danh mục)</Accordion.Control>
            <Accordion.Panel>
              <DataTable rows={data.feedback.by_rules_version} columns={[
                { key: "rules_version", label: "Quy tắc" },
                { key: "n", label: "Cảnh báo", align: "right" },
                { key: "handled", label: "Đã gọi", align: "right" },
                { key: "false_alarm_rate", label: "Báo nhầm", align: "right", render: (r) => pct(r.false_alarm_rate) },
                { key: "downgrade_rate", label: "Hạ mức hoặc báo nhầm", align: "right", render: (r) => pct(r.downgrade_rate) },
              ]} />
            </Accordion.Panel>
          </Accordion.Item>
        )}
        <Accordion.Item value="errors">
          <Accordion.Control>Lỗi AI gần đây (người sửa) · {data.errors.length}</Accordion.Control>
          <Accordion.Panel>
            {data.errors.length ? <RecordsTable data={{ columns: Object.keys(data.errors[0]), rows: data.errors }} /> : <Text size="sm" c="dimmed">Chưa có.</Text>}
          </Accordion.Panel>
        </Accordion.Item>
      </Accordion>
    </>
  );
}
