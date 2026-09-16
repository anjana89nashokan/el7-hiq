/**
 * Custom React hook for Server-Sent Events (SSE) streaming
 * Handles /send-stream endpoint communication with progress tracking
 */

import { useState, useCallback } from 'react';
import { API_BASE_URL } from '../config/env';
import { apiFetch } from "../utils/apiFetch";

type FeatureType =
  | 'profiling'
  | 'relationship'
  | 'data_dictionary'
  | 'anomaly'
  | 'metadata'
  | 'similarity';

interface SSEStreamOptions {
  endpoint?: string;
  onProgress?: (progress: number, message: string) => void;
  onComplete?: (result: any) => void;
  onError?: (error: string) => void;
  featureType?: FeatureType;
}

interface PhaseEntry {
  message: string;
  progress?: number;
  timestamp: number;
}

interface StreamState {
  isStreaming: boolean;
  progress: number;
  statusMessage: string;
  error: string | null;
  result: any | null;
  toolResponse: any | null;        // NEW: Store tool output (profiling data)
  llmAnalysis: string;              // NEW: Accumulate LLM tokens
  isAnalyzing: boolean;             // NEW: LLM analysis phase indicator
  currentBatch: number;             // NEW: Current batch number for Data Dictionary
  totalBatches: number;             // NEW: Total batches for Data Dictionary
  featureType?: FeatureType;
  phaseHistory: PhaseEntry[];
  anomalyMetadata?: {
    tablesAnalyzed: number;
    severityDistribution?: Record<string, number>;
    processingMode?: string;
    batchDetails?: any[];
  };
}

