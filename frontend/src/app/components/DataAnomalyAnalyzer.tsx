import React, { useState, useEffect, useRef } from 'react';
import { AlertCircle, Loader2 } from 'lucide-react';
import axiosInstance from '../utils/axios-interceptor';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import TableWithExport from './TableWithExport';
import ChatPopup from './ChatPopup';
import { useLocation } from 'react-router-dom';

function getStoredSession() {
  const sessionId = sessionStorage.getItem("session_id");
  const appName = sessionStorage.getItem("app_name");
  const userId = sessionStorage.getItem("user_id");
  return { sessionId, appName, userId };
}

// Helper function to safely round percentage values to the nearest integer
const roundPercentage = (value: any): string | number => {
  if (value === null || value === undefined || value === '') return '';
  const num = Number(value);
  return isNaN(num) ? value : Math.round(num);
};

const DataAnomalyAnalyzer: React.FC<{ setAnomalyData: any; anomalyData: any; onLoadingChange?: (isLoading: boolean) => void; hasApiBeenCalled?: boolean; markApiCalled?: () => void; modifiedResponse?: any }> = ({ setAnomalyData, anomalyData, onLoadingChange, hasApiBeenCalled, markApiCalled, modifiedResponse }) => {
  const [isLoading, setIsLoading] = useState(false);
  const [analysisResult, setAnalysisResult] = useState(anomalyData ? anomalyData.text_response : '');
  const [error, setError] = useState<string | null>(null);
  const [chatOpen, setChatOpen] = useState(false);
  const [anomalyToolResponse, setAnomalyToolResponse] = useState<any>(null);
  const [isScrolled, setIsScrolled] = useState(false);
  const hasCalledRef = useRef(false);

  const location = useLocation();
  const profilingData = location.state?.data;
  const getSeverityClass = (severity: string) => {
    if (severity === 'high') return 'bg-red-100 text-red-800';
    if (severity === 'medium') return 'bg-yellow-100 text-yellow-800';
    return 'bg-green-100 text-green-800';
  };

  const callAnomalyAnalysis = async (message?: string) => {
    const tableNames =
      profilingData?.successful_uploads
        ?.map((f: any) => f.table_name?.split(".").pop())
        .filter(Boolean) || [];

    const defaultMessage =
      tableNames.length > 0
        ? `use profiling agent to do [Data Anomaly Analysis] on the following tables: ${tableNames.join(", ")}`
        : `use profiling agent to do [Data Anomaly Analysis]`;

    const finalMessage = message || defaultMessage;

    setIsLoading(true);
    onLoadingChange?.(true);
    setError(null);
    setAnalysisResult(null);

    try {
      const { sessionId, appName, userId } = getStoredSession();
      const requestData = {
        appName,
        sessionId,
        userId,
        newMessage: {
          parts: [{ text: finalMessage }],
          role: "user"
        },
        streaming: false
      };
      
      const response = await axiosInstance.post('/messages/send', requestData);

      const data = response.data;

      let analysisText = '';
      if (data && Array.isArray(data)) {
        const analysisResponse = data[0].text_response;
        if (analysisResponse) analysisText = analysisResponse;

        const functionResponse = data[0];
        if (functionResponse) {
          setAnomalyData(functionResponse);
          if (functionResponse.tool_response?.data_anomaly_analysis_tool_response) {
            setAnomalyToolResponse(functionResponse.tool_response.data_anomaly_analysis_tool_response);
          }
        }
      }

      setAnalysisResult(analysisText || 'Analysis completed but no text content found.');
    } catch (err: any) {
      console.error('Error calling anomaly analysis:', err);
      setError(`Failed to perform analysis: ${err.message}`);
    } finally {
      setIsLoading(false);
      onLoadingChange?.(false);
    }
  };

  // Handle modified response from chat modal
  useEffect(() => {
    if (modifiedResponse && Array.isArray(modifiedResponse) && modifiedResponse.length > 0) {
      const responseText = modifiedResponse[0].text_response || JSON.stringify(modifiedResponse[0]);
      setAnalysisResult(responseText);
      setAnomalyData(modifiedResponse[0]);
      if (modifiedResponse[0].tool_response?.data_anomaly_analysis_tool_response) {
        setAnomalyToolResponse(modifiedResponse[0].tool_response.data_anomaly_analysis_tool_response);
      }
      hasCalledRef.current = true;
    }
  }, [modifiedResponse]);

  // Initialize with existing data if available
  useEffect(() => {
    // Skip initialization if we have a modified response
    if (modifiedResponse && Array.isArray(modifiedResponse) && modifiedResponse.length > 0) {
      return;
    }
    
    if (anomalyData && !analysisResult) {
      setAnalysisResult(anomalyData.text_response || '');
      if (anomalyData.tool_response?.data_anomaly_analysis_tool_response) {
        setAnomalyToolResponse(anomalyData.tool_response.data_anomaly_analysis_tool_response);
      }
      hasCalledRef.current = true;
    }
  }, [anomalyData, modifiedResponse]);

  // Auto-call analysis only if no existing data and hasn't been called before
  useEffect(() => {
    // Skip auto-call if we have a modified response
    if (modifiedResponse && Array.isArray(modifiedResponse) && modifiedResponse.length > 0) {
      return;
    }
    
    if (!analysisResult && !hasCalledRef.current && !anomalyData && !hasApiBeenCalled) {
      hasCalledRef.current = true;
      markApiCalled?.();
      callAnomalyAnalysis();
    }
  }, [analysisResult, anomalyData, hasApiBeenCalled, modifiedResponse]);

  // Handle scroll for floating button
  useEffect(() => {
    const handleScroll = () => {
      setIsScrolled(window.scrollY > 100);
    };
    window.addEventListener('scroll', handleScroll);
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  const handleRetry = () => {
    setError(null);
    setAnalysisResult('');
    setAnomalyData(null);
    setAnomalyToolResponse(null);
    hasCalledRef.current = false;
    callAnomalyAnalysis();
  };

  return (
    <div className="max-w-6xl mx-auto p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-base font-bold text-brand-darkblue flex items-center gap-2">
          Data Anomaly Analysis
        </h2>
        <button
          onClick={handleRetry}
          className="bg-brand-darkblue hover:bg-brand-blue text-white px-3 py-1 rounded text-sm font-medium transition-colors cursor-pointer"
        >
          Retry
        </button>
      </div>

      {/* Loading State */}
      {isLoading && (
        <div className="flex items-center justify-center py-8">
          <Loader2 className="w-8 h-8 animate-spin text-font-blue mr-3" />
          <div className="text-center">
            <p className="text-gray-600 mb-1">Generating data anomaly...</p>
            <p className="text-sm text-gray-500">This may take a few moments</p>
          </div>
        </div>
      )}

      {/* Analysis Result */}
      <div>
        <Markdown remarkPlugins={[remarkGfm]} components={{ table: TableWithExport }}>
          {analysisResult}
        </Markdown>
      </div>

      {/* Data Anomaly Analysis Tool Response */}
      {anomalyToolResponse && (
        <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4">
          <h4 className="font-medium text-yellow-800 mb-4">Data Anomaly Analysis Results</h4>
          
          {/* Summary Statistics */}
          {anomalyToolResponse.summary_statistics && (
            <div className="mb-4">
              <h5 className="font-medium text-yellow-700 mb-2">Summary Statistics</h5>
              <div className="grid grid-cols-2 gap-4 text-sm">
                <div>
                  <p><strong>Total Tables:</strong> {anomalyToolResponse.summary_statistics.total_tables_analyzed}</p>
                  <p><strong>Total Anomalies:</strong> {anomalyToolResponse.summary_statistics.total_anomalies}</p>
                  <p><strong>Quality Score:</strong> {anomalyToolResponse.summary_statistics.overall_data_quality_score}</p>
                </div>
                <div>
                  <p><strong>Severity Distribution:</strong></p>
                  <ul className="ml-4">
                    <li>High: {anomalyToolResponse.summary_statistics.severity_distribution.high}</li>
                    <li>Medium: {anomalyToolResponse.summary_statistics.severity_distribution.medium}</li>
                    <li>Low: {anomalyToolResponse.summary_statistics.severity_distribution.low}</li>
                  </ul>
                </div>
              </div>
            </div>
          )}

          {/* Table Anomaly Reports */}
          {anomalyToolResponse.table_anomaly_reports && (
            <div>
              <h5 className="font-medium text-yellow-700 mb-2">Table Anomaly Reports</h5>
              {Object.entries(anomalyToolResponse.table_anomaly_reports).map(([tableName, tableData]: [string, any]) => (
                <div key={tableName} className="mb-4 bg-white border border-yellow-300 rounded p-3">
                  <h6 className="font-medium text-gray-800 mb-2">{tableData.table_name}</h6>
                  
                  {/* Table Level Anomalies */}
                  {tableData.table_level_anomalies && tableData.table_level_anomalies.length > 0 && (
                    <div className="mb-3">
                      <p className="font-medium text-sm mb-2">Table Level Anomalies:</p>
                      <div className="overflow-x-auto">
                        <table className="min-w-full text-xs border border-gray-300">
                          <thead className="bg-gray-100">
                            <tr>
                              <th className="border border-gray-300 px-2 py-1 text-left">Issue</th>
                              <th className="border border-gray-300 px-2 py-1 text-left">Severity</th>
                              <th className="border border-gray-300 px-2 py-1 text-left">Anomaly Type</th>
                              <th className="border border-gray-300 px-2 py-1 text-left">Affected Columns</th>
                              <th className="border border-gray-300 px-2 py-1 text-left">Recommendation</th>
                            </tr>
                          </thead>
                          <tbody>
                            {tableData.table_level_anomalies.map((anomaly: any, index: number) => (
                              <tr key={anomaly.issue || `anomaly-${index}`}>
                                <td className="border border-gray-300 px-2 py-1">{anomaly.issue}</td>
                                <td className="border border-gray-300 px-2 py-1">
                                  <span className={`px-2 py-1 rounded text-xs ${getSeverityClass(anomaly.severity)}`}>
                                    {anomaly.severity}
                                  </span>
                                </td>
                                <td className="border border-gray-300 px-2 py-1">{anomaly.anomaly_type}</td>
                                <td className="border border-gray-300 px-2 py-1">
                                  {anomaly.affected_columns?.map((col: any, i: number) => (
                                    <div key={col.column || `col-${i}`} className="text-xs">
                                      {col.column} ({col.data_type}, {roundPercentage(col.null_percentage)}% null)
                                    </div>
                                  ))}
                                </td>
                                <td className="border border-gray-300 px-2 py-1">{anomaly.recommendation}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}

                  {/* Column Anomalies */}
                  {tableData.column_anomalies && Object.keys(tableData.column_anomalies).length > 0 && (
                    <div>
                      <p className="font-medium text-sm mb-2">Column Anomalies:</p>
                      {Object.entries(tableData.column_anomalies).map(([columnName, anomalies]: [string, any]) => (
                        <div key={columnName} className="mb-2">
                          <p className="font-medium text-xs text-gray-700">{columnName}:</p>
                          <div className="overflow-x-auto">
                            <table className="min-w-full text-xs border border-gray-300 mt-1">
                              <thead className="bg-gray-50">
                                <tr>
                                  <th className="border border-gray-300 px-2 py-1 text-left">Issue</th>
                                  <th className="border border-gray-300 px-2 py-1 text-left">Severity</th>
                                  <th className="border border-gray-300 px-2 py-1 text-left">Type</th>
                                  <th className="border border-gray-300 px-2 py-1 text-left">Details</th>
                                  <th className="border border-gray-300 px-2 py-1 text-left">Recommendation</th>
                                </tr>
                              </thead>
                              <tbody>
                                {anomalies.map((anomaly: any, index: number) => (
                                  <tr key={anomaly.issue || `col-anomaly-${index}`}>
                                    <td className="border border-gray-300 px-2 py-1">{anomaly.issue}</td>
                                    <td className="border border-gray-300 px-2 py-1">
                                      <span className={`px-1 py-0.5 rounded text-xs ${getSeverityClass(anomaly.severity)}`}>
                                        {anomaly.severity}
                                      </span>
                                    </td>
                                    <td className="border border-gray-300 px-2 py-1">{anomaly.anomaly_type}</td>
                                    <td className="border border-gray-300 px-2 py-1">
                                      {anomaly.case_patterns && (
                                        <div>
                                          {anomaly.case_patterns.map((pattern: any, i: number) => (
                                            <div key={pattern.pattern || `pattern-${i}`} className="text-xs mb-1">
                                              <strong>{pattern.pattern}:</strong> {pattern.count} ({roundPercentage(pattern.percentage)}%)
                                              {pattern.examples && (
                                                <div className="text-gray-600 ml-2">
                                                  Examples: {pattern.examples.slice(0, 2).join(', ')}
                                                </div>
                                              )}
                                            </div>
                                          ))}
                                        </div>
                                      )}
                                    </td>
                                    <td className="border border-gray-300 px-2 py-1">{anomaly.recommendation}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Chat Popup */}
      <ChatPopup
        isOpen={chatOpen}
        onClose={() => setChatOpen(false)}
        currentStep="Data Anomaly Analysis"
      />

      {/* Error Display */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4">
          <div className="flex items-start gap-3">
            <AlertCircle className="h-5 w-5 text-red-600 mt-0.5" />
            <div>
              <h3 className="text-red-800 font-medium">Analysis Error</h3>
              <p className="text-red-700 text-sm mt-1">{error}</p>
            </div>
          </div>
        </div>
      )}

      {/* Floating Retry Button */}
      {isScrolled && analysisResult && !isLoading && (
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

export default DataAnomalyAnalyzer;