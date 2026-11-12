import { Badge, Box, Group, Paper, Stack, Text, ThemeIcon, Title, type MantineColor } from "@mantine/core";
import { IconAlertTriangleFilled, IconCircleCheckFilled, IconUrgent } from "@tabler/icons-react";
import type { ReactNode } from "react";
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

export function StatCard({ label, value, hint, icon, color = "brand", emphasis }: {
  label: string; value: ReactNode; hint?: ReactNode; icon: ReactNode; color?: MantineColor; emphasis?: boolean;
}) {
  return (
    <Paper p="lg" h="100%" style={emphasis ? { borderColor: "var(--mantine-color-danger-3)",
      background: "linear-gradient(180deg, var(--mantine-color-danger-0), #fff 70%)" } : undefined}>
      <Group justify="space-between" align="flex-start" wrap="nowrap">
        <Stack gap={6}>
          <Text size="sm" c="dimmed" fw={500}>{label}</Text>
          <Text fz={32} fw={700} lh={1} c={emphasis ? "danger.7" : "ink.9"} style={{ fontVariantNumeric: "tabular-nums" }}>
            {value}
          </Text>
          {hint && <Text size="xs" c="dimmed">{hint}</Text>}
        </Stack>
        <ThemeIcon size={44} radius="md" variant="light" color={color}>{icon}</ThemeIcon>
      </Group>
    </Paper>
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
