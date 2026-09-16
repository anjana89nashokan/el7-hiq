import React, { useState, useEffect, useRef } from 'react';
import { Loader2, FileSearch, AlertCircle, Download } from 'lucide-react';
import axiosInstance from '../utils/axios-interceptor';
import remarkGfm from 'remark-gfm'
import { unified } from "unified";
import remarkParse from "remark-parse";
import ChatPopup from './ChatPopup';
import ValidationAuditTable from './ValidationAuditTable';
import {
  buildDataDictionaryMarkdownTable,
  formatMostOccurrences,
  resolveDataDictionaryDisplay,
} from '../utils/dataDictionaryUtils';
interface DataDictionaryState {
  response: string;
  json: any;
  resultData: any[];
  validationAuditLog: any[];
  isDdPresent: boolean;
  isCompleted: boolean;
  dataDictionaryTableId?: string;
}

interface DataDictionaryProps {
  profilingData: any;
  relationshipAnalysis: any;
  setDataDictionaryJson: React.Dispatch<React.SetStateAction<any>>;
  setDataDictionaryResponse: (response: string) => void;
  prevResponse?: string;
  onLoadingChange?: (isLoading: boolean) => void;
  onRetry?: React.MutableRefObject<(() => void) | null>;
  dataDictionaryState?: DataDictionaryState;
  setDataDictionaryState?: React.Dispatch<React.SetStateAction<DataDictionaryState>>;
  exportFunctionRef?: React.MutableRefObject<(() => Promise<void>) | null>;
  hasApiBeenCalled?: boolean;
  markApiCalled?: () => void;
  isLoadingDataDictionary?: boolean;
  modifiedResponse?: any;
}

