import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Loader2, FileSearch, AlertCircle, Download } from 'lucide-react';
import axiosInstance from '../utils/axios-interceptor';
import {
  parseMetadataTableIds,
  resolveDataDictionaryTableId,
} from '../utils/dataDictionaryUtils';
import { instructionsData } from "../config/instructionsConfig";
import { valuesData } from "../config/valuesConfig";
import * as XLSX from "xlsx";
/* -------------------- Types -------------------- */

interface FileSpecMapping {
  Field: string;
  Value: string | null;
}

interface RowMetadataItem {
  File_Name: string;
  Attribute_Name: string;
  Logical_Attribute_Name: string;
  Attribute_Description: string;
  Data_Type: string;
  Length: string;
  Precision: string;
  Format: string;
  Nullability: string;
  Most_Occurrences: string;
  Primary_Key: string;
  Foreign_Key: string;
  Alternate_Key1: string;
}

interface ToolResponse {
  file_specs_mapping?: FileSpecMapping[];
  row_level_metadata?: RowMetadataItem[];
  notes?: string;
}

interface ApiResponseItem {
  text_response?: string;
  tool_response?: ToolResponse;
  message?: string;
  metadata_table_id?: string;
  filespecs_table_id?: string;
}

interface MetadataTemplateProps {
  profilingData: unknown;
  setMetadataTemplateResponse: React.Dispatch<React.SetStateAction<string>>;
  metadataResponse: unknown;
  dataDictionaryTableId?: string;
  dataDictionaryJson?: unknown;
  onLoadingChange?: (isLoading: boolean) => void;
  hasApiBeenCalled?: boolean;
  markApiCalled?: () => void;
  modifiedResponse?: any;
  exportRef?: React.MutableRefObject<(() => void) | null>;
}

/* -------------------- Constants -------------------- */

const TEMPLATE_MESSAGE = 'Generate a comprehensive [Metadata Template]\n Template path: templates/Metadata_template.xlsx';

const FIELD_MAPPING = {
  'vendor name': 'vendorName',
  'frequency mode': 'deliveryFrequency',
  'vendor email': 'contactPerson',
  'delivery frequency': 'deliveryFrequency'
} as const;

const FILE_SPEC_PROJECT_FIELD_MAP: Record<string, string> = {
  'Vendor Name': 'vendorName',
  'Transfer Method': 'transferMethod',
  'Vendor Contact Name': 'contactName',
  'Frequency Mode': 'frequencyMode',
  'Vendor Phone Number': 'phoneNumber',
  'Dependencies': 'dependencies',
  'Vendor Email': 'contactPerson',
  'Email Notification DL': 'emailNotificationDl',
  'Date Timestamp Format': 'dateTimestampFormat',
  'Header Record Number': 'headerRecordNumber',
  'Trailer Record Number': 'trailerRecordNumber',
  'Quote Indicator': 'quoteIndicator',
  'File Population Type': 'populationType',
  'File Compression Type': 'compressionType',
  'Receive File when no Data (Empty Files)': 'receiveFileWhenNoData',
  'Assumptions': 'assumptions',
  'Vendor Server Name': 'serverName',
};

