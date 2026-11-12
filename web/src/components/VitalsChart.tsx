import { LineChart } from "@mantine/charts";
import { Text } from "@mantine/core";
import type { BpSeries, GlucoseSeries } from "../lib/api";
import { fmtShort } from "../lib/format";

export function BpChart({ bp, h = 260 }: { bp: BpSeries; h?: number }) {
  return (
    <LineChart h={h} data={bp.rows.map((r) => ({ ...r, t: fmtShort(r.t) }))} dataKey="t" unit=" mmHg" withLegend
      legendProps={{ verticalAlign: "bottom" }} curveType="monotone" connectNulls
      series={[{ name: "sys", label: "Tâm thu", color: "blue.6" }, { name: "dia", label: "Tâm trương", color: "brand.6" },
        { name: "ewma", label: "EWMA tâm thu", color: "gray.5", strokeDasharray: "5 5" }]}
      referenceLines={[{ y: bp.yellow, label: `Vàng ${bp.yellow}`, color: "warn.6" }, { y: bp.red, label: `Đỏ ${bp.red}`, color: "danger.6" },
        ...(bp.upper ? [{ y: bp.upper, label: `Nền + ${bp.sigma_k}σ`, color: "gray.5" }] : [])]} />
  );
}

export function GlucoseChart({ g, h = 240 }: { g: GlucoseSeries; h?: number }) {
  return (
    <LineChart h={h} data={g.rows.map((r) => ({ ...r, t: fmtShort(String(r.t)) }))} dataKey="t" unit=" mmol/L"
      withLegend legendProps={{ verticalAlign: "bottom" }} connectNulls curveType="monotone"
      series={Object.entries(g.labels).map(([k, label], i) => ({ name: k, label, color: ["brand.6", "blue.6", "grape.6"][i] }))}
      referenceLines={[{ y: g.fasting_threshold, label: `Ngưỡng đói ${g.fasting_threshold}`, color: "warn.6" }]} />
  );
}

export function VitalsCharts({ charts }: { charts: { bp: BpSeries | null; glucose: GlucoseSeries | null } }) {
  if (!charts.bp && !charts.glucose) return <Text size="sm" c="dimmed">Chưa có số đo.</Text>;
  return (
    <>
      {charts.bp && <BpChart bp={charts.bp} />}
      {charts.glucose && <GlucoseChart g={charts.glucose} />}
    </>
  );
}
