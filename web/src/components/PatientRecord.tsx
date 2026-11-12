import {
  Alert, Badge, Box, Button, Group, Modal, Paper, Select, SimpleGrid, Stack, Switch, Text, Textarea,
} from "@mantine/core";
import { IconAlertTriangle, IconBabyCarriage, IconClipboardHeart, IconPackage, IconStethoscope } from "@tabler/icons-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { api } from "../lib/api";
import { useCan } from "../lib/auth";
import { fmtDate } from "../lib/format";
import { fail, ok } from "../lib/notify";
import { SectionTitle } from "./ui";

export const PACKAGES = ["Gói thai sản trọn gói", "Gói thai sản từ 28 tuần"];

export type Program = {
  in_package: boolean; package_name: string | null; package_since: string | null;
  override_monitoring: boolean; override_reason: string | null;
};

export type ObHistory = {
  age: number; para: string; gravida: number; lmp: string; edd: string; edd_basis: string; blood_group: string;
  allergy: string; medical_history: string[]; obstetric_history: string[]; family_history: string;
  risk_summary: string[]; delivery?: { date: string; ga: string; mode: string; baby: string; apgar: string };
  source: string;
};

/** Nhãn chương trình theo dõi – hiện ở đầu hồ sơ và danh sách. */
export function ProgramBadges({ p }: { p: Pick<Program, "in_package" | "package_name" | "override_monitoring"> }) {
  return (
    <Group gap={6} wrap="wrap">
      {p.in_package
        ? <Badge color="brand" variant="light" leftSection={<IconPackage size={12} />}>{p.package_name ?? "Theo gói"}</Badge>
        : <Badge color="gray" variant="light">Không theo gói</Badge>}
      {p.override_monitoring && <Badge color="grape" variant="light" leftSection={<IconStethoscope size={12} />}>Bác sĩ chỉ định theo dõi</Badge>}
    </Group>
  );
}

/** Sửa chương trình: điều dưỡng / bác sĩ đổi theo gói; chỉ bác sĩ chỉ định theo dõi ngoài gói (bắt buộc lý do). */
export function ProgramEditor({ patientId, program, opened, onClose }: {
  patientId: number; program: Program; opened: boolean; onClose: () => void;
}) {
  const can = useCan();
  const qc = useQueryClient();
  const [inPkg, setInPkg] = useState(program.in_package);
  const [pkg, setPkg] = useState<string | null>(program.package_name ?? PACKAGES[0]);
  const [ovr, setOvr] = useState(program.override_monitoring);
  const [reason, setReason] = useState(program.override_reason ?? "");
  const canOverride = can("override_monitoring");
  const save = useMutation({
    mutationFn: () => api<Program>(`/api/patients/${patientId}/program`, {
      method: "PUT", json: { in_package: inPkg, package_name: inPkg ? pkg : null, override_monitoring: ovr, override_reason: ovr ? reason : null },
    }),
    onSuccess: () => { ok("Đã lưu chương trình theo dõi."); qc.invalidateQueries({ queryKey: ["profile"] });
      qc.invalidateQueries({ queryKey: ["patients"] }); onClose(); },
    onError: (e) => fail(e),
  });
  return (
    <Modal opened={opened} onClose={onClose} title={<Text fw={700}>Chương trình theo dõi</Text>} centered>
      <Stack gap="md">
        <Switch label="Thai phụ theo gói thai sản" checked={inPkg} onChange={(e) => setInPkg(e.currentTarget.checked)} />
        {inPkg && <Select label="Gói" data={PACKAGES} value={pkg} onChange={setPkg} allowDeselect={false} />}
        <Paper p="sm" radius="md" bg="ink.0" withBorder={false}>
          <Stack gap="xs">
            <Switch label="Bác sĩ chỉ định theo dõi từ xa ngoài gói" checked={ovr} disabled={!canOverride}
              onChange={(e) => setOvr(e.currentTarget.checked)} />
            {ovr && <Textarea label="Lý do chỉ định" placeholder="Ví dụ: tăng huyết áp từ 28 tuần, nguy cơ tiền sản giật"
              value={reason} onChange={(e) => setReason(e.currentTarget.value)} disabled={!canOverride} autosize minRows={2}
              error={reason.trim().length > 0 && reason.trim().length < 10 ? "Ghi lý do ít nhất 10 ký tự" : undefined} />}
            {!canOverride && <Text size="xs" c="dimmed">Chỉ bác sĩ được chỉ định theo dõi ngoài gói.</Text>}
          </Stack>
        </Paper>
        <Text size="xs" c="dimmed">Chỉ thai phụ theo gói hoặc được bác sĩ chỉ định mới được gửi form theo dõi tại nhà.</Text>
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>Hủy</Button>
          <Button loading={save.isPending} onClick={() => save.mutate()}>Lưu</Button>
        </Group>
      </Stack>
    </Modal>
  );
}

