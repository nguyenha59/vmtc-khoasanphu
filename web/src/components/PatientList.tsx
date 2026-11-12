import {
  Badge, Box, Button, Group, Modal, Pagination, Paper, SegmentedControl, Select, Stack, Switch, Text, TextInput,
} from "@mantine/core";
import { IconPlus, IconSearch, IconUserPlus } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../lib/api";
import { useCan } from "../lib/auth";
import { fmtDate } from "../lib/format";
import { fail, ok } from "../lib/notify";
import { DataTable } from "./DataTable";
import { PACKAGES, ProgramBadges } from "./PatientRecord";

type Row = {
  patient_id: number; code: string; pid: string; full_name: string; phone_masked: string; stage: string;
  in_package: boolean; package_name: string | null; override_monitoring: boolean; consent: string; next_appt: string | null;
};
type ListResp = { items: Row[]; total: number; page: number; size: number };
type Filter = "all" | "package" | "override" | "none";

const SIZE = 25;

/** Danh sách hồ sơ thai phụ: tìm theo mã / PID / tên, lọc theo chương trình, tạo hồ sơ mới. */
export function PatientList({ onOpen }: { onOpen: (code: string) => void }) {
  const can = useCan();
  const [q, setQ] = useState("");
  const [term, setTerm] = useState("");
  const [program, setProgram] = useState<Filter>("all");
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);
  const list = useQuery({
    queryKey: ["patients", term, program, page],
    queryFn: () => api<ListResp>(`/api/patients?q=${encodeURIComponent(term)}&program=${program}&page=${page}&size=${SIZE}`),
    placeholderData: (prev) => prev,
  });
  const pages = list.data ? Math.max(1, Math.ceil(list.data.total / SIZE)) : 1;

  return (
    <Paper p="lg">
      <Group justify="space-between" wrap="wrap" gap="sm" mb="md">
        <Box>
          <Text fw={700}>Hồ sơ thai phụ</Text>
          <Text size="sm" c="dimmed">{list.data ? `${list.data.total} hồ sơ` : "Đang tải…"} · bấm một dòng để mở hồ sơ</Text>
        </Box>
        {can("manage_patient") && (
          <Button leftSection={<IconUserPlus size={16} />} onClick={() => setCreating(true)}>Tạo hồ sơ</Button>
        )}
      </Group>
      <Group gap="sm" wrap="wrap" mb="md">
        <form onSubmit={(e) => { e.preventDefault(); setTerm(q.trim()); setPage(1); }} style={{ flex: "1 1 260px" }}>
          <TextInput placeholder="Tìm theo mã R####, PID hoặc tên" value={q} onChange={(e) => setQ(e.currentTarget.value)}
            leftSection={<IconSearch size={16} />} aria-label="Tìm hồ sơ" />
        </form>
        <SegmentedControl value={program} onChange={(v) => { setProgram(v as Filter); setPage(1); }} data={[
          { value: "all", label: "Tất cả" }, { value: "package", label: "Theo gói" },
          { value: "override", label: "BS chỉ định" }, { value: "none", label: "Ngoài gói" },
        ]} />
      </Group>
      <DataTable rows={list.data?.items ?? []} pageSize={SIZE} rowKey={(r) => r.patient_id} minWidth={860}
        empty={list.isLoading ? "Đang tải…" : "Không có hồ sơ phù hợp."} onRowClick={(r) => onOpen(r.code)}
        card={(r) => (
          <Stack gap={4}>
            <Group justify="space-between" wrap="nowrap">
              <Text fw={700}>{r.code} <Text span size="sm" c="dimmed" fw={400}>· {r.pid}</Text></Text>
              <Text size="xs" c="dimmed">{r.stage}</Text>
            </Group>
            <Text size="sm">{r.full_name}</Text>
            <ProgramBadges p={r} />
          </Stack>
        )}
        columns={[
          { key: "code", label: "Mã", width: 90, nowrap: true, render: (r) => <Text size="sm" fw={700}>{r.code}</Text> },
          { key: "pid", label: "PID", width: 110, nowrap: true, render: (r) => <Text size="sm" ff="monospace">{r.pid}</Text> },
          { key: "full_name", label: "Họ tên", render: (r) => <Text size="sm" fw={500}>{r.full_name}</Text> },
          { key: "phone_masked", label: "SĐT", width: 130, nowrap: true, render: (r) => <Text size="sm" ff="monospace">{r.phone_masked}</Text> },
          { key: "stage", label: "Thai kỳ", width: 120, nowrap: true },
          { key: "program", label: "Chương trình", render: (r) => <ProgramBadges p={r} /> },
          { key: "next_appt", label: "Hẹn tới", width: 110, nowrap: true, render: (r) => fmtDate(r.next_appt) },
        ]} />
      {pages > 1 && (
        <Group justify="center" mt="md"><Pagination total={pages} value={page} onChange={setPage} size="sm" /></Group>
      )}
      {creating && <CreatePatient onClose={() => setCreating(false)} onCreated={onOpen} />}
    </Paper>
  );
}

