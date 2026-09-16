import axiosInstance from "../utils/axios-interceptor";
import { fetchProfilingDashboardPayload } from "../end-points/profilingResultApi";

type SheetTableEntry = {
  sheet_name?: string | null;
  table_name?: string;
};

const pickRowValue = (row: Record<string, unknown>, ...keys: string[]): string => {
  for (const key of keys) {
    const match = Object.entries(row).find(
      ([columnName]) => columnName.toLowerCase() === key.toLowerCase(),
    );
    if (match && match[1] != null && String(match[1]).trim() !== "") {
      return String(match[1]);
    }
  }
  return "";
};

const collectSheetTables = (uploadData: any): SheetTableEntry[] => {
  const uploads = uploadData?.successful_uploads;
  if (!Array.isArray(uploads)) return [];

  return uploads.flatMap((upload: any) => upload?.access_info?.tables_created || []);
};

const findSheetTable = (
  tables: SheetTableEntry[],
  needles: string[],
  matchAll = false,
): string | null => {
  for (const entry of tables) {
    const haystack = `${entry.sheet_name || ""} ${entry.table_name || ""}`.toLowerCase();
    const matched = matchAll
      ? needles.every((needle) => haystack.includes(needle))
      : needles.some((needle) => haystack.includes(needle));
    if (matched) {
      return entry.table_name || null;
    }
  }
  return null;
};

const normalizeTableSuffix = (tableName: string): string => {
  const shortName = tableName.split(".").pop() || tableName;
  return shortName.toLowerCase();
};

const discoverIbcTableCandidates = (uploadData: any): string[] => {
  const tables = collectSheetTables(uploadData);
  const candidates = new Set<string>();

  for (const entry of tables) {
    if (entry.table_name) {
      candidates.add(entry.table_name);
    }
  }

  const uploads = uploadData?.successful_uploads;
  if (Array.isArray(uploads)) {
    for (const upload of uploads) {
      const tableName = upload?.table_name;
      if (typeof tableName !== "string" || !tableName.trim()) continue;

      const suffix = normalizeTableSuffix(tableName);
      [
        `${suffix}_nulls___lengths`,
        `${suffix}_nulls_and_lengths`,
        `${suffix}_defaults`,
        `${suffix}_foreign_keys`,
        `${suffix}_foreignkeys`,
      ].forEach((name) => candidates.add(name));

      if (upload?.access_info?.tables_created) {
        continue;
      }

      const prefixMatch = suffix.match(/^(sttm_profiling_[^_]+(?:_[^_]+)*)/);
      if (prefixMatch?.[1]) {
        const prefix = prefixMatch[1];
        [
          `${prefix}_nulls___lengths`,
          `${prefix}_defaults`,
          `${prefix}_foreign_keys`,
        ].forEach((name) => candidates.add(name));
      }
    }
  }

  return [...candidates];
};

const resolveIbcDashboardTableNames = (uploadData: any): {
  nullsTable: string | null;
  defaultsTable: string | null;
  foreignKeysTable: string | null;
} => {
  const tables = collectSheetTables(uploadData);

  const fromAccessInfo = {
    nullsTable:
      findSheetTable(tables, ["nulls___lengths", "nulls_and_lengths"], true) ||
      findSheetTable(tables, ["null", "length"], true),
    defaultsTable: findSheetTable(tables, ["default"], false),
    foreignKeysTable:
      findSheetTable(tables, ["foreign_keys"], true) ||
      findSheetTable(tables, ["foreign"], false),
  };

  if (
    fromAccessInfo.nullsTable ||
    fromAccessInfo.defaultsTable ||
    fromAccessInfo.foreignKeysTable
  ) {
    return fromAccessInfo;
  }

  const candidates = discoverIbcTableCandidates(uploadData);
  return {
    nullsTable:
      candidates.find((name) => name.includes("null") && name.includes("length")) ||
      null,
    defaultsTable: candidates.find((name) => name.includes("default")) || null,
    foreignKeysTable: candidates.find((name) => name.includes("foreign")) || null,
  };
};

