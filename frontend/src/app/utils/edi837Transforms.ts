/** X12 837 date/time → JSON formats (mapping step). Non-temporal fields are always direct. */

import type { Edi837MappingRow } from "./edi837MappingRows";

export type Edi837TransformId =
  | "passthrough"
  | "date_ccyymmdd_iso"
  | "date_yymmdd_iso"
  | "time_hhmm"
  | "time_hhmmss";

export type Edi837DateTimeFormatId = Exclude<Edi837TransformId, "passthrough">;

export type Edi837TemporalKind = "date" | "time";

export interface Edi837FormatOption {
  id: Edi837DateTimeFormatId;
  label: string;
  description: string;
}

export const EDI837_DATE_FORMAT_OPTIONS: Edi837FormatOption[] = [
  {
    id: "date_ccyymmdd_iso",
    label: "CCYYMMDD → ISO 8601",
    description: "20160918 → 2016-09-18",
  },
  {
    id: "date_yymmdd_iso",
    label: "YYMMDD → ISO 8601",
    description: "160918 → 2016-09-18",
  },
];

export const EDI837_TIME_FORMAT_OPTIONS: Edi837FormatOption[] = [
  { id: "time_hhmm", label: "HHMM → HH:MM", description: "0932 → 09:32" },
  { id: "time_hhmmss", label: "HHMMSS → HH:MM:SS", description: "093201 → 09:32:01" },
];

export function formatOptionsForKind(kind: Edi837TemporalKind): Edi837FormatOption[] {
  return kind === "date" ? EDI837_DATE_FORMAT_OPTIONS : EDI837_TIME_FORMAT_OPTIONS;
}

export function formatLabel(id: Edi837DateTimeFormatId): string {
  return (
    EDI837_DATE_FORMAT_OPTIONS.find((o) => o.id === id)?.label ??
    EDI837_TIME_FORMAT_OPTIONS.find((o) => o.id === id)?.label ??
    id
  );
}

function digitsOnly(value: string): string {
  return value.replace(/\D/g, "");
}

function padDigits(d: string, length: number): string {
  if (!d) return d;
  if (d.length > length) return d.slice(0, length);
  return d.padStart(length, "0");
}

export function applyEdi837Transform(value: string, transformId: Edi837TransformId): unknown {
  const raw = value ?? "";
  const v = raw.trim();
  switch (transformId) {
    case "passthrough":
      return raw;
    case "date_ccyymmdd_iso": {
      const d = padDigits(digitsOnly(v), 8);
      if (d.length !== 8) return raw;
      return `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}`;
    }
    case "date_yymmdd_iso": {
      const d = padDigits(digitsOnly(v), 6);
      if (d.length !== 6) return raw;
      const yy = Number.parseInt(d.slice(0, 2), 10);
      const century = yy < 50 ? 2000 : 1900;
      const year = century + yy;
      return `${year}-${d.slice(2, 4)}-${d.slice(4, 6)}`;
    }
    case "time_hhmm": {
      const d = padDigits(digitsOnly(v), 4);
      if (d.length !== 4) return raw;
      return `${d.slice(0, 2)}:${d.slice(2, 4)}`;
    }
    case "time_hhmmss": {
      const d = padDigits(digitsOnly(v), 6);
      if (d.length !== 6) return raw;
      return `${d.slice(0, 2)}:${d.slice(2, 4)}:${d.slice(4, 6)}`;
    }
    default:
      return raw;
  }
}

type TemporalInput = Pick<
  Edi837MappingRow,
  "segment_id" | "element_id" | "element_name" | "value" | "meaning"
>;