export const useSSEStream = (options: SSEStreamOptions = {}) => {
  const {
    // endpoint = '/messages-strm/send-stream',  // OLD: Original streaming endpoint
    endpoint = '/messages-strm/send-stream-new',  // NEW: Enhanced endpoint with vendor DD validation, HITL, and dynamic tool support
    onProgress,
    onComplete,
    onError,
    featureType
  } = options;

  const [streamState, setStreamState] = useState<StreamState>({
    isStreaming: false,
    progress: 0,
    statusMessage: '',
    error: null,
    result: null,
    toolResponse: null,
    llmAnalysis: '',
    isAnalyzing: false,
    currentBatch: 0,
    totalBatches: 0,
    featureType,
    phaseHistory: [],
    anomalyMetadata: undefined
  });

  const startStream = useCallback(async (requestData: any) => {
    console.log('[SSE] Starting SSE stream...', {
      endpoint,
      requestData: requestData instanceof FormData ? 'FormData' : JSON.stringify(requestData).substring(0, 100) + '...'
    });

    setStreamState({
      isStreaming: true,
      progress: 0,
      statusMessage: 'Initializing...',
      error: null,
      result: null,
      toolResponse: null,
      llmAnalysis: '',
      isAnalyzing: false,
      currentBatch: 0,
      totalBatches: 0,
      featureType,
      phaseHistory: [],
      anomalyMetadata: undefined
    });

    try {
      // Construct full URL - backend is on port 8001
      const fullUrl = `${API_BASE_URL}${endpoint}`;

      console.log('[SSE] Connecting to:', fullUrl);

      const isFormData = requestData instanceof FormData;

      const response = await apiFetch(fullUrl, {
        method: 'POST',
        //credentials: 'include',
        headers: isFormData
          ? { 'Accept': 'text/event-stream' }
          : {
            'Content-Type': 'application/json',
            'Accept': 'text/event-stream'
          },
        body: isFormData ? requestData : JSON.stringify(requestData)
      });

      if (!response.ok) {
        console.error('[SSE] HTTP error:', response.status, response.statusText);
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      console.log('[SSE] Connection established, starting stream...');

      const reader = response.body?.getReader();
      if (!reader) {
        console.error('[SSE] Response body is not readable');
        throw new Error('Response body is not readable');
      }

      const decoder = new TextDecoder();
      console.log('[SSE] Stream reader initialized');

      const readStream = async () => {
        const appendPhaseHistory = (prev: StreamState, entry?: PhaseEntry) => {
          if (!entry || !entry.message) {
            return prev.phaseHistory;
          }
          const last = prev.phaseHistory[prev.phaseHistory.length - 1];
          if (last && last.message === entry.message) {
            return prev.phaseHistory;
          }
          return [...prev.phaseHistory.slice(-5), entry];
        };

        try {
          let chunkCount = 0;
          let buffer = ''; // Buffer for incomplete data across chunks

          while (true) {
            const { done, value } = await reader.read();

            if (done) {
              console.log('[SSE] Stream completed, total chunks received:', chunkCount);
              setStreamState(prev => ({ ...prev, isStreaming: false }));
              break;
            }

            chunkCount++;
            const chunk = decoder.decode(value, { stream: true });
            console.log(`[SSE] Chunk #${chunkCount} received (${chunk.length} bytes)`);

            // Add chunk to buffer
            buffer += chunk;

            // Process complete lines from buffer
            const lines = buffer.split('\n');

            // Keep the last incomplete line in buffer
            buffer = lines.pop() || '';

            for (const line of lines) {
              if (line.startsWith('event:')) {
                // Parse event type
                const eventType = line.substring(6).trim();
                console.log('[SSE] Event type:', eventType);

                // Read next line for data
                continue;
              }

              if (line.startsWith('data:')) {
                const dataStr = line.substring(5).trim();

                // Skip empty data lines
                if (!dataStr) {
                  continue;
                }

                try {
                  const data = JSON.parse(dataStr);
                  console.log('[SSE] Data received:', {
                    phase: data.phase,
                    progress: data.progress,
                    message: data.message?.substring(0, 50),
                    hasToken: !!data.token,
                    hasToolResponse: !!data.tool_response,
                    hasResult: !!data.result
                  });

                  // Update progress
                  if (data.progress !== undefined) {
                    console.log(`[SSE] Progress update: ${data.progress}%`);
                    setStreamState(prev => {
                      const next = {
                        ...prev,
                        progress: data.progress
                      };
                      if (featureType === 'anomaly' && data.message) {
                        next.phaseHistory = appendPhaseHistory(prev, {
                          message: data.message,
                          progress: data.progress,
                          timestamp: Date.now()
                        });
                      }
                      return next;
                    });

                    if (onProgress && data.message) {
                      onProgress(data.progress, data.message);
                    }
                  }

                  // Update status message
                  if (data.message) {
                    console.log('[SSE] Status message:', data.message);
                    setStreamState(prev => ({
                      ...prev,
                      statusMessage: data.message,
                      phaseHistory:
                        featureType === 'anomaly'
                          ? appendPhaseHistory(prev, {
                            message: data.message,
                            progress: data.progress,
                            timestamp: Date.now()
                          })
                          : prev.phaseHistory
                    }));
                  }

                  // NEW: Handle tool completion (95%)
                  if (data.tool_name && data.tool_response) {
                    console.log('[SSE] Tool complete event received:', {
                      tool_name: data.tool_name,
                      progress: data.progress,
                      message: data.message
                    });
                    setStreamState(prev => {
                      const next: StreamState = {
                        ...prev,
                        progress: data.progress || 95,
                        toolResponse: data.tool_response,
                        statusMessage: data.message || 'Tool complete. Analyzing results...'
                      };

                      if (featureType === 'anomaly') {
                        next.anomalyMetadata = {
                          tablesAnalyzed:
                            data.tool_response?.tables_analyzed ||
                            data.tool_response?.summary_statistics?.total_tables_analyzed ||
                            0,
                          severityDistribution: data.tool_response?.summary_statistics?.severity_distribution,
                          processingMode: data.tool_response?.processing_mode,
                          batchDetails: data.tool_response?.batch_details || []
                        };
                        next.phaseHistory = appendPhaseHistory(prev, {
                          message: data.message || 'Anomaly detection complete',
                          progress: data.progress || 95,
                          timestamp: Date.now()
                        });
                      }

                      return next;
                    });
                    continue;
                  }

                  // NEW: Handle LLM analysis start (96%)
                  if (data.progress >= 96 && data.message?.includes('analyzing')) {
                    console.log('[SSE] LLM analysis started:', {
                      progress: data.progress,
                      message: data.message
                    });
                    setStreamState(prev => ({
                      ...prev,
                      isAnalyzing: true,
                      progress: data.progress || 96,
                      statusMessage: data.message
                    }));
                    continue;
                  }

                  // NEW: Handle LLM token streaming (96-100%)
                  if (data.token !== undefined) {
                    console.log('[SSE] LLM token received:', {
                      token_preview: data.token.substring(0, 50) + '...',
                      cumulative_length: data.cumulative?.length || 'N/A',
                      progress: data.progress,
                      batch_num: data.batch_num,
                      total_batches: data.total_batches
                    });
                    setStreamState(prev => ({
                      ...prev,
                      llmAnalysis: data.cumulative || (prev.llmAnalysis + data.token),
                      progress: data.progress || prev.progress,
                      isAnalyzing: true,
                      currentBatch: data.batch_num || prev.currentBatch,
                      totalBatches: data.total_batches || prev.totalBatches,
                      phaseHistory:
                        featureType === 'anomaly'
                          ? appendPhaseHistory(prev, {
                            message: data.message || 'Streaming anomaly insights',
                            progress: data.progress,
                            timestamp: Date.now()
                          })
                          : prev.phaseHistory
                    }));
                    continue;
                  }

                  // NEW: Handle batch start (Data Dictionary specific)
                  if (data.batch_num !== undefined && data.total_batches !== undefined && data.columns_in_batch !== undefined) {
                    console.log('[SSE] Batch start event:', {
                      batch_num: data.batch_num,
                      total_batches: data.total_batches,
                      columns_in_batch: data.columns_in_batch,
                      progress: data.progress
                    });
                    setStreamState(prev => ({
                      ...prev,
                      currentBatch: data.batch_num,
                      totalBatches: data.total_batches,
                      progress: data.progress || prev.progress,
                      statusMessage: data.message || prev.statusMessage,
                      isAnalyzing: true
                    }));
                    continue;
                  }

                  // Handle completion
                  if (data.phase === 'complete') {
                    console.log('[SSE] Complete event received:', {
                      progress: 100,
                      hasResult: !!data.result,
                      resultKeys: data.result ? Object.keys(data.result) : []
                    });
                    setStreamState(prev => ({
                      ...prev,
                      isStreaming: false,
                      isAnalyzing: false,
                      progress: 100,
                      result: data.result,
                      statusMessage: 'Complete!',
                      phaseHistory:
                        featureType === 'anomaly'
                          ? appendPhaseHistory(prev, {
                            message: 'Anomaly analysis complete',
                            progress: 100,
                            timestamp: Date.now()
                          })
                          : prev.phaseHistory
                    }));

                    if (onComplete) {
                      console.log('[SSE] Calling onComplete callback');
                      onComplete(data.result);
                    }

                    console.log('[SSE] Stream finished successfully');
                    return; // Exit stream
                  }

                  // Handle errors
                  if (data.phase === 'error') {
                    const errorMsg = data.message || 'Unknown error occurred';
                    console.error('[SSE] Error event received:', {
                      error: errorMsg,
                      phase: data.phase
                    });
                    setStreamState(prev => ({
                      ...prev,
                      isStreaming: false,
                      isAnalyzing: false,
                      error: errorMsg
                    }));

                    if (onError) {
                      console.log('[SSE] Calling onError callback');
                      onError(errorMsg);
                    }

                    console.log('[SSE] Stream terminated with error');
                    return; // Exit stream
                  }
                } catch (parseError) {
                  // Log parse errors but don't break the stream
                  // This can happen when large responses are split across chunks
                  console.warn('[SSE] Error parsing SSE data (may be incomplete chunk):', parseError);
                  console.warn('[SSE] Problematic data preview:', dataStr.substring(0, 200) + '...');
                  console.warn('[SSE] Data length:', dataStr.length, 'bytes');
                  // Continue processing other lines
                }
              }
            }
          }
        } catch (readError) {
          console.error('[SSE] Stream reading error:', readError);
          const errorMsg = readError instanceof Error ? readError.message : 'Stream reading failed';

          setStreamState(prev => ({
            ...prev,
            isStreaming: false,
            error: errorMsg
          }));

          if (onError) {
            onError(errorMsg);
          }
        }
      };

      await readStream();

    } catch (error) {
      console.error('[SSE] Fatal stream error:', error);
      const errorMsg = error instanceof Error ? error.message : 'Failed to start stream';

      setStreamState(prev => ({
        ...prev,
        isStreaming: false,
        error: errorMsg
      }));

      if (onError) {
        console.log('[SSE] Calling onError callback (fatal)');
        onError(errorMsg);
      }

      console.log('[SSE] Stream terminated due to fatal error');
    }
  }, [endpoint, onProgress, onComplete, onError, featureType]);

  const resetStream = useCallback(() => {
    console.log('[SSE] Resetting stream state');
    setStreamState({
      isStreaming: false,
      progress: 0,
      statusMessage: '',
      error: null,
      result: null,
      toolResponse: null,
      llmAnalysis: '',
      isAnalyzing: false,
      currentBatch: 0,
      totalBatches: 0,
      featureType,
      phaseHistory: [],
      anomalyMetadata: undefined
    });
  }, [featureType]);

  return {
    ...streamState,
    startStream,
    resetStream
  };
};
 