const fetchTableRows = async (tableName: string): Promise<Record<string, unknown>[]> => {
  const resp = await axiosInstance.get("/data/table", {
    params: { table_name: tableName },
  });
  return resp.data?.status === "success" ? resp.data.tool_response || [] : [];
};

export type IbcSheetDashboardData = {
  tab1: Array<Record<string, unknown>>;
  tab2: Array<Record<string, unknown>>;
  tab3: Array<Record<string, unknown>>;
};

export const hasIbcSheetTables = (uploadData: any): boolean => {
  const resolved = resolveIbcDashboardTableNames(uploadData);
  return Boolean(resolved.nullsTable || resolved.defaultsTable || resolved.foreignKeysTable);
};

const mapIbcRowsToDashboard = (
  nullsRows: Record<string, unknown>[],
  defaultsRows: Record<string, unknown>[],
  foreignKeyRows: Record<string, unknown>[],
): IbcSheetDashboardData => ({
  tab1: nullsRows.map((row) => ({
    fileName: pickRowValue(row, "fileName", "file_name", "FileName"),
    fieldName: pickRowValue(row, "fieldName", "field_name", "FieldName"),
    null: pickRowValue(row, "null", "Null"),
    notNull: pickRowValue(row, "notNull", "not_null", "NotNull"),
    maxLength: pickRowValue(row, "maxLength", "max_length", "MaxLength"),
  })),
  tab2: defaultsRows.map((row) => ({
    fileName: pickRowValue(row, "fileName", "file_name", "FileName"),
    fieldName: pickRowValue(row, "fieldName", "field_name", "FieldName"),
    defaultValue:
      pickRowValue(row, "defaultValue", "default_value", "DefaultValue") || "N/A",
    defaultPct: pickRowValue(row, "defaultPct", "default_pct", "DefaultPct"),
  })),
  tab3: foreignKeyRows.map((row) => ({
    fileName: pickRowValue(row, "fileName", "file_name", "FileName"),
    fieldName: pickRowValue(row, "fieldName", "field_name", "FieldName"),
    dataType: pickRowValue(row, "dataType", "data_type", "DataType"),
    length: pickRowValue(row, "length", "Length"),
    primaryKey: pickRowValue(row, "primaryKey", "primary_key", "PrimaryKey"),
    foreignKey:
      pickRowValue(row, "foreignKey", "foreign_key", "ForeignKey") || "Yes",
    reference: pickRowValue(row, "reference", "Reference"),
  })),
});

export const loadIbcSheetDashboard = async (
  uploadData: any,
): Promise<IbcSheetDashboardData | null> => {
  const { nullsTable, defaultsTable, foreignKeysTable } =
    resolveIbcDashboardTableNames(uploadData);

  if (!nullsTable && !defaultsTable && !foreignKeysTable) {
    return null;
  }

  const [nullsRows, defaultsRows, foreignKeyRows] = await Promise.all([
    nullsTable ? fetchTableRows(nullsTable) : Promise.resolve([]),
    defaultsTable ? fetchTableRows(defaultsTable) : Promise.resolve([]),
    foreignKeysTable ? fetchTableRows(foreignKeysTable) : Promise.resolve([]),
  ]);

  if (!nullsRows.length && !defaultsRows.length && !foreignKeyRows.length) {
    return null;
  }

  return mapIbcRowsToDashboard(nullsRows, defaultsRows, foreignKeyRows);
};

const safeRoundPercent = (value: unknown): string => {
  if (value === null || value === undefined || value === "") return "0%";
  const num = Number(value);
  return Number.isNaN(num) ? String(value) : `${Math.round(num)}%`;
};

