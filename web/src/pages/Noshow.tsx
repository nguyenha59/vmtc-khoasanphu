import {
  Alert, Badge, Button, FileInput, Grid, Group, Paper, Progress, SimpleGrid, Skeleton, Stack, Text, TextInput, Tooltip,
} from "@mantine/core";
import {
  IconAlertTriangle, IconCalendar, IconCalendarX, IconChartBar, IconDownload, IconRefresh, IconStar, IconUpload, IconUsers,
} from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import dayjs from "dayjs";
import { useState } from "react";
import { DataTable } from "../components/DataTable";
import { PageHeader, SectionTitle, StatCard } from "../components/ui";
import { api } from "../lib/api";
import { useCan } from "../lib/auth";
import { fail, ok, postForm } from "../lib/notify";

type Row = { code: string; phone_masked: string; appt: string; services: string; prob: number; factors: string[]; action: string };
type Reinv = { code: string; phone_masked: string; appt: string; services: string; ga: string; priority: string; is_priority: boolean };
type Data = {
  date: string; yesterday: string; has_model: boolean; model: { version: string; auc_lgb: number; auc_lr: number } | null;
  call_threshold: number; sms_threshold: number; n_appt: number; rows: Row[]; reinvite: Reinv[]; high_risk: number;
  arrivals_imported: boolean;
  demo_model?: boolean;
  model_warning?: string | null;
};

