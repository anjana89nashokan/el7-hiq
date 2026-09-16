import React, { useState, useEffect, useRef } from 'react';
import { FileSearch, AlertCircle, Download, FileText } from 'lucide-react';
import { useSSEStream } from '../hooks/useSSEStream';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import TableWithExport from './TableWithExport';
import ChatPopup from './ChatPopup';
import DataDictionaryChat from './DataDictionaryChat';
import {
  formatMostOccurrences,
  getInlineDataDictionaryRows,
  resolveDataDictionaryDisplay,
} from '../utils/dataDictionaryUtils';

interface StreamingDataDictionaryViewProps {
  profilingData: any;
  relationshipAnalysis: any;
  setDataDictionaryJson: React.Dispatch<React.SetStateAction<any>>;
  setDataDictionaryResponse: (response: string) => void;
  onDataDictionaryTableId?: (tableId: string) => void;
  dataDictionaryTableId?: string;
  prevResponse?: string;
  hasApiBeenCalled: boolean;
  markApiCalled: () => void;
  onStreamingStart?: () => void;
  onStreamingEnd?: () => void;
  modifiedResponse?: any;
}

const StreamingDataDictionaryView: React.FC<StreamingDataDictionaryViewProps> = ({
  profilingData,
  relationshipAnalysis,
  setDataDictionaryJson,
  setDataDictionaryResponse,
  onDataDictionaryTableId,
  dataDictionaryTableId,
  prevResponse,
  hasApiBeenCalled,
  markApiCalled,
  onStreamingStart,
  onStreamingEnd,
  modifiedResponse
}) => {
  const [response, setResponse] = useState<string>(prevResponse || '');
  const [chatOpen, setChatOpen] = useState(false);
  const [showDebugView, setShowDebugView] = useState(false);
  const [fullResult, setFullResult] = useState<any>(null);
  const [resultRows, setResultRows] = useState<any[]>([]);
  const [isScrolled, setIsScrolled] = useState(false);
  const hasSentRef = useRef(false);

  function getStoredSession() {
    const sessionId = sessionStorage.getItem('session_id');
    const appName = sessionStorage.getItem('app_name');
    const userId = sessionStorage.getItem('user_id');
    return { sessionId, appName, userId };
  }

  const applyDataDictionaryResult = async (finalResult: any) => {
    const resolved = await resolveDataDictionaryDisplay(finalResult);
    const normalizedFullResult = {
      ...finalResult,
      text_response: resolved.responseText,
      tool_response: {
        ...(finalResult?.tool_response || {}),
        result: resolved.rows,
        data_dictionary_table_id:
          resolved.tableId ||
          finalResult?.tool_response?.data_dictionary_table_id ||
          finalResult?.data_dictionary_table_id,
      },
    };

    setResponse(resolved.responseText);
    setDataDictionaryResponse(resolved.responseText);
    setFullResult(normalizedFullResult);
    setResultRows(resolved.rows);

    if (resolved.rows.length > 0) {
      setDataDictionaryJson([resolved.rows]);
    }

    const tableId =
      resolved.tableId ||
      finalResult?.tool_response?.data_dictionary_table_id ||
      finalResult?.data_dictionary_table_id;
    if (tableId) {
      onDataDictionaryTableId?.(tableId);
    }
  };

  // Use SSE streaming hook with Data Dictionary specific handling
  // IMPORTANT: Uses /send-large-data-dict endpoint (large_data_root_agent)
  const {
    isStreaming,
    progress,
    statusMessage,
    error,
    result,
    // toolResponse,
    llmAnalysis,
    isAnalyzing,
    currentBatch,
    totalBatches,
    startStream,
    resetStream
  } = useSSEStream({
    endpoint: '/messages-strm/send-large-data-dict',  // NEW: Large data flow endpoint (isolated agent)
    onProgress: (progressValue, message) => {
      console.log(`[DataDict-Large] Progress: ${progressValue}% - ${message}`);

      // Log phase transitions
      if (progressValue === 95) {
        console.log('[DataDict-Large] Phase 2: Tool execution complete - technical data merged');
      } else if (progressValue >= 95 && progressValue < 100) {
        console.log('[DataDict-Large] Phase 3: Batched LLM generation - creating business descriptions');
      }
    },
    onComplete: async (finalResult) => {
      await applyDataDictionaryResult(finalResult);
      onStreamingEnd?.();
    },
    onError: (errorMsg) => {
      console.error('[DataDict-Large] Generation error:', errorMsg);
      console.error('[DataDict-Large] Error details:', { errorMsg, timestamp: new Date().toISOString() });
      
      // Notify parent that streaming ended due to error
      onStreamingEnd?.();
    }
  });

  // Helper function to get current phase info based on progress
  const getCurrentPhase = (progress: number, isAnalyzingLLM: boolean, currentBatchNum: number, totalBatchesNum: number) => {
    if (progress < 95) {
      return {
        id: 1,
        name: 'Tool Execution',
        emoji: '',
        icon: 'settings',
        description: 'Merging technical data from profiling and relationship analysis',
        color: 'blue'
      };
    } else if (progress === 95) {
      return {
        id: 2,
        name: 'Tool Complete',
        emoji: '',
        icon: 'check',
        description: `Technical data merged. Starting batched business description generation...`,
        color: 'green'
      };
    } else if (isAnalyzingLLM && progress < 100) {
      return {
        id: 3,
        name: 'Batched Generation',
        emoji: '',
        icon: 'brain',
        description: currentBatchNum && totalBatchesNum
          ? `Processing batch ${currentBatchNum}/${totalBatchesNum} - generating business-friendly descriptions`
          : 'Gemini is generating business descriptions for your data dictionary',
        color: 'purple'
      };
    } else if (progress >= 99.5 && progress < 100) {
      return {
        id: 4,
        name: 'Finalizing',
        emoji: '',
        icon: 'file',
        description: 'Generating final data dictionary table with summary...',
        color: 'indigo'
      };
    } else {
      return {
        id: 5,
        name: 'Complete',
        emoji: '',
        icon: 'celebration',
        description: 'Data dictionary ready! Review your comprehensive table below',
        color: 'green'
      };
    }
  };

  // Helper to get all phases for visual indicators
  const allPhases = [
    { id: 1, name: 'Tool Execution', threshold: 0, emoji: '' },
    { id: 2, name: 'Tool Complete', threshold: 95, emoji: '' },
    { id: 3, name: 'Batched Generation', threshold: 95.1, emoji: '' },
    { id: 4, name: 'Finalizing', threshold: 99.5, emoji: '' },
    { id: 5, name: 'Complete', threshold: 100, emoji: '' }
  ];

  const sendDataDictionaryMessage = async (message: string) => {

    // Notify parent that streaming started
    onStreamingStart?.();

    const requestData = {
      appName: getStoredSession().appName,
      sessionId: getStoredSession().sessionId,
      userId: getStoredSession().userId,
      newMessage: {
        parts: [
          {
            text: message
          }
        ],
        role: 'user'
      },
      streaming: true,
      stateDelta: {}
    };

    // Wrap in FormData to match server expectation
    const formData = new FormData();
    formData.append('request', JSON.stringify(requestData));

    await startStream(formData);
  };

  // Sync result from SSE hook to local response state (backup mechanism)
  useEffect(() => {
    if (result && !isStreaming) {
      void applyDataDictionaryResult(result);
    }
  }, [result, isStreaming]);

  // Hydrate rows when revisiting the step with a saved table id
  useEffect(() => {
    const tableId =
      dataDictionaryTableId ||
      fullResult?.tool_response?.data_dictionary_table_id ||
      fullResult?.data_dictionary_table_id;

    if (!tableId || resultRows.length > 0 || isStreaming) return;

    let cancelled = false;

    void (async () => {
      try {
        await applyDataDictionaryResult({
          data_dictionary_table_id: tableId,
          tool_response: { data_dictionary_table_id: tableId },
        });
      } catch (hydrateErr) {
        if (!cancelled) {
          console.error('Failed to hydrate streaming data dictionary rows:', hydrateErr);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [dataDictionaryTableId, fullResult, isStreaming, resultRows.length]);

  // Sync prevResponse prop changes (from ChatModal "apply changes")
  useEffect(() => {
    if (prevResponse && prevResponse !== response) {
      setResponse(prevResponse);
    }
  }, [prevResponse]);

  // Handle modified response from ChatModal
  useEffect(() => {
    if (modifiedResponse && modifiedResponse.length > 0) {
      const modifiedData = modifiedResponse[0];
      if (modifiedData.text_response) {
        setResponse(modifiedData.text_response);
        setDataDictionaryResponse(modifiedData.text_response);
      }
      if (modifiedData.tool_response?.result) {
        setDataDictionaryJson([modifiedData.tool_response.result]);
        setResultRows(modifiedData.tool_response.result);
        setFullResult(modifiedData);
      }
    }
  }, [modifiedResponse]);

  // Auto-send message when profiling and relationship data are available
  useEffect(() => {
    if (profilingData && relationshipAnalysis && !hasSentRef.current && !response && !isStreaming && !hasApiBeenCalled) {
      hasSentRef.current = true;
      markApiCalled();

      // Simple message - backend will retrieve data from session state
      const message = 'Create data dictionary';

      sendDataDictionaryMessage(message);
    }
  }, [profilingData, relationshipAnalysis, isStreaming, response, hasApiBeenCalled]);

  const handleDownloadCSV = () => {
    const dataToExport =
      resultRows.length > 0
        ? resultRows
        : fullResult?.tool_response?.result ||
          getInlineDataDictionaryRows(fullResult);
    
    if (!dataToExport || !Array.isArray(dataToExport) || dataToExport.length === 0) {
      alert('No data available to download');
      return;
    }
    
    const headers = ['File Name', 'Field Name', 'Field Business Name', 'Data Type', 'Length', 'Format', 'Nullable', 'Most Occurrences', 'Primary Key', 'Foreign Key', 'Field Description'];
    
    const csvRows = dataToExport.map((item: any) => {
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
      alert('No data rows to export');
      return;
    }

    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `data-dictionary-${new Date().toISOString().split('T')[0]}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const handleRetry = () => {

    setResponse('');
    setFullResult(null);
    setResultRows([]);
    setShowDebugView(false);
    resetStream();
    hasSentRef.current = false;

    const message = 'Create data dictionary';
    sendDataDictionaryMessage(message);
  };

  // Handle scroll for floating button
  useEffect(() => {
    const handleScroll = () => {
      setIsScrolled(window.scrollY > 100);
    };
    window.addEventListener('scroll', handleScroll);
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  return (
    <div className="data-dictionary-streaming-view space-y-6">
      {/* Response Card */}
      <div className="data-dictionary-content">
        <div className="p-4 border-b border-gray-300 bg-white rounded-t-lg">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-2">
              <div
                className={`w-3 h-3 rounded-full ${
                  isStreaming ? 'bg-yellow-500 animate-pulse' : error ? 'bg-red-500' : 'bg-green-500'
                }`}
              ></div>
              <h3 className="font-semibold text-gray-800">
                {isStreaming ? 'Processing...' : error ? 'Data Dictionary Error' : 'Data Dictionary'}
              </h3>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={handleRetry}
                className="bg-brand-darkblue hover:bg-brand-blue text-white px-3 py-1 rounded text-sm font-medium transition-colors cursor-pointer"
              >
                Retry
              </button>
              {/* <button
                onClick={() => setChatOpen(true)}
                className="text-sm text-gray-600 hover:text-gray-900 flex items-center space-x-1"
              >
                <MessageCircle className="w-5 h-5" />
                <span>Chat</span>
              </button> */}
              {response && !isStreaming && !error && (
                  <button
                    onClick={handleDownloadCSV}
                    className="px-3 py-1 text-sm bg-brand-primary hover:bg-brand-primary-hover text-white rounded-md flex items-center gap-1 cursor-pointer"
                  >
                    <Download className="w-4 h-4" />
                    Download CSV
                  </button>
              )}
            </div>
          </div>
        </div>

        <div className="p-6">
          {/* Status Info - Minimal & Professional */}
          {isStreaming && (
            <div className="mb-4 bg-gray-50 border border-gray-200 rounded p-2">
              <p className="text-xs text-gray-600">
                Progress: {progress.toFixed(0)}%
                {currentBatch > 0 && totalBatches > 0 && ` • Batch ${currentBatch}/${totalBatches}`}
                {statusMessage && ` • ${statusMessage}`}
              </p>
            </div>
          )}

          {/* Streaming Progress - Enhanced 5-Phase UI */}
          {isStreaming && (
            <div className="space-y-5">
              {/* Current Phase Header */}
              <div className="bg-gradient-to-r from-brand-surface to-teal-50 border border-teal-200 rounded-lg p-4">
                <div className="flex items-start gap-3">
                  <span className="text-2xl">
                    {getCurrentPhase(progress, isAnalyzing, currentBatch, totalBatches).emoji}
                  </span>
                  <div className="flex-1">
                    <div className="flex items-center justify-between mb-1">
                      <h4 className="text-sm font-semibold text-gray-900">
                        Phase {getCurrentPhase(progress, isAnalyzing, currentBatch, totalBatches).id}: {getCurrentPhase(progress, isAnalyzing, currentBatch, totalBatches).name}
                      </h4>
                      <span className="text-xs font-medium text-font-blue bg-teal-100 px-2 py-1 rounded">
                        {progress.toFixed(1)}%
                      </span>
                    </div>
                    <p className="text-xs text-gray-600 mb-3">
                      {getCurrentPhase(progress, isAnalyzing, currentBatch, totalBatches).description}
                    </p>

                    {/* Batch progress indicator */}
                    {currentBatch > 0 && totalBatches > 0 && (
                      <div className="bg-teal-100 border border-teal-300 rounded px-2 py-1.5 mb-2">
                        <p className="text-xs text-font-blue font-medium">
                          Processing batch {currentBatch} of {totalBatches}
                        </p>
                      </div>
                    )}

                    {/* Status message */}
                    {statusMessage && (
                      <div className="bg-white/80 rounded px-2 py-1.5 border border-gray-200">
                        <p className="text-xs text-gray-700">
                          {statusMessage}
                        </p>
                      </div>
                    )}
                  </div>
                </div>
              </div>

              {/* LLM Token Streaming Display */}
              {isAnalyzing && llmAnalysis && (
                <div className="bg-brand-surface border border-teal-200 rounded-lg p-4">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-lg"></span>
                    <h5 className="text-sm font-semibold text-brand-darkblue">
                      Business Description Generation Preview
                    </h5>
                    <div className="flex-1 flex justify-end">
                      <div className="animate-pulse flex gap-1">
                        <div className="w-2 h-2 bg-brand-primary rounded-full"></div>
                        <div className="w-2 h-2 bg-brand-primary rounded-full animation-delay-200"></div>
                        <div className="w-2 h-2 bg-brand-primary rounded-full animation-delay-400"></div>
                      </div>
                    </div>
                  </div>
                  <div className="bg-white rounded px-3 py-2 max-h-48 overflow-y-auto">
                    <div className="text-xs text-gray-800 prose max-w-none">
                      <Markdown remarkPlugins={[remarkGfm]}>
                        {llmAnalysis}
                      </Markdown>
                    </div>
                  </div>
                  <p className="text-xs text-font-blue mt-2">
                    Streaming business descriptions in real-time from Gemini...
                  </p>
                </div>
              )}

              {/* Progress Bar */}
              <div className="space-y-2">
                <div className="w-full bg-gray-200 rounded-full h-3 overflow-hidden">
                  <div
                    className="bg-gradient-to-r from-brand-primary to-brand-primary-hover h-3 rounded-full transition-all duration-500 ease-out relative"
                    style={{ width: `${progress}%` }}
                  >
                    <div className="absolute inset-0 bg-white/20 animate-pulse"></div>
                  </div>
                </div>
              </div>

              {/* Phase Indicators - 5-PHASE SYSTEM */}
              <div className="flex items-center justify-between gap-2 px-1">
                {allPhases.map((phase, index) => {
                  let isCompleted = false;
                  let isActive = false;

                  if (phase.threshold === 0) {
                    // Tool Execution (0-95%)
                    isCompleted = progress >= 95;
                    isActive = progress < 95 && !isCompleted;
                  } else if (phase.threshold === 95) {
                    // Tool Complete (95%)
                    isCompleted = progress > 95;
                    isActive = progress === 95;
                  } else if (phase.threshold === 95.1) {
                    // Batched Generation (95-99.5%)
                    isCompleted = progress >= 99.5;
                    isActive = isAnalyzing && progress > 95 && progress < 99.5;
                  } else if (phase.threshold === 99.5) {
                    // Finalizing (99.5-100%)
                    isCompleted = progress === 100;
                    isActive = progress >= 99.5 && progress < 100;
                  } else if (phase.threshold === 100) {
                    // Complete (100%)
                    isCompleted = progress === 100 && !isAnalyzing;
                    isActive = false;
                  }

                  return (
                    <div key={phase.id} className="flex flex-col items-center flex-1">
                      <div className="flex items-center w-full mb-2">
                        {index > 0 && (
                          <div className={`flex-1 h-0.5 ${isCompleted || isActive ? 'bg-brand-primary' : 'bg-gray-300'} transition-colors duration-300`}></div>
                        )}
                        <div
                          className={`w-4 h-4 rounded-full flex items-center justify-center transition-all duration-300 ${isCompleted
                              ? 'bg-green-500 ring-2 ring-green-200'
                              : isActive
                                ? 'bg-brand-primary ring-2 ring-teal-200 animate-pulse'
                                : 'bg-gray-300'
                            }`}
                          title={phase.name}
                        >
                          <span className="text-[8px]"></span>
                        </div>
                        {index < allPhases.length - 1 && (
                          <div className={`flex-1 h-0.5 ${isCompleted ? 'bg-brand-primary' : 'bg-gray-300'} transition-colors duration-300`}></div>
                        )}
                      </div>
                      <span className={`text-[10px] text-center leading-tight ${isActive ? 'text-font-blue font-semibold' : isCompleted ? 'text-green-600' : 'text-gray-500'
                        }`}>
                        {phase.name.split(' ').map((word, i) => (
                          <span key={i} className="block">{word}</span>
                        ))}
                      </span>
                    </div>
                  );
                })}
              </div>

              {/* Processing Info */}
              <div className="flex items-center justify-center gap-2 text-xs text-gray-500">
                <div className="animate-spin rounded-full h-3 w-3 border-b-2 border-brand-primary"></div>
                <span>Generating data dictionary...</span>
              </div>
            </div>
          )}

          {/* Error Display */}
          {error && !isStreaming && (
            <div className="flex items-start space-x-3 py-4">
              <AlertCircle className="w-6 h-6 text-red-500 mt-1 flex-shrink-0" />
              <div>
                <p className="text-red-800 font-medium mb-1">Generation Failed</p>
                <p className="text-red-600 text-sm">{error}</p>
              </div>
            </div>
          )}

          {/* Results Display */}
          {(response || resultRows.length > 0 || (modifiedResponse && modifiedResponse.length > 0)) && !isStreaming && !error && (
            <div className="space-y-6">
              {resultRows.length > 0 && (
                <div className="bg-white rounded-lg border border-gray-200">
                  <div className="px-5 py-3 border-b border-gray-200">
                    <h3 className="text-base font-semibold text-gray-800">Data Dictionary</h3>
                  </div>
                  <div className="p-5 overflow-x-auto">
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
                        {resultRows.map((item, index) => {
                          const mostOccStr = formatMostOccurrences(
                            item.most_occurrences ?? item['Most Occurrences']
                          );
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
                  <div className="px-5 py-3 border-t border-gray-100 bg-gray-50">
                    <p className="text-xs text-gray-500">
                      Generated at {new Date().toLocaleString()}
                    </p>
                  </div>
                </div>
              )}

              {/* Summary Section - Clean Card Layout */}
              {(() => {
                const currentResponse = (modifiedResponse && modifiedResponse.length > 0) 
                  ? modifiedResponse[0].text_response 
                  : response;
                
                // Parse summary from response
                const summaryMatch = currentResponse?.match(/\*\*Summary:\*\*\s*([\s\S]*?)(?=\*\*Data Type Distribution|\n\n##|$)/i);
                const dataTypeMatch = currentResponse?.match(/\*\*Data Type Distribution:\*\*\s*([\s\S]*?)(?=\n\n##|$)/i);
                const tableMatch = currentResponse?.match(/(##\s*Data Dictionary[\s\S]*)/i);

                return (
                  <>
                    {(summaryMatch || dataTypeMatch) && (
                      <div className="bg-gradient-to-br from-brand-surface to-teal-50 border border-teal-200 rounded-lg p-5">
                        <div className="flex items-center gap-2 mb-4">
                          <FileText className="w-5 h-5 text-font-blue" />
                          <h3 className="text-lg font-semibold text-gray-800">Summary</h3>
                        </div>

                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                          {/* Summary Stats */}
                          {summaryMatch && (
                            <div className="bg-white rounded-lg p-4 shadow-sm">
                              <h4 className="text-sm font-medium text-gray-600 mb-3">Overview</h4>
                              <div className="space-y-2">
                                {summaryMatch[1].split('\n').filter((line: string) => line.trim().startsWith('-')).map((line: string, idx: number) => {
                                  const parts = line.replace(/^-\s*/, '').split(':');
                                  return (
                                    <div key={idx} className="flex justify-between items-center">
                                      <span className="text-sm text-gray-600">{parts[0]?.trim()}</span>
                                      <span className="text-sm font-semibold text-gray-900">{parts[1]?.trim()}</span>
                                    </div>
                                  );
                                })}
                              </div>
                            </div>
                          )}

                          {/* Data Type Distribution */}
                          {dataTypeMatch && (
                            <div className="bg-white rounded-lg p-4 shadow-sm">
                              <h4 className="text-sm font-medium text-gray-600 mb-3">Data Type Distribution</h4>
                              <div className="space-y-2">
                                {dataTypeMatch[1].split('\n').filter((line: string) => line.trim().startsWith('-')).map((line: string, idx: number) => {
                                  const parts = line.replace(/^-\s*/, '').split(':');
                                  const type = parts[0]?.trim();
                                  const count = parts[1]?.trim();
                                  const colors: { [key: string]: string } = {
                                    'STRING': 'bg-teal-100 text-font-blue',
                                    'DATE': 'bg-green-100 text-green-700',
                                    'INTEGER': 'bg-teal-100 text-font-blue',
                                    'FLOAT': 'bg-teal-100 text-orange-700',
                                    'BOOLEAN': 'bg-pink-100 text-pink-700'
                                  };
                                  const colorClass = colors[type as string] || 'bg-gray-100 text-gray-700';
                                  return (
                                    <div key={idx} className="flex justify-between items-center">
                                      <span className={`text-xs font-medium px-2 py-1 rounded ${colorClass}`}>{type}</span>
                                      <span className="text-sm font-semibold text-gray-900">{count}</span>
                                    </div>
                                  );
                                })}
                              </div>
                            </div>
                          )}
                        </div>
                      </div>
                    )}

                    {/* Data Dictionary Table (markdown fallback when rows not hydrated yet) */}
                    {tableMatch && resultRows.length === 0 && (
                      <div className="bg-white rounded-lg border border-gray-200">
                        <div className="px-5 py-3 border-b border-gray-200 flex items-center justify-between">
                          <h3 className="text-base font-semibold text-gray-800">Data Dictionary</h3>
                          {(fullResult || (modifiedResponse && modifiedResponse.length > 0)) && (
                            <>
                              {modifiedResponse && modifiedResponse.length > 0 && (
                                <span className="text-xs bg-green-100 text-green-700 px-2 py-1 rounded mr-2">
                                  Modified
                                </span>
                              )}
                              <button
                                onClick={() => setShowDebugView(!showDebugView)}
                                className="text-xs text-gray-500 hover:text-gray-700 underline transition-colors"
                              >
                                {showDebugView ? 'Hide technical details' : 'View technical details'}
                              </button>
                            </>
                          )}
                        </div>
                        <div className="p-5">
                          <div className="prose max-w-none text-gray-800">
                            <Markdown
                              remarkPlugins={[remarkGfm]}
                              components={{ table: TableWithExport }}
                            >
                              {tableMatch[1]}
                            </Markdown>
                          </div>
                        </div>
                        <div className="px-5 py-3 border-t border-gray-100 bg-gray-50">
                          <p className="text-xs text-gray-500">
                            Generated at {new Date().toLocaleString()}
                          </p>
                        </div>
                      </div>
                    )}

                    {!tableMatch && resultRows.length === 0 && response && (
                      <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
                        Data dictionary generation finished, but no field rows were returned. Click Retry to regenerate.
                      </div>
                    )}
                  </>
                );
              })()}

              {/* Technical Details - Minimal */}
              {(fullResult || (modifiedResponse && modifiedResponse.length > 0)) && showDebugView && (
                <div className="border-t border-gray-200 pt-4 bg-gray-50 rounded-lg p-4">
                  <div className="space-y-3">
                    {/* API Info */}
                    <div>
                      <p className="text-xs text-gray-600 mb-2">
                        <strong>Endpoint:</strong> /api/send-large-data-dict<br />
                        <strong>Agent:</strong> large_data_root_agent<br />
                        <strong>Tool:</strong> {(modifiedResponse && modifiedResponse.length > 0 
                          ? modifiedResponse[0].tool_response?.source 
                          : fullResult?.tool_response?.source) === 'vendor_upload' ? 'extract_and_map_vendor_dd' : 'batched_data_dictionary_tool'}<br />
                        <strong>Fields:</strong> {(modifiedResponse && modifiedResponse.length > 0 
                          ? modifiedResponse[0].tool_response?.total_fields 
                          : fullResult?.tool_response?.total_fields) || 0}
                        {modifiedResponse && modifiedResponse.length > 0 && (
                          <>
                            <br />
                            <strong>Status:</strong> Modified via Chat Interface
                          </>
                        )}
                      </p>
                    </div>

                    {/* Tool Response - Compact */}
                    <div>
                      <div className="flex items-center justify-between mb-1">
                        <h4 className="text-xs font-medium text-gray-700">Response Data</h4>
                        <button
                          onClick={() => navigator.clipboard.writeText(
                            JSON.stringify(
                              modifiedResponse && modifiedResponse.length > 0 
                                ? modifiedResponse[0].tool_response 
                                : fullResult?.tool_response, 
                              null, 
                              2
                            )
                          )}
                          className="text-xs text-font-blue hover:text-font-blue"
                        >
                          Copy JSON
                        </button>
                      </div>
                      <pre className="bg-white border border-gray-200 rounded p-2 text-xs overflow-x-auto max-h-64">
                        {JSON.stringify(
                          modifiedResponse && modifiedResponse.length > 0 
                            ? modifiedResponse[0].tool_response 
                            : fullResult?.tool_response, 
                          null, 
                          2
                        )}
                      </pre>
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Waiting State */}
          {!isStreaming && !error && !response && !(modifiedResponse && modifiedResponse.length > 0) && (
            <div className="text-center py-8 text-gray-500">
              <FileSearch className="w-12 h-12 mx-auto mb-3 text-gray-300" />
              <p>Waiting for profiling and relationship data...</p>
            </div>
          )}
        </div>
      </div>

      {/* Interactive Chat for DD Modifications - Only show after DD is generated */}
      {(response || (modifiedResponse && modifiedResponse.length > 0)) && !isStreaming && !error && (
        <DataDictionaryChat
          onDataDictionaryUpdate={(updatedDD) => {

            // Update all related states to trigger re-render
            if (updatedDD?.text_response) {
              setResponse(updatedDD.text_response);
              setDataDictionaryResponse(updatedDD.text_response);
            }
            if (updatedDD?.tool_response?.result) {
              setDataDictionaryJson([updatedDD.tool_response.result]);
            }

            setFullResult(updatedDD);
          }}
        />
      )}

      <ChatPopup
        isOpen={chatOpen}
        onClose={() => setChatOpen(false)}
        currentStep="Data Dictionary"
      />

      {/* Floating Retry Button */}
      {isScrolled && response && !isStreaming && !error && (
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

export default StreamingDataDictionaryView;
