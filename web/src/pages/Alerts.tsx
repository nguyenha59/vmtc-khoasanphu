import {
  Anchor, Badge, Box, Button, Checkbox, Group, Paper, Skeleton, Stack, Table, Text, Textarea, UnstyledButton,
} from "@mantine/core";
import { useMediaQuery } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import {
  IconAlertTriangle, IconAmbulance, IconArrowLeft, IconCheck, IconChecks, IconCopy, IconFileDescription, IconListDetails,
  IconPhone, IconPhoneCall, IconSend,
} from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { EmptyState } from "../components/ui";
import { reasonTags } from "../lib/reasons";
import { VitalsCharts } from "../components/VitalsChart";
import { api, type AlertDetail, type AlertList, type DownReview } from "../lib/api";
import { useCan } from "../lib/auth";
import { relTime } from "../lib/format";
import { LEVEL, type Level } from "../theme";
import classes from "./Alerts.module.css";

/** Tiêu đề ngắn của cảnh báo – cùng cách đọc với nhãn lý do (bỏ thuật ngữ thống kê, bỏ trùng). */
function headline(reasons: string[]): string {
  const tags = reasonTags(reasons);
  if (!tags.length) return "Chưa ghi lý do";
  const shown = tags.slice(0, 3).map((t) => t.text).join(" · ");
  return tags.length > 3 ? `${shown} · +${tags.length - 3} lý do` : shown;
}

/** Thời gian chờ ngắn gọn: "35 phút", "5 giờ", "4 ngày". */
function waitText(iso: string | null): string {
  if (!iso) return "—";
  const m = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 60000));
  if (m < 60) return `${m} phút`;
  if (m < 60 * 24) return `${Math.floor(m / 60)} giờ`;
  return `${Math.floor(m / 1440)} ngày`;
}

const TIME_FMT = new Intl.DateTimeFormat("vi-VN", { timeZone: "Asia/Bangkok", hour: "2-digit", minute: "2-digit" });
const DATE_FMT = new Intl.DateTimeFormat("vi-VN", { timeZone: "Asia/Bangkok", day: "2-digit", month: "2-digit", year: "numeric" });
function fmtAt(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return `${TIME_FMT.format(d)}, ${DATE_FMT.format(d)}`;
}

const gaText = (ga: string) => (ga.startsWith("Hậu sản") ? ga : `Tuần ${ga}`);

/* ------------------------------------------------------------------ Bàn làm việc cảnh báo
 * Trái: danh sách cảnh báo mở (sắp theo mức, rồi thời gian chờ). Phải: ca đang chọn – đầu ca, lý do, tóm tắt,
 * câu trả lời gốc, và cột Xử trí. Màn hẹp: danh sách và chi tiết là hai màn riêng.
 */
export function Alerts() {
  return <AlertWorkspace />;
}

export function AlertPage() {
  const { id } = useParams();
  return <AlertWorkspace selectedId={Number(id)} />;
}

const YELLOW_PREVIEW = 3;