/** Date/time fields can use Transform + format; everything else is locked to Direct. */
export function classifyEdi837Temporal(row: TemporalInput): Edi837TemporalKind | null {
  const name = row.element_name.toLowerCase();
  const mean = (row.meaning || "").toLowerCase();
  const digits = (row.value || "").trim().replace(/\D/g, "");

  if (
    mean.includes("hhmm") ||
    name.includes("time") ||
    row.element_id === "BHT05" ||
    (row.segment_id === "DTP" && row.element_id === "DTP03" && mean.includes("tm"))
  ) {
    return "time";
  }
  if (row.segment_id === "DTP" && row.element_id === "DTP03") {
    if (mean.includes("tm") || mean.includes("hhmm")) return "time";
    return "date";
  }
  if (
    mean.includes("ccyymmdd") ||
    mean.includes("yymmdd") ||
    mean.includes("(d8)") ||
    name.includes("date") ||
    row.element_id === "BHT04"
  ) {
    return "date";
  }
  if (name.includes("date") && digits.length === 8) return "date";
  if (name.includes("time") && digits.length >= 4) return "time";
  return null;
}

export function suggestDateTimeFormat(row: TemporalInput, kind: Edi837TemporalKind): Edi837DateTimeFormatId {
  const mean = (row.meaning || "").toLowerCase();
  const digits = (row.value || "").trim().replace(/\D/g, "");
  if (kind === "time") {
    if (digits.length >= 6 || mean.includes("hhmmss")) return "time_hhmmss";
    return "time_hhmm";
  }
  if (mean.includes("yymmdd") || digits.length === 6) return "date_yymmdd_iso";
  return "date_ccyymmdd_iso";
}

export type Edi837TransformMode = "direct" | "transform";

export type Edi837RowApprovalStatus = "pending" | "approved";

export interface Edi837RowMappingConfig {
  mode: Edi837TransformMode;
  formatId?: Edi837DateTimeFormatId;
  status: Edi837RowApprovalStatus;
}

export function defaultRowMappingConfig(row: Edi837MappingRow): Edi837RowMappingConfig {
  const kind = classifyEdi837Temporal(row);
  if (!kind) {
    return { mode: "direct", status: "approved" };
  }
  return {
    mode: "transform",
    formatId: suggestDateTimeFormat(row, kind),
    status: "pending",
  };
}

export function transformModeLabel(row: Edi837MappingRow, config: Edi837RowMappingConfig): string {
  if (!classifyEdi837Temporal(row)) return "Direct";
  return config.mode === "transform" ? "Transform" : "Direct";
}

function formatMatchesKind(formatId: Edi837DateTimeFormatId, kind: Edi837TemporalKind): boolean {
  return kind === "date" ? formatId.startsWith("date_") : formatId.startsWith("time_");
}

export function effectiveFormatId(
  row: Edi837MappingRow,
  config: Edi837RowMappingConfig
): Edi837DateTimeFormatId | null {
  const kind = classifyEdi837Temporal(row);
  if (!kind || config.mode !== "transform") return null;
  const suggested = suggestDateTimeFormat(row, kind);
  const chosen = config.formatId ?? suggested;
  return formatMatchesKind(chosen, kind) ? chosen : suggested;
}

export function resolveTransformId(
  row: Edi837MappingRow,
  config: Edi837RowMappingConfig
): Edi837TransformId {
  if (!classifyEdi837Temporal(row) || config.mode === "direct") {
    return "passthrough";
  }
  return effectiveFormatId(row, config) ?? suggestDateTimeFormat(row, classifyEdi837Temporal(row)!);
}

/** Table/drawer preview — always reflects chosen format when Transform is on. */
export function previewMappingValue(row: Edi837MappingRow, config: Edi837RowMappingConfig): string {
  const transformId = resolveTransformId(row, config);
  return String(applyEdi837Transform(row.value, transformId));
}

export type Edi837TransformMap = Record<string, Edi837TransformId>;

export function buildExportTransformMap(
  rows: Edi837MappingRow[],
  rowConfigs: Record<string, Edi837RowMappingConfig>
): Edi837TransformMap {
  const out: Edi837TransformMap = {};
  for (const row of rows) {
    const config = rowConfigs[row.id] ?? defaultRowMappingConfig(row);
    out[row.id] = resolveTransformId(row, config);
  }
  return out;
}
