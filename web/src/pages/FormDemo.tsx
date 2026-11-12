import { Alert, Button, CopyButton, Group, Paper, Select, Stack, Text, TextInput } from "@mantine/core";
import { IconCheck, IconCopy, IconExternalLink, IconInfoCircle, IconLink } from "@tabler/icons-react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { PageHeader } from "../components/ui";
import { api } from "../lib/api";
import { fail } from "../lib/notify";

export function FormDemo() {
  const [code, setCode] = useState("R0412");
  const [form, setForm] = useState<string | null>("F6");
  const cat = useQuery({ queryKey: ["forms-catalog"], queryFn: () => api<{ forms: { code: string; name: string }[] }>("/api/forms/catalog") });
  const create = useMutation({
    mutationFn: () => api<{ token: string; path: string }>("/api/forms/demo-token", { method: "POST", json: { code, form } }),
    onError: (e) => fail(e, "Không tạo được đường dẫn"),
  });
  const url = create.data ? `${window.location.origin}${create.data.path}` : "";

  return (
    <>
      <PageHeader title="Form thai phụ" subtitle="Chế độ demo: tạo đường dẫn form cho một thai phụ (dữ liệu giả)" />
      <Paper p="lg" maw={640}>
        <Stack>
          <Alert variant="light" color="brand" icon={<IconInfoCircle size={18} />}>
            Thai phụ thật nhận đường dẫn qua tin nhắn. Trang form không hỏi tên, số điện thoại; AI không trả lời hay tư vấn cho thai phụ.
          </Alert>
          <Group grow align="flex-end">
            <TextInput label="Mã thai phụ" value={code} onChange={(e) => setCode(e.currentTarget.value)} />
            <Select label="Form" value={form} onChange={setForm} allowDeselect={false}
              data={(cat.data?.forms ?? []).map((f) => ({ value: f.code, label: `${f.code} – ${f.name}` }))} />
          </Group>
          <Button leftSection={<IconLink size={18} />} loading={create.isPending} disabled={!code || !form} onClick={() => create.mutate()}>
            Tạo đường dẫn demo</Button>
          {url && (
            <Paper p="md" bg="brand.0" withBorder={false} shadow="none">
              <Text size="sm" fw={600} mb={6}>Đường dẫn (dùng một lần)</Text>
              <Text size="xs" ff="monospace" style={{ wordBreak: "break-all" }}>{url}</Text>
              <Group mt="sm" gap="xs">
                <CopyButton value={url}>{({ copied, copy }) => (
                  <Button size="xs" variant="light" onClick={copy} leftSection={copied ? <IconCheck size={14} /> : <IconCopy size={14} />}>
                    {copied ? "Đã chép" : "Chép"}</Button>)}</CopyButton>
                <Button size="xs" component="a" href={create.data!.path} target="_blank" leftSection={<IconExternalLink size={14} />}>Mở form</Button>
              </Group>
            </Paper>
          )}
        </Stack>
      </Paper>
    </>
  );
}
