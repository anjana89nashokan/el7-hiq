import { AlertTriangle, XCircle } from "lucide-react";
import TableProfilingDisplay from "../components/TableProfilingDisplay";
import EmptyState from "../components/EmptyState";

interface RenderStep7ContentProps {
  initialMessageData: any[];
  dataDictionaryJson: any;
  dataDictionaryState?: any;
  anomalyData: any;
  similarityResponse: string;
  relationshipResponse?: any;
  profilingSession?: {
    sessionId?: string | null;
    userId?: string | null;
    appName?: string | null;
  };
  skippedSteps: Set<number>;
  setCurrentStep: (step: number) => void;
  exportRef?: React.RefObject<(() => void) | null>;
  uploadData?: any;
}

export const normalizeTableKey = (name: unknown): string => {
  if (typeof name !== "string" || !name.trim()) return "";
  const trimmed = name.trim();
  const parts = trimmed.split(".");
  return parts[parts.length - 1] || trimmed;
};

export const tableNamesMatch = (left: unknown, right: unknown): boolean => {
  const a = typeof left === "string" ? left.trim() : "";
  const b = typeof right === "string" ? right.trim() : "";
  if (!a || !b) return false;
  if (a === b) return true;
  return normalizeTableKey(a) === normalizeTableKey(b);
};

export const resolveDataDictionaryFieldName = (row: any): string =>
  row?.field_name ||
  row?.["Attribute Name"] ||
  row?.["Field Name"] ||
  row?.attribute_name ||
  "";

export const resolveDataDictionaryTableName = (row: any): string =>
  row?.file_name || row?.["File Name"] || row?.fileName || "";

export const normalizeDataDictionaryRow = (row: any) => {
  if (!row || typeof row !== "object" || Array.isArray(row)) return null;

  const fileName = resolveDataDictionaryTableName(row);
  const fieldName = resolveDataDictionaryFieldName(row);
  if (!fileName || !fieldName) return null;

  const description =
    row.field_description ||
    row["Attribute Description"] ||
    row["Field Description"] ||
    row.description ||
    "";

  return {
    ...row,
    file_name: fileName,
    "File Name": fileName,
    field_name: fieldName,
    "Attribute Name": fieldName,
    field_description: description,
    "Attribute Description": description,
  };
};

export const normalizeDataDictionaryRows = (rows: any[]): any[] =>
  rows
    .map((row) => normalizeDataDictionaryRow(row))
    .filter((row): row is Record<string, unknown> => Boolean(row));

export const extractDataDictionaryRows = (raw: unknown): any[] => {
  let rows: any[] = [];
  if (!raw) return rows;
  if (Array.isArray(raw)) {
    if (raw.length === 0) return rows;
    if (Array.isArray(raw[0])) {
      rows = raw[0].filter((row) => row && typeof row === "object");
    } else if (typeof raw[0] === "object") {
      rows = raw.filter((row) => row && typeof row === "object");
    }
  } else if (typeof raw === "object") {
    const obj = raw as Record<string, unknown>;
    if (Array.isArray(obj.result) && obj.result.length > 0) {
      rows = obj.result as any[];
    } else if (Array.isArray(obj.tool_response)) {
      rows = extractDataDictionaryRows(obj.tool_response);
    } else {
      const nested = obj.tool_response as Record<string, unknown> | undefined;
      if (nested && Array.isArray(nested.result)) {
        rows = nested.result as any[];
      }
    }
  }
  return normalizeDataDictionaryRows(rows);
};

export const buildStep3ReduxPayload = (
  dataDictionaryJson: unknown,
  dataDictionaryState?: { resultData?: unknown; json?: unknown },
) => {
  const candidate =
    dataDictionaryJson ??
    dataDictionaryState?.json ??
    dataDictionaryState?.resultData;
  const rows = extractDataDictionaryRows(candidate);
  if (!rows.length) return null;
  return { tool_response: rows };
};

export const resolveDataDictionaryPayload = (
  dataDictionaryJson: any,
  dataDictionaryState?: any,
) => {
  const rows = extractDataDictionaryRows(
    dataDictionaryJson ?? dataDictionaryState?.json ?? dataDictionaryState?.resultData,
  );
  if (!rows.length) {
    return null;
  }
  return [rows];
};

export const renderStep7Content = ({
  initialMessageData,
  dataDictionaryJson,
  dataDictionaryState,
  anomalyData,
  similarityResponse,
  relationshipResponse,
  profilingSession,
  skippedSteps,
  setCurrentStep,
  exportRef,
  uploadData,
}: RenderStep7ContentProps) => {
  const resolvedDataDictionary = resolveDataDictionaryPayload(dataDictionaryJson, dataDictionaryState);

  if (initialMessageData.length > 0 && resolvedDataDictionary) {
    return (
      <TableProfilingDisplay
        profilingData={initialMessageData}
        uploadData={uploadData}
        dataDictionary={resolvedDataDictionary}
        anomalyData={anomalyData}
        similarityData={similarityResponse}
        relationshipData={relationshipResponse}
        profilingSession={profilingSession}
        isStep4Skipped={skippedSteps.has(4)}
        exportRef={exportRef}
      />
    );
  }
  
  if (initialMessageData.length === 0) {
    return (
      <EmptyState
        icon={XCircle}
        title="Missing Profiling Data"
        description="Cannot display detailed profiling without initial data."
        iconColor="text-red-500"
        bgColor="bg-red-50"
        borderColor="border-red-200"
        titleColor="text-red-700"
        descColor="text-red-600"
        action={
          <button
            onClick={() => setCurrentStep(1)}
            className="bg-red-600 hover:bg-red-700 text-white px-4 py-2 rounded-lg transition-colors"
          >
            Return to Dataset Overview
          </button>
        }
      />
    );
  }
  
  return (
    <EmptyState
      icon={AlertTriangle}
      title="Data Dictionary Required"
      description="Please complete the Data Dictionary step first."
      iconColor="text-yellow-500"
      bgColor="bg-yellow-50"
      borderColor="border-yellow-200"
      titleColor="text-yellow-700"
      descColor="text-yellow-600"
      action={
        <button
          onClick={() => setCurrentStep(3)}
          className="bg-yellow-600 hover:bg-yellow-700 text-white px-4 py-2 rounded-lg transition-colors"
        >
          Go to Data Dictionary
        </button>
      }
    />
  );
};
