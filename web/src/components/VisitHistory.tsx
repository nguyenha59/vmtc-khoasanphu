import { Accordion, Badge, Box, Divider, Group, SimpleGrid, Stack, Table, Text } from "@mantine/core";
import { IconFlask, IconStethoscope } from "@tabler/icons-react";
import type { ReactNode } from "react";
import { fmtDate } from "../lib/format";

type RefRange = (number | string | null)[];
export type Lab = { code: string; name: string; value: number | string; unit: string; loinc: string; ref: RefRange; flag: "" | "H" | "L" | "A" };
export type Measurement = { name: string; value: number; unit: string };
export type VisitResults = {
  vitals?: { bp: string; bp_sys: number; bp_dia: number; pulse: number; weight: number; edema: string; bp_flag?: "" | "yellow" | "red" };
  obstetric: { fhr: string; fundal_height_cm?: number; presentation?: string } | null;
  labs: Lab[];
  ultrasound: { kind: string; measurements: Measurement[]; fhr: string; placenta: string; conclusion: string } | null;
  diagnoses: { code: string; name: string; primary: boolean }[];
  plan: string[];
  source: string; reference_version: string | null; rules_version?: string;
};

/** Một lần khám (API /api/profile → visits). `results` = null khi chưa nối HIS/LIS. */
export type Visit = {
  id: number; date: string; time: string; ga: string; services: string[]; doctor: string; department: string;
  note: string; attend: "came" | "no_show" | "unknown" | "upcoming"; attend_label: string;
  arrival_sms: string | null; next_date: string | null; merged: boolean; results: VisitResults | null;
};

const ATTEND_COLOR: Record<Visit["attend"], string> = { came: "ok", no_show: "danger", unknown: "gray", upcoming: "blue" };
const FLAG: Record<Exclude<Lab["flag"], "">, { label: string; color: string }> = {
  H: { label: "Cao", color: "danger" }, L: { label: "Thấp", color: "warn" }, A: { label: "Bất thường", color: "danger" },
};

const fmtNum = (v: number | string) => (typeof v === "number" ? v.toLocaleString("vi-VN") : v);

/** Khoảng tham chiếu như phiếu LIS: "110 – 150", "≤ 5,0", "≥ 100", hoặc giá trị định tính. */
function refText(ref: RefRange): string {
  if (ref.length && ref.every((x) => typeof x === "string")) return ref.join(", ");
  const [lo, hi] = ref as (number | null)[];
  if (lo != null && hi != null) return `${fmtNum(lo)} – ${fmtNum(hi)}`;
  if (hi != null) return `≤ ${fmtNum(hi)}`;
  if (lo != null) return `≥ ${fmtNum(lo)}`;
  return "—";
}

function Section({ title, icon, children, wide }: { title: string; icon?: ReactNode; children: ReactNode; wide?: boolean }) {
  return (
    <Box style={wide ? { gridColumn: "1 / -1" } : undefined}>
      <Group gap={6} mb={6}>
        {icon}
        <Text size="xs" fw={700} c="ink.5" tt="uppercase" style={{ letterSpacing: ".04em" }}>{title}</Text>
      </Group>
      {children}
    </Box>
  );
}

function KV({ k, v, strong, color }: { k: string; v: ReactNode; strong?: boolean; color?: string }) {
  return (
    <Group gap="xs" wrap="nowrap" justify="space-between" py={2} style={{ borderBottom: "1px dashed var(--mantine-color-ink-1)" }}>
      <Text size="sm" c="dimmed">{k}</Text>
      <Text size="sm" fw={strong ? 700 : 500} c={color} ta="right">{v}</Text>
    </Group>
  );
}

function Missing({ hint }: { hint: string }) {
  return <Text size="sm" c="dimmed" fs="italic">Chưa có dữ liệu · {hint}</Text>;
}

