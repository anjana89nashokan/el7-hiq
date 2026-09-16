import axiosInstance from './axios-interceptor';

export type DataDictionaryRow = Record<string, unknown>;

const TABLE_ID_KEYS = [
  'data_dictionary_table_id',
  'data_dictionary_table_name',
  'datadict_table_id',
  'table_name',
] as const;

function parseTableIdFromMessage(message: unknown): string | undefined {
  if (typeof message !== 'string' || !message.trim()) return undefined;

  const backtickMatch = message.match(/`([^`]+\.datadict_[^`]+)`/i);
  if (backtickMatch?.[1]) return backtickMatch[1].trim();

  const fullPathMatch = message.match(
    /([a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.datadict_[a-f0-9-]+)/i
  );
  if (fullPathMatch?.[1]) return fullPathMatch[1].trim();

  const shortMatch = message.match(/\b(datadict_[a-f0-9-]+)\b/i);
  if (shortMatch?.[1]) return shortMatch[1].trim();

  return undefined;
}

function unwrapRecord(value: unknown): Record<string, unknown> | null {
  if (!value) return null;
  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value);
      return typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)
        ? (parsed as Record<string, unknown>)
        : null;
    } catch {
      return null;
    }
  }
  if (typeof value === 'object' && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return null;
}

export function getInlineDataDictionaryRows(source: unknown): DataDictionaryRow[] {
  const payload = unwrapRecord(source);
  if (!payload) {
    if (Array.isArray(source) && source.length > 0 && typeof source[0] === 'object') {
      return source as DataDictionaryRow[];
    }
    return [];
  }

  const toolResponse = unwrapRecord(payload.tool_response) ?? payload;

  const candidates = [
    toolResponse.result,
    toolResponse.data_dictionary,
    toolResponse.fields,
    toolResponse.rows,
    payload.result,
  ];

  for (const candidate of candidates) {
    if (Array.isArray(candidate) && candidate.length > 0) {
      return candidate as DataDictionaryRow[];
    }
  }

  return [];
}

export function resolveDataDictionaryTableId(source: unknown): string | undefined {
  const payload = unwrapRecord(source);
  if (!payload) return undefined;

  const toolResponse = unwrapRecord(payload.tool_response) ?? payload;

  for (const key of TABLE_ID_KEYS) {
    const value = toolResponse[key] ?? payload[key];
    if (typeof value === 'string' && value.trim()) {
      return value.trim();
    }
  }

  return (
    parseTableIdFromMessage(toolResponse.message) ||
    parseTableIdFromMessage(payload.message) ||
    parseTableIdFromMessage(payload.text_response)
  );
}