function Field({ label, children, wide }: { label: string; children: ReactNode; wide?: boolean }) {
  return (
    <Box style={wide ? { gridColumn: "1 / -1" } : undefined}>
      <Text size="xs" c="ink.5" fw={600} tt="uppercase" style={{ letterSpacing: ".04em" }}>{label}</Text>
      <Box mt={2}>{children}</Box>
    </Box>
  );
}

/** Thông tin sản khoa của hồ sơ (phần hành chính – tiền sử của phiếu khám thai). */
export function ObstetricSummary({ h }: { h: ObHistory | null }) {
  if (!h) {
    return (
      <Paper p="lg">
        <SectionTitle icon={<IconClipboardHeart size={18} />}>Thông tin sản khoa</SectionTitle>
        <Text size="sm" c="dimmed">Chưa có thông tin sản khoa (tiền sử, PARA, nhóm máu) – sẽ có khi nối HIS.</Text>
      </Paper>
    );
  }
  const allergic = !/không ghi nhận/i.test(h.allergy);
  return (
    <Paper p="lg">
      <SectionTitle icon={<IconClipboardHeart size={18} />}
        right={h.source === "synthetic" ? <Badge size="sm" color="gray" variant="outline">Dữ liệu giả</Badge> : null}>
        Thông tin sản khoa
      </SectionTitle>
      {allergic && (
        <Alert color="danger" variant="light" icon={<IconAlertTriangle size={18} />} p="xs" mb="md">
          <Text size="sm" fw={600}>Dị ứng: {h.allergy}</Text>
        </Alert>
      )}
      <SimpleGrid cols={{ base: 2, sm: 3, lg: 4 }} spacing="md" verticalSpacing="md">
        <Field label="Tuổi"><Text size="sm" fw={600}>{h.age}</Text></Field>
        <Field label="PARA (trước lần này)"><Text size="sm" fw={600} ff="monospace">{h.para}</Text>
          <Text size="xs" c="dimmed">Lần mang thai thứ {h.gravida}</Text></Field>
        <Field label="Kỳ kinh cuối"><Text size="sm" fw={600}>{fmtDate(h.lmp)}</Text></Field>
        <Field label="Ngày dự sinh"><Text size="sm" fw={600}>{fmtDate(h.edd)}</Text>
          <Text size="xs" c="dimmed">{h.edd_basis}</Text></Field>
        <Field label="Nhóm máu"><Text size="sm" fw={600}>{h.blood_group}</Text></Field>
        <Field label="Dị ứng"><Text size="sm" fw={allergic ? 700 : 400} c={allergic ? "danger.7" : undefined}>{h.allergy}</Text></Field>
        <Field label="Tiền sử gia đình"><Text size="sm">{h.family_history}</Text></Field>
        <Field label="Tiền sử nội khoa"><Stack gap={0}>{h.medical_history.map((x) => <Text key={x} size="sm">{x}</Text>)}</Stack></Field>
        <Field label="Tiền sử sản khoa" wide>
          <Stack gap={2}>{h.obstetric_history.map((x) => <Text key={x} size="sm">• {x}</Text>)}</Stack>
        </Field>
        {h.delivery && (
          <Field label="Cuộc sinh lần này" wide>
            <Group gap="xs" wrap="wrap">
              <Badge color="ok" variant="light" leftSection={<IconBabyCarriage size={12} />}>{fmtDate(h.delivery.date)}</Badge>
              <Text size="sm">{h.delivery.mode} lúc {h.delivery.ga} tuần · {h.delivery.baby} · Apgar {h.delivery.apgar}</Text>
            </Group>
          </Field>
        )}
      </SimpleGrid>
    </Paper>
  );
}