function LabTable({ labs }: { labs: Lab[] }) {
  return (
    <Table.ScrollContainer minWidth={520}>
      <Table striped highlightOnHover verticalSpacing={6} fz="sm">
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Xét nghiệm</Table.Th>
            <Table.Th ta="right">Kết quả</Table.Th>
            <Table.Th>Đơn vị</Table.Th>
            <Table.Th>Tham chiếu</Table.Th>
            <Table.Th w={90}>Cờ</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {labs.map((l) => (
            <Table.Tr key={l.code}>
              <Table.Td>
                <Text size="sm">{l.name}</Text>
                <Text size="xs" c="dimmed" ff="monospace">LOINC {l.loinc}</Text>
              </Table.Td>
              <Table.Td ta="right"><Text size="sm" fw={l.flag ? 700 : 500} c={l.flag ? `${FLAG[l.flag].color}.7` : undefined}>{fmtNum(l.value)}</Text></Table.Td>
              <Table.Td><Text size="sm" c="dimmed">{l.unit || "—"}</Text></Table.Td>
              <Table.Td><Text size="sm" c="dimmed">{refText(l.ref)}</Text></Table.Td>
              <Table.Td>{l.flag ? <Badge size="sm" color={FLAG[l.flag].color} variant="light">{l.flag} · {FLAG[l.flag].label}</Badge> : null}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

function Results({ r }: { r: VisitResults }) {
  const v = r.vitals;
  const bpColor = v?.bp_flag === "red" ? "danger.7" : v?.bp_flag === "yellow" ? "warn.7" : undefined;
  const primary = r.diagnoses.filter((d) => d.primary);
  const secondary = r.diagnoses.filter((d) => !d.primary);
  return (
    <>
      <Section title="Khám toàn thân">
        {v ? (
          <Stack gap={0}>
            <KV k="Huyết áp" v={<>{v.bp} mmHg{v.bp_flag ? <Badge ml={6} size="xs" color={v.bp_flag === "red" ? "danger" : "warn"}>
              {v.bp_flag === "red" ? "Rất cao" : "Cao"}</Badge> : null}</>} strong={!!v.bp_flag} color={bpColor} />
            <KV k="Mạch" v={`${v.pulse} lần/phút`} />
            <KV k="Cân nặng" v={`${fmtNum(v.weight)} kg`} />
            <KV k="Phù" v={v.edema} color={v.edema !== "Không phù" ? "warn.7" : undefined} />
          </Stack>
        ) : <Missing hint="Huyết áp, mạch, cân nặng, phù" />}
      </Section>
      <Section title="Khám sản khoa">
        {r.obstetric ? (
          <Stack gap={0}>
            <KV k="Tim thai" v={r.obstetric.fhr} />
            {r.obstetric.fundal_height_cm != null && <KV k="Bề cao tử cung" v={`${r.obstetric.fundal_height_cm} cm`} />}
            {r.obstetric.presentation && <KV k="Ngôi thai" v={r.obstetric.presentation}
              color={r.obstetric.presentation !== "Ngôi đầu" ? "warn.7" : undefined} />}
          </Stack>
        ) : <Text size="sm" c="dimmed">Không áp dụng ở lần khám này</Text>}
      </Section>
      <Section title="Xét nghiệm" icon={<IconFlask size={14} color="var(--mantine-color-ink-5)" />} wide>
        {r.labs.length ? <LabTable labs={r.labs} /> : <Text size="sm" c="dimmed">Không chỉ định xét nghiệm lần này</Text>}
      </Section>
      {r.ultrasound && (
        <Section title={r.ultrasound.kind} wide>
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="lg">
            <Stack gap={0}>
              {r.ultrasound.measurements.map((m) => <KV key={m.name} k={m.name} v={`${fmtNum(m.value)} ${m.unit}`} />)}
              <KV k="Tim thai" v={r.ultrasound.fhr} />
            </Stack>
            <Stack gap={4}>
              <Text size="sm"><Text span c="dimmed">Bánh rau: </Text>{r.ultrasound.placenta.replace(/^Bánh rau /, "")}</Text>
              <Text size="sm" fw={600}>Kết luận</Text>
              <Text size="sm">{r.ultrasound.conclusion}</Text>
            </Stack>
          </SimpleGrid>
        </Section>
      )}
      <Section title="Chẩn đoán (ICD-10)" icon={<IconStethoscope size={14} color="var(--mantine-color-ink-5)" />}>
        <Stack gap={6}>
          {[...primary, ...secondary].map((d) => (
            <Group key={d.code} gap="xs" wrap="nowrap" align="flex-start">
              <Badge variant={d.primary ? "filled" : "light"} color={d.code.startsWith("O") ? "grape" : "brand"} ff="monospace"
                style={{ flex: "none" }}>{d.code}</Badge>
              <Box>
                <Text size="sm" fw={d.primary ? 600 : 400}>{d.name}</Text>
                {d.primary && r.diagnoses.length > 1 && <Text size="xs" c="dimmed">Chẩn đoán chính</Text>}
              </Box>
            </Group>
          ))}
        </Stack>
      </Section>
      <Section title="Xử trí">
        {r.plan.length ? <Stack gap={2}>{r.plan.map((x) => <Text key={x} size="sm">• {x}</Text>)}</Stack> : <Text size="sm">—</Text>}
      </Section>
    </>
  );
}

function VisitSummary({ v }: { v: Visit }) {
  const r = v.results;
  if (!r) return <Text size="xs" c="dimmed" lineClamp={1}>{v.services.join(", ") || "—"}</Text>;
  const abn = r.labs.filter((l) => l.flag).length;
  const main = r.diagnoses.find((d) => d.primary);
  return (
    <Group gap={6} wrap="wrap">
      {main && <Badge size="xs" variant="light" color={main.code.startsWith("O") ? "grape" : "gray"} ff="monospace">{main.code}</Badge>}
      {r.vitals && <Text size="xs" c={r.vitals.bp_flag ? "danger.7" : "dimmed"} fw={r.vitals.bp_flag ? 700 : 400}>HA {r.vitals.bp}</Text>}
      {r.ultrasound && <Badge size="xs" variant="outline" color="blue">Siêu âm</Badge>}
      {abn > 0 && <Badge size="xs" color="danger" variant="light">{abn} XN bất thường</Badge>}
    </Group>
  );
}

/**
 * Các phần của một lần khám theo thứ tự phiếu khám thai (QĐ 4128/QĐ-BYT – chăm sóc trước sinh):
 * thông tin lần khám → lý do khám → khám toàn thân → khám sản khoa → xét nghiệm → siêu âm → chẩn đoán → xử trí, hẹn lần sau.
 * Phần nào chưa có dữ liệu ghi rõ "chưa có dữ liệu" – không để trống (dễ hiểu nhầm là bình thường).
 */
export function VisitHistory({ visits }: { visits: Visit[] }) {
  if (!visits.length) return <Text c="dimmed" size="sm" py="lg" ta="center">Chưa có lần khám nào trong hệ thống.</Text>;
  const past = visits.filter((v) => v.attend !== "upcoming");
  const came = past.filter((v) => v.attend === "came").length;
  const noShow = past.filter((v) => v.attend === "no_show").length;
  const synthetic = visits.some((v) => v.results?.source === "synthetic");
  const latestDone = visits.find((v) => v.results);
  return (
    <Stack gap="sm">
      <Group gap="xs" wrap="wrap" justify="space-between">
        <Group gap="xs" wrap="wrap">
          <Badge variant="light" color="gray">{past.length} lần đã hẹn</Badge>
          <Badge variant="light" color="ok">{came} lần đến</Badge>
          {noShow > 0 && <Badge variant="light" color="danger">{noShow} lần không đến</Badge>}
        </Group>
        {synthetic && <Badge variant="outline" color="gray">Kết quả khám là dữ liệu giả lập</Badge>}
      </Group>
      <Accordion variant="separated" radius="md" multiple defaultValue={latestDone ? [String(latestDone.id)] : []}>
        {visits.map((v) => (
          <Accordion.Item key={v.id} value={String(v.id)}>
            <Accordion.Control>
              <Group justify="space-between" wrap="wrap" gap="xs">
                <Group gap="md" wrap="nowrap" style={{ minWidth: 0 }}>
                  <Box style={{ flex: "none", width: 82 }}>
                    <Text fw={700} size="sm">{fmtDate(v.date)}</Text>
                    <Text size="xs" c="dimmed">{v.time}</Text>
                  </Box>
                  <Box style={{ minWidth: 0 }}>
                    <Text size="sm" fw={600}>{v.ga.includes("+") ? `Tuần ${v.ga}` : v.ga}</Text>
                    <VisitSummary v={v} />
                  </Box>
                </Group>
                <Badge color={ATTEND_COLOR[v.attend]} variant={v.attend === "no_show" ? "filled" : "light"}>{v.attend_label}</Badge>
              </Group>
            </Accordion.Control>
            <Accordion.Panel>
              <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="lg" verticalSpacing="lg">
                <Section title="Thông tin lần khám">
                  <Text size="sm">{fmtDate(v.date)} lúc {v.time} · {v.ga.includes("+") ? `tuổi thai ${v.ga}` : v.ga}</Text>
                  <Text size="sm">{v.doctor || "—"}{v.department ? ` · ${v.department}` : ""}</Text>
                  {v.arrival_sms && <Text size="sm" c="dimmed">Tin nhắn hẹn đến lúc {v.arrival_sms}</Text>}
                  {v.merged && <Text size="xs" c="grape.7">Gộp từ nhiều dòng lịch hẹn</Text>}
                </Section>
                <Section title="Lý do khám / dịch vụ">
                  {v.services.length ? v.services.map((s) => <Text key={s} size="sm">• {s}</Text>) : <Text size="sm">—</Text>}
                </Section>
                {v.note && (
                  <Section title="Ghi chú của bác sĩ (HIS)" wide>
                    <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>{v.note}</Text>
                  </Section>
                )}
                {v.results ? <Results r={v.results} />
                  : v.attend === "upcoming" ? null
                  : v.attend === "no_show" ? <Section title="Kết quả khám" wide><Text size="sm" c="dimmed">Thai phụ không đến – không có kết quả.</Text></Section>
                  : <Section title="Kết quả khám" wide><Missing hint="khám toàn thân, khám sản, xét nghiệm, siêu âm, chẩn đoán ICD-10 – sẽ có khi nối HIS/LIS" /></Section>}
                <Section title="Hẹn lần sau" wide>
                  <Text size="sm">{v.next_date ? `Lần hẹn kế tiếp: ${fmtDate(v.next_date)}` : v.attend === "upcoming" ? "Lần khám sắp tới" : "Chưa có lịch hẹn kế tiếp"}</Text>
                </Section>
              </SimpleGrid>
              {v.results && (
                <>
                  <Divider my="sm" />
                  <Text size="xs" c="dimmed">
                    Nguồn: {v.results.source === "synthetic" ? "dữ liệu giả lập" : v.results.source}
                    {v.results.reference_version ? ` · bảng tham chiếu ${v.results.reference_version}` : ""}
                    {v.results.rules_version ? ` · quy tắc ${v.results.rules_version}` : ""}
                  </Text>
                </>
              )}
            </Accordion.Panel>
          </Accordion.Item>
        ))}
      </Accordion>
    </Stack>
  );
}