const METADATA_TABLE_ID_PATTERN =
  /([a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.metadata_template_[a-f0-9-]+)/i;
const FILESPECS_TABLE_ID_PATTERN =
  /([a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.Filespecs_[a-f0-9-]+)/i;

export function parseMetadataTableIds(source: unknown): {
  metadataTableId?: string;
  filespecsTableId?: string;
} {
  const payload = unwrapRecord(source);
  if (!payload) return {};

  const metadataTableId =
    (typeof payload.metadata_table_id === 'string' && payload.metadata_table_id.trim()) ||
    (typeof payload.metadataTableId === 'string' && payload.metadataTableId.trim()) ||
    undefined;
  const filespecsTableId =
    (typeof payload.filespecs_table_id === 'string' && payload.filespecs_table_id.trim()) ||
    (typeof payload.filespecsTableId === 'string' && payload.filespecsTableId.trim()) ||
    undefined;

  if (metadataTableId || filespecsTableId) {
    return { metadataTableId, filespecsTableId };
  }

  const textCandidates = [
    payload.text_response,
    payload.message,
    typeof source === 'string' ? source : undefined,
  ];

  let resolvedMetadata: string | undefined;
  let resolvedFilespecs: string | undefined;

  for (const text of textCandidates) {
    if (typeof text !== 'string') continue;
    resolvedMetadata =
      resolvedMetadata || text.match(METADATA_TABLE_ID_PATTERN)?.[1]?.trim();
    resolvedFilespecs =
      resolvedFilespecs || text.match(FILESPECS_TABLE_ID_PATTERN)?.[1]?.trim();
  }

  return {
    metadataTableId: resolvedMetadata,
    filespecsTableId: resolvedFilespecs,
  };
}

export async function fetchDataDictionaryRows(tableId: string): Promise<{
  rows: DataDictionaryRow[];
  textResponse?: string;
}> {
  const tableResp = await axiosInstance.get('/data/table', {
    params: { table_name: tableId },
  });

  if (tableResp.data?.status !== 'success') {
    return { rows: [] };
  }

  const rows = Array.isArray(tableResp.data.tool_response)
    ? tableResp.data.tool_response
    : [];

  return {
    rows,
    textResponse:
      typeof tableResp.data.text_response === 'string'
        ? tableResp.data.text_response
        : undefined,
  };
}

export function formatMostOccurrences(val: unknown): string {
  if (Array.isArray(val)) return val.join(', ');
  if (!val) return '';
  return String(val).replace(/^\[|\]$/g, '').replace(/"/g, '').replace(/'/g, '');
}

export function normalizeDataDictionaryRows(rows: DataDictionaryRow[]): DataDictionaryRow[] {
  return rows.map((row) => {
    const mostOcc = formatMostOccurrences(
      row.most_occurrences ?? row['Most Occurrences']
    );
    return {
      ...row,
      most_occurrences: mostOcc,
      'Most Occurrences': mostOcc,
    };
  });
}

export function buildDataDictionaryMarkdownTable(tableData: DataDictionaryRow[]): string {
  if (!tableData?.length) return '';

  const headers = [
    'File Name',
    'Field Name',
    'Field Business Name',
    'Data Type',
    'Length',
    'Format',
    'Nullable',
    'Most Occurrences',
    'Primary Key',
    'Foreign Key',
    'Field Description',
  ];
  const headerRow = `| ${headers.join(' | ')} |`;
  const separatorRow = `| ${headers.map(() => '---').join(' | ')} |`;
  const dataRows = tableData
    .map((item) => {
      const mostOccStr = formatMostOccurrences(
        item.most_occurrences ?? item['Most Occurrences']
      );
      return `| ${[
        item.file_name ?? item['File Name'],
        item.field_name ?? item['Field Name'] ?? item['Attribute Name'],
        item.business_name ?? item['Field Business Name'] ?? item['Logical Attribute Name'],
        item.data_type ?? item['Data Type'],
        item.length ?? item['Length'] ?? 0,
        item.format ?? item['Format'],
        item.nullable ?? item['Nullable'] ?? item['Nullability'],
        mostOccStr,
        item.primary_key ?? item['Primary Key'],
        item.foreign_key ?? item['Foreign Key'],
        item.field_description ??
          item['Field Description'] ??
          item['Attribute Description'],
      ]
        .map((val) => val ?? '')
        .join(' | ')} |`;
    })
    .join('\n');

  return `# Data Dictionary Generation Complete\n\n## Data Dictionary\n\n${headerRow}\n${separatorRow}\n${dataRows}`;
}

export async function resolveDataDictionaryDisplay(source: unknown): Promise<{
  rows: DataDictionaryRow[];
  tableId?: string;
  responseText: string;
  validationAuditLog: DataDictionaryRow[];
  isDdPresent: boolean;
}> {
  const payload = unwrapRecord(source) ?? {};

  const toolResponse = unwrapRecord(payload.tool_response) ?? payload;

  let rows = getInlineDataDictionaryRows(source);
  const tableId = resolveDataDictionaryTableId(source);
  let fetchedTextResponse: string | undefined;

  if (rows.length === 0 && tableId) {
    try {
      const fetched = await fetchDataDictionaryRows(tableId);
      rows = fetched.rows;
      fetchedTextResponse = fetched.textResponse;
    } catch (error) {
      console.error('Error fetching data dictionary from BigQuery:', error);
    }
  }

  rows = normalizeDataDictionaryRows(rows);

  const validationAuditLog = Array.isArray(toolResponse.validation_audit_log)
    ? (toolResponse.validation_audit_log as DataDictionaryRow[])
    : [];

  const markdownFromBackend =
    (typeof payload.text_response === 'string' && payload.text_response.includes('|')
      ? payload.text_response
      : undefined) ||
    (typeof toolResponse.text_response === 'string' && toolResponse.text_response.includes('|')
      ? toolResponse.text_response
      : undefined) ||
    fetchedTextResponse;

  const responseText =
    rows.length > 0
      ? buildDataDictionaryMarkdownTable(rows)
      : markdownFromBackend ||
        (typeof toolResponse.message === 'string' ? toolResponse.message : undefined) ||
        (typeof payload.text_response === 'string'
          ? payload.text_response
          : 'Data dictionary generated successfully.');

  const isDdPresent =
    typeof toolResponse.is_dd_present === 'boolean'
      ? toolResponse.is_dd_present
      : rows.length > 0 || Boolean(tableId);

  return {
    rows,
    tableId,
    responseText,
    validationAuditLog,
    isDdPresent,
  };
}
