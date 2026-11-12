import { Group, Pagination, ScrollArea, Table, Text } from "@mantine/core";
import { useState, type ReactNode } from "react";

export type Column<T> = { key: string; label: string; render?: (row: T) => ReactNode; width?: number | string; align?: "left" | "right" | "center" };

export function DataTable<T>({ rows, columns, pageSize = 25, onRowClick, empty = "Không có dòng nào.", rowKey, activeKey }: {
  rows: T[]; columns: Column<T>[]; pageSize?: number; onRowClick?: (row: T) => void; empty?: string;
  rowKey?: (row: T, i: number) => string | number; activeKey?: string | number | null;
}) {
  const [page, setPage] = useState(1);
  const pages = Math.max(1, Math.ceil(rows.length / pageSize));
  const cur = Math.min(page, pages);
  const slice = rows.slice((cur - 1) * pageSize, cur * pageSize);
  if (!rows.length) return <Text c="dimmed" size="sm" py="lg" ta="center">{empty}</Text>;
  return (
    <>
      <ScrollArea type="auto">
        <Table highlightOnHover verticalSpacing="sm" horizontalSpacing="md" fz="sm" miw={640}
          styles={{ th: { color: "var(--mantine-color-ink-5)", fontWeight: 600, fontSize: 12, textTransform: "uppercase", letterSpacing: ".03em", whiteSpace: "nowrap" } }}>
          <Table.Thead bg="ink.0">
            <Table.Tr>{columns.map((c) => <Table.Th key={c.key} w={c.width} ta={c.align}>{c.label}</Table.Th>)}</Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {slice.map((r, i) => {
              const k = rowKey ? rowKey(r, i) : i;
              return (
                <Table.Tr key={k} onClick={onRowClick ? () => onRowClick(r) : undefined}
                  style={{ cursor: onRowClick ? "pointer" : undefined }}
                  bg={activeKey !== undefined && activeKey === k ? "brand.0" : undefined}>
                  {columns.map((c) => (
                    <Table.Td key={c.key} ta={c.align}>
                      {c.render ? c.render(r) : String((r as Record<string, unknown>)[c.key] ?? "—")}
                    </Table.Td>
                  ))}
                </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
      </ScrollArea>
      {pages > 1 && (
        <Group justify="space-between" mt="md">
          <Text size="xs" c="dimmed">{rows.length} dòng</Text>
          <Pagination size="sm" total={pages} value={cur} onChange={setPage} />
        </Group>
      )}
    </>
  );
}

/** Bảng từ {columns, rows} của API (DataFrame). */
export function RecordsTable({ data, pageSize }: { data: { columns: string[]; rows: Record<string, unknown>[] }; pageSize?: number }) {
  const fmt = (v: unknown) => {
    if (v === null || v === undefined) return "—";
    if (typeof v === "number") return Number.isInteger(v) ? String(v) : v <= 1 && v >= 0 ? `${(v * 100).toFixed(1)}%` : v.toFixed(3);
    if (typeof v === "string" && /^\d{4}-\d{2}-\d{2}T/.test(v)) return v.slice(5, 16).replace("T", " ");
    return String(v);
  };
  return <DataTable rows={data.rows} pageSize={pageSize}
    columns={data.columns.map((c) => ({ key: c, label: c, render: (r: Record<string, unknown>) => fmt(r[c]) }))} />;
}
