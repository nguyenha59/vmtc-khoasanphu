import {
  Alert, Box, Button, Center, Container, Group, Loader, NumberInput, Paper, Progress, Radio, SegmentedControl, Stack, Text,
  Textarea, ThemeIcon, Title,
} from "@mantine/core";
import { IconAlertTriangle, IconCircleCheck, IconHeartHandshake, IconPhone, IconUrgent } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { LogoMark } from "../components/Logo";
import classes from "./PublicForm.module.css";

type Clinic = { name: string; hotline: string; emergency_number: string };
type Q = { key: string; type: "yesno" | "number"; text: string; min: number | null; max: number | null; step: number };
type FormDef = { code: string; form: string; name: string; questions: Q[]; epds: { options: string[]; items: { key: string; text: string }[] } | null; clinic: Clinic };
type Result = { level: string; code: string; reminder: string | null; clinic: Clinic };

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  const d = await res.json().catch(() => ({}));
  if (!res.ok) {
    const det = d.detail;
    const err = new Error(typeof det === "string" ? det : det?.message ?? "Đường dẫn không hợp lệ.") as Error & { clinic?: Clinic };
    err.clinic = det?.clinic;
    throw err;
  }
  return d as T;
}

function Emergency({ clinic, strong }: { clinic: Clinic | null; strong?: boolean }) {
  const em = clinic?.emergency_number ?? "115";
  return (
    <Alert color="danger" variant={strong ? "filled" : "light"} radius="md" icon={<IconUrgent size={20} />} className={classes.emergency}>
      <Text size="sm" fw={strong ? 600 : 400}>
        Form này không thay cho khám cấp cứu. Nếu chị đau bụng nhiều, ra máu, ra nước, khó thở, co giật… hãy gọi{" "}
        <a href={`tel:${em}`} className={classes.tel}>{em}</a> hoặc đến cơ sở y tế gần nhất ngay.
        {clinic && <> Đường dây khoa: <a href={`tel:${clinic.hotline.replace(/\s/g, "")}`} className={classes.tel}>{clinic.hotline}</a>.</>}
      </Text>
    </Alert>
  );
}

