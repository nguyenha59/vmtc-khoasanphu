import { Group, Stack, Text } from "@mantine/core";

export function LogoMark({ size = 36 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" aria-hidden>
      <rect x="2" y="2" width="44" height="44" rx="12" fill="#7FD6C2" />
      <path d="M24 35c-6.5-4.8-11-8.6-11-13.4A5.8 5.8 0 0 1 24 18.2a5.8 5.8 0 0 1 11 3.4C35 26.4 30.5 30.2 24 35z" fill="#0B3B36" />
    </svg>
  );
}

export function Logo({ light = false }: { light?: boolean }) {
  return (
    <Group gap={10} wrap="nowrap">
      <LogoMark />
      <Stack gap={0}>
        <Text fw={700} size="md" c={light ? "#F0FAF7" : "ink.9"} lh={1.2}>Trợ lý thai kỳ</Text>
        <Text size="xs" c={light ? "#9FC9BF" : "dimmed"} lh={1.3}>Lịch hẹn · theo dõi</Text>
      </Stack>
    </Group>
  );
}