const extractProfilingTables = (payload: unknown): Record<string, unknown>[] => {
  if (!payload) return [];

  let parsed: unknown = payload;
  if (typeof parsed === "string") {
    try {
      parsed = JSON.parse(parsed);
    } catch {
      return [];
    }
  }

  if (Array.isArray(parsed)) {
    if (parsed.length > 0 && typeof parsed[0] === "object" && parsed[0] !== null) {
      const first = parsed[0] as Record<string, unknown>;
      if (first.tool_response) {
        return extractProfilingTables(first.tool_response);
      }
      if (first.column_analysis || first.table_reference || first.table_name) {
        return parsed as Record<string, unknown>[];
      }
    }
    return [];
  }

  if (typeof parsed !== "object" || parsed === null) return [];
  const obj = parsed as Record<string, unknown>;

  const nestedSources = [
    obj.all_tables,
    obj.result,
    obj.tables,
    (obj.intelligent_profiling_tool_response as Record<string, unknown> | undefined)
      ?.all_tables,
    (obj.intelligent_profiling_tool_response as Record<string, unknown> | undefined)
      ?.result,
    (obj.tool_response as Record<string, unknown> | undefined)?.all_tables,
    (obj.tool_response as Record<string, unknown> | undefined)?.result,
  ];

  for (const source of nestedSources) {
    if (Array.isArray(source) && source.length > 0) {
      return source as Record<string, unknown>[];
    }
  }

  if (obj.column_analysis || obj.table_reference || obj.table_name) {
    return [obj];
  }

  return [];
};

export const buildProfilingDashboardFromToolResponse = (
  payload: unknown,
): IbcSheetDashboardData | null => {
  const tables = extractProfilingTables(payload);
  if (!tables.length) return null;

  const tab1: Array<Record<string, unknown>> = [];
  const tab2: Array<Record<string, unknown>> = [];

  tables.forEach((table, index) => {
    const tableRef =
      (table.table_reference as string) ||
      (table.table_name as string) ||
      (table.name as string) ||
      `Table_${index + 1}`;
    const fileName = tableRef.includes(".") ? tableRef.split(".").pop() || tableRef : tableRef;
    const columnData =
      (table.column_analysis as Record<string, Record<string, unknown>>) ||
      (table.columns as Record<string, Record<string, unknown>>) ||
      (table.column_stats as Record<string, Record<string, unknown>>);

    if (!columnData || typeof columnData !== "object") return;

    Object.entries(columnData).forEach(([columnName, col]) => {
      let nullPercentage = 0;
      if (col.null_percentage !== undefined) nullPercentage = Number(col.null_percentage);
      else if (col.null_pct !== undefined) nullPercentage = Number(col.null_pct);
      else if (col.nulls_pct !== undefined) nullPercentage = Number(col.nulls_pct);
      else if (col.null_count !== undefined && col.total_count !== undefined) {
        nullPercentage = (Number(col.null_count) / Number(col.total_count)) * 100;
      }

      let maxLength = 0;
      if (col.max_length !== undefined) maxLength = Number(col.max_length);
      else if (col.avg_length !== undefined) maxLength = Number(col.avg_length);
      else if (col.average_length !== undefined) maxLength = Number(col.average_length);
      else if (col.length !== undefined) maxLength = Number(col.length);

      tab1.push({
        fileName,
        fieldName: columnName,
        null: safeRoundPercent(nullPercentage),
        notNull: safeRoundPercent(100 - nullPercentage),
        maxLength: Number.isNaN(maxLength) ? 0 : Math.ceil(maxLength) || 0,
      });

      const defaultAnalysis =
        (table.default_value_analysis as Record<string, Record<string, unknown>> | undefined)?.[
          columnName
        ] || col.default_analysis;
      const defaultValue =
        (defaultAnalysis as Record<string, unknown> | undefined)?.default_value ??
        col.default_value ??
        "N/A";
      const defaultPct =
        (defaultAnalysis as Record<string, unknown> | undefined)?.default_pct ??
        col.default_pct ??
        0;

      tab2.push({
        fileName,
        fieldName: columnName,
        defaultValue: defaultValue === null ? "NULL" : defaultValue,
        defaultPct: safeRoundPercent(defaultPct),
      });
    });
  });

  if (!tab1.length && !tab2.length) return null;
  return { tab1, tab2, tab3: [] };
};

