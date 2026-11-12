import {
  Accordion, Alert, Anchor, Badge, Box, Button, Checkbox, Grid, Group, Paper, SegmentedControl, Skeleton, Stack,
  Switch, Table, Text, Textarea, TextInput, ThemeIcon, Title, Tooltip, UnstyledButton,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import {
  IconAlertTriangle, IconArrowLeft, IconChartLine, IconCheck, IconChecks, IconChevronRight, IconFileText, IconLayersIntersect,
  IconListDetails, IconPhone, IconPhoneCall, IconRobot, IconRulerMeasure, IconSearch, IconSend, IconStethoscope, IconTrendingUp,
} from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { EmptyState, LevelBadge, LevelDot, PageHeader } from "../components/ui";
import { VitalsCharts } from "../components/VitalsChart";
import { api, type AlertDetail, type AlertList, type DownReview } from "../lib/api";
import { useCan } from "../lib/auth";
import { fmtDateTime, relTime } from "../lib/format";
import { LEVEL, type Level } from "../theme";
import classes from "./Alerts.module.css";

const TEMP_VI: Record<string, string> = { current: "hiện tại", past_resolved: "đã qua", unknown: "không rõ" };
const EXP_VI: Record<string, string> = { patient: "thai phụ", other: "người khác" };

function headline(reasons: string[]): string {
  return reasons.length ? reasons.join(" · ") : "Chưa ghi lý do";
}

/* ------------------------------------------------------------------ Danh sách */
export function Alerts() {
  const can = useCan();
  const [showClosed, setShowClosed] = useState(false);
  const [filter, setFilter] = useState("all");
  const [q, setQ] = useState("");

  const list = useQuery({
    queryKey: ["alerts", showClosed],
    queryFn: () => api<AlertList>(`/api/alerts?include_closed=${showClosed}`),
  });
  const items = useMemo(() => {
    const k = q.trim().toLowerCase();
    const seen = new Set<string>();
    return (list.data?.items ?? []).filter((i) => {
      const ok = (filter === "all" || (filter === "escalated" ? i.status === "escalated" : i.level === filter))
        && (!k || i.code.toLowerCase().includes(k) || i.reasons.some((r) => r.toLowerCase().includes(k)));
      if (!ok) return false;
      if (i.status === "closed") return true;
      // Gộp: mỗi thai phụ một dòng (dòng đầu = mức cao nhất, chờ lâu nhất – máy chủ đã sắp)
      if (seen.has(i.code)) return false;
      seen.add(i.code);
      return true;
    });
  }, [list.data, filter, q]);

  const digest = useMutation({
    mutationFn: () => api<{ sent: number }>("/api/alerts/digest", { method: "POST" }),
    onSuccess: (r) => notifications.show({ color: "ok", title: "Đã gửi bản gom vàng", message: `${r.sent} cảnh báo vàng.` }),
    onError: (e) => notifications.show({ color: "danger", title: "Không gửi được", message: String((e as Error).message) }),
  });

  const counts = list.data?.counts ?? { red: 0, yellow: 0, awaiting_doctor: 0, downgrade_reviews: 0 };

  return (
    <>
      <PageHeader title="Cảnh báo"
        subtitle={`Đỏ xử lý ngay · vàng gom gửi lúc ${(list.data?.digest_times ?? []).join(", ")} · mỗi thai phụ một dòng`}
        actions={can("handle_alerts") && (
          <Button variant="default" leftSection={<IconSend size={16} />} loading={digest.isPending} onClick={() => digest.mutate()}>
            Gửi bản gom vàng ngay
          </Button>
        )} />
      {can("review_downgrades") && counts.downgrade_reviews > 0 && <DowngradeReviews />}
      <Paper p={0} className={classes.listPaper}>
        <Group p="md" gap="sm" wrap="wrap" className={classes.listHead}>
          <SegmentedControl value={filter} onChange={setFilter} radius="md" data={[
            { value: "all", label: "Tất cả" },
            { value: "red", label: `Đỏ · ${counts.red}` },
            { value: "yellow", label: `Vàng · ${counts.yellow}` },
            { value: "escalated", label: `Chờ bác sĩ · ${counts.awaiting_doctor}` },
          ]} />
          <TextInput flex={1} miw={200} size="sm" placeholder="Tìm theo mã hoặc triệu chứng (vd: ra máu)" value={q}
            onChange={(e) => setQ(e.currentTarget.value)} leftSection={<IconSearch size={15} />} aria-label="Tìm cảnh báo" />
          <Switch size="sm" label="Hiện cả cảnh báo đã đóng" checked={showClosed} onChange={(e) => setShowClosed(e.currentTarget.checked)} />
        </Group>
        {list.isLoading && <Stack p="md">{[0, 1, 2, 3].map((i) => <Skeleton key={i} h={64} />)}</Stack>}
        {!list.isLoading && items.length === 0 && (
          <EmptyState icon={<IconCheck size={28} />} title="Không có cảnh báo cần xử lý" text="Danh sách trống với bộ lọc hiện tại." />
        )}
        {items.map((i) => (
          <UnstyledButton key={i.id} component={Link} to={`/canh-bao/${i.id}`} className={classes.row} data-level={i.level}>
            <Group gap="md" wrap="nowrap" align="center">
              <LevelDot level={i.level} />
              <Box flex={1} style={{ minWidth: 0 }}>
                <Group gap={8} wrap="wrap">
                  <Text fw={700} size="sm" c={i.level === "red" ? "danger.8" : "ink.9"}>Cảnh báo về: {headline(i.reasons)}</Text>
                  {i.demo && <Badge size="xs" color="gray" variant="outline">DEMO – nhân viên tự điền</Badge>}
                  {i.status !== "closed" && (i.patient_open ?? 0) > 1 && (
                    <Badge size="xs" color="brand" variant="light">+{(i.patient_open ?? 1) - 1} cảnh báo khác – gọi 1 lần</Badge>
                  )}
                </Group>
                <Text size="xs" c="dimmed" mt={2}>
                  <b>{i.code}</b> · tuần thai {i.ga} · form {i.form} · {relTime(i.created_at)}
                </Text>
              </Box>
              <Badge size="sm" variant={i.status === "open" || i.status === "escalated" ? "filled" : "light"}
                color={i.status === "open" ? (i.level === "red" ? "danger" : "warn") : i.status === "escalated" ? "grape" : "gray"}>
                {i.status_label}</Badge>
              <IconChevronRight size={18} color="var(--mantine-color-ink-4)" />
            </Group>
          </UnstyledButton>
        ))}
      </Paper>
    </>
  );
}

/* ------------------------------------------------------------------ Bác sĩ: đỏ đã hạ cần xem lại */
function DowngradeReviews() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["alert-reviews"], queryFn: () => api<{ items: DownReview[]; sample_rate: number }>("/api/alerts/reviews") });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["alert-reviews"] }); qc.invalidateQueries({ queryKey: ["alerts"] }); };
  const fail = (e: unknown) => notifications.show({ color: "danger", title: "Lỗi", message: (e as Error).message });
  const ack = useMutation({
    mutationFn: (ids: number[]) => api<{ acked: number }>("/api/alerts/reviews/ack", { method: "POST", json: { review_ids: ids } }),
    onSuccess: (r) => { notifications.show({ color: "ok", message: `Đã xem ${r.acked} ca.` }); refresh(); }, onError: fail,
  });
  const reopen = useMutation({
    mutationFn: (rid: number) => api<{ alert_id: number }>(`/api/alerts/reviews/${rid}/reopen`, { method: "POST" }),
    onSuccess: () => { notifications.show({ color: "ok", message: "Đã mở lại – cảnh báo chuyển “Chờ bác sĩ”." }); refresh(); },
    onError: fail,
  });
  const items = q.data?.items ?? [];
  if (!items.length) return null;
  return (
    <Paper p="lg" mb="lg" className={classes.reviews}>
      <Group justify="space-between" mb="sm" wrap="wrap">
        <Box>
          <Text fw={700}>Đỏ đã hạ – bác sĩ xem lại ({items.length})</Text>
          <Text size="xs" c="dimmed">
            Điều dưỡng đã gọi và hạ mức. Lướt qua; ca nào thấy chưa ổn thì “Mở lại”. Tỷ lệ đưa vào xem lại:
            {" "}{Math.round((q.data?.sample_rate ?? 1) * 100)}% (lý do nguy hiểm nhất luôn 100%).
          </Text>
        </Box>
        <Button leftSection={<IconChecks size={16} />} loading={ack.isPending}
          onClick={() => ack.mutate(items.map((i) => i.review_id))}>Đã xem tất cả</Button>
      </Group>
      <Table verticalSpacing="xs" fz="sm" highlightOnHover>
        <Table.Thead><Table.Tr>
          <Table.Th>Thai phụ</Table.Th><Table.Th>Cảnh báo về</Table.Th><Table.Th>Điều dưỡng chốt</Table.Th>
          <Table.Th>Ghi xử trí</Table.Th><Table.Th w={110} />
        </Table.Tr></Table.Thead>
        <Table.Tbody>{items.map((i) => (
          <Table.Tr key={i.review_id}>
            <Table.Td><Anchor component={Link} to={`/canh-bao/${i.alert_id}`} fw={600}>{i.code}</Anchor></Table.Td>
            <Table.Td>{headline(i.reasons)}</Table.Td>
            <Table.Td><Badge variant="light" color={i.final_level === "false_alarm" ? "gray" : "warn"}>{i.final_label}</Badge>
              <Text size="xs" c="dimmed">{i.by} · {relTime(i.handled_at)}</Text></Table.Td>
            <Table.Td><Text size="sm" lineClamp={2}>{i.note || "—"}</Text></Table.Td>
            <Table.Td><Button size="xs" variant="light" color="danger" loading={reopen.isPending && reopen.variables === i.review_id}
              onClick={() => reopen.mutate(i.review_id)}>Mở lại</Button></Table.Td>
          </Table.Tr>
        ))}</Table.Tbody>
      </Table>
    </Paper>
  );
}