function AlertWorkspace({ selectedId }: { selectedId?: number }) {
  const can = useCan();
  const qc = useQueryClient();
  const nav = useNavigate();
  const wide = useMediaQuery("(min-width: 62em)", true, { getInitialValueInEffect: false });
  const [filter, setFilter] = useState<"all" | "red" | "yellow" | "escalated" | "closed">("all");
  const [moreYellow, setMoreYellow] = useState(false);
  const showClosed = filter === "closed";

  const list = useQuery({
    queryKey: ["alerts", showClosed],
    queryFn: () => api<AlertList>(`/api/alerts?include_closed=${showClosed}`),
  });
  const items = useMemo(() => {
    const seen = new Set<string>();
    return (list.data?.items ?? []).filter((i) => {
      const open = i.status !== "closed";
      if (filter === "closed") return !open;
      if (!open) return false;
      if (filter === "red" && i.level !== "red") return false;
      if (filter === "yellow" && i.level === "red") return false;
      if (filter === "escalated" && i.status !== "escalated") return false;
      // Gộp: mỗi thai phụ một dòng (dòng đầu = mức cao nhất, chờ lâu nhất – máy chủ đã sắp)
      if (seen.has(i.code)) return false;
      seen.add(i.code);
      return true;
    });
  }, [list.data, filter]);

  // Màn rộng: chưa chọn ca nào → mở ca đầu danh sách
  useEffect(() => {
    if (wide && !selectedId && items.length) nav(`/canh-bao/${items[0].id}`, { replace: true });
  }, [wide, selectedId, items, nav]);

  const digest = useMutation({
    mutationFn: () => api<{ sent: number }>("/api/alerts/digest", { method: "POST" }),
    onSuccess: (r) => notifications.show({ color: "ok", title: "Đã gửi bản gom vàng", message: `${r.sent} cảnh báo vàng.` }),
    onError: (e) => notifications.show({ color: "danger", title: "Không gửi được", message: String((e as Error).message) }),
  });

  const counts = list.data?.counts ?? { red: 0, yellow: 0, awaiting_doctor: 0, downgrade_reviews: 0 };
  const reds = items.filter((i) => i.level === "red" && i.status !== "closed");
  const yellows = items.filter((i) => i.level !== "red" && i.status !== "closed");
  const closedRows = items.filter((i) => i.status === "closed");
  const selectedHidden = yellows.findIndex((i) => i.id === selectedId) >= YELLOW_PREVIEW;
  const yellowShown = moreYellow || selectedHidden || filter === "yellow" ? yellows : yellows.slice(0, YELLOW_PREVIEW);
  const showList = wide || !selectedId;
  const showDetail = wide || !!selectedId;

  const chip = (key: typeof filter, label: string, n: number | null, tone: string) => (
    <UnstyledButton key={key} className={classes.chip} data-tone={tone} data-active={filter === key || undefined}
      onClick={() => setFilter(key)}>
      {label}{n !== null && <b>{n}</b>}
    </UnstyledButton>
  );

  return (
    <>
      {can("review_downgrades") && counts.downgrade_reviews > 0 && showList && <DowngradeReviews />}
      <div className={classes.layout}>
        {showList && (
          <Paper p={0} className={classes.listCard}>
            <Box px="md" pt="md" pb="sm" className={classes.listHead}>
              <Group justify="space-between" mb="sm">
                <Text fw={700} size="lg">Cảnh báo mở</Text>
                <Text size="sm" c="ink.5">{counts.red + counts.yellow} ca</Text>
              </Group>
              <Group gap={6}>
                {chip("all", "Tất cả", counts.red + counts.yellow, "ink")}
                {chip("red", "Khẩn", counts.red, "red")}
                {chip("yellow", "Theo dõi", counts.yellow, "yellow")}
                {counts.awaiting_doctor > 0 && chip("escalated", "Chờ bác sĩ", counts.awaiting_doctor, "grape")}
                {chip("closed", "Đã đóng", null, "gray")}
              </Group>
              <Text size="xs" c="ink.5" mt="sm">Sắp xếp: mức độ, rồi thời gian chờ lâu nhất</Text>
            </Box>

            <div className={classes.listScroll}>
              {list.isLoading && <Stack p="sm">{[0, 1, 2, 3].map((i) => <Skeleton key={i} h={84} radius="md" />)}</Stack>}
              {!list.isLoading && items.length === 0 && (
                <EmptyState icon={<IconCheck size={28} />} title="Không có cảnh báo" text="Danh sách trống với bộ lọc hiện tại." />
              )}

              {reds.length > 0 && (
                <>
                  <div className={classes.groupHead} data-tone="red">Khẩn · gọi ngay</div>
                  {reds.map((i) => (
                    <UnstyledButton key={i.id} component={Link} to={`/canh-bao/${i.id}`} className={classes.item}
                      data-level="red" data-active={i.id === selectedId || undefined}>
                      <Group justify="space-between" wrap="nowrap" gap={6}>
                        <Group gap={8} wrap="nowrap" style={{ minWidth: 0 }}>
                          <Text fw={700} size="sm">{i.code}</Text>
                          <Text size="xs" c="ink.5" truncate>{gaText(i.ga)}</Text>
                        </Group>
                        <span className={classes.waitBadge}>Chờ {waitText(i.created_at)}</span>
                      </Group>
                      <Text size="sm" c="ink.8" mt={6} lh={1.4}>{headline(i.reasons)}</Text>
                      <Group gap={6} mt={6}>
                        <Text size="xs" c="ink.5">Form {i.form} · {i.status === "escalated" ? "Chờ bác sĩ" : i.status_label}</Text>
                        {(i.patient_open ?? 0) > 1 && <Text size="xs" c="brand.7" fw={600}>+{(i.patient_open ?? 1) - 1} cảnh báo khác</Text>}
                        {i.demo && <Badge size="xs" color="gray" variant="outline">DEMO</Badge>}
                      </Group>
                    </UnstyledButton>
                  ))}
                </>
              )}

              {yellows.length > 0 && (
                <>
                  <div className={classes.groupHead} data-tone="yellow">Theo dõi · trong ca</div>
                  {yellowShown.map((i) => (
                    <UnstyledButton key={i.id} component={Link} to={`/canh-bao/${i.id}`} className={classes.item}
                      data-level="yellow" data-active={i.id === selectedId || undefined}>
                      <Group justify="space-between" wrap="nowrap" gap={6}>
                        <Group gap={8} wrap="nowrap" style={{ minWidth: 0 }}>
                          <Text fw={700} size="sm">{i.code}</Text>
                          <Text size="xs" c="ink.5" truncate>{gaText(i.ga)}</Text>
                        </Group>
                        <Text size="xs" fw={600} className={classes.waitText}>{waitText(i.created_at)}</Text>
                      </Group>
                      <Text size="sm" c="ink.8" mt={4} lh={1.4}>{headline(i.reasons)}</Text>
                      {i.status === "escalated" && <Text size="xs" c="grape.7" fw={600} mt={4}>Chờ bác sĩ</Text>}
                    </UnstyledButton>
                  ))}
                  {yellowShown.length < yellows.length && (
                    <Anchor component="button" size="sm" fw={600} c="brand.7" td="underline" px={4} py={8}
                      onClick={() => setMoreYellow(true)}>
                      Xem thêm {yellows.length - yellowShown.length} ca theo dõi
                    </Anchor>
                  )}
                </>
              )}

              {closedRows.length > 0 && (
                <>
                  <div className={classes.groupHead} data-tone="gray">Đã đóng</div>
                  {closedRows.map((i) => (
                    <UnstyledButton key={i.id} component={Link} to={`/canh-bao/${i.id}`} className={classes.item}
                      data-level={i.level} data-closed data-active={i.id === selectedId || undefined}>
                      <Group justify="space-between" wrap="nowrap" gap={6}>
                        <Text fw={700} size="sm">{i.code}</Text>
                        <Text size="xs" c="ink.5">{relTime(i.created_at)}</Text>
                      </Group>
                      <Text size="sm" c="ink.7" mt={4}>{headline(i.reasons)}</Text>
                      <Text size="xs" c="ink.5" mt={4}>{LEVEL[i.level as Level]?.label ?? i.level} · {i.status_label}</Text>
                    </UnstyledButton>
                  ))}
                </>
              )}
            </div>

            {can("handle_alerts") && (
              <Box px="md" py="sm" className={classes.listFoot}>
                <Button variant="subtle" size="xs" color="ink" leftSection={<IconSend size={14} />} loading={digest.isPending}
                  onClick={() => digest.mutate()}>Gửi bản gom vàng ngay</Button>
              </Box>
            )}
          </Paper>
        )}

        {showDetail && (
          <div className={classes.detailCol}>
            {!wide && (
              <Anchor component={Link} to="/canh-bao" size="sm" c="dimmed" mb="sm" display="block">
                <Group gap={4}><IconArrowLeft size={16} /> Danh sách cảnh báo</Group>
              </Anchor>
            )}
            {selectedId ? (
              <AlertDetailView key={selectedId} id={selectedId} finalLevels={list.data?.final_levels ?? {}}
                onChanged={() => {
                  qc.invalidateQueries({ queryKey: ["alerts"] });
                  qc.invalidateQueries({ queryKey: ["overview"] });
                }} />
            ) : (
              <Paper p="xl" className={classes.card}>
                <EmptyState icon={<IconListDetails size={28} />} title="Chọn một ca" text="Bấm một ca trong danh sách để xem chi tiết và xử trí." />
              </Paper>
            )}
          </div>
        )}
      </div>
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

/* ------------------------------------------------------------------ Chi tiết ca */
const FINAL_INFO: Record<string, { label: string; hint: string; tone: string }> = {
  keep_red: { label: "Giữ mức đỏ", hint: "Cần khám / chuyển cấp cứu", tone: "red" },
  down_yellow: { label: "Hạ xuống vàng", hint: "Đã ổn hơn, theo dõi tiếp", tone: "yellow" },
  false_alarm: { label: "Báo nhầm", hint: "Thai phụ điền sai / không có triệu chứng", tone: "gray" },
};

/** "Chị có ra máu âm đạo không? → trả lời: Có" → câu hỏi trong ngoặc kép + câu trả lời đậm. */
function Evidence({ detail }: { detail: string }) {
  const m = detail.match(/^(.*?)\s*→\s*trả lời:\s*(.*)$/);
  if (!m) return <Text size="sm" c="ink.8" mt={10}>{detail}</Text>;
  return <Text size="sm" c="ink.8" mt={10}>“{m[1]}” → <b>{m[2]}</b></Text>;
}

function sourceText(src: string): string {
  if (/^ai\b/i.test(src)) return `AI · ${src.replace(/^ai\s*/i, "")}`;
  if (/xu hướng/i.test(src)) return `Xu hướng · ${src}`;
  return `Quy tắc · ${src.charAt(0).toLowerCase()}${src.slice(1)}`;
}

function LayerCell({ n, name, level, note }: { n: number; name: string; level: string | null; note?: string }) {
  const def = level ? LEVEL[level as Level] : undefined;
  return (
    <div className={classes.layerCell}>
      <Text size="xs" c="ink.5">Lớp {n} · {name}</Text>
      <Text size="sm" fw={700} className={classes.layerVal} data-level={def ? level : undefined}>{note ?? def?.label ?? "Không có"}</Text>
    </div>
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
  const [allAnswers, setAllAnswers] = useState(false);
  const [allWhy, setAllWhy] = useState(false);
  const [joint, setJoint] = useState(true); // xử trí chung các cảnh báo khác của cùng thai phụ

  // Bản nháp chỉ lấy từ máy chủ khi mở cảnh báo (hoặc sau khi lưu xong) – tải lại do hiện số / chuyển bác sĩ /
  // focus cửa sổ không được xóa ghi chú đang soạn. `base` = phiên bản bản nháp dựa vào; khác CSDL → máy chủ trả 409.
  const [initFor, setInitFor] = useState<number | null>(null);
  const [base, setBase] = useState<number | null>(null);
  useEffect(() => {
    if (d && initFor !== d.id) {
      setNote(d.note);
      setFinalLevel(d.final_level);
      setBase(d.version);
      setInitFor(d.id);
    }
  }, [d, initFor]);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["alert", id] });
    onChanged();
  };
  const fail = (e: unknown) => notifications.show({ color: "danger", title: "Lỗi", message: (e as Error).message });
  const reveal = useMutation({
    mutationFn: () => api<{ phone: string; version: number }>(`/api/alerts/${id}/reveal-phone`, { method: "POST" }),
    onSuccess: (r) => { setPhone(r.phone); setBase(r.version); refresh(); }, onError: fail,
  });
  const escalate = useMutation({
    mutationFn: () => api<{ version: number }>(`/api/alerts/${id}/escalate`, { method: "POST" }),
    onSuccess: (r) => { notifications.show({ color: "ok", message: "Đã chuyển bác sĩ." }); setBase(r.version); refresh(); },
    onError: fail,
  });
  const alsoIds = joint ? (d?.siblings ?? []).map((x) => x.id) : [];
  const done = (r: { status: string }) => {
    notifications.show({
      color: "ok", title: "Đã lưu xử trí",
      message: r.status === "awaiting_doctor" ? "Đã chuyển bác sĩ quyết định (ca giữ đỏ)." : r.status === "closed" ? "Cảnh báo đã đóng." : "Đã cập nhật.",
    });
    setInitFor(null); // đã lưu → lấy lại bản nháp từ máy chủ
    refresh();
  };
  const save = useMutation({
    mutationFn: (close: boolean) => api<{ status: string }>(`/api/alerts/${id}/handle`, { method: "POST",
      json: { note, final_level: finalLevel, close, also_ids: alsoIds, version: base } }),
    onSuccess: done, onError: fail,
  });
  const decide = useMutation({
    mutationFn: (decision: string) => api<{ status: string }>(`/api/alerts/${id}/handle`, { method: "POST",
      json: { note, final_level: "keep_red", close: true, decision, also_ids: alsoIds, version: base } }),
    onSuccess: done, onError: fail,
  });

  if (error) return <Paper p="xl" className={classes.card}><EmptyState icon={<IconAlertTriangle size={28} />} title="Không mở được cảnh báo" text={(error as Error).message} /></Paper>;
  if (isLoading || !d) return <Stack><Skeleton h={96} radius="lg" /><Skeleton h={260} radius="lg" /><Skeleton h={200} radius="lg" /></Stack>;
  // Ca đỏ: hiện trước các lý do Đỏ (vì sao khẩn); lý do Vàng thu gọn để không rối.
  const hasRedWhy = d.why.some((w) => w.level === "red");
  const whyShown = hasRedWhy && !allWhy ? d.why.filter((w) => w.level === "red") : d.why;
  const hiddenWhy = d.why.length - (hasRedWhy ? d.why.filter((w) => w.level === "red").length : d.why.length);

  const L = d.layers;
  const isRed = d.level === "red";
  const closed = d.status === "closed";
  const isDoctor = can("decide_red");
  const keepRed = finalLevel === "keep_red";
  const canHandle = can("handle_alerts") && !closed;
  // Ca đỏ: phải chọn mức sau gọi mới đóng; bác sĩ "Giữ đỏ" thì dùng nút quyết định ở bước 1
  const closeBlocked = !canHandle || (isRed && !finalLevel) || (isRed && keepRed && isDoctor);
  const closeLabel = isRed && keepRed && !isDoctor ? "Lưu & chuyển bác sĩ quyết định" : "Lưu & đóng cảnh báo";
  const risk = d.summary.match(/Nhóm nguy cơ:\s*([^.]+)/)?.[1];

  // Câu trả lời bất thường = câu được nêu trong lý do cảnh báo; phần tự viết bất thường khi AI tìm thấy dấu hiệu
  const abnormalKeys = new Set(d.answers.filter((a) => d.why.some((w) => w.detail.includes(a.text))).map((a) => a.key));
  const freeAbnormal = !!d.free_text && L.ai.findings.some((f) => f.present);
  const nAbnormal = abnormalKeys.size + (freeAbnormal ? 1 : 0);
  const nTotal = d.answers.length + (d.free_text ? 1 : 0);
  const rows = d.answers.map((a, i) => ({ n: i + 1, ...a, bad: abnormalKeys.has(a.key) }))
    .filter((r) => allAnswers || r.bad);

  const copySummary = async () => {
    try {
      await navigator.clipboard.writeText(d.summary);
      notifications.show({ color: "ok", message: "Đã sao chép tóm tắt." });
    } catch {
      notifications.show({ color: "danger", message: "Trình duyệt không cho sao chép." });
    }
  };

  return (
    <Stack gap="md">
      {/* Đầu ca */}
      <Paper p="lg" className={classes.card}>
        <Group justify="space-between" wrap="wrap" gap="sm" align="flex-start">
          <Box style={{ minWidth: 0 }}>
            <Group gap="sm" wrap="wrap" align="center">
              <span className={classes.levelPill} data-level={d.level}>
                <IconAlertTriangle size={14} /> Mức {LEVEL[d.level as Level]?.label.toLowerCase() ?? d.level}
              </span>
              <Text fz={26} fw={800} lh={1.1} c="ink.9">{d.code}</Text>
              <Text size="sm" c="ink.6">{d.ga.startsWith("Hậu sản") ? d.ga : `Tuần thai ${d.ga}`}{risk ? ` · Nhóm nguy cơ: ${risk}` : ""}</Text>
              {d.ai_escalated && <Badge size="sm" color="violet" variant="light">AI nâng mức</Badge>}
              {d.demo && <Badge size="sm" color="gray" variant="outline">DEMO</Badge>}
            </Group>
            <Text size="sm" c="ink.6" mt={8}>
              Gửi Form {d.form} lúc {fmtAt(d.submitted_at)}
              {" · "}
              {closed ? <Text span c="ink.6">{d.status_label}</Text>
                : <Text span c={isRed ? "danger.7" : "warn.8"} fw={600}>chờ {waitText(d.created_at)}, {d.status === "escalated" ? "đang chờ bác sĩ" : d.status_label.toLowerCase()}</Text>}
            </Text>
          </Box>
          <Button component={Link} to={`/ho-so?q=${encodeURIComponent(d.code)}`} variant="default"
            leftSection={<IconFileDescription size={16} />}>Hồ sơ đầy đủ</Button>
        </Group>
      </Paper>

      <div className={classes.detailGrid}>
        <Stack gap="md" style={{ minWidth: 0 }}>
          {/* Vì sao */}
          <Paper p="lg" className={classes.card}>
            <Text fw={700} size="lg" mb="md">{isRed ? "Vì sao ca này khẩn" : "Vì sao ca này cần theo dõi"}</Text>
            {d.why.length === 0 && <Text size="sm" c="dimmed">Không có lý do chi tiết được lưu.</Text>}
            <div className={classes.whyGrid}>
              {whyShown.map((w, i) => (
                <div key={i} className={classes.why} data-level={w.level}>
                  <Group gap={8} wrap="nowrap" align="flex-start">
                    <span className={classes.dot} data-level={w.level} />
                    <Text fw={700} size="sm" c="ink.9" lh={1.35}>{w.title}</Text>
                  </Group>
                  <Text size="xs" c="ink.5" mt={6}>{sourceText(w.source)}</Text>
                  {w.detail && <Evidence detail={w.detail} />}
                  {w.basis ? (
                    <Text size="xs" c="ink.6" mt={8} className={classes.basis}><b>Căn cứ:</b> {w.basis}</Text>
                  ) : w.level === "red" ? (
                    <Text size="xs" c="danger.7" mt={8} className={classes.basis}><b>Căn cứ:</b> chưa ghi nguồn – báo bác sĩ rà quy tắc</Text>
                  ) : null}
                </div>
              ))}
            </div>
            {hiddenWhy > 0 && (
              <Button variant="subtle" size="xs" mt="sm" onClick={() => setAllWhy((v) => !v)}>
                {allWhy ? "Ẩn lý do mức Vàng" : `Xem thêm ${hiddenWhy} lý do mức Vàng`}
              </Button>
            )}
            <div className={classes.layers}>
              <LayerCell n={1} name="Quy tắc" level={L.rule.level} />
              <LayerCell n={2} name="Xu hướng" level={L.trend.items.length ? L.trend.level : null}
                note={L.trend.items.length ? undefined : "Không đủ dữ liệu"} />
              <LayerCell n={3} name="AI" level={L.ai.level} />
            </div>
            {L.rule.implausible.length > 0 && <Text size="xs" c="warn.8" mt="xs">Nghi nhập sai: {L.rule.implausible.join(", ")}</Text>}
            {L.ai.skipped ? <Text size="xs" c="ink.6" mt="xs">Phần tự viết đọc bằng quy tắc; bỏ qua AI vì quy tắc đã xác định Đỏ.</Text>
              : L.ai.needs_human && <Text size="xs" c="warn.8" mt="xs">AI không chắc – cần người xem.</Text>}
            {L.trend.items.map((x) => <Text key={x.kind} size="xs" c="ink.6" mt={4}>Xu hướng {x.kind}: {x.reason}</Text>)}
          </Paper>

          {/* Tóm tắt */}
          <Paper p="lg" className={classes.card}>
            <Group justify="space-between" wrap="nowrap" mb="sm" gap="xs" align="flex-start">
              <Group gap="xs" wrap="wrap" style={{ minWidth: 0 }}>
                <Text fw={700} size="lg">Tóm tắt cho bác sĩ</Text>
                <span className={classes.grayPill}>Tạo tự động · kiểm tra trước khi gửi</span>
              </Group>
              <Button size="xs" variant="default" leftSection={<IconCopy size={14} />} onClick={copySummary} style={{ flex: "none" }}>Sao chép</Button>
            </Group>
            <Text size="sm" c="ink.8" lh={1.6}>{d.summary || "—"}</Text>
          </Paper>

          {/* Câu trả lời gốc */}
          <Paper p={0} className={classes.card}>
            <Group justify="space-between" wrap="nowrap" p="lg" pb="sm" gap="xs">
              <Group gap="sm" align="baseline">
                <Text fw={700} size="lg">Câu trả lời gốc</Text>
                <Text size="sm" c="ink.5">{nTotal} câu · {nAbnormal} bất thường</Text>
              </Group>
              <Button size="xs" variant="default" onClick={() => setAllAnswers((v) => !v)} style={{ flex: "none" }}>
                {allAnswers ? "Chỉ câu bất thường" : `Hiện cả ${nTotal} câu`}
              </Button>
            </Group>
            <Table verticalSpacing={10} horizontalSpacing="lg" fz="sm" className={classes.answers}>
              <Table.Tbody>
                {rows.map((r) => (
                  <Table.Tr key={r.key} data-bad={r.bad || undefined}>
                    <Table.Td w={72} c="ink.5" fz="xs" style={{ whiteSpace: "nowrap" }}>Câu {r.n}</Table.Td>
                    <Table.Td c="ink.8">{r.text}</Table.Td>
                    <Table.Td ta="right" fw={r.bad ? 700 : 500} c={r.bad ? "danger.7" : "ink.8"}>{String(r.value)}</Table.Td>
                  </Table.Tr>
                ))}
                {d.free_text && (allAnswers || freeAbnormal) && (
                  <Table.Tr data-bad={freeAbnormal || undefined}>
                    <Table.Td w={72} c="ink.5" fz="xs" style={{ whiteSpace: "nowrap" }}>Tự viết</Table.Td>
                    <Table.Td c="ink.8">Phần thai phụ tự viết</Table.Td>
                    <Table.Td ta="right" fw={freeAbnormal ? 700 : 500} c={freeAbnormal ? "danger.7" : "ink.8"}>“{d.free_text}”</Table.Td>
                  </Table.Tr>
                )}
                {rows.length === 0 && !(d.free_text && freeAbnormal) && (
                  <Table.Tr><Table.Td colSpan={3} c="dimmed">Không có câu bất thường – bấm “Hiện cả {nTotal} câu”.</Table.Td></Table.Tr>
                )}
              </Table.Tbody>
            </Table>
          </Paper>

          {(d.charts.bp || d.charts.glucose) && (
            <Paper p="lg" className={classes.card}>
              <Text fw={700} size="lg" mb="md">Chỉ số 45 ngày</Text>
              <VitalsCharts charts={d.charts} />
            </Paper>
          )}
        </Stack>

        {/* Xử trí */}
        <Paper p="lg" className={`${classes.card} ${classes.actions}`}>
          <Text fw={700} size="lg" mb="md">Xử trí</Text>
          {d.doctor_decision?.status === "done" && (
            <Box className={classes.notice} data-tone="ok" mb="md">
              <Text size="sm" fw={700}>Bác sĩ: {d.doctor_decision.label}</Text>
              <Text size="xs" c="ink.6">{d.doctor_decision.note || "—"} · {fmtAt(d.doctor_decision.at)}</Text>
            </Box>
          )}
          {d.status === "escalated" && (
            <Box className={classes.notice} data-tone="grape" mb="md">
              <Text size="sm" fw={700}>Chờ bác sĩ quyết định</Text>
              <Text size="xs" c="ink.6">{d.note || "Điều dưỡng đã gọi và giữ mức đỏ."}</Text>
            </Box>
          )}
          {closed && <Text size="sm" c="dimmed" mb="md">Cảnh báo đã đóng.</Text>}

          <Group gap={8} mb="sm"><span className={classes.step}>1</span><Text fw={700} size="sm">Liên hệ thai phụ</Text></Group>
          <Stack gap="xs">
            {phone ? (
              <Button component="a" href={`tel:${phone}`} size="md" leftSection={<IconPhoneCall size={18} />} fullWidth>Gọi {phone}</Button>
            ) : (
              <Button size="md" leftSection={<IconPhone size={18} />} onClick={() => reveal.mutate()} loading={reveal.isPending}
                disabled={!can("reveal_phone")} fullWidth>Hiện số &amp; gọi</Button>
            )}
            {phone && <Text size="xs" c="dimmed">Đã ghi nhật ký xem số điện thoại.</Text>}
            {!isDoctor && (
              <Button size="md" variant="default" onClick={() => escalate.mutate()} loading={escalate.isPending}
                disabled={!canHandle || d.status === "escalated"} fullWidth>Chuyển bác sĩ kèm tóm tắt</Button>
            )}
            {isRed && isDoctor && !closed && (
              <>
                <Text size="xs" fw={600} c="ink.6" mt={4}>Quyết định của bác sĩ (giữ đỏ – 1 bấm)</Text>
                {Object.entries(d.decisions).map(([k, label]) => (
                  <Button key={k} color={k === "emergency" ? "danger" : "ink"} variant={k === "emergency" ? "filled" : "default"}
                    leftSection={k === "emergency" ? <IconAmbulance size={18} /> : null}
                    loading={decide.isPending && decide.variables === k} onClick={() => decide.mutate(k)} fullWidth>{label}</Button>
                ))}
              </>
            )}
            {d.siblings.length > 0 && !closed && (
              <Box className={classes.notice} data-tone="gray">
                <Text size="sm" fw={600}>Thai phụ còn {d.siblings.length} cảnh báo khác chưa đóng</Text>
                {d.siblings.map((x) => (
                  <Text key={x.id} size="xs" component={Link} to={`/canh-bao/${x.id}`} display="block" c="ink.6">
                    • {x.level === "red" ? "Đỏ" : "Vàng"} – {headline(x.reasons)} (form {x.form})
                  </Text>
                ))}
                <Checkbox mt={6} size="xs" label="Gọi 1 lần – áp dụng cùng xử trí" checked={joint}
                  onChange={(e) => setJoint(e.currentTarget.checked)} />
              </Box>
            )}
          </Stack>

          <div className={classes.divider} />

          <Group gap={8} mb="sm"><span className={classes.step}>2</span><Text fw={700} size="sm">Kết quả sau cuộc gọi</Text></Group>
          <Text size="sm" c="ink.7" mb={6}>Mức sau khi gọi{isRed && <Text span c="danger.7"> *</Text>}</Text>
          <Stack gap={8}>
            {Object.entries(finalLevels).map(([key, apiLabel]) => {
              const info = FINAL_INFO[key] ?? { label: apiLabel, hint: "", tone: "gray" };
              return (
                <UnstyledButton key={key} className={classes.option} data-active={finalLevel === key || undefined}
                  disabled={!canHandle} onClick={() => setFinalLevel(key)}>
                  <Group gap={10} wrap="nowrap" align="flex-start">
                    <span className={classes.optDot} data-tone={info.tone} />
                    <Box>
                      <Text size="sm" fw={600} c="ink.9">{info.label}</Text>
                      {info.hint && <Text size="xs" c="ink.5">{info.hint}</Text>}
                    </Box>
                  </Group>
                </UnstyledButton>
              );
            })}
          </Stack>
          {isRed && !isDoctor && finalLevel && finalLevel !== "keep_red" && (
            <Text size="xs" c="ink.5" mt={6}>Ca đỏ được hạ mức sẽ vào danh sách bác sĩ xem lại trong ngày.</Text>
          )}
          {isRed && isDoctor && keepRed && <Text size="xs" c="ink.5" mt={6}>Giữ đỏ: chọn một quyết định ở bước 1.</Text>}

          <Textarea label="Ghi xử trí" mt="md" autosize minRows={3} value={note} disabled={!canHandle}
            onChange={(e) => setNote(e.currentTarget.value)} placeholder="Ví dụ: đã gọi, thai phụ hết đau, hẹn tái khám đúng lịch" />

          <Button mt="md" size="md" fullWidth leftSection={<IconCheck size={18} />} disabled={closeBlocked}
            loading={save.isPending && save.variables === true} onClick={() => save.mutate(true)}>{closeLabel}</Button>
          <Button mt={6} variant="subtle" fullWidth disabled={!canHandle}
            loading={save.isPending && save.variables === false} onClick={() => save.mutate(false)}>Lưu, giữ cảnh báo mở</Button>
          {isRed && !finalLevel && canHandle && <Text size="xs" c="ink.5" ta="center" mt={4}>Chọn mức sau khi gọi để lưu &amp; đóng</Text>}
        </Paper>
      </div>
    </Stack>
  );
}
