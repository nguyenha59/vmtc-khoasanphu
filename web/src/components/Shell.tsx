import {
  ActionIcon, AppShell, Avatar, Badge, Box, Burger, Group, Indicator, Menu, NavLink, ScrollArea, Stack, Text,
  TextInput, Tooltip, UnstyledButton,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import {
  IconBell, IconCalendarEvent, IconCalendarX, IconChecklist, IconChevronDown, IconClipboardText, IconHeartbeat,
  IconLayoutDashboard, IconLogout, IconMailForward, IconSearch, IconSettings, IconUrgent,
} from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { NavLink as RouterLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api, type Me, type Overview } from "../lib/api";
import { ROLE_LABELS, useLogout } from "../lib/auth";
import { longToday } from "../lib/format";
import { Logo } from "./Logo";
import classes from "./Shell.module.css";

type Item = { to: string; label: string; icon: ReactNode; perm?: string; badge?: number };

export function Shell({ me }: { me: Me }) {
  const [opened, { toggle, close }] = useDisclosure();
  const logout = useLogout();
  const navigate = useNavigate();
  const loc = useLocation();
  const [q, setQ] = useState("");
  const can = (p?: string) => !p || me.permissions.includes(p);
  const { data: ov } = useQuery({ queryKey: ["overview"], queryFn: () => api<Overview>("/api/overview"), refetchInterval: 60_000 });
  const openRed = ov?.metrics.open_red ?? 0;

  const work: Item[] = [
    { to: "/", label: "Tổng quan", icon: <IconLayoutDashboard size={19} stroke={1.7} /> },
    { to: "/lich-hen", label: "Lịch hẹn", icon: <IconCalendarEvent size={19} stroke={1.7} />, perm: "upload_his" },
    { to: "/lich-gui-form", label: "Lịch gửi form", icon: <IconMailForward size={19} stroke={1.7} />, perm: "form_schedule" },
    { to: "/canh-bao", label: "Cảnh báo", icon: <IconBell size={19} stroke={1.7} />, perm: "view_alerts", badge: ov?.metrics.open_alerts },
    { to: "/ho-so", label: "Hồ sơ theo dõi", icon: <IconHeartbeat size={19} stroke={1.7} />, perm: "view_profile" },
  ];
  const admin: Item[] = [
    { to: "/du-doan-bo-hen", label: "Dự đoán bỏ hẹn", icon: <IconCalendarX size={19} stroke={1.7} />, perm: "view_noshow" },
    { to: "/chat-luong-ai", label: "Chất lượng AI", icon: <IconChecklist size={19} stroke={1.7} />, perm: "view_quality" },
    { to: "/cau-hinh", label: "Cấu hình quy tắc", icon: <IconSettings size={19} stroke={1.7} />, perm: "view_config" },
  ];
  const demo: Item[] = [{ to: "/form-demo", label: "Form thai phụ", icon: <IconClipboardText size={19} stroke={1.7} />, perm: "demo_form" }];

  const section = (title: string, items: Item[]) => {
    const visible = items.filter((i) => can(i.perm));
    if (!visible.length) return null;
    return (
      <Box mb="md">
        <Text className={classes.sectionTitle}>{title}</Text>
        {visible.map((i) => (
          <NavLink key={i.to} component={RouterLink} to={i.to} end={i.to === "/"} label={i.label} leftSection={i.icon}
            onClick={close} className={classes.link}
            active={i.to === "/" ? loc.pathname === "/" : loc.pathname.startsWith(i.to)}
            rightSection={i.badge ? <Badge size="sm" variant="filled" color={openRed ? "danger" : "brand.5"} radius="xl">{i.badge}</Badge> : null} />
        ))}
      </Box>
    );
  };

  const search = (e: React.FormEvent) => {
    e.preventDefault();
    if (q.trim()) navigate(`/ho-so?q=${encodeURIComponent(q.trim())}`);
  };

  return (
    <AppShell header={{ height: 64 }} navbar={{ width: 264, breakpoint: "sm", collapsed: { mobile: !opened } }}
      padding={{ base: "md", sm: "xl" }}>
      <AppShell.Header className={classes.header}>
        <Group h="100%" px={{ base: "md", sm: "xl" }} justify="space-between" wrap="nowrap">
          <Group gap="sm" wrap="nowrap">
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" />
            <Box hiddenFrom="sm"><Logo /></Box>
            <form onSubmit={search}>
              <TextInput visibleFrom="sm" w={320} placeholder="Tìm thai phụ theo mã R0412 hoặc PID" value={q}
                onChange={(e) => setQ(e.currentTarget.value)} leftSection={<IconSearch size={16} />} radius="xl"
                aria-label="Tìm thai phụ" />
            </form>
          </Group>
          <Group gap="md" wrap="nowrap">
            <Text size="sm" c="dimmed" visibleFrom="md">{longToday()} · {ov?.shift ?? ""}</Text>
            {can("view_alerts") && (
              <Tooltip label={openRed ? `${openRed} cảnh báo đỏ chưa xử lý` : "Không có cảnh báo đỏ"}>
                <Indicator disabled={!openRed} color="danger" label={openRed} size={16} offset={4} processing>
                  <ActionIcon variant="subtle" color="gray" size="lg" radius="xl" onClick={() => navigate("/canh-bao")}
                    aria-label="Cảnh báo">
                    <IconUrgent size={20} />
                  </ActionIcon>
                </Indicator>
              </Tooltip>
            )}
            <Menu position="bottom-end" width={220} shadow="md">
              <Menu.Target>
                <UnstyledButton className={classes.user}>
                  <Group gap={8} wrap="nowrap">
                    <Avatar color="brand" radius="xl" size={34}>{me.name.split(" ").pop()?.[0] ?? "?"}</Avatar>
                    <Box visibleFrom="sm">
                      <Text size="sm" fw={600} lh={1.2}>{me.name}</Text>
                      <Text size="xs" c="dimmed" lh={1.2}>{ROLE_LABELS[me.role] ?? me.role}</Text>
                    </Box>
                    <IconChevronDown size={14} />
                  </Group>
                </UnstyledButton>
              </Menu.Target>
              <Menu.Dropdown>
                <Menu.Label>{me.username}</Menu.Label>
                <Menu.Item color="danger" leftSection={<IconLogout size={16} />} onClick={logout}>Đăng xuất</Menu.Item>
              </Menu.Dropdown>
            </Menu>
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar className={classes.navbar}>
        <Box px="lg" py="md" visibleFrom="sm" className={classes.brand}><Logo light /></Box>
        <ScrollArea flex={1} px="sm" pt="md">
          <form onSubmit={search}>
            <TextInput hiddenFrom="sm" mb="md" placeholder="Tìm mã / PID" value={q} onChange={(e) => setQ(e.currentTarget.value)}
              leftSection={<IconSearch size={16} />} radius="xl" aria-label="Tìm thai phụ" />
          </form>
          {section("Công việc", work)}
          {section("Quản trị", admin)}
          {section("Demo", demo)}
        </ScrollArea>
        <Stack gap={2} p="md" className={classes.footer}>
          <Text size="xs" c="#9FC9BF">MVP · chỉ dữ liệu giả</Text>
          <Text size="xs" c="#6E9A90">AI chạy cục bộ (Ollama)</Text>
        </Stack>
      </AppShell.Navbar>

      <AppShell.Main className={classes.main}>
        <Box maw={1360} mx="auto"><Outlet /></Box>
      </AppShell.Main>
    </AppShell>
  );
}
