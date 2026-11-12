import { Alert, Box, Button, Group, PasswordInput, SimpleGrid, Stack, Text, TextInput, ThemeIcon, Title } from "@mantine/core";
import { IconAlertCircle, IconBellRinging, IconCalendarCheck, IconLock, IconShieldLock, IconUser } from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { LogoMark } from "../components/Logo";
import { api, type Me } from "../lib/api";
import classes from "./Login.module.css";

const FEATURES = [
  { icon: IconCalendarCheck, title: "Lịch hẹn & giờ đến", text: "Đọc file HIS, đề xuất giờ đến, xuất tin nhắn WorldSMS." },
  { icon: IconBellRinging, title: "Cảnh báo 3 lớp", text: "Quy tắc, xu hướng chỉ số và AI đọc phần tự viết của thai phụ." },
  { icon: IconShieldLock, title: "Bảo vệ dữ liệu", text: "Mã hóa thông tin cá nhân, AI chạy cục bộ, mọi thao tác có nhật ký." },
];

export function Login() {
  const qc = useQueryClient();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const me = await api<Me>("/api/auth/login", { method: "POST", json: { username, password } });
      qc.setQueryData(["me"], me);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Không đăng nhập được.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <SimpleGrid cols={{ base: 1, md: 2 }} spacing={0} mih="100vh">
      <Box className={classes.hero} visibleFrom="md">
        <Stack justify="space-between" h="100%" p={56} pos="relative" style={{ zIndex: 1 }}>
          <Group gap={12}>
            <LogoMark size={44} />
            <Text fw={700} fz="lg" c="#F0FAF7">Trợ lý thai kỳ</Text>
          </Group>
          <Stack gap="xl" maw={460}>
            <Title order={1} c="#fff" fz={38} lh={1.15}>
              Theo dõi thai phụ giữa hai lần khám, <span className={classes.accent}>không bỏ sót ca nguy hiểm.</span>
            </Title>
            <Stack gap="lg">
              {FEATURES.map((f) => (
                <Group key={f.title} gap="md" wrap="nowrap" align="flex-start">
                  <ThemeIcon size={40} radius="md" className={classes.featureIcon}><f.icon size={20} /></ThemeIcon>
                  <Box>
                    <Text fw={600} c="#F0FAF7">{f.title}</Text>
                    <Text size="sm" c="#A9CFC6">{f.text}</Text>
                  </Box>
                </Group>
              ))}
            </Stack>
          </Stack>
          <Text size="xs" c="#6E9A90">Phòng khám Sản phụ khoa · MVP chạy với dữ liệu giả</Text>
        </Stack>
      </Box>

      <Stack justify="center" align="center" p="xl" bg="ink.0">
        <Box w="100%" maw={400}>
          <Group gap={10} mb="xl" hiddenFrom="md"><LogoMark /><Text fw={700}>Trợ lý thai kỳ</Text></Group>
          <Title order={2} fz={28}>Đăng nhập</Title>
          <Text c="dimmed" size="sm" mt={6} mb="xl">Dành cho điều dưỡng, bác sĩ và quản trị của khoa.</Text>
          <form onSubmit={submit}>
            <Stack gap="md">
              {error && <Alert color="danger" variant="light" icon={<IconAlertCircle size={18} />}>{error}</Alert>}
              <TextInput label="Tên đăng nhập" size="md" leftSection={<IconUser size={18} />} value={username}
                onChange={(e) => setUsername(e.currentTarget.value)} autoComplete="username" required autoFocus />
              <PasswordInput label="Mật khẩu" size="md" leftSection={<IconLock size={18} />} value={password}
                onChange={(e) => setPassword(e.currentTarget.value)} autoComplete="current-password" required />
              <Button type="submit" size="md" mt="sm" loading={loading} fullWidth>Đăng nhập</Button>
            </Stack>
          </form>
          <Text size="xs" c="dimmed" mt="xl" ta="center">Tài khoản demo: nurse / doctor / admin</Text>
        </Box>
      </Stack>
    </SimpleGrid>
  );
}