export function PublicForm() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const [def, setDef] = useState<FormDef | null>(null);
  const [error, setError] = useState<{ message: string; clinic: Clinic | null } | null>(null);
  const [answers, setAnswers] = useState<Record<string, string | number>>({});
  const [freeText, setFreeText] = useState("");
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<Result | null>(null);

  useEffect(() => {
    if (!token) { setError({ message: "Đường dẫn không hợp lệ. Vui lòng dùng đúng đường dẫn trong tin nhắn.", clinic: null }); return; }
    call<FormDef>(`/api/public/form/${encodeURIComponent(token)}`).then(setDef)
      .catch((e: Error & { clinic?: Clinic }) => setError({ message: e.message, clinic: e.clinic ?? null }));
  }, [token]);

  const total = (def?.questions.length ?? 0) + (def?.epds?.items.length ?? 0);
  const answered = Object.keys(answers).length;
  const set = (k: string, v: string | number | null) => setAnswers((a) => {
    const n = { ...a };
    if (v === null || v === "") delete n[k]; else n[k] = v;
    return n;
  });

  const submit = async () => {
    setSending(true);
    try {
      const r = await call<Result>(`/api/public/form/${encodeURIComponent(token)}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ answers, free_text: freeText }),
      });
      setResult(r);
      window.scrollTo({ top: 0 });
    } catch (e) {
      const err = e as Error & { clinic?: Clinic };
      setError({ message: err.message, clinic: err.clinic ?? def?.clinic ?? null });
    } finally {
      setSending(false);
    }
  };

  return (
    <Box className={classes.page}>
      <Box className={classes.top}>
        <Container size={560} py="md">
          <Group gap={10}><LogoMark size={32} /><Text fw={700} c="#F0FAF7">{def?.clinic.name ?? "Phòng khám Sản phụ khoa"}</Text></Group>
        </Container>
      </Box>
      <Container size={560} py="lg" px="md">
        {result ? <ResultView r={result} /> : error ? (
          <Stack>
            <Emergency clinic={error.clinic} />
            <Paper p="xl" ta="center">
              <ThemeIcon size={56} radius={99} variant="light" color="warn" mx="auto"><IconAlertTriangle size={28} /></ThemeIcon>
              <Text fw={600} mt="md">{error.message}</Text>
            </Paper>
          </Stack>
        ) : !def ? <Center py={80}><Loader color="brand" /></Center> : (
          <Stack gap="md">
            <Box>
              <Title order={2} fz={24}>{def.name}</Title>
              <Text size="sm" c="dimmed">Mã của chị: <b>{def.code}</b></Text>
            </Box>
            <Emergency clinic={def.clinic} />
            <Paper p="md" className={classes.progress}>
              <Group justify="space-between" mb={6}><Text size="sm" fw={600}>Đã trả lời {answered}/{total} câu</Text>
                <Text size="sm" c="dimmed">{total ? Math.round((answered / total) * 100) : 100}%</Text></Group>
              <Progress value={total ? (answered / total) * 100 : 100} radius="xl" size="md" />
            </Paper>

            {def.questions.map((q, i) => (
              <Paper key={q.key} p="lg" className={classes.q} data-done={answers[q.key] !== undefined || undefined}>
                <Text size="xs" c="dimmed" fw={600} mb={4}>CÂU {i + 1}</Text>
                <Text fw={600} mb="md" fz="md">{q.text}</Text>
                {q.type === "yesno" ? (
                  <SegmentedControl fullWidth size="md" radius="md" value={String(answers[q.key] ?? "")}
                    onChange={(v) => set(q.key, v)} data={["Không", "Có"]} color={answers[q.key] === "Có" ? "warn" : "brand"} />
                ) : (
                  <NumberInput size="md" min={q.min ?? 0} max={q.max ?? 999} step={q.step} decimalScale={q.step < 1 ? 1 : 0}
                    decimalSeparator="," placeholder="Nhập số" value={answers[q.key] ?? ""} inputMode="decimal"
                    onChange={(v) => set(q.key, v === "" ? null : Number(v))} />
                )}
              </Paper>
            ))}

            {def.epds && (
              <Paper p="lg">
                <Text fw={700} mb="xs">Trong 7 ngày qua:</Text>
                <Stack gap="lg">
                  {def.epds.items.map((it) => (
                    <Radio.Group key={it.key} label={it.text} value={answers[it.key] !== undefined ? String(answers[it.key]) : null}
                      onChange={(v) => set(it.key, Number(v))} styles={{ label: { fontWeight: 600, marginBottom: 8 } }}>
                      <Stack gap={8}>{def.epds!.options.map((o, idx) => <Radio key={o} value={String(idx)} label={o} size="md" />)}</Stack>
                    </Radio.Group>
                  ))}
                </Stack>
              </Paper>
            )}

            <Paper p="lg">
              <Textarea label="Chị muốn nói thêm điều gì? (không bắt buộc)" description="Không ghi tên, số điện thoại." autosize
                minRows={3} maxLength={2000} value={freeText} onChange={(e) => setFreeText(e.currentTarget.value)} size="md" />
            </Paper>

            <Button size="lg" radius="md" loading={sending} onClick={submit} className={classes.submit}>Gửi</Button>
            <Text size="xs" c="dimmed" ta="center" pb="xl">Thông tin được gửi tới khoa và được bảo mật.</Text>
          </Stack>
        )}
      </Container>
    </Box>
  );
}

function ResultView({ r }: { r: Result }) {
  const em = r.clinic.emergency_number;
  const hot = r.clinic.hotline;
  if (r.level === "red") {
    return (
      <Stack gap="md">
        <Paper p="xl" className={classes.red}>
          <ThemeIcon size={64} radius={99} color="danger" mx="auto" display="flex"><IconUrgent size={34} /></ThemeIcon>
          <Title order={2} ta="center" mt="md" c="danger.8">Chị cần đến cơ sở y tế ngay</Title>
          <Text ta="center" mt="xs">Khoa đã nhận được thông tin của chị và sẽ liên hệ trong ngày.</Text>
        </Paper>
        <Button component="a" href={`tel:${em}`} size="xl" color="danger" leftSection={<IconPhone size={22} />}>Gọi {em}</Button>
        <Button component="a" href={`tel:${hot.replace(/\s/g, "")}`} size="lg" variant="outline" color="danger"
          leftSection={<IconPhone size={20} />}>Gọi đường dây khoa {hot}</Button>
      </Stack>
    );
  }
  if (r.level === "yellow") {
    return (
      <Stack gap="md">
        <Paper p="xl" ta="center" style={{ borderColor: "var(--mantine-color-warn-3)" }}>
          <ThemeIcon size={64} radius={99} color="warn" variant="light" mx="auto" display="flex"><IconHeartHandshake size={34} /></ThemeIcon>
          <Title order={2} mt="md">Cảm ơn chị</Title>
          <Text mt="xs">Khoa sẽ liên hệ với chị trong 24 giờ.</Text>
        </Paper>
        <Emergency clinic={r.clinic} />
      </Stack>
    );
  }
  return (
    <Stack gap="md">
      <Paper p="xl" ta="center">
        <ThemeIcon size={64} radius={99} color="ok" variant="light" mx="auto" display="flex"><IconCircleCheck size={34} /></ThemeIcon>
        <Title order={2} mt="md">Cảm ơn chị đã trả lời!</Title>
        {r.reminder && <Text mt="sm" c="dimmed">{r.reminder}</Text>}
      </Paper>
      <Emergency clinic={r.clinic} />
    </Stack>
  );
}
