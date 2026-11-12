import { Badge, Box, Group, Paper, Stack, Text, ThemeIcon, Title, type MantineColor } from "@mantine/core";
import { IconAlertTriangleFilled, IconCircleCheckFilled, IconRobot, IconTrendingUp, IconUrgent } from "@tabler/icons-react";
import type { ReactNode } from "react";
import type { ReasonTag } from "../lib/reasons";
import { LEVEL, type Level } from "../theme";

const LEVEL_ICON = { red: IconUrgent, yellow: IconAlertTriangleFilled, green: IconCircleCheckFilled };

export function LevelBadge({ level, label, size = "md" }: { level: string | null | undefined; label?: string; size?: "sm" | "md" | "lg" }) {
  const lv = (level ?? "") as Level;
  const def = LEVEL[lv];
  if (!def) return <Badge color="gray" size={size}>{label ?? level ?? "—"}</Badge>;
  const Icon = LEVEL_ICON[lv];
  return (
    <Badge color={def.color} size={size} variant="light" leftSection={<Icon size={size === "sm" ? 11 : 13} />}
      styles={{ root: { textTransform: "none", fontWeight: 600 } }}>
      {label ?? def.label}
    </Badge>
  );
}

export function LevelDot({ level }: { level: string }) {
  const def = LEVEL[level as Level];
  return (
    <Box w={10} h={10} style={{ borderRadius: 99, flex: "none", background: def?.hex ?? "#9AA9B6",
      boxShadow: `0 0 0 3px ${def ? def.hex + "22" : "#9AA9B622"}` }} />
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <Group justify="space-between" align="flex-end" mb="lg" wrap="wrap" gap="sm">
      <Stack gap={4}>
        <Title order={1}>{title}</Title>
        {subtitle && <Text c="dimmed" size="sm">{subtitle}</Text>}
      </Stack>
      {actions && <Group gap="xs">{actions}</Group>}
    </Group>
  );
}

/** Ô số liệu. Trên điện thoại: đệm nhỏ hơn, số nhỏ hơn, biểu tượng ẩn – để xếp 2 cột không bị chật. */
export function StatCard({ label, value, hint, icon, color = "brand", emphasis }: {
  label: string; value: ReactNode; hint?: ReactNode; icon: ReactNode; color?: MantineColor; emphasis?: boolean;
}) {
  return (
    <Paper p={{ base: "sm", sm: "lg" }} h="100%" style={emphasis ? { borderColor: "var(--mantine-color-danger-3)",
      background: "linear-gradient(180deg, var(--mantine-color-danger-0), #fff 70%)" } : undefined}>
      <Group justify="space-between" align="flex-start" wrap="nowrap" gap="xs">
        <Stack gap={6} style={{ minWidth: 0 }}>
          <Text size="sm" c="dimmed" fw={500} lh={1.3}>{label}</Text>
          <Text fz={{ base: 26, sm: 32 }} fw={700} lh={1} c={emphasis ? "danger.7" : "ink.9"}
            style={{ fontVariantNumeric: "tabular-nums" }}>
            {value}
          </Text>
          {hint && <Text size="xs" c="dimmed">{hint}</Text>}
        </Stack>
        <ThemeIcon size={44} radius="md" variant="light" color={color} visibleFrom="sm">{icon}</ThemeIcon>
      </Group>
    </Paper>
  );
}

/** Trạng thái cảnh báo: chưa xử lý = đặc (màu theo mức), chờ bác sĩ = tím, đã đóng = xám nhạt. */
export function StatusBadge({ status, label, level }: { status: string; label: string; level: string }) {
  const open = status === "open" || status === "escalated";
  const color = status === "open" ? (level === "red" ? "danger" : "warn") : status === "escalated" ? "grape" : "gray";
  return <Badge size="sm" variant={open ? "filled" : "light"} color={color} style={{ flex: "none" }}>{label}</Badge>;
}

/** Lý do cảnh báo dạng nhãn ngắn (xem lib/reasons.ts). `max`: số nhãn hiện, còn lại gom "+n". */
export function ReasonTags({ tags, max = 3, level }: { tags: ReasonTag[]; max?: number; level?: string }) {
  if (!tags.length) return <Text size="sm" c="dimmed">Chưa ghi lý do</Text>;
  const shown = tags.slice(0, max);
  const rest = tags.length - shown.length;
  const color = level === "red" ? "danger" : level === "yellow" ? "warn" : "gray";
  return (
    <Group gap={6} wrap="wrap">
      {shown.map((t) => (
        <Badge key={t.text} size="md" radius="sm" color={t.source === "rule" ? color : t.source === "trend" ? "blue" : "grape"}
          variant="light" leftSection={t.source === "ai" ? <IconRobot size={12} /> : t.source === "trend" ? <IconTrendingUp size={12} /> : null}
          styles={{ root: { textTransform: "none", fontWeight: 600, height: "auto", minHeight: 22, paddingBlock: 2 },
            label: { whiteSpace: "normal", overflow: "visible" } }}>
          {t.text}
        </Badge>
      ))}
      {rest > 0 && <Badge size="md" radius="sm" color="gray" variant="outline" styles={{ root: { textTransform: "none" } }}>+{rest}</Badge>}
    </Group>
  );
}

export function SectionTitle({ icon, children, right }: { icon?: ReactNode; children: ReactNode; right?: ReactNode }) {
  return (
    <Group justify="space-between" mb="md" wrap="nowrap">
      <Group gap={8} wrap="nowrap">
        {icon && <ThemeIcon variant="light" size={30} radius="md">{icon}</ThemeIcon>}
        <Title order={3}>{children}</Title>
      </Group>
      {right}
    </Group>
  );
}

export function EmptyState({ icon, title, text }: { icon: ReactNode; title: string; text?: string }) {
  return (
    <Stack align="center" gap={6} py={40}>
      <ThemeIcon size={56} radius={99} variant="light" color="ok">{icon}</ThemeIcon>
      <Text fw={600} mt={6}>{title}</Text>
      {text && <Text size="sm" c="dimmed" ta="center" maw={360}>{text}</Text>}
    </Stack>
  );
}