/* ------------------------------------------------------------------ Màn chi tiết */
export function AlertPage() {
  const { id } = useParams();
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["alerts", false], queryFn: () => api<AlertList>("/api/alerts?include_closed=false") });
  return (
    <Stack gap="md">
      <Anchor component={Link} to="/canh-bao" size="sm" c="dimmed">
        <Group gap={4}><IconArrowLeft size={16} /> Danh sách cảnh báo</Group>
      </Anchor>
      <AlertDetailView key={id} id={Number(id)} finalLevels={list.data?.final_levels ?? {}}
        onChanged={() => {
          qc.invalidateQueries({ queryKey: ["alerts"] });
          qc.invalidateQueries({ queryKey: ["overview"] });
        }} />
    </Stack>
  );
}

function LayerCard({ icon, title, level, children }: { icon: ReactNode; title: string; level: string; children: ReactNode }) {
  return (
    <Paper p="md" h="100%" className={classes.layer} data-level={level}>
      <Group justify="space-between" mb="xs" wrap="nowrap">
        <Group gap={8} wrap="nowrap">
          <ThemeIcon variant="light" color={LEVEL[level as Level]?.color ?? "gray"} size={28}>{icon}</ThemeIcon>
          <Text fw={600} size="sm">{title}</Text>
        </Group>
        <LevelBadge level={level} size="sm" />
      </Group>
      <Stack gap={4}>{children}</Stack>
    </Paper>
  );
}

