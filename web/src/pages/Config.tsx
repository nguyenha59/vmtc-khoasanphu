import {
  Alert, Badge, Button, Code, Group, Modal, Paper, Select, Skeleton, Stack, Tabs, Text, Textarea, TextInput, ThemeIcon, Tooltip,
} from "@mantine/core";
import { IconCheck, IconClockHour4, IconEdit, IconHistory, IconLock, IconX } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { DataTable } from "../components/DataTable";
import { PageHeader, SectionTitle } from "../components/ui";
import { api } from "../lib/api";
import { fmtDateTime } from "../lib/format";
import { fail, ok } from "../lib/notify";

type Rule = { key: string; value: unknown; basis: string; requires_doctor: boolean; locked: boolean; pending: boolean; editable: boolean; choices: string[] | null };
type Data = {
  rules_version: string; can_approve: boolean;
  groups: { key: string; label: string; rules: Rule[] }[];
  pending: { id: number; key: string; old: unknown; new: unknown; reason: string; by: string; at: string | null }[];
  history: { key: string; status: string; version: string | null; at: string | null; by: string }[];
  his: { columns: { name: string; field: string; required: boolean }[]; date_format: string; time_format: string };
};
const show = (v: unknown) => (typeof v === "object" && v !== null ? JSON.stringify(v) : String(v));

