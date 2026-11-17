import { Anchor, Badge, Button, Checkbox, Group, Paper, Stack, Text, Textarea } from "@mantine/core";
import { IconCheck, IconExternalLink, IconX } from "@tabler/icons-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { fmtShort } from "../lib/format";

export type Evidence = { source: string; label: string; text: string; date?: string | null; confidence?: number | null; model?: string | null };
export type ProposalItem = { code: string; name: string; kind: string; schedule: string; n_questions: number;
  flags: { flag: string; name: string; evidence: Evidence[] }[] };
export type Proposal = { patient_id: number; code: string; stage: string; last_visit: string | null; next_appt: string | null;
  current: string[]; flags: string[]; modules: string[]; items: ProposalItem[] };

type Props = {
  p: Proposal;
  busy: boolean;
  canAct: boolean;
  onConfirm: (codes: string[], insulin: boolean) => void;
  onDecline: (codes: string[], reason: string) => void;
};

/** Một đề xuất theo dõi thêm: hồ sơ tóm tắt + CĂN CỨ từng đề xuất để điều dưỡng kiểm trước khi xác nhận / từ chối. */
export function ProposalCard({ p, busy, canAct, onConfirm, onDecline }: Props) {
  const [insulin, setInsulin] = useState(false);
  const [declining, setDeclining] = useState<string | null>(null);
  const [reason, setReason] = useState("");

  return (
    <Paper p="sm" withBorder shadow="none" radius="md">
      <Group justify="space-between" align="flex-start" wrap="wrap" gap="xs" mb={6}>
        <div>
          <Text fw={700} size="sm">{p.code} · {p.stage}</Text>
          <Text size="xs" c="dimmed">
            Khám gần nhất {p.last_visit ? fmtShort(p.last_visit) : "—"} · Hẹn tới {p.next_appt ? fmtShort(p.next_appt) : "—"}
            {p.current.length ? ` · Đang theo dõi: ${p.current.join(", ")}` : ""}
          </Text>
        </div>
        <Anchor component={Link} to={`/ho-so?q=${encodeURIComponent(p.code)}`} size="xs">
          <Group gap={4} wrap="nowrap"><IconExternalLink size={14} />Mở hồ sơ</Group>
        </Anchor>
      </Group>

      <Stack gap="xs">
        {p.items.map((it) => {
          const noBasis = it.flags.every((f) => f.evidence.every((e) => e.source === "ai_note" && !e.date));
          return (
            <Paper key={it.code} p="xs" bg="gray.0" shadow="none" radius="sm">
              <Group justify="space-between" wrap="wrap" gap={6}>
                <Text size="sm" fw={600}>Đề xuất: {it.name} <Badge size="xs" variant="light" ml={4}>{it.code}</Badge></Text>
                <Text size="xs" c="dimmed">{it.schedule} · {it.n_questions} câu hỏi</Text>
              </Group>
              <Text size="xs" fw={600} mt={6}>Căn cứ</Text>
              <Stack gap={2}>
                {it.flags.map((f) => f.evidence.map((e, i) => (
                  <Text key={`${f.flag}-${i}`} size="xs">
                    <b>{f.name}</b> – {e.label}{e.date ? ` (${fmtShort(e.date)})` : ""}: “{e.text}”
                    {e.confidence != null && <Text span c="dimmed"> · AI tin cậy {Math.round(e.confidence * 100)}%</Text>}
                  </Text>
                )))}
              </Stack>
              {noBasis && <Text size="xs" c="warn.8" mt={4}>Không tìm thấy ghi chú gốc – mở hồ sơ để kiểm trước khi xác nhận.</Text>}

              {declining === it.code ? (
                <Stack gap={6} mt="xs">
                  <Textarea size="xs" autosize minRows={2} label="Lý do không đồng ý (bắt buộc)" value={reason}
                    placeholder="VD: đã hỏi khách, không có tăng huyết áp; ghi chú cũ của thai trước"
                    onChange={(e) => setReason(e.currentTarget.value)} />
                  <Group gap="xs" justify="flex-end">
                    <Button size="xs" variant="subtle" onClick={() => { setDeclining(null); setReason(""); }}>Hủy</Button>
                    <Button size="xs" color="danger" loading={busy} disabled={reason.trim().length < 3}
                      onClick={() => onDecline([it.code], reason.trim())}>Gửi – không theo dõi</Button>
                  </Group>
                </Stack>
              ) : (
                <Group gap="sm" justify="flex-end" mt="xs">
                  {it.code === "M-DTD" && <Checkbox size="xs" label="Khách đang dùng insulin" checked={insulin}
                    onChange={(e) => setInsulin(e.currentTarget.checked)} />}
                  <Button size="xs" variant="default" leftSection={<IconX size={14} />} disabled={!canAct}
                    onClick={() => setDeclining(it.code)}>Không đồng ý</Button>
                  <Button size="xs" leftSection={<IconCheck size={14} />} loading={busy} disabled={!canAct}
                    onClick={() => onConfirm([it.code], insulin)}>Xác nhận theo dõi</Button>
                </Group>
              )}
            </Paper>
          );
        })}
      </Stack>
    </Paper>
  );
}