const normalizeMetadataRow = (row: Record<string, unknown>): RowMetadataItem => ({
  File_Name: String(row.File_Name ?? row.file_name ?? row['File Name'] ?? ''),
  Attribute_Name: String(row.Attribute_Name ?? row.attribute_name ?? row.field_name ?? row['Attribute Name'] ?? ''),
  Logical_Attribute_Name: String(
    row.Logical_Attribute_Name ?? row.logical_attribute_name ?? row.business_name ?? row['Logical Attribute Name'] ?? ''
  ),
  Attribute_Description: String(
    row.Attribute_Description ?? row.attribute_description ?? row.field_description ?? row['Attribute Description'] ?? ''
  ),
  Data_Type: String(row.Data_Type ?? row.data_type ?? row['Data Type'] ?? ''),
  Length: String(row.Length ?? row.length ?? row['Length'] ?? ''),
  Precision: String(row.Precision ?? row.precision ?? row['Precision'] ?? ''),
  Format: String(row.Format ?? row.format ?? row['Format'] ?? ''),
  Nullability: String(row.Nullability ?? row.nullability ?? row.nullable ?? row['Nullability'] ?? ''),
  Most_Occurrences: String(
    row.Most_Occurrences ?? row.most_occurrences ?? row['Most Occurrences'] ?? ''
  ),
  Primary_Key: String(row.Primary_Key ?? row.primary_key ?? row['Primary Key'] ?? ''),
  Foreign_Key: String(row.Foreign_Key ?? row.foreign_key ?? row['Foreign Key'] ?? ''),
  Alternate_Key1: String(row.Alternate_Key1 ?? row.alternate_key1 ?? row['Alternate Key1'] ?? ''),
});

const mapDatadictRowsToMetadata = (rows: Record<string, unknown>[]): RowMetadataItem[] =>
  rows.map((row) =>
    normalizeMetadataRow({
      File_Name: row['File Name'] ?? row.file_name,
      Attribute_Name: row['Attribute Name'] ?? row.field_name,
      Logical_Attribute_Name: row['Logical Attribute Name'] ?? row.business_name,
      Attribute_Description: row['Attribute Description'] ?? row.field_description,
      Data_Type: row['Data Type'] ?? row.data_type,
      Length: row['Length'] ?? row.length,
      Precision: row['Precision'] ?? row.precision,
      Format: row['Format'] ?? row.format,
      Nullability: row['Nullability'] ?? row.nullable,
      Most_Occurrences: row['Most Occurrences'] ?? row.most_occurrences,
      Primary_Key: row['Primary Key'] ?? row.primary_key,
      Foreign_Key: row['Foreign Key'] ?? row.foreign_key,
      Alternate_Key1: row['Alternate Key1'] ?? row.alternate_key1,
    })
  );

/* const EXCEL_HEADERS = [
  "Attribute Name", "Logical Attribute Name", "Attribute Description",
  "Data Type", "Length", "Precision", "Format", "Nullability",
  "Default Value", "Primary Key", "Foreign Key", "Alternate Key1"
] as const; */

/* -------------------- Helpers -------------------- */

const getStoredSession = () => ({
  sessionId: sessionStorage.getItem("session_id") ?? '',
  appName: sessionStorage.getItem("app_name") ?? '',
  userId: sessionStorage.getItem("user_id") ?? '',
});

const getProjectDetails = (): Record<string, string> => {
  const projectDetails = sessionStorage.getItem("project_details");
  if (!projectDetails) return {};

  try {
    const parsed = JSON.parse(projectDetails);
    return typeof parsed === 'object' && parsed !== null ? parsed : {};
  } catch {
    return {};
  }
};

const getProjectDetailValue = (field: string): string => {
  return getProjectDetails()[field] ?? '';
};

const getUploadFileNames = (profilingData: unknown): string => {
  const data = profilingData as {
    successful_uploads?: Array<{ filename?: string; table_name?: string }>;
  };
  const names = (data?.successful_uploads ?? [])
    .map((item) => item.filename || item.table_name?.split('.').pop() || '')
    .filter(Boolean);
  return names.join(', ');
};