export function Noshow() {
  const qc = useQueryClient();
  const can = useCan();
  const [d, setD] = useState(dayjs().add(1, "day").format("YYYY-MM-DD"));
  const [file, setFile] = useState<File | null>(null);
  const { data, isLoading } = useQuery({ queryKey: ["noshow", d], queryFn: () => api<Data>(`/api/noshow?d=${d}`) });
  const refresh = () => qc.invalidateQueries({ queryKey: ["noshow"] });

  const retrain = useMutation({
    mutationFn: () => api<{ version: string; auc_lgb: number; auc_lr: number }>("/api/noshow/retrain", { method: "POST" }),
    onSuccess: (r) => { ok(`AUC LightGBM ${r.auc_lgb.toFixed(3)} · Logistic ${r.auc_lr.toFixed(3)}`, `Mô hình ${r.version}`); refresh(); },
    onError: (e) => fail(e),
  });
  const arrivals = useMutation({
    mutationFn: () => { const f = new FormData(); f.append("file", file!); return postForm<{ rows: number }>("/api/noshow/arrivals", f); },
    onSuccess: (r) => { ok(`Đã nhập ${r.rows} lượt đến.`); setFile(null); refresh(); }, onError: (e) => fail(e),
  });

  const nCall = data?.rows.filter((r) => r.action === "Gọi xác nhận").length ?? 0;
  const nSms = data?.rows.filter((r) => r.action === "Gửi thêm SMS").length ?? 0;

  return (
    <>
      <PageHeader title="Dự đoán bỏ hẹn" subtitle="Xếp hạng nguy cơ không đến và danh sách mời đặt lại"
        actions={<>
          <TextInput type="date" value={d} onChange={(e) => e.currentTarget.value && setD(e.currentTarget.value)}
            leftSection={<IconCalendar size={16} />} w={190} aria-label="Ngày hẹn cần dự đoán" />
          {can("retrain_model") && <Button variant="default" leftSection={<IconRefresh size={16} />} loading={retrain.isPending}
            onClick={() => retrain.mutate()}>Huấn luyện lại</Button>}
        </>} />

      {isLoading || !data ? <Skeleton h={400} radius="lg" /> : !data.has_model ? (
        <Alert color="warn" icon={<IconAlertTriangle size={18} />} title="Chưa có mô hình">
          {data.model_warning || "Chưa có mô hình đủ điều kiện. Quản trị viên có thể huấn luyện lại khi dữ liệu đã sẵn sàng."}
        </Alert>
      ) : (
        <>
          {data.demo_model && (
            <Alert color="blue" variant="light" icon={<IconAlertTriangle size={18} />} mb="lg" title="Mô hình minh họa">
              Mô hình đang học trên dữ liệu giả lập – chỉ để trình diễn luồng làm việc, chưa dùng để quyết định gọi điện.
              Cần huấn luyện lại trên lịch hẹn và danh sách khách đến thật (tối thiểu vài tháng) rồi đánh giá trước khi dùng.
            </Alert>
          )}
          <SimpleGrid cols={{ base: 2, lg: 4 }} mb="lg">
            <StatCard label="Lịch hẹn" value={data.n_appt} hint={dayjs(data.date).format("DD/MM/YYYY")} icon={<IconUsers size={22} />} />
            <StatCard label={`Nguy cơ cao (≥ ${data.call_threshold})`} value={data.high_risk} icon={<IconAlertTriangle size={22} />} color="danger" />
            <StatCard label="Bỏ hẹn hôm qua" value={data.arrivals_imported ? data.reinvite.length : "—"}
              hint={data.arrivals_imported ? `${data.reinvite.filter((r) => r.is_priority).length} ưu tiên`
                : "Chưa nhập danh sách khách đến hôm qua"} icon={<IconCalendarX size={22} />} color="warn" />
            <StatCard label="AUC LightGBM" value={data.model!.auc_lgb.toFixed(3)}
              hint={`Logistic ${data.model!.auc_lr.toFixed(3)} · ${data.model!.version}`} icon={<IconChartBar size={22} />} color="grape" />
          </SimpleGrid>

          <Grid gutter="lg">
            <Grid.Col span={{ base: 12, lg: 7 }}>
              <Paper p="lg">
                <SectionTitle icon={<IconChartBar size={18} />} right={data.rows.length > 0 && (
                  <Button component="a" href={`/api/noshow/call-list.xlsx?d=${data.date}`} size="xs" variant="light"
                    leftSection={<IconDownload size={14} />}>Xuất danh sách gọi / SMS</Button>)}>
                  Xếp hạng nguy cơ không đến
                </SectionTitle>
                {data.rows.length > 0 && <Text size="xs" c="dimmed" mb="sm">Gọi xác nhận: {nCall} · Gửi thêm SMS: {nSms}</Text>}
                <DataTable rows={data.rows} pageSize={20} empty="Chưa có lịch hẹn cho ngày này (tải file HIS ở màn Lịch hẹn)." columns={[
                  { key: "code", label: "Mã", render: (r) => <Text fw={600} size="sm">{r.code}</Text> },
                  { key: "appt", label: "Lịch hẹn" },
                  { key: "prob", label: "Nguy cơ", width: 150, render: (r) => (
                    <Group gap={8} wrap="nowrap">
                      <Progress value={r.prob * 100} w={70} size="sm"
                        color={r.prob >= data.call_threshold ? "danger" : r.prob >= data.sms_threshold ? "warn" : "brand"} />
                      <Text size="xs" fw={600} style={{ fontVariantNumeric: "tabular-nums" }}>{r.prob.toFixed(2)}</Text>
                    </Group>) },
                  { key: "factors", label: "Yếu tố chính", render: (r) => (
                    <Tooltip label={r.factors.join(" · ")} multiline w={300}><Text size="xs" c="dimmed" lineClamp={2}>{r.factors.join(" · ")}</Text></Tooltip>) },
                  { key: "action", label: "Đề xuất", render: (r) => r.action === "—" ? <Text c="dimmed" size="sm">—</Text>
                    : <Badge color={r.action === "Gọi xác nhận" ? "danger" : "warn"}>{r.action}</Badge> },
                ]} />
              </Paper>
            </Grid.Col>
            <Grid.Col span={{ base: 12, lg: 5 }}>
              <Stack gap="lg">
                <Paper p="lg">
                  <SectionTitle icon={<IconCalendarX size={18} />} right={data.reinvite.length > 0 && (
                    <Button component="a" href="/api/noshow/reinvite.xlsx" size="xs" variant="light" leftSection={<IconDownload size={14} />}>Xuất file</Button>)}>
                    Mời đặt lại (bỏ hẹn {dayjs(data.yesterday).format("DD/MM")})
                  </SectionTitle>
                  {!data.arrivals_imported ? <Text size="sm" c="warn.8">Chưa nhập danh sách khách đến của hôm qua – nhập ở bên dưới thì mới biết ai bỏ hẹn.</Text>
                    : !data.reinvite.length ? <Text size="sm" c="dimmed">Hôm qua không có khách bỏ hẹn.</Text> : (
                    <Stack gap={6}>
                      {data.reinvite.map((r) => (
                        <Group key={r.code + r.appt} justify="space-between" wrap="nowrap" p="xs"
                          style={{ borderRadius: 8, background: r.is_priority ? "var(--mantine-color-warn-0)" : undefined }}>
                          <div>
                            <Group gap={6}><Text fw={600} size="sm">{r.code}</Text>
                              {r.is_priority && <Badge size="xs" color="warn" leftSection={<IconStar size={10} />}>{r.priority}</Badge>}</Group>
                            <Text size="xs" c="dimmed">{r.appt} · tuần {r.ga} · {r.services}</Text>
                          </div>
                          <Text size="xs" ff="monospace" c="dimmed">{r.phone_masked}</Text>
                        </Group>
                      ))}
                    </Stack>
                  )}
                </Paper>
                <Paper p="lg">
                  <SectionTitle icon={<IconUpload size={18} />}>Danh sách khách đến</SectionTitle>
                  <Text size="xs" c="dimmed" mb="sm">arrivals_YYYY-MM-DD.xlsx (cột pid, visit_date, checkin_time) – để lập danh sách bỏ hẹn.</Text>
                  <Group align="flex-end" wrap="nowrap">
                    <FileInput flex={1} placeholder="Chọn file" accept=".xlsx,.csv" value={file} onChange={setFile} clearable />
                    <Button disabled={!file} loading={arrivals.isPending} onClick={() => arrivals.mutate()}>Nhập</Button>
                  </Group>
                </Paper>
              </Stack>
            </Grid.Col>
          </Grid>
        </>
      )}
    </>
  );
}