const DataDictionaryView: React.FC<DataDictionaryProps> = ({ profilingData, relationshipAnalysis, setDataDictionaryJson, setDataDictionaryResponse, prevResponse, onLoadingChange, onRetry, dataDictionaryState, setDataDictionaryState, exportFunctionRef, hasApiBeenCalled, markApiCalled, isLoadingDataDictionary, modifiedResponse }) => {
  const [isLoading, setIsLoading] = useState(isLoadingDataDictionary || false);
  const [response, setResponse] = useState<string>(dataDictionaryState?.response || prevResponse || '');
  const [error, setError] = useState<string>('');
  const [hasAnalyzed, setHasAnalyzed] = useState(dataDictionaryState?.isCompleted || false);
  const [chatOpen, setChatOpen] = useState(false);
  const [showMappingModal, setShowMappingModal] = useState(false);
  const [mappingData, setMappingData] = useState<any>(null);
  const [mappingForm, setMappingForm] = useState<{ [key: string]: string }>({});
  const [validationAuditLog, setValidationAuditLog] = useState<any[]>(dataDictionaryState?.validationAuditLog || []);
  const [resultData, setResultData] = useState<any[]>(dataDictionaryState?.resultData || []);
  const [isDdPresent, setIsDdPresent] = useState(dataDictionaryState?.isDdPresent || false);
  const [showUploadModal, setShowUploadModal] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [isUpdatedData, setIsUpdatedData] = useState(false);
  const [toast, setToast] = useState<{ message: string, type: 'success' | 'error' } | null>(null);
  const [isScrolled, setIsScrolled] = useState(false);

  const hasSentRef = useRef(false);

  // Handle modified response from chat modal
  useEffect(() => {
    if (modifiedResponse && Array.isArray(modifiedResponse) && modifiedResponse.length > 0) {
      const responseData = modifiedResponse[0];
      
      const responseText = typeof responseData.text_response === 'string' 
        ? responseData.text_response 
        : (responseData.text_response?.message || JSON.stringify(responseData));
      const toolResponse = responseData.tool_response;
      
      setResponse(responseText);
      setDataDictionaryResponse(responseText);
      setHasAnalyzed(true);
      
      // Process tool response if available - this is crucial for step 3
      if (toolResponse) {
        processToolResponse(toolResponse, responseText);
      }
    }
  }, [modifiedResponse]);

  // Sync local loading state with global loading state
  useEffect(() => {
    setIsLoading(isLoadingDataDictionary || false);
  }, [isLoadingDataDictionary]);

  // Handle scroll for floating button
    useEffect(() => {
      const handleScroll = () => {
        setIsScrolled(window.scrollY > 100);
      };
      window.addEventListener('scroll', handleScroll);
      return () => window.removeEventListener('scroll', handleScroll);
    }, []);


  const showToast = (message: string | any, type: 'success' | 'error') => {
    const messageText = typeof message === 'string' ? message : JSON.stringify(message);
    setToast({ message: messageText, type });
    setTimeout(() => setToast(null), 3000);
  };

  const getStoredSession = () => ({
    sessionId: sessionStorage.getItem("session_id"),
    appName: sessionStorage.getItem("app_name"),
    userId: sessionStorage.getItem("user_id")
  });

  const normalizeDataDictionaryJson = (value: any) => {
    if (!value) return null;
    if (Array.isArray(value)) {
      if (value.length === 0) return null;
      if (typeof value[0] === "object" && !Array.isArray(value[0])) {
        return [value];
      }
      return value;
    }
    if (typeof value === "object") {
      if (Array.isArray(value.result) && value.result.length > 0) {
        return [value.result];
      }
      return [value];
    }
    return null;
  };

  const markdownToJson = async (md: string) => {
    const tree = unified().use(remarkParse).use(remarkGfm).parse(md);
    return tree.children
      .filter((node: any) => node.type === "table")
      .map((node: any) => {
        const headers = node.children[0].children.map((c: any) => c.children[0]?.value);
        const rows = node.children.slice(1).map((row: any) =>
          row.children.map((cell: any) => cell.children[0]?.value || "")
        );
        return rows.map((row: string[]) => Object.fromEntries(row.map((cell, i) => [headers[i], cell])));
      });
  };

  const processToolResponse = async (toolResponse: any, textResponse?: string) => {
    const parsedToolResponse =
      typeof toolResponse === 'string'
        ? (() => {
            try {
              return JSON.parse(toolResponse);
            } catch {
              return toolResponse;
            }
          })()
        : toolResponse;

    const resolved = await resolveDataDictionaryDisplay({
      tool_response: parsedToolResponse,
      text_response: textResponse,
    });

    if (
      resolved.rows.length === 0 &&
      resolved.validationAuditLog.length === 0 &&
      (resolved.tableId ||
        parsedToolResponse?.data_dictionary_table_id ||
        parsedToolResponse?.table_name)
    ) {
      setError('No data dictionary rows were returned. Please retry generation.');
    } else {
      setError('');
    }

    let responseText = resolved.responseText;

    const newValidationAuditLog =
      resolved.validationAuditLog.length > 0
        ? resolved.validationAuditLog
        : validationAuditLog;

    setValidationAuditLog(newValidationAuditLog);
    setResultData(resolved.rows);
    setIsDdPresent(resolved.isDdPresent);

    if (toolResponse?.status === "needs_user_input") {
      setMappingData(parsedToolResponse);
      const initialForm = Object.keys(parsedToolResponse.proposed_mapping).reduce((acc, key) => {
        acc[key] = parsedToolResponse.proposed_mapping[key].vendor_column || '';
        return acc;
      }, {} as { [key: string]: string });
      setMappingForm(initialForm);
      setShowMappingModal(true);
      responseText = textResponse || "Please review the proposed mapping.";
    }

    const normalizedJson = normalizeDataDictionaryJson(resolved.rows);
    if (normalizedJson) {
      setDataDictionaryJson(normalizedJson);
    }

    if (setDataDictionaryState) {
      setDataDictionaryState({
        response: responseText,
        json: normalizedJson || dataDictionaryState?.json || null,
        resultData: resolved.rows,
        validationAuditLog: newValidationAuditLog,
        isDdPresent: resolved.isDdPresent,
        isCompleted: true,
        dataDictionaryTableId:
          resolved.tableId || dataDictionaryState?.dataDictionaryTableId,
      });
    }

    return responseText;
  };

  const sendDataDictionaryMessage = async (message: string, customParts?: any[]) => {
    setIsLoading(true);
    onLoadingChange?.(true);
    setError('');

    try {
      const response = await axiosInstance.post('/messages/data-dictionary',
        {
          appName: getStoredSession().appName,
          sessionId: getStoredSession().sessionId,
          userId: getStoredSession().userId,
          newMessage: {
            parts: customParts || [{ text: message }],
            role: "user"
          },
          streaming: false,
          stateDelta: {}
        },
        { headers: { 'Content-Type': 'application/json' } }
      );

      if (!response.status || response.status !== 200) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const data = await response.data;
      let responseText = "Data dictionary generated successfully.";

      if (data?.tool_response) {
        responseText = await processToolResponse(data.tool_response, typeof data.text_response === 'string' ? data.text_response : data.text_response?.message || data.message);
      } else if (Array.isArray(data) && data[0]) {
        const firstItem = data[0];
        if (firstItem.tool_response) {
          responseText = await processToolResponse(firstItem.tool_response, typeof firstItem.text_response === 'string' ? firstItem.text_response : firstItem.text_response?.message || firstItem.message);
        } else {
          responseText = typeof firstItem.text_response === 'string' ? firstItem.text_response : firstItem.text_response?.message || firstItem.message || "Data processed successfully.";
        }
      } else if (data.text || data.response || data.message) {
        responseText = data.text || data.response || data.message;
      }

      setResponse(responseText);
      setDataDictionaryResponse(responseText);
      setHasAnalyzed(true);

    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to generate data dictionary');
    } finally {
      setIsLoading(false);
      onLoadingChange?.(false);
    }
  };

  const createMessage = () => {
    // const profilingResults = profilingData[0]?.tool_response?.result;

    return `Create a [Data Dictionary] using the profiling results and relationship analysis.`;
    // return `Create a [Data Dictionary] using the following information:\n\n` +
    //        `"profiling_output": ${JSON.stringify(profilingResults)},\n` +
    //        `"relationships_output": ${JSON.stringify(relationshipAnalysis)}`;
  };

  // Initialize state from global state when component mounts
  useEffect(() => {
    if (dataDictionaryState?.isCompleted && !hasAnalyzed) {
      setHasAnalyzed(true);
      hasSentRef.current = true;
    }

    if (dataDictionaryState?.response && !response) {
      setResponse(dataDictionaryState.response);
    }
    
    // Sync resultData from global state if available
    if (dataDictionaryState?.resultData && Array.isArray(dataDictionaryState.resultData) && dataDictionaryState.resultData.length > 0) {
      setResultData(dataDictionaryState.resultData);
    }
  }, [dataDictionaryState?.isCompleted, hasAnalyzed, dataDictionaryState?.resultData, dataDictionaryState?.response, response]);

  // Re-fetch rows from BigQuery when we have a table id but no rendered rows
  useEffect(() => {
    const tableId = dataDictionaryState?.dataDictionaryTableId;
    if (!tableId || resultData.length > 0 || isLoading) return;
    if (!hasAnalyzed && !dataDictionaryState?.isCompleted) return;

    let cancelled = false;

    void (async () => {
      try {
        const resolved = await resolveDataDictionaryDisplay({
          tool_response: { data_dictionary_table_id: tableId },
        });
        if (cancelled || resolved.rows.length === 0) return;

        setResultData(resolved.rows);
        setResponse(resolved.responseText);
        setDataDictionaryResponse(resolved.responseText);
        const normalizedJson = normalizeDataDictionaryJson(resolved.rows);
        if (normalizedJson) {
          setDataDictionaryJson(normalizedJson);
        }
        if (setDataDictionaryState) {
          setDataDictionaryState((prev) => ({
            ...prev,
            response: resolved.responseText,
            json: normalizedJson || prev.json,
            resultData: resolved.rows,
            isCompleted: true,
            dataDictionaryTableId: tableId,
          }));
        }
      } catch (hydrateErr) {
        console.error('Failed to hydrate data dictionary rows:', hydrateErr);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [
    dataDictionaryState?.dataDictionaryTableId,
    dataDictionaryState?.isCompleted,
    hasAnalyzed,
    isLoading,
    resultData.length,
    setDataDictionaryJson,
    setDataDictionaryResponse,
    setDataDictionaryState,
  ]);

  // Auto-send message only if no existing data and not already analyzed
  useEffect(() => {
    // Skip auto-send if we have a modified response
    if (modifiedResponse && Array.isArray(modifiedResponse) && modifiedResponse.length > 0) {
      return;
    }
    
    if (profilingData && !hasAnalyzed && !isLoading && !hasSentRef.current && !response && !dataDictionaryState?.isCompleted && !hasApiBeenCalled) {
      hasSentRef.current = true;
      markApiCalled?.();
      sendDataDictionaryMessage(createMessage());
    }
  }, [profilingData, relationshipAnalysis, hasAnalyzed, isLoading, dataDictionaryState?.isCompleted, hasApiBeenCalled, modifiedResponse]);

  const handleRetry = async () => {
    setHasAnalyzed(false);
    setError('');
    setResponse('');
    setResultData([]);
    setValidationAuditLog([]);
    setIsDdPresent(false);
    hasSentRef.current = false;
    setIsLoading(true);
    onLoadingChange?.(true);

    // Clear global state
    if (setDataDictionaryState) {
      setDataDictionaryState({
        response: "",
        json: null,
        resultData: [],
        validationAuditLog: [],
        isDdPresent: false,
        isCompleted: false
      });
    }

    try {
      const response = await axiosInstance.post('/messages/data-dictionary',
        {
          appName: getStoredSession().appName,
          sessionId: getStoredSession().sessionId,
          userId: getStoredSession().userId,
          newMessage: {
            parts: [{ text: createMessage() }],
            role: "user"
          },
          streaming: false,
          stateDelta: {}
        },
        { headers: { 'Content-Type': 'application/json' } }
      );

      if (!response.status || response.status !== 200) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const data = await response.data;
      let responseText = "Data dictionary generated successfully.";

      if (data?.tool_response) {
        responseText = await processToolResponse(data.tool_response, typeof data.text_response === 'string' ? data.text_response : data.text_response?.message || data.message);
      } else if (Array.isArray(data) && data[0]) {
        const firstItem = data[0];
        if (firstItem.tool_response) {
          responseText = await processToolResponse(firstItem.tool_response, typeof firstItem.text_response === 'string' ? firstItem.text_response : firstItem.text_response?.message || firstItem.message);
        } else {
          responseText = typeof firstItem.text_response === 'string' ? firstItem.text_response : firstItem.text_response?.message || firstItem.message || "Data processed successfully.";
        }
      } else if (data.text || data.response || data.message) {
        responseText = data.text || data.response || data.message;
      }

      setResponse(responseText);
      setDataDictionaryResponse(responseText);
      setHasAnalyzed(true);

    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to generate data dictionary');
    } finally {
      setIsLoading(false);
      onLoadingChange?.(false);
    }
  };

  // Expose handleRetry method to parent component via callback
  useEffect(() => {
    if (onRetry) {
      onRetry.current = handleRetry;
    }
  }, [handleRetry, onRetry]);

  const handleExport = async () => {
    if (!response) return;
    const json = await markdownToJson(response);
    setDataDictionaryJson(json);

    // Update global state with JSON
    if (setDataDictionaryState) {
      setDataDictionaryState(prev => ({
        ...prev,
        json: json
      }));
    }

    showToast("Data Dictionary Added Successfully", 'success');
  };

  // Expose handleExport function to parent component via ref
  useEffect(() => {
    if (exportFunctionRef) {
      exportFunctionRef.current = handleExport;
    }
  }, [response, exportFunctionRef]);

  const handleUploadUpdatedDD = () => {
    setShowUploadModal(true);
  };

  const handleFileSelect = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file && (file.type === 'text/csv' || file.type === 'application/vnd.ms-excel' || file.type === 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')) {
      setSelectedFile(file);
    } else {
      showToast('Please select a valid CSV or Excel file', 'error');
    }
  };

  const handleFileUpload = async () => {
    if (!selectedFile) {
      showToast('Please select a file first', 'error');
      return;
    }

    setIsUploading(true);
    try {
      const formData = new FormData();
      formData.append('file', selectedFile);
      formData.append('session_id', getStoredSession().sessionId || '123');

      const response = await axiosInstance.post('/messages/data-dictionary/reupload', formData, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      });

      if (response.status === 200) {
        const data = Array.isArray(response.data) ? response.data[0] : response.data;

        // Close modal on any response
        setShowUploadModal(false);
        setSelectedFile(null);

        if (data?.tool_response?.status === 'error') {
          const errorMsg = data.tool_response.error_type === 'invalid_template_schema'
            ? `Template error: Missing columns: ${data.tool_response.missing_columns?.join(', ')}. Extra columns: ${data.tool_response.extra_columns?.join(', ')}. Please fix and upload again.`
            : 'Upload failed. Please check your file and try again.';
          showToast(errorMsg, 'error');
        } else if (data?.tool_response?.result) {
          const newResultData = data.tool_response.result;
          const newResponse = buildDataDictionaryMarkdownTable(newResultData);

          setResultData(newResultData);
          setResponse(newResponse);
          setIsUpdatedData(true);
          const normalizedJson = normalizeDataDictionaryJson(newResultData);
          if (normalizedJson) {
            setDataDictionaryJson(normalizedJson);
          }

          // Update global state
          if (setDataDictionaryState) {
            setDataDictionaryState(prev => ({
              ...prev,
              json: normalizedJson || prev.json,
              resultData: newResultData,
              response: newResponse,
              isCompleted: true
            }));
          }

          showToast('Data Dictionary updated successfully!', 'success');
        }
      }
    } catch (error) {
      console.error('File upload error:', error);
      setShowUploadModal(false);
      setSelectedFile(null);
      const errorMessage = error instanceof Error ? error.message : 'Failed to upload file';
      showToast(errorMessage, 'error');
    } finally {
      setIsUploading(false);
    }
  };

  const handleDownloadCSV = () => {
    
    // Try to get data from multiple sources
    let dataToExport = resultData;
    if (!dataToExport || !Array.isArray(dataToExport) || dataToExport.length === 0) {
      dataToExport = dataDictionaryState?.resultData || [];
    }
       
    // Check if we have valid data
    if (!dataToExport || !Array.isArray(dataToExport) || dataToExport.length === 0) {
      showToast('No data available to download', 'error');
      return;
    }
    
    const headers = ['File Name', 'Field Name', 'Field Business Name', 'Data Type', 'Length', 'Format', 'Nullable', 'Most Occurrences', 'Primary Key', 'Foreign Key', 'Field Description'];
    
    const csvRows = dataToExport.map((item) => {
      const mostOccStr = formatMostOccurrences(item.most_occurrences ?? item['Most Occurrences']);
      const row = [
        item.file_name || item['File Name'] || '',
        item.field_name || item['Field Name'] || item['Attribute Name'] || '',
        item.business_name || item['Field Business Name'] || item['Logical Attribute Name'] || '',
        item.data_type || item['Data Type'] || '',
        item.length || item['Length'] || '',
        item.format || item['Format'] || '',
        item.nullable || item['Nullable'] || item['Nullability'] || '',
        mostOccStr,
        item.primary_key || item['Primary Key'] || '',
        item.foreign_key || item['Foreign Key'] || '',
        item.field_description || item['Field Description'] || item['Attribute Description'] || ''
      ];
      
      return row.map(field => `"${String(field || '').replace(/"/g, '""')}"`).join(',');
    });
    
    const csvContent = [
      headers.join(','),
      ...csvRows
    ].join('\n');

    if (csvContent.length <= headers.join(',').length + 1) {
      showToast('No data rows to export', 'error');
      return;
    }

    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `data-dictionary-${new Date().toISOString().split('T')[0]}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    
    showToast('CSV downloaded successfully', 'success');
  };

  const handleValidationAuditSave = async (editedData: any[]) => {
    const response = await axiosInstance.post('/messages/data-dictionary',
      {
        appName: getStoredSession().appName,
        sessionId: getStoredSession().sessionId,
        userId: getStoredSession().userId,
        newMessage: {
          parts: [{
            text: "[Finalize Standardized Data Dictionary]",
            context: {
              updated_validation_audit_log: editedData
            }
          }],
          role: "user"
        },
        streaming: false,
        stateDelta: {}
      },
      { headers: { 'Content-Type': 'application/json' } }
    );

    if (response.status === 200) {
      const data = response.data;
      let newResultData = resultData;
      if (Array.isArray(data) && (data[0]?.tool_response?.result || data[0]?.tool_response)) {
        newResultData = data[0].tool_response.result || [];
        setResultData(newResultData);
      } else if (data?.tool_response?.result || data?.tool_response) {
        newResultData = data.tool_response.result || [];
        setResultData(newResultData);
      }
      const normalizedJson = normalizeDataDictionaryJson(newResultData);
      if (normalizedJson) {
        setDataDictionaryJson(normalizedJson);
      }
      setValidationAuditLog(editedData);

      // Update global state
      if (setDataDictionaryState) {
        setDataDictionaryState(prev => ({
          ...prev,
          json: normalizedJson || prev.json,
          resultData: newResultData,
          validationAuditLog: editedData
        }));
      }
      return response;
    }
  };

  return (
    <div className="data-dic-content">
      <div className="p-4 border-b border-gray-100 bg-gray-50 rounded-t-lg ">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <div className={`w-3 h-3 rounded-full ${
              isLoading ? 'bg-yellow-500 animate-pulse' :
              error ? 'bg-red-500' : 'bg-green-500'
            }`}></div>
            <h3 className="font-semibold text-gray-800">
              {(() => {
                if (isLoading) return 'Processing...';
                if (error) return 'Data Dictionary Error';
                return 'Data Dictionary';
              })()}
            </h3>
          </div>

          <div className="flex items-center space-x-3">
            <button
              onClick={handleRetry}
              className="bg-brand-darkblue hover:bg-brand-blue text-white px-3 py-1 rounded text-sm font-medium transition-colors cursor-pointer"
            >
              Retry
            </button>
          </div>
        </div>

        {(resultData.length > 0 || (dataDictionaryState?.resultData && dataDictionaryState.resultData.length > 0)) && !isLoading && !error && (
          <div className="flex items-center space-x-3 mt-3 pt-3 border-t border-gray-200">
            <button
              onClick={handleDownloadCSV}
              className="px-3 py-1 text-sm bg-brand-primary hover:bg-brand-primary-hover text-white rounded-md flex items-center space-x-1 cursor-pointer"
            >
              <Download className="w-4 h-4" />
              <span>Download CSV ({resultData.length || dataDictionaryState?.resultData?.length || 0} rows)</span>
            </button>

            <button
              onClick={handleUploadUpdatedDD}
              className="px-3 py-1 text-sm bg-brand-primary hover:bg-brand-primary-hover text-white rounded-md cursor-pointer"
            >
              Upload Updated Data Dictionary
            </button>

            {/* <button
              onClick={handleExport}
              className="px-3 py-1 text-sm bg-green-600 hover:bg-green-700 hover:cursor-pointer text-white rounded-md"
            >
              Add to profiling table
            </button> */}
          </div>
        )}
      </div>

      {/* Upload Modal */}
      {showUploadModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg max-w-md w-full mx-4">
            <div className="p-6">
              <h3 className="text-lg font-semibold mb-4">Upload Updated Data Dictionary</h3>
              <div className="space-y-4">
                <div>
                  <label htmlFor="file-upload" className="block text-sm font-medium text-gray-700 mb-2">
                    Select Excel or CSV file
                  </label>
                  <input
                    id="file-upload"
                    type="file"
                    accept=".csv,.xlsx,.xls"
                    onChange={handleFileSelect}
                    className="block w-full text-sm text-gray-500 file:mr-4 file:py-2 file:px-4 file:rounded-md file:border-0 file:text-sm file:font-semibold file:bg-brand-surface file:text-font-blue hover:file:bg-teal-100"
                  />
                </div>
                {selectedFile && (
                  <div className="text-sm text-gray-600">
                    Selected: {selectedFile.name}
                  </div>
                )}
              </div>
              <div className="flex justify-end space-x-3 mt-6">
                <button
                  onClick={() => {
                    setShowUploadModal(false);
                    setSelectedFile(null);
                  }}
                  className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800"
                  disabled={isUploading}
                >
                  Cancel
                </button>
                <button
                  onClick={handleFileUpload}
                  disabled={!selectedFile || isUploading}
                  className="px-4 py-2 text-sm bg-brand-primary hover:bg-brand-primary-hover text-white rounded-md disabled:bg-gray-400 disabled:cursor-not-allowed"
                >
                  {isUploading ? 'Uploading...' : 'Upload'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Mapping Modal */}
      {showMappingModal && mappingData && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg max-w-2xl w-full mx-4 max-h-[80vh] flex flex-col">
            <div className="p-6 pb-0">
              <h3 className="text-lg font-semibold mb-4">Review Proposed Mapping</h3>
            </div>
            <div className="px-6 overflow-y-auto flex-1">
              <form className="space-y-4">
                {Object.keys(mappingData.proposed_mapping).map((fieldName) => {
                  const selectedValues = new Set(Object.values(mappingForm).filter(Boolean));

                  return (
                    <div key={fieldName} className="flex flex-col">
                      <label htmlFor={`mapping-${fieldName}`} className="text-sm font-medium text-gray-700 mb-1">
                        {fieldName}
                      </label>
                      <select
                        id={`mapping-${fieldName}`}
                        value={mappingForm[fieldName] || ''}
                        onChange={(e) => setMappingForm(prev => ({ ...prev, [fieldName]: e.target.value }))}
                        className="border border-gray-300 rounded-md px-3 py-2 text-sm"
                      >
                        <option value="">Select vendor column</option>
                        {mappingData.available_vendor_columns.map((column: string) => (
                          <option
                            key={column}
                            value={column}
                            disabled={selectedValues.has(column) && mappingForm[fieldName] !== column}
                          >
                            {column}
                          </option>
                        ))}
                      </select>
                    </div>
                  );
                })}
              </form>
            </div>
            <div className="p-6 pt-0">
              <div className="flex justify-end space-x-3 mt-6">
                <button
                  onClick={() => setShowMappingModal(false)}
                  className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800"
                >
                  Cancel
                </button>
                <button
                  onClick={() => {
                    const confirmedMapping = Object.keys(mappingForm).reduce((acc, key) => {
                      acc[key] = mappingForm[key] || null;
                      return acc;
                    }, {} as { [key: string]: string | null });

                    const customParts = [{
                      text: "[User has confirmed the mapping. Please proceed with the final validation.]",
                      context: {
                        confirmed_mapping: confirmedMapping
                      }
                    }];

                    setShowMappingModal(false);
                    sendDataDictionaryMessage("", customParts);
                  }}
                  className="px-4 py-2 text-sm bg-brand-darkblue hover:bg-brand-darkblue/80 text-white rounded-md cursor-pointer"
                >
                  Review
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="py-4">
        {isLoading && (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="w-8 h-8 animate-spin text-font-blue mr-3" />
            <div className="text-center">
              <p className="text-gray-600 mb-1">Generating data dictionary...</p>
              <p className="text-sm text-gray-500">This may take a few moments</p>
            </div>
          </div>
        )}

        {error && (
          <div className="flex items-start space-x-3 py-4">
            <AlertCircle className="w-6 h-6 text-red-500 mt-1 flex-shrink-0" />
            <div>
              <p className="text-red-800 font-medium mb-1">Request Failed</p>
              <p className="text-red-600 text-sm">{error}</p>
            </div>
          </div>
        )}

        <section className="data-dictionary-results">
          {(response || validationAuditLog.length > 0 || resultData.length > 0) && !isLoading && !error && (
            <div className="prose max-w-none">
              {/* Result Data Table */}
              {resultData.length > 0 && (
                <div className="mb-6">
                  <h4 className="text-lg font-semibold mb-3">{isUpdatedData ? 'Final Data Dictionary Results' : 'Data Dictionary Results'}</h4>
                  <div className="overflow-x-auto">
                    <table className="min-w-full border border-gray-300">
                      <thead className="bg-gray-50">
                        <tr>
                          <th className="border border-gray-300 px-4 py-2 text-left">File Name</th>
                          <th className="border border-gray-300 px-4 py-2 text-left">Field Name</th>
                          <th className="border border-gray-300 px-4 py-2 text-left">Field Business Name</th>
                          <th className="border border-gray-300 px-4 py-2 text-left">Data Type</th>
                          <th className="border border-gray-300 px-4 py-2 text-left">Length</th>
                          <th className="border border-gray-300 px-4 py-2 text-left">Format</th>
                          <th className="border border-gray-300 px-4 py-2 text-left">Nullable</th>
                          <th className="border border-gray-300 px-4 py-2 text-left">Most Occurrences</th>
                          <th className="border border-gray-300 px-4 py-2 text-left">Primary Key</th>
                          <th className="border border-gray-300 px-4 py-2 text-left">Foreign Key</th>
                          <th className="border border-gray-300 px-4 py-2 text-left">Field Description</th>
                        </tr>
                      </thead>
                      <tbody>
                        {resultData.map((item, index) => {
                          const mostOccStr = formatMostOccurrences(item.most_occurrences ?? item['Most Occurrences']);
                          return (
                          <tr key={index} className="hover:bg-gray-50">
                            <td className="border border-gray-300 px-4 py-2">{item['File Name'] || item.file_name || ''}</td>
                            <td className="border border-gray-300 px-4 py-2">{item['Attribute Name'] || item.field_name || ''}</td>
                            <td className="border border-gray-300 px-4 py-2">{item['Logical Attribute Name'] || item.business_name || ''}</td>
                            <td className="border border-gray-300 px-4 py-2">{item['Data Type'] || item.data_type || ''}</td>
                            <td className="border border-gray-300 px-4 py-2">{item['Length'] || item.length || 0}</td>
                            <td className="border border-gray-300 px-4 py-2">{item['Format'] || item.format || ''}</td>
                            <td className="border border-gray-300 px-4 py-2">{item['Nullability'] || item.nullable || ''}</td>
                            <td className="border border-gray-300 px-4 py-2">{mostOccStr}</td>
                            <td className="border border-gray-300 px-4 py-2">{item['Primary Key'] || item.primary_key || ''}</td>
                            <td className="border border-gray-300 px-4 py-2">{item['Foreign Key'] || item.foreign_key || ''}</td>
                            <td className="border border-gray-300 px-4 py-2">{item['Attribute Description'] || item.field_description || ''}</td>
                          </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {validationAuditLog.length > 0 && (
                <ValidationAuditTable
                  validationData={validationAuditLog}
                  onSave={handleValidationAuditSave}
                />
              )}

              {resultData.length === 0 && validationAuditLog.length === 0 && hasAnalyzed && (
                <div className="mb-6 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
                  Data dictionary generation finished, but no field rows were returned. Click Retry to regenerate.
                </div>
              )}

              <div className="mt-4 pt-4 border-t border-gray-100">
                <p className="text-xs text-gray-500">
                  Data dictionary generated at {new Date().toLocaleString()}
                </p>
              </div>
            </div>
          )}
        </section>

        {!isLoading && !error && !response && !hasAnalyzed && (
          <div className="text-center py-8 text-gray-500">
            <FileSearch className="w-12 h-12 mx-auto mb-3 text-gray-300" />
            <p>Waiting for Data Dictionary Generation...</p>
          </div>
        )}
      </div>

      {/* <LogViewer /> */}

      <ChatPopup isOpen={chatOpen} onClose={() => setChatOpen(false)} currentStep='Data Dictionary' />

      {/* Toast Notification */}
      {toast && (
        <div className={`fixed top-4 right-4 z-50 px-4 py-3 rounded-md shadow-lg transition-all duration-300 ${toast.type === 'success' ? 'bg-green-500 text-white' : 'bg-red-500 text-white'
          }`}>
          {String(toast.message)}
        </div>
      )}

      {/* Floating Retry Button */}
      {isScrolled && response && !isLoading && (
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

export default DataDictionaryView;