const MetadataTemplate: React.FC<MetadataTemplateProps> = ({
  profilingData,
  setMetadataTemplateResponse,
  metadataResponse,
  dataDictionaryTableId,
  dataDictionaryJson,
  onLoadingChange,
  hasApiBeenCalled,
  markApiCalled,
  modifiedResponse,
  exportRef,
}) => {

  const [isLoading, setIsLoading] = useState(false);
  const [response, setResponse] = useState<ApiResponseItem[] | null>(null);
  const [error, setError] = useState<string>('');
  const [isScrolled, setIsScrolled] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const hasSentRef = useRef(false);
  const loadingStartedAtRef = useRef<number | null>(null);

  /* -------------------- Process Tool Response -------------------- */

  const fetchTableData = useCallback(async (tableId: string) => {
    try {
      const resp = await axiosInstance.get('/data/table', {
        params: { table_name: tableId }
      });
      return resp.data?.status === 'success' ? resp.data.tool_response : [];
    } catch (err) {
      return [];
    }
  }, []);

  const processToolResponse = useCallback(async (responseItem: any) => {
    const parsedIds = parseMetadataTableIds(responseItem);
    const metadataTableId =
      responseItem?.metadata_table_id || parsedIds.metadataTableId;
    const filespecsTableId =
      responseItem?.filespecs_table_id || parsedIds.filespecsTableId;

    const finalResponse: ToolResponse = {
      file_specs_mapping: [],
      row_level_metadata: []
    };

    const effectiveDictionaryTableId =
      dataDictionaryTableId ||
      resolveDataDictionaryTableId(dataDictionaryJson) ||
      resolveDataDictionaryTableId(responseItem);

    const [metadataData, filespecsData] = await Promise.all([
      metadataTableId ? fetchTableData(metadataTableId) : Promise.resolve([]),
      filespecsTableId ? fetchTableData(filespecsTableId) : Promise.resolve([]),
    ]);

    let rowLevelMetadata = Array.isArray(metadataData)
      ? metadataData.map((row: Record<string, unknown>) => normalizeMetadataRow(row))
      : [];

    if (rowLevelMetadata.length === 0 && effectiveDictionaryTableId) {
      const datadictRows = await fetchTableData(effectiveDictionaryTableId);
      if (Array.isArray(datadictRows) && datadictRows.length > 0) {
        rowLevelMetadata = mapDatadictRowsToMetadata(datadictRows);
      }
    }

    finalResponse.row_level_metadata = rowLevelMetadata;
    finalResponse.file_specs_mapping = Array.isArray(filespecsData) ? filespecsData : [];

    const responseData = [{ tool_response: finalResponse }];
    setResponse(responseData);
    setMetadataTemplateResponse(JSON.stringify(responseData));
  }, [dataDictionaryJson, dataDictionaryTableId, fetchTableData, setMetadataTemplateResponse]);

  /* -------------------- API Call -------------------- */

  const generateMetadataTemplate = useCallback(async (message: string) => {
    setIsLoading(true);
    onLoadingChange?.(true);
    setError('');
    loadingStartedAtRef.current = Date.now();
    setElapsedSeconds(0);

    const session = getStoredSession();
    const effectiveDictionaryTableId =
      dataDictionaryTableId || resolveDataDictionaryTableId(dataDictionaryJson);

    try {
      const apiResponse = await axiosInstance.post(
        '/messages/metadata_fill',
        {
          appName: session.appName,
          sessionId: session.sessionId,
          userId: session.userId,
          newMessage: {
            parts: [{ text: message }],
            role: "user"
          },
          streaming: false,
          stateDelta: {},
          additional_data: effectiveDictionaryTableId
            ? { data_dictionary_table_id: effectiveDictionaryTableId }
            : undefined,
        },
        {
          headers: { 'Content-Type': 'application/json' },
          timeout: 600000,
        }
      );

      if (apiResponse.status !== 200) {
        throw new Error(`HTTP error! status: ${apiResponse.status}`);
      }

      const data = apiResponse.data;
      const payload = Array.isArray(data) ? data : [data];
      const itemWithTables = payload.find((item) => {
        const ids = parseMetadataTableIds(item);
        return (
          item?.metadata_table_id ||
          item?.filespecs_table_id ||
          ids.metadataTableId ||
          ids.filespecsTableId
        );
      });

      if (itemWithTables) {
        await processToolResponse(itemWithTables);
      } else if (payload[0]?.tool_response) {
        await processToolResponse(payload[0]);
      } else {
        setResponse(payload);
        setMetadataTemplateResponse(JSON.stringify(payload));
      }
    } catch (err) {
      const messageText =
        err instanceof Error ? err.message : 'Failed to generate the template.';
      if (messageText === 'Network Error') {
        setError(
          'The request timed out or lost connection while generating the metadata template. Click Retry — generation should complete within a minute when the Data Dictionary step is finished.'
        );
      } else {
        setError(messageText);
      }
    } finally {
      setIsLoading(false);
      onLoadingChange?.(false);
      loadingStartedAtRef.current = null;
    }
  }, [dataDictionaryJson, dataDictionaryTableId, onLoadingChange, processToolResponse, setMetadataTemplateResponse]);

  /* -------------------- Initialize with existing data -------------------- */

  useEffect(() => {
    if (!metadataResponse || response) return;

    try {
      const parsed =
        typeof metadataResponse === 'string'
          ? JSON.parse(metadataResponse)
          : metadataResponse;
      const items = Array.isArray(parsed) ? parsed : [parsed];
      const itemWithTables = items.find((item) => {
        const ids = parseMetadataTableIds(item);
        return (
          item?.tool_response?.row_level_metadata?.length ||
          item?.tool_response?.file_specs_mapping?.length ||
          item?.metadata_table_id ||
          item?.filespecs_table_id ||
          ids.metadataTableId ||
          ids.filespecsTableId
        );
      });

      if (itemWithTables?.tool_response?.row_level_metadata?.length ||
          itemWithTables?.tool_response?.file_specs_mapping?.length) {
        setResponse(items as ApiResponseItem[]);
        hasSentRef.current = true;
        return;
      }

      if (itemWithTables) {
        hasSentRef.current = true;
        void processToolResponse(itemWithTables);
        return;
      }

      setResponse(items as ApiResponseItem[]);
      hasSentRef.current = true;
    } catch {
      // ignore invalid data
    }
  }, [metadataResponse, response, processToolResponse]);

  // Sync metadataResponse prop changes (from ChatModal "apply changes")
  useEffect(() => {
    if (metadataResponse && response) {
      try {
        const parsed = typeof metadataResponse === 'string' 
          ? JSON.parse(metadataResponse) 
          : metadataResponse;
        const currentStr = JSON.stringify(response);
        const newStr = JSON.stringify(parsed);
        if (currentStr !== newStr) {
          setResponse(parsed as ApiResponseItem[]);
        }
      } catch {
        // ignore invalid data
      }
    }
  }, [metadataResponse]);

  // Handle modified response from ChatModal
  useEffect(() => {
    if (modifiedResponse) {
      setResponse(modifiedResponse);
      setMetadataTemplateResponse(JSON.stringify(modifiedResponse));
    }
  }, [modifiedResponse, setMetadataTemplateResponse]);

  useEffect(() => {
    if (!isLoading) return undefined;

    const timer = window.setInterval(() => {
      if (loadingStartedAtRef.current) {
        setElapsedSeconds(
          Math.floor((Date.now() - loadingStartedAtRef.current) / 1000)
        );
      }
    }, 1000);

    return () => window.clearInterval(timer);
  }, [isLoading]);

  /* -------------------- Trigger Call -------------------- */

  useEffect(() => {
    if (profilingData && !isLoading && !hasSentRef.current && !response && !metadataResponse && !hasApiBeenCalled) {
      hasSentRef.current = true;
      markApiCalled?.();
      generateMetadataTemplate(TEMPLATE_MESSAGE);
    }
  }, [profilingData, isLoading, response, metadataResponse, generateMetadataTemplate, hasApiBeenCalled, markApiCalled]);

  /* -------------------- Parse Response If String -------------------- */

  useEffect(() => {
    if (typeof response === 'string') {
      try {
        setResponse(JSON.parse(response));
      } catch {
        // ignore invalid JSON
      }
    }
  }, [response]);
  
  const handleRetry = useCallback(() => {
    setError('');
    setResponse(null);
    setMetadataTemplateResponse('');
    hasSentRef.current = false;
    generateMetadataTemplate(TEMPLATE_MESSAGE);
  }, [generateMetadataTemplate, setMetadataTemplateResponse]);

  // Handle scroll for floating button
  useEffect(() => {
    const handleScroll = () => {
      setIsScrolled(window.scrollY > 100);
    };
    window.addEventListener('scroll', handleScroll);
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  /* -------------------- Excel Export -------------------- */

  const getActualValue = useCallback((item: FileSpecMapping) => {
    if (item.Value && item.Value !== null && String(item.Value).trim()) {
      return String(item.Value);
    }

    const fieldName = item.Field?.trim() || '';
    const projectField = FILE_SPEC_PROJECT_FIELD_MAP[fieldName];
    if (projectField) {
      const projectValue = getProjectDetailValue(projectField);
      if (projectValue) return projectValue;
    }

    if (fieldName === 'Physical File Name') {
      const uploadNames = getUploadFileNames(profilingData);
      if (uploadNames) return uploadNames;
    }

    if (fieldName === 'File Extension') {
      const uploads = (profilingData as { successful_uploads?: Array<{ filename?: string }> })
        ?.successful_uploads ?? [];
      const extensions = [...new Set(
        uploads
          .map((item) => item.filename?.match(/\.[^.]+$/)?.[0] || '')
          .filter(Boolean)
      )];
      if (extensions.length > 0) return extensions.join(', ');
    }

    if (fieldName === 'Frequency Mode') {
      const deliveryFrequency = getProjectDetailValue('deliveryFrequency');
      if (deliveryFrequency) return deliveryFrequency;
    }

    const mappedField = FIELD_MAPPING[fieldName.toLowerCase() as keyof typeof FIELD_MAPPING];
    return mappedField ? getProjectDetailValue(mappedField) : '';
  }, [profilingData]);

  const generateUniqueSheetName = useCallback((baseName: string, used: Set<string>) => {
    let name = baseName.substring(0, 31);
    let counter = 1;

    while (used.has(name)) {
      const suffix = `_${counter}`;
      name = baseName.substring(0, 31 - suffix.length) + suffix;
      counter++;
    }

    used.add(name);
    return name;
  }, []);

  const exportExcelFile = useCallback(() => {
    const currentResponse = modifiedResponse || response;
    if (!currentResponse?.length) return;

    const toolResponse = currentResponse[currentResponse.length - 1]?.tool_response;
    if (!toolResponse) return;

    const wb = XLSX.utils.book_new();

    // Add static sheets
    // ---------------- Instructions Sheet ----------------
const instructionRows = instructionsData.map(item => [item.Guideline]);

const instructionSheet = XLSX.utils.aoa_to_sheet(instructionRows);

// Merge first row across columns (A1:N1)
instructionSheet["!merges"] = [
  {
    s: { r: 0, c: 0 },
    e: { r: 0, c: 13 }
  }
];

// Set column width so text spreads like template
instructionSheet["!cols"] = [{ wch: 120 }];

XLSX.utils.book_append_sheet(wb, instructionSheet, "Instructions");

    XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(valuesData), "Values");

    // File Specs mapping
    if (toolResponse.file_specs_mapping?.length) {
      const rows = toolResponse.file_specs_mapping.map((item: FileSpecMapping) => ({
        "Template Field": item.Field || "",
        "Value": getActualValue(item) || ""
      }));

      const ws = XLSX.utils.json_to_sheet(rows);
      XLSX.utils.book_append_sheet(wb, ws, "FileSpecs");
    }

    // Row Level metadata
if (toolResponse.row_level_metadata?.length) {

  const fileNames = [...new Set(toolResponse.row_level_metadata.map((item: RowMetadataItem) => item.File_Name))];
  const used = new Set<string>();

  fileNames.forEach((fileName) => {

    const name = generateUniqueSheetName(String(fileName) || "Sheet", used);

    const fileData = toolResponse.row_level_metadata.filter(
      (item: RowMetadataItem) => item.File_Name === fileName
    );

    // -------- Top Metadata Rows --------
    // Determine entity type based on upload method
    const isStreamingFlow = globalThis.location?.pathname.includes('/streaming') || 
                           sessionStorage.getItem('upload_type') === 'streaming';
    const entityType = isStreamingFlow ? 'Table' : 'File';
    
    const metadataRows = [
      ["Entity Type", entityType],
      ["File Type", entityType === 'File' ? 'Incoming' : ''],
      ["Entity Physical Name", fileName],
      /* ["Entity Business Name", `${fileName} File`],
      ["Entity Description", `This file contains ${fileName} metadata`], */
      ["Entity Business Name", ""],
      ["Entity Description", ""],
      []
    ];

    // -------- Header Row --------
    const headerRow = [[
      "Attribute Name",
      "Logical Attribute Name",
      "Attribute Description",
      "Data Type",
      "Length",
      "Precision",
      "Format",
      "Nullability",
      "Most Occurrences",
      "Primary Key",
      "Foreign Key",
      "Alternate Key1"
    ]];

    // -------- Data Rows --------
    const dataRows = fileData.map((item: RowMetadataItem) => [
      item.Attribute_Name,
      item.Logical_Attribute_Name,
      item.Attribute_Description,
      item.Data_Type,
      item.Length,
      item.Precision,
      item.Format,
      item.Nullability,
      item.Most_Occurrences,
      item.Primary_Key,
      item.Foreign_Key,
      item.Alternate_Key1
    ]);

    const sheetData = [
      ...metadataRows,
      ...headerRow,
      ...dataRows
    ];

    const ws = XLSX.utils.aoa_to_sheet(sheetData);

    XLSX.utils.book_append_sheet(wb, ws, name);

  });
}

    XLSX.writeFile(wb, "Metadata_Template.xlsx");
  }, [response, modifiedResponse, getActualValue, generateUniqueSheetName]);

  useEffect(() => {
    if (exportRef) exportRef.current = exportExcelFile;
  }, [exportRef, exportExcelFile]);

  /* -------------------- JSX -------------------- */

  return (
    <div className="w-full">
      <div className="bg-white rounded-lg shadow-md border border-gray-200">
        <div className="p-4 border-b border-gray-100 bg-gray-50 rounded-t-lg">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-2">
              <div
                className={`w-3 h-3 rounded-full ${
                  isLoading
                    ? 'bg-yellow-500 animate-pulse'
                    : error
                    ? 'bg-red-500'
                    : 'bg-green-500'
                }`}
              ></div>

              <h3 className="font-semibold text-gray-800">
                {isLoading
                  ? 'Generating Metadata Template...'
                  : error
                  ? 'Generation Error'
                  : 'Generated Metadata Template'}
                {modifiedResponse && (
                  <span className="text-xs bg-green-100 text-green-700 px-2 py-1 rounded ml-2">
                    Modified
                  </span>
                )}
              </h3>
            </div>

            <div className="flex gap-4">
              <button
              onClick={handleRetry}
              className="bg-brand-darkblue hover:bg-brand-blue text-white px-3 py-1 rounded text-sm font-medium transition-colors cursor-pointer"
            >
              Retry
            </button>

              <button
                onClick={exportExcelFile}
                className="flex items-center gap-2 text-sm text-font-blue hover:text-font-blue font-medium"
              >
                <Download size={16} />
                Export as Excel
              </button>
            </div>
          </div>
        </div>

        <div className="p-6">

          {isLoading && (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="w-8 h-8 animate-spin text-font-blue mr-3" />
              <div className="text-center">
                <p className="text-gray-600 mb-1">Generating your metadata template...</p>
                <p className="text-sm text-gray-500">
                  Copying field metadata from your Data Dictionary. This usually finishes within a minute.
                </p>
                {elapsedSeconds > 0 && (
                  <p className="text-xs text-gray-400 mt-2">Elapsed: {elapsedSeconds}s</p>
                )}
              </div>
            </div>
          )}

          {error && (
            <div className="flex items-start space-x-3 py-4">
              <AlertCircle className="w-6 h-6 text-red-500 mt-1 flex-shrink-0" />
              <div>
                <p className="text-red-800 font-medium mb-1">Template Generation Failed</p>
                <p className="text-red-600 text-sm">{error}</p>
              </div>
            </div>
          )}

          {(response || modifiedResponse) && !isLoading && !error && (
            <div className="space-y-4">
              {/* Debug info */}
              {/* <div className="bg-gray-100 p-4 rounded text-xs">
                <strong>Debug - Response structure:</strong>
                <pre>{JSON.stringify(response, null, 2)}</pre>
              </div> */}
              
              {/* Process response */}
              {(() => {
                // Use modified response if available, otherwise use regular response
                const currentResponse = modifiedResponse || response;
                
                // Handle array response format (our new format)
                if (Array.isArray(currentResponse) && currentResponse.length > 0) {
                  const lastItem = currentResponse[currentResponse.length - 1];
                  const toolResponse = lastItem?.tool_response;
                  
                  if (!toolResponse) return null;

                  const hasFileSpecs = (toolResponse.file_specs_mapping?.length || 0) > 0;
                  const hasRowMetadata = (toolResponse.row_level_metadata?.length || 0) > 0;

                  if (!hasFileSpecs && !hasRowMetadata) {
                    return (
                      <div className="rounded-lg border border-yellow-200 bg-yellow-50 p-4 text-sm text-yellow-800">
                        The metadata tables were created, but no rows were returned yet. Retry after
                        confirming the Data Dictionary step completed successfully.
                      </div>
                    );
                  }
                  
                  return (
                    <div className="space-y-6">
                      {/* File Specs Mapping */}
                      {toolResponse.file_specs_mapping?.length > 0 && (
                        <div>
                          <h4 className="text-lg font-semibold mb-3">File Specifications</h4>
                          <div className="overflow-x-auto">
                            <table className="min-w-full border border-gray-300">
                              <thead className="bg-gray-50">
                                <tr>
                                  <th className="px-4 py-2 border-b text-left font-medium">Template Field</th>
                                  <th className="px-4 py-2 border-b text-left font-medium">Value</th>
                                </tr>
                              </thead>
                              <tbody>
                                {toolResponse.file_specs_mapping.map((item: FileSpecMapping, index: number) => (
                                  <tr key={index} className="hover:bg-gray-50">
                                    <td className="px-4 py-2 border-b">{item.Field || ''}</td>
                                    <td className="px-4 py-2 border-b">{getActualValue(item) || ''}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </div>
                      )}

                      {/* Row Level Metadata */}
                      {toolResponse.row_level_metadata?.length > 0 && (
                        <div>
                          <h4 className="text-lg font-semibold mb-3">Row Level Metadata</h4>
                          {(() => {
                            const fileNames = [...new Set(toolResponse.row_level_metadata?.map((item: RowMetadataItem) => item.File_Name) || [])];
                            
                            return fileNames.map((fileName) => {
                              const fileData = toolResponse.row_level_metadata?.filter((item: RowMetadataItem) => item.File_Name === fileName) || [];
                              
                              return (
                                <div key={fileName as React.Key} className="mb-6">
                                  <h5 className="text-md font-medium mb-2 text-font-blue">
                                    File: {(fileName as string) || 'Unknown'}
                                  </h5>
                                  <div className="overflow-x-auto">
                                    <table className="min-w-full border border-gray-300">
                                      <thead className="bg-gray-50">
                                        <tr>
                                          <th className="px-3 py-2 border-b text-left font-medium text-sm">Attribute Name</th>
                                          <th className="px-3 py-2 border-b text-left font-medium text-sm">Logical Name</th>
                                          <th className="px-3 py-2 border-b text-left font-medium text-sm">Description</th>
                                          <th className="px-3 py-2 border-b text-left font-medium text-sm">Data Type</th>
                                          <th className="px-3 py-2 border-b text-left font-medium text-sm">Length</th>
                                          <th className="px-3 py-2 border-b text-left font-medium text-sm">Precision</th>
                                          <th className="px-3 py-2 border-b text-left font-medium text-sm">Format</th>
                                          <th className="px-3 py-2 border-b text-left font-medium text-sm">Nullability</th>
                                          <th className="px-3 py-2 border-b text-left font-medium text-sm">Most Occurrences</th>
                                          <th className="px-3 py-2 border-b text-left font-medium text-sm">Primary Key</th>
                                          <th className="px-3 py-2 border-b text-left font-medium text-sm">Foreign Key</th>
                                          <th className="px-3 py-2 border-b text-left font-medium text-sm">Alternate Key1</th>
                                        </tr>
                                      </thead>
                                      <tbody>
                                        {fileData.map((item: RowMetadataItem, index: number) => (
                                          <tr key={index} className="hover:bg-gray-50">
                                            <td className="px-3 py-2 border-b text-sm">{item.Attribute_Name || ''}</td>
                                            <td className="px-3 py-2 border-b text-sm">{item.Logical_Attribute_Name || ''}</td>
                                            <td className="px-3 py-2 border-b text-sm">{item.Attribute_Description || ''}</td>
                                            <td className="px-3 py-2 border-b text-sm">{item.Data_Type || ''}</td>
                                            <td className="px-3 py-2 border-b text-sm">{item.Length || ''}</td>
                                            <td className="px-3 py-2 border-b text-sm">{item.Precision || ''}</td>
                                            <td className="px-3 py-2 border-b text-sm">{item.Format || ''}</td>
                                            <td className="px-3 py-2 border-b text-sm">{item.Nullability || ''}</td>
                                            <td className="px-3 py-2 border-b text-sm">{item.Most_Occurrences || ''}</td>
                                            <td className="px-3 py-2 border-b text-sm">{item.Primary_Key || ''}</td>
                                            <td className="px-3 py-2 border-b text-sm">{item.Foreign_Key || ''}</td>
                                            <td className="px-3 py-2 border-b text-sm">{item.Alternate_Key1 || ''}</td>
                                          </tr>
                                        ))}
                                      </tbody>
                                    </table>
                                  </div>
                                </div>
                              );
                            });
                          })()}
                        </div>
                      )}
                    </div>
                  );
                }

                return null;
              })()}

              <div className="mt-4 pt-4 border-t border-gray-100">
                <p className="text-xs text-gray-500">
                  Template generated at {new Date().toLocaleString()}
                </p>
              </div>
            </div>
          )}

          {!isLoading && !error && !response && !modifiedResponse && (
            <div className="text-center py-8 text-gray-500">
              <FileSearch className="w-12 h-12 mx-auto mb-3 text-gray-300" />
              <p>Waiting for data to generate template...</p>
            </div>
          )}
        </div>
      </div>

      {/* Floating Retry Button */}
      {isScrolled && (response || modifiedResponse) && !isLoading && !error && (
        <button
          onClick={handleRetry}
          className="fixed bottom-6 right-6 bg-brand-darkblue hover:bg-brand-blue text-white px-3 py-1 rounded text-sm font-medium transition-colors cursor-pointer shadow-lg z-50"
        >
          Retry
        </button>
      )}
    </div>
  );
};

export default MetadataTemplate;