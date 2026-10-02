import type { Edi837MappingRow } from "./edi837MappingRows";
import {
  defaultRowMappingConfig,
  type Edi837RowMappingConfig,
} from "./edi837Transforms";

const PREFIX = "edi837-json-mappings:";

export interface Edi837MappingPackage {
  version: 2;
  rows: Record<string, Edi837RowMappingConfig>;
}

export function emptyEdi837MappingPackage(): Edi837MappingPackage {
  return { version: 2, rows: {} };
}

export function loadEdi837MappingPackage(hl7SessionId: string): Edi837MappingPackage {
  try {
    const raw = localStorage.getItem(`${PREFIX}${hl7SessionId}`);
    if (!raw) return emptyEdi837MappingPackage();
    const parsed = JSON.parse(raw) as Edi837MappingPackage | Record<string, string>;
    if (parsed && typeof parsed === "object" && (parsed as Edi837MappingPackage).version === 2) {
      return parsed as Edi837MappingPackage;
    }
    return emptyEdi837MappingPackage();
  } catch {
    return emptyEdi837MappingPackage();
  }
}

export function saveEdi837MappingPackage(hl7SessionId: string, pkg: Edi837MappingPackage): void {
  localStorage.setItem(`${PREFIX}${hl7SessionId}`, JSON.stringify(pkg));
}

/** Ensure every decoded row has a config entry (defaults for new fields). */
export function mergeMappingRows(
  pkg: Edi837MappingPackage,
  allRows: Edi837MappingRow[]
): Edi837MappingPackage {
  const rows = { ...pkg.rows };
  let changed = false;
  for (const row of allRows) {
    if (!rows[row.id]) {
      rows[row.id] = defaultRowMappingConfig(row);
      changed = true;
    }
  }
  if (!changed) return pkg;
  return { version: 2, rows };
}

export function getRowConfig(
  pkg: Edi837MappingPackage,
  row: Edi837MappingRow
): Edi837RowMappingConfig {
  return pkg.rows[row.id] ?? defaultRowMappingConfig(row);
}
