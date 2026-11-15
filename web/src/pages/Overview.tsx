import {
  Anchor, Badge, Box, Button, Grid, Group, Paper, RingProgress, SimpleGrid, Skeleton, Stack, Text, ThemeIcon, UnstyledButton,
} from "@mantine/core";
import {
  IconArrowRight, IconBell, IconCalendarEvent, IconCheck, IconChecklist, IconCircleDashed, IconClockHour4,
  IconMailForward, IconMessage2, IconUrgent,
} from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { reasonTags } from "../lib/reasons";
import { EmptyState, PageHeader, SectionTitle, StatCard } from "../components/ui";
import { api, type Overview as OverviewData } from "../lib/api";
import { useMe } from "../lib/auth";
import { fmtDate, relTime } from "../lib/format";
import classes from "./Overview.module.css";

function greeting() {
  const h = new Date().getHours();
  return h < 11 ? "Chào buổi sáng" : h < 14 ? "Chào buổi trưa" : h < 18 ? "Chào buổi chiều" : "Chào buổi tối";
}

export function Overview() {
  const { data: me } = useMe();
  const navigate = useNavigate();
  const { data, isLoading } = useQuery({ queryKey: ["overview"], queryFn: () => api<OverviewData>("/api/overview") });

  if (isLoading || !data) {
    return (
      <Stack>
        <Skeleton h={40} w={320} />
        <SimpleGrid cols={{ base: 2, lg: 4 }} spacing={{ base: "xs", sm: "md" }}>{[0, 1, 2, 3].map((i) => <Skeleton key={i} h={120} radius="lg" />)}</SimpleGrid>
        <Skeleton h={360} radius="lg" />
      </Stack>
    );
  }
  const m = data.metrics;
  const done = data.steps.filter((s) => s.done).length;
  const pct = Math.round((done / data.steps.length) * 100);

  return (
    <>
      <PageHeader title={`${greeting()}, ${me?.name ?? ""}`}
        subtitle={`${data.shift} · lịch hẹn ngày mai ${fmtDate(data.tomorrow)}`}
        actions={m.open_red > 0 && (
          <Button color="danger" leftSection={<IconUrgent size={18} />} onClick={() => navigate("/canh-bao")}>
            {m.open_red} cảnh báo đỏ cần xử lý
          </Button>
        )} />

      <SimpleGrid cols={{ base: 2, lg: 4 }} spacing={{ base: "xs", sm: "md" }} mb="lg">
        <StatCard label="Lịch hẹn ngày mai" value={m.appointments_tomorrow} hint={fmtDate(data.tomorrow)} icon={<IconCalendarEvent size={22} />} />
        <StatCard label="Form gửi hôm nay" value={m.forms_today} hint="đã xuất tin nhắn" icon={<IconMailForward size={22} />} color="blue" />
        <StatCard label="Câu trả lời 24 giờ" value={m.responses_24h} hint="từ thai phụ" icon={<IconMessage2 size={22} />} color="grape" />
        <StatCard label="Cảnh báo chưa xử lý" value={m.open_alerts} hint={m.open_red ? `${m.open_red} đỏ` : "không có ca đỏ"}
          icon={<IconBell size={22} />} color={m.open_red ? "danger" : "warn"} emphasis={m.open_red > 0} />
      </SimpleGrid>

      <Grid gutter="lg">
        <Grid.Col span={{ base: 12, md: 7 }}>
          <Paper p="lg" h="100%">
            <SectionTitle icon={<IconChecklist size={18} />}
              right={<Badge variant="light" color={done === data.steps.length ? "ok" : "brand"} size="lg">{done}/{data.steps.length} bước</Badge>}>
              Việc trong ngày
            </SectionTitle>
            <Group align="center" gap="xl" mb="md" wrap="nowrap">
              <RingProgress size={92} thickness={9} roundCaps sections={[{ value: pct, color: "brand" }]}
                label={<Text ta="center" fw={700} size="lg">{pct}%</Text>} />
              <Text size="sm" c="dimmed">Làm lần lượt từ trên xuống. Bước đã xong được đánh dấu tự động theo dữ liệu trong hệ thống.</Text>
            </Group>
            <Stack gap={8}>
              {data.steps.map((s, i) => (
                <UnstyledButton key={s.key} component={Link} to={s.to} className={classes.step} data-done={s.done || undefined}>
                  <Group justify="space-between" wrap="nowrap">
                    <Group gap="md" wrap="nowrap">
                      <ThemeIcon size={32} radius={99} variant={s.done ? "filled" : "light"} color={s.done ? "ok" : s.pending ? "warn" : "gray"}>
                        {s.done ? <IconCheck size={17} /> : s.pending ? <IconClockHour4 size={17} /> : <IconCircleDashed size={17} />}
                      </ThemeIcon>
                      <Box>
                        <Text size="xs" c="dimmed" fw={600}>BƯỚC {i + 1}</Text>
                        <Text fw={600} size="sm" td={s.done ? undefined : undefined}>{s.label}</Text>
                      </Box>
                    </Group>
                    <Group gap="sm" wrap="nowrap">
                      {s.done ? <Badge color="ok">Xong</Badge> : s.pending ? <Badge color="warn">Còn {s.pending} ca</Badge> : <Badge color="gray">Chưa làm</Badge>}
                      <IconArrowRight size={16} className={classes.arrow} />
                    </Group>
                  </Group>
                </UnstyledButton>
              ))}
            </Stack>
          </Paper>
        </Grid.Col>

        <Grid.Col span={{ base: 12, md: 5 }}>
          <Paper p="lg" h="100%">
            <SectionTitle icon={<IconBell size={18} />} right={<Anchor component={Link} to="/canh-bao" size="sm" fw={600}>Xem tất cả</Anchor>}>
              Cảnh báo mới nhất
            </SectionTitle>
            {!me?.permissions.includes("view_alerts") ? (
              <EmptyState icon={<IconCheck size={28} />} title="Không hiển thị với vai trò này"
                text="Cảnh báo có thông tin lâm sàng – chỉ điều dưỡng và bác sĩ được xem." />
            ) : data.latest_alerts.length === 0 ? (
              <EmptyState icon={<IconCheck size={28} />} title="Chưa có cảnh báo" text="Khi thai phụ trả lời form có dấu hiệu bất thường, cảnh báo sẽ hiện ở đây." />
            ) : (
              <Stack gap={8}>
                {data.latest_alerts.map((a) => (
                  <UnstyledButton key={a.id} component={Link} to={`/canh-bao/${a.id}`} className={classes.alert} data-level={a.level}>
                    <Group justify="space-between" wrap="nowrap" gap={6}>
                      <Group gap={8} wrap="nowrap">
                        <span className={classes.dot} data-level={a.level} />
                        <Text fw={700} size="sm">{a.code}</Text>
                      </Group>
                      {a.level === "red"
                        ? <span className={classes.waitBadge}>Chờ {relTime(a.created_at).replace(" trước", "")}</span>
                        : <Text size="xs" fw={600} c="warn.8">{relTime(a.created_at).replace(" trước", "")}</Text>}
                    </Group>
                    <Text size="sm" c="ink.8" mt={4} lineClamp={2}>{reasonTags(a.reasons ?? []).map((t) => t.text).join(" · ") || "Chưa ghi lý do"}</Text>
                    <Text size="xs" c="ink.5" mt={2}>{a.status_label}</Text>
                  </UnstyledButton>
                ))}
              </Stack>
            )}
          </Paper>
        </Grid.Col>
      </Grid>
    </>
  );
}