export const buildForeignKeysFromRelationship = (relationshipData: unknown): Array<Record<string, unknown>> => {
  if (!relationshipData) return [];

  let parsed: unknown = relationshipData;
  if (typeof parsed === "string") {
    try {
      parsed = JSON.parse(parsed);
    } catch {
      return [];
    }
  }

  const toolResponse =
    (parsed as { tool_response?: Record<string, unknown> })?.tool_response ||
    (Array.isArray(parsed) ? (parsed[0] as { tool_response?: Record<string, unknown> })?.tool_response : undefined) ||
    (parsed as Record<string, unknown>);

  const relationships =
    (toolResponse?.cross_table_relationships as unknown[]) ||
    (toolResponse?.relationships as unknown[]) ||
    (toolResponse?.foreign_keys as unknown[]);

  if (!Array.isArray(relationships)) return [];

  return relationships.flatMap((rel) => {
    if (!rel || typeof rel !== "object") return [];
    const row = rel as Record<string, unknown>;
    const sourceTable = String(
      row.source_table || row.sourceTable || row.table_name || row.table_reference || "",
    );
    const sourceColumn = String(
      row.source_column ||
        row.sourceColumn ||
        row.column_name ||
        row.field_name ||
        row.fieldName ||
        "",
    );
    const targetTable = String(row.target_table || row.targetTable || row.reference_table || "");
    const targetColumn = String(
      row.target_column || row.targetColumn || row.reference_column || row.reference || "",
    );
    if (!sourceColumn) return [];

    const fileName = sourceTable.includes(".")
      ? sourceTable.split(".").pop() || sourceTable
      : sourceTable || "Unknown";
    const reference =
      targetTable && targetColumn
        ? `${targetTable}.${targetColumn}`
        : targetColumn || targetTable || String(row.reference || "");

    return [
      {
        fileName,
        fieldName: sourceColumn,
        dataType: String(row.data_type || row.dataType || ""),
        length: String(row.length || row.max_length || ""),
        primaryKey: String(row.primary_key || row.primaryKey || ""),
        foreignKey: "Yes",
        reference,
      },
    ];
  });
};

export type DetailedProfilingLoadOptions = {
  uploadData?: unknown;
  profilingMessageData?: unknown;
  relationshipData?: unknown;
  session?: {
    sessionId?: string | null;
    userId?: string | null;
    appName?: string | null;
  };
};

export const loadDetailedProfilingDashboard = async (
  uploadDataOrOptions?: unknown,
  profilingMessageData?: unknown,
): Promise<IbcSheetDashboardData | null> => {
  const options: DetailedProfilingLoadOptions =
    uploadDataOrOptions &&
    typeof uploadDataOrOptions === "object" &&
    ("profilingMessageData" in (uploadDataOrOptions as object) ||
      "relationshipData" in (uploadDataOrOptions as object) ||
      "session" in (uploadDataOrOptions as object))
      ? (uploadDataOrOptions as DetailedProfilingLoadOptions)
      : {
          uploadData: uploadDataOrOptions,
          profilingMessageData,
        };

  const { uploadData, relationshipData, session } = options;
  let { profilingMessageData: profilingPayload } = options;

  if (uploadData) {
    try {
      const ibcDashboard = await loadIbcSheetDashboard(uploadData);
      if (ibcDashboard?.tab1?.length || ibcDashboard?.tab2?.length || ibcDashboard?.tab3?.length) {
        if (!ibcDashboard.tab3.length && relationshipData) {
          ibcDashboard.tab3 = buildForeignKeysFromRelationship(relationshipData);
        }
        return ibcDashboard;
      }
    } catch (error) {
      console.error("Failed to load IBC dashboard tables:", error);
    }
  }

  let dashboard = buildProfilingDashboardFromToolResponse(profilingPayload);
  if (!dashboard && session?.sessionId && session.userId && session.appName) {
    const fetched = await fetchProfilingDashboardPayload({
      sessionId: session.sessionId,
      userId: session.userId,
      appName: session.appName,
    });
    if (fetched) {
      profilingPayload = fetched;
      dashboard = buildProfilingDashboardFromToolResponse(fetched);
    }
  }

  if (!dashboard) return null;

  const relationshipFkRows = buildForeignKeysFromRelationship(relationshipData);
  if (relationshipFkRows.length) {
    dashboard = {
      ...dashboard,
      tab3: relationshipFkRows,
    };
  }

  return dashboard;
};