function AlertDetailView({ id, finalLevels, onChanged }: {
  id: number; finalLevels: Record<string, string>; onChanged: () => void;
}) {
  const can = useCan();
  const qc = useQueryClient();
  const { data: d, isLoading, error } = useQuery({ queryKey: ["alert", id], queryFn: () => api<AlertDetail>(`/api/alerts/${id}`), retry: false });
  const [phone, setPhone] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [finalLevel, setFinalLevel] = useState<string | null>(null);
  const [close, setClose] = useState(true);

  useEffect(() => {
    if (d) {
      setNote(d.note);
      setFinalLevel(d.final_level);
    }
  }, [d]);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["alert", id] });
    onChanged();
  };
  const fail = (e: unknown) => notifications.show({ color: "danger", title: "Lỗi", message: (e as Error).message });
  const reveal = useMutation({
    mutationFn: () => api<{ phone: string }>(`/api/alerts/${id}/reveal-phone`, { method: "POST" }),
    onSuccess: (r) => { setPhone(r.phone); refresh(); }, onError: fail,
  });
  const escalate = useMutation({
    mutationFn: () => api(`/api/alerts/${id}/escalate`, { method: "POST" }),
    onSuccess: () => { notifications.show({ color: "ok", message: "Đã ghi: đã báo bác sĩ." }); refresh(); }, onError: fail,
  });
  const [joint, setJoint] = useState(true); // xử trí chung các cảnh báo khác của cùng thai phụ
  const alsoIds = joint ? (d?.siblings ?? []).map((x) => x.id) : [];
  const done = (r: { status: string }) => {
    notifications.show({
      color: "ok", title: "Đã lưu xử trí",
      message: r.status === "awaiting_doctor" ? "Đã chuyển bác sĩ quyết định (ca giữ đỏ)." : r.status === "closed" ? "Cảnh báo đã đóng." : "Đã cập nhật.",
    });
    refresh();
  };
  const save = useMutation({
    mutationFn: () => api<{ status: string }>(`/api/alerts/${id}/handle`, { method: "POST",
      json: { note, final_level: finalLevel, close, also_ids: alsoIds } }),
    onSuccess: done, onError: fail,
  });
  const decide = useMutation({
    mutationFn: (decision: string) => api<{ status: string }>(`/api/alerts/${id}/handle`, { method: "POST",
      json: { note, final_level: "keep_red", close: true, decision, also_ids: alsoIds } }),
    onSuccess: done, onError: fail,
  });

  if (error) return <Paper p="xl"><EmptyState icon={<IconAlertTriangle size={28} />} title="Không mở được cảnh báo" text={(error as Error).message} /></Paper>;
  if (isLoading || !d) return <Stack><Skeleton h={140} radius="lg" /><Skeleton h={200} radius="lg" /><Skeleton h={280} radius="lg" /></Stack>;
  const L = d.layers;
  const isRed = d.level === "red";
  const closed = d.status === "closed";
  const isDoctor = can("decide_red");
  const keepRed = finalLevel === "keep_red";
  // Ca đỏ: phải chọn mức sau gọi mới đóng; bác sĩ "Giữ đỏ" thì dùng nút quyết định
  const saveBlocked = closed || !can("handle_alerts") || (isRed && close && !finalLevel) || (isRed && close && keepRed && isDoctor);
  const saveLabel = isRed && close && keepRed && !isDoctor ? "Lưu – chuyển bác sĩ quyết định" : "Lưu xử trí";

  return (
    <Stack gap="lg">
      {/* Đầu trang: cảnh báo về cái gì */}
      <Paper p="lg" className={classes.head} data-level={d.level}>
        <Group gap="sm" mb={6}>
          <LevelBadge level={d.level} size="lg" />
          <Badge color="gray" variant="light">{d.status_label}</Badge>
          {d.ai_escalated && <Badge color="violet" variant="light" leftSection={<IconRobot size={12} />}>AI nâng mức</Badge>}
          {d.demo && <Badge color="gray" variant="outline">DEMO – nhân viên tự điền, không phải thai phụ gửi</Badge>}
        </Group>
        <Text size="sm" c="dimmed" fw={500}>Cảnh báo về</Text>
        <Title order={2} className={classes.headline} data-level={d.level}>{headline(d.reasons)}</Title>
        <Group gap="lg" mt="sm" wrap="wrap">
          <Text size="sm"><Text span c="dimmed">Thai phụ </Text><b>{d.code}</b></Text>
          <Text size="sm"><Text span c="dimmed">Tuần thai </Text>{d.ga}</Text>
          <Text size="sm"><Text span c="dimmed">Form </Text>{d.form}</Text>
          <Text size="sm"><Text span c="dimmed">Gửi lúc </Text>{fmtDateTime(d.submitted_at)}</Text>
          <Text size="sm" ff="monospace"><Text span c="dimmed" ff="inherit">SĐT </Text>{d.phone_masked}</Text>
        </Group>
      </Paper>

      <Grid gutter="lg" align="flex-start">
        <Grid.Col span={{ base: 12, md: 8 }}>
          <Stack gap="lg">
            {/* Vì sao */}
            <Paper p="lg">
              <Group gap={8} mb="md">
                <ThemeIcon variant="light" size={28}><IconListDetails size={16} /></ThemeIcon>
                <Text fw={700}>Vì sao có cảnh báo này</Text>
              </Group>
              {d.why.length === 0 && <Text size="sm" c="dimmed">Không có lý do chi tiết được lưu.</Text>}
              <Stack gap={0}>
                {d.why.map((w, i) => (
                  <Group key={i} gap="md" wrap="nowrap" align="flex-start" className={classes.why} data-level={w.level}>
                    <Box pt={6}><LevelDot level={w.level} /></Box>
                    <Box flex={1} style={{ minWidth: 0 }}>
                      <Group gap={8} wrap="wrap">
                        <Text fw={700}>{w.title}</Text>
                        <LevelBadge level={w.level} size="sm" />
                      </Group>
                      <Text size="xs" c="dimmed" tt="uppercase" fw={600} mt={2}>{w.source}</Text>
                      {w.detail && <Text size="sm" mt={4}>{w.detail}</Text>}
                    </Box>
                  </Group>
                ))}
              </Stack>
              <Alert mt="md" variant="light" color="brand" icon={<IconStethoscope size={18} />} title="Tóm tắt cho bác sĩ" radius="md">
                {d.summary || "—"}
              </Alert>
            </Paper>

            {(d.charts.bp || d.charts.glucose) && (
              <Paper p="lg">
                <Group gap={8} mb="md"><ThemeIcon variant="light" size={28}><IconChartLine size={16} /></ThemeIcon><Text fw={600}>Chỉ số 45 ngày</Text></Group>
                <VitalsCharts charts={d.charts} />
              </Paper>
            )}

            <Accordion variant="contained" radius="lg" chevronPosition="right" className={classes.accordion} multiple>
              <Accordion.Item value="answers">
                <Accordion.Control icon={<IconFileText size={18} />}>Câu trả lời gốc ({d.answers.length} câu)</Accordion.Control>
                <Accordion.Panel>
                  <Table striped highlightOnHover verticalSpacing="xs" fz="sm">
                    <Table.Thead><Table.Tr><Table.Th>Câu hỏi</Table.Th><Table.Th w={120}>Trả lời</Table.Th></Table.Tr></Table.Thead>
                    <Table.Tbody>
                      {d.answers.map((a) => (
                        <Table.Tr key={a.key} fw={a.value === "Có" ? 700 : undefined}>
                          <Table.Td>{a.text}</Table.Td><Table.Td fw={600}>{String(a.value)}</Table.Td>
                        </Table.Tr>
                      ))}
                    </Table.Tbody>
                  </Table>
                  <Text size="sm" mt="md"><b>Phần tự viết:</b> {d.free_text || <Text span c="dimmed">(trống)</Text>}</Text>
                </Accordion.Panel>
              </Accordion.Item>
              <Accordion.Item value="layers">
                <Accordion.Control icon={<IconLayersIntersect size={18} />}>Chi tiết 3 lớp đánh giá (quy tắc · xu hướng · AI)</Accordion.Control>
                <Accordion.Panel>
                  <Grid gutter="md">
                    <Grid.Col span={{ base: 12, sm: 4 }}>
                      <LayerCard icon={<IconRulerMeasure size={16} />} title="Quy tắc" level={L.rule.level}>
                        {L.rule.reasons.length ? L.rule.reasons.map((r) => <Text key={r} size="sm">• {r}</Text>) : <Text size="sm" c="dimmed">Không có</Text>}
                        {L.rule.implausible.length > 0 && <Text size="xs" c="warn.8">Nghi nhập sai: {L.rule.implausible.join(", ")}</Text>}
                      </LayerCard>
                    </Grid.Col>
                    <Grid.Col span={{ base: 12, sm: 4 }}>
                      <LayerCard icon={<IconTrendingUp size={16} />} title="Xu hướng" level={L.trend.level}>
                        {L.trend.items.length ? L.trend.items.map((x) => <Text key={x.kind} size="sm">• {x.kind}: {x.reason}</Text>) : <Text size="sm" c="dimmed">Chưa đủ số đo</Text>}
                      </LayerCard>
                    </Grid.Col>
                    <Grid.Col span={{ base: 12, sm: 4 }}>
                      <LayerCard icon={<IconRobot size={16} />} title="AI" level={L.ai.level}>
                        {L.ai.confidence !== null && <Text size="xs" c="dimmed">Độ tin cậy {Math.round(L.ai.confidence * 100)}%</Text>}
                        {L.ai.findings.map((x, i) => (
                          <Tooltip key={i} label={`“${x.evidence}”`} multiline w={260} withArrow>
                            <Text size="sm">• {x.name}{x.present ? "" : " (phủ định)"} <Text span size="xs" c="dimmed">– {TEMP_VI[x.temporality] ?? x.temporality}, {EXP_VI[x.experiencer] ?? x.experiencer}</Text></Text>
                          </Tooltip>
                        ))}
                        {L.ai.findings.length === 0 && <Text size="sm" c="dimmed">Không thấy gì thêm</Text>}
                        {L.ai.needs_human && <Text size="xs" c="warn.8"><IconAlertTriangle size={12} /> AI không chắc – cần người xem</Text>}
                        <Text size="xs" c="dimmed" mt={4}>{L.ai.model || "—"} · prompt {L.ai.prompt_version || "—"} · quy tắc {L.ai.rules_version}</Text>
                      </LayerCard>
                    </Grid.Col>
                  </Grid>
                </Accordion.Panel>
              </Accordion.Item>
            </Accordion>
          </Stack>
        </Grid.Col>

        {/* Xử trí */}
        <Grid.Col span={{ base: 12, md: 4 }}>
          <Paper p="lg" className={classes.actions}>
            <Text fw={700} mb="md">Xử trí</Text>
            {d.doctor_decision?.status === "done" && (
              <Alert color="ok" variant="light" mb="md" icon={<IconStethoscope size={18} />} title={`Bác sĩ: ${d.doctor_decision.label}`}>
                {d.doctor_decision.note || "—"} · {fmtDateTime(d.doctor_decision.at)}
              </Alert>
            )}
            {d.status === "escalated" && (
              <Alert color="grape" variant="light" mb="md" icon={<IconStethoscope size={18} />} title="Chờ bác sĩ quyết định">
                {d.note || "Điều dưỡng đã gọi và giữ mức đỏ."}
              </Alert>
            )}
            {closed && <Text size="sm" c="dimmed" mb="md">Cảnh báo đã đóng.</Text>}

            {isRed && isDoctor && !closed && (
              <Box mb="md">
                <Text size="sm" fw={600} mb={6}>Quyết định của bác sĩ (giữ đỏ – 1 bấm)</Text>
                <Stack gap={6}>
                  {Object.entries(d.decisions).map(([k, label]) => (
                    <Button key={k} color={k === "emergency" ? "danger" : "brand"} variant={k === "emergency" ? "filled" : "light"}
                      loading={decide.isPending && decide.variables === k} onClick={() => decide.mutate(k)} fullWidth>{label}</Button>
                  ))}
                </Stack>
                <Text size="xs" c="dimmed" mt={6}>Ghi chú (không bắt buộc) lấy từ ô “Ghi xử trí” bên dưới.</Text>
              </Box>
            )}

            {d.siblings.length > 0 && !closed && (
              <Box mb="md" className={classes.siblings}>
                <Text size="sm" fw={600}>Thai phụ còn {d.siblings.length} cảnh báo khác chưa đóng</Text>
                {d.siblings.map((x) => (
                  <Text key={x.id} size="xs" component={Link} to={`/canh-bao/${x.id}`} display="block" c="dimmed">
                    • {x.level === "red" ? "Đỏ" : "Vàng"} – {headline(x.reasons)} (form {x.form}, {relTime(x.created_at)})
                  </Text>
                ))}
                <Checkbox mt={6} size="xs" label="Gọi 1 lần – áp dụng cùng xử trí cho các cảnh báo này" checked={joint}
                  onChange={(e) => setJoint(e.currentTarget.checked)} />
              </Box>
            )}
            <Stack gap="sm" mb="md">
              {phone ? (
                <Button component="a" href={`tel:${phone}`} color="brand" leftSection={<IconPhoneCall size={18} />} fullWidth>Gọi {phone}</Button>
              ) : (
                <Button leftSection={<IconPhone size={18} />} onClick={() => reveal.mutate()} loading={reveal.isPending}
                  disabled={!can("reveal_phone")} fullWidth>Gọi thai phụ (hiện số)</Button>
              )}
              {!isDoctor && (
                <Button variant="default" leftSection={<IconStethoscope size={18} />} onClick={() => escalate.mutate()}
                  loading={escalate.isPending} disabled={!can("handle_alerts") || closed || d.status === "escalated"} fullWidth>
                  Chuyển bác sĩ</Button>
              )}
              {phone && <Text size="xs" c="dimmed">Đã ghi nhật ký xem số điện thoại.</Text>}
            </Stack>
            <Stack gap="md">
              <Textarea label="Ghi xử trí" autosize minRows={3} value={note} onChange={(e) => setNote(e.currentTarget.value)}
                placeholder="Ví dụ: đã gọi, thai phụ hết đau, hẹn tái khám đúng lịch" />
              <Box>
                <Text size="sm" fw={500} mb={6}>Mức sau khi gọi{isRed && <Text span c="danger.7"> *</Text>}</Text>
                <SegmentedControl fullWidth value={finalLevel ?? ""} onChange={setFinalLevel}
                  data={Object.entries(finalLevels).map(([value, label]) => ({ value, label }))} />
                {isRed && close && !finalLevel && <Text size="xs" c="danger.7" mt={4}>Ca đỏ: chọn mức sau khi gọi trước khi đóng.</Text>}
                {isRed && !isDoctor && finalLevel && finalLevel !== "keep_red" && close && (
                  <Text size="xs" c="dimmed" mt={4}>Ca đỏ được hạ mức sẽ vào danh sách bác sĩ xem lại trong ngày.</Text>
                )}
                {isRed && isDoctor && keepRed && close && <Text size="xs" c="dimmed" mt={4}>Giữ đỏ: chọn một quyết định ở trên.</Text>}
              </Box>
              <Checkbox label="Đóng cảnh báo" checked={close} onChange={(e) => setClose(e.currentTarget.checked)} />
              <Button leftSection={<IconCheck size={18} />} onClick={() => save.mutate()} loading={save.isPending}
                disabled={saveBlocked} fullWidth>{saveLabel}</Button>
            </Stack>
          </Paper>
        </Grid.Col>
      </Grid>
    </Stack>
  );
}