export function Config() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["config"], queryFn: () => api<Data>("/api/config") });
  const [edit, setEdit] = useState<Rule | null>(null);
  const [value, setValue] = useState("");
  const [reason, setReason] = useState("");
  const refresh = () => qc.invalidateQueries({ queryKey: ["config"] });

  const propose = useMutation({
    mutationFn: () => api<{ status: string }>("/api/config/propose", { method: "POST", json: { key: edit!.key, value, reason } }),
    onSuccess: (r) => { ok(r.status === "approved" ? "Đã áp dụng (khóa không cần bác sĩ duyệt)." : "Đã gửi đề xuất – chờ bác sĩ duyệt."); setEdit(null); refresh(); },
    onError: (e) => fail(e, "Không gửi được đề xuất"),
  });
  const decide = useMutation({
    mutationFn: ({ id, action }: { id: number; action: "approve" | "reject" }) =>
      api<{ rules_version?: string }>(`/api/config/changes/${id}/${action}`, { method: "POST" }),
    onSuccess: (r) => { ok(r.rules_version ? `Đã duyệt – rules_version mới ${r.rules_version}.` : "Đã từ chối."); refresh(); },
    onError: (e) => fail(e),
  });

  if (isLoading || !data) return <Stack><Skeleton h={40} w={300} /><Skeleton h={400} radius="lg" /></Stack>;
  const open = (r: Rule) => { setEdit(r); setValue(r.choices ? String(r.value) : show(r.value)); setReason(""); };

  return (
    <>
      <PageHeader title="Cấu hình quy tắc"
        subtitle={<>Phiên bản đang hiệu lực <Code>{data.rules_version}</Code> · ngưỡng y khoa chỉ có hiệu lực sau khi bác sĩ duyệt</>} />

      {data.pending.length > 0 && (
        <Paper p="lg" mb="lg" style={{ borderColor: "var(--mantine-color-warn-3)" }}>
          <SectionTitle icon={<IconClockHour4 size={18} />}>Yêu cầu chờ duyệt ({data.pending.length})</SectionTitle>
          <Stack gap="sm">
            {data.pending.map((c) => (
              <Paper key={c.id} p="md" withBorder shadow="none" radius="md" bg="warn.0">
                <Group justify="space-between" wrap="wrap" gap="sm">
                  <div>
                    <Text fw={600} size="sm">{c.key}</Text>
                    <Text size="sm"><Code>{show(c.old)}</Code> → <Code color="brand.1">{show(c.new)}</Code></Text>
                    <Text size="xs" c="dimmed" mt={4}>Lý do: {c.reason || "—"} · {c.by} · {fmtDateTime(c.at)}</Text>
                  </div>
                  {data.can_approve ? (
                    <Group gap="xs">
                      <Button size="xs" color="ok" leftSection={<IconCheck size={14} />} onClick={() => decide.mutate({ id: c.id, action: "approve" })}>Duyệt</Button>
                      <Button size="xs" variant="light" color="danger" leftSection={<IconX size={14} />} onClick={() => decide.mutate({ id: c.id, action: "reject" })}>Từ chối</Button>
                    </Group>
                  ) : <Text size="xs" c="dimmed">Chỉ bác sĩ được duyệt</Text>}
                </Group>
              </Paper>
            ))}
          </Stack>
        </Paper>
      )}

      <Paper p={0} mb="lg" style={{ overflow: "hidden" }}>
        <Tabs defaultValue={data.groups[0]?.key} px="md" pt="sm">
          <Tabs.List>
            {data.groups.map((g) => <Tabs.Tab key={g.key} value={g.key}>{g.label}</Tabs.Tab>)}
            <Tabs.Tab value="his">Cột file HIS</Tabs.Tab>
          </Tabs.List>
          {data.groups.map((g) => (
            <Tabs.Panel key={g.key} value={g.key} py="md">
              <DataTable rows={g.rules} pageSize={50} columns={[
                { key: "key", label: "Quy tắc", render: (r) => <Text size="sm" ff="monospace">{r.key}</Text> },
                { key: "value", label: "Giá trị", render: (r) => <Code>{show(r.value)}</Code> },
                { key: "basis", label: "Căn cứ", render: (r) => <Text size="xs" c="dimmed">{r.basis}</Text> },
                { key: "requires_doctor", label: "Bác sĩ duyệt", render: (r) => (r.requires_doctor ? <Badge variant="light" color="grape">Có</Badge> : <Text size="sm" c="dimmed">Không</Text>) },
                { key: "status", label: "Trạng thái", render: (r) => r.locked ? <Badge color="gray" leftSection={<IconLock size={11} />}>Khóa</Badge>
                  : r.pending ? <Badge color="warn">Chờ duyệt</Badge> : <Badge color="ok" variant="light">Hiệu lực</Badge> },
                { key: "act", label: "", align: "right", render: (r) => r.editable ? (
                  <Button size="xs" variant="subtle" leftSection={<IconEdit size={14} />} onClick={() => open(r)}>
                    {g.key === "channels" ? "Đổi" : "Đề xuất"}</Button>)
                  : r.locked ? <Tooltip label="Khóa an toàn – không đổi được"><ThemeIcon variant="transparent" color="gray"><IconLock size={16} /></ThemeIcon></Tooltip> : null },
              ]} />
            </Tabs.Panel>
          ))}
          <Tabs.Panel value="his" py="md">
            <DataTable rows={data.his.columns} pageSize={50} columns={[
              { key: "name", label: "Tên cột trong file HIS" }, { key: "field", label: "Trường chuẩn", render: (r) => <Code>{r.field}</Code> },
              { key: "required", label: "Bắt buộc", render: (r) => (r.required ? <Badge variant="light">Có</Badge> : "") },
            ]} />
            <Alert mx="md" mt="md" color="warn" variant="light">Định dạng ngày {data.his.date_format}, giờ {data.his.time_format}. [CẦN XÁC NHẬN] tên cột thật – sửa trong config/his_columns.yaml khi có file mẫu đã che.</Alert>
          </Tabs.Panel>
        </Tabs>
      </Paper>

      <Paper p="lg">
        <SectionTitle icon={<IconHistory size={18} />}>Lịch sử thay đổi</SectionTitle>
        <DataTable rows={data.history} empty="Chưa có thay đổi." columns={[
          { key: "key", label: "Khóa", render: (r) => <Text size="sm" ff="monospace">{r.key}</Text> },
          { key: "status", label: "Kết quả", render: (r) => <Badge color={r.status === "approved" ? "ok" : "danger"} variant="light">{r.status === "approved" ? "Đã duyệt" : "Từ chối"}</Badge> },
          { key: "version", label: "Phiên bản", render: (r) => r.version ?? "—" },
          { key: "at", label: "Lúc", render: (r) => fmtDateTime(r.at) }, { key: "by", label: "Người quyết định" },
        ]} />
      </Paper>

      <Modal opened={!!edit} onClose={() => setEdit(null)} title={<Text fw={700}>{edit?.choices ? "Đổi kênh" : "Đề xuất thay đổi"}</Text>} size="lg" radius="lg">
        {edit && (
          <Stack>
            <Text size="sm" ff="monospace">{edit.key}</Text>
            <Text size="sm" c="dimmed">Hiện tại: <Code>{show(edit.value)}</Code></Text>
            {edit.choices ? <Select label="Giá trị mới" data={edit.choices} value={value} onChange={(v) => setValue(v ?? "")} allowDeselect={false} />
              : <Textarea label="Giá trị mới (YAML/JSON)" description='Giữ đúng dạng giá trị hiện tại, ví dụ {"sys": 140, "dia": 90}'
                autosize minRows={2} value={value} onChange={(e) => setValue(e.currentTarget.value)} styles={{ input: { fontFamily: "monospace" } }} />}
            <TextInput label="Lý do / căn cứ" value={reason} onChange={(e) => setReason(e.currentTarget.value)} />
            {edit.requires_doctor && <Alert color="grape" variant="light">Khóa này cần bác sĩ duyệt mới có hiệu lực.</Alert>}
            <Group justify="flex-end"><Button variant="default" onClick={() => setEdit(null)}>Hủy</Button>
              <Button loading={propose.isPending} onClick={() => propose.mutate()}>Gửi</Button></Group>
          </Stack>
        )}
      </Modal>
    </>
  );
}