function CreatePatient({ onClose, onCreated }: { onClose: () => void; onCreated: (code: string) => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState({ pid: "", full_name: "", phone: "", nationality: "Việt Nam", edd: "", in_package: true,
    package_name: PACKAGES[0] as string | null });
  const set = <K extends keyof typeof f>(k: K, v: (typeof f)[K]) => setF((x) => ({ ...x, [k]: v }));
  const save = useMutation({
    mutationFn: () => api<{ patient_id: number; code: string }>("/api/patients", { method: "POST", json: {
      ...f, edd: f.edd || null, package_name: f.in_package ? f.package_name : null,
      pid: f.pid.trim(), full_name: f.full_name.trim(), phone: f.phone.replace(/\s/g, ""),
    } }),
    onSuccess: (r) => {
      ok(`Đã tạo hồ sơ ${r.code}.`);
      qc.invalidateQueries({ queryKey: ["patients"] });
      onClose(); onCreated(r.code);
    },
    onError: (e) => fail(e, "Chưa tạo được hồ sơ"),
  });
  const pidOk = /^[A-Za-z0-9]{4,20}$/.test(f.pid.trim());
  const phoneOk = /^(\+?84|0)\d{9}$/.test(f.phone.replace(/\s/g, ""));
  const nameOk = f.full_name.trim().split(/\s+/).length >= 2;
  const ready = pidOk && phoneOk && nameOk && (!f.in_package || !!f.package_name);

  return (
    <Modal opened onClose={onClose} centered size="lg" title={<Text fw={700}>Tạo hồ sơ thai phụ</Text>}>
      <form onSubmit={(e) => { e.preventDefault(); if (ready) save.mutate(); }}>
        <Stack gap="md">
          <Group grow align="flex-start" wrap="wrap">
            <TextInput label="PID (mã bệnh nhân HIS)" required value={f.pid} onChange={(e) => set("pid", e.currentTarget.value)}
              error={f.pid && !pidOk ? "Chỉ chữ và số, 4–20 ký tự" : undefined} miw={200} />
            <TextInput label="Số điện thoại" required value={f.phone} onChange={(e) => set("phone", e.currentTarget.value)}
              error={f.phone && !phoneOk ? "10 số, bắt đầu bằng 0" : undefined} inputMode="tel" miw={200} />
          </Group>
          <TextInput label="Họ và tên" required value={f.full_name} onChange={(e) => set("full_name", e.currentTarget.value)}
            error={f.full_name && !nameOk ? "Ghi đủ họ và tên" : undefined} />
          <Group grow align="flex-start" wrap="wrap">
            <TextInput label="Quốc tịch" value={f.nationality} onChange={(e) => set("nationality", e.currentTarget.value)} miw={200} />
            <TextInput type="date" label="Ngày dự sinh" description="Theo siêu âm quý 1 nếu có" value={f.edd}
              onChange={(e) => set("edd", e.currentTarget.value)} miw={200} />
          </Group>
          <Paper p="sm" radius="md" bg="ink.0">
            <Stack gap="xs">
              <Switch label="Thai phụ theo gói thai sản" checked={f.in_package} onChange={(e) => set("in_package", e.currentTarget.checked)} />
              {f.in_package && <Select label="Gói" data={PACKAGES} value={f.package_name} onChange={(v) => set("package_name", v)} allowDeselect={false} />}
              <Text size="xs" c="dimmed">Thai phụ theo gói được gửi form theo dõi tại nhà. Ngoài gói cần bác sĩ chỉ định (sửa trong hồ sơ).</Text>
            </Stack>
          </Paper>
          <Badge variant="light" color="gray" style={{ alignSelf: "flex-start" }}>Họ tên và SĐT được mã hóa khi lưu</Badge>
          <Group justify="flex-end">
            <Button variant="default" onClick={onClose}>Hủy</Button>
            <Button type="submit" leftSection={<IconPlus size={16} />} disabled={!ready} loading={save.isPending}>Tạo hồ sơ</Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
