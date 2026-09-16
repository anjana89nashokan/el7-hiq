import React, { useState, useEffect, useRef } from 'react';
import { Loader2, FileSearch, AlertCircle } from 'lucide-react';
import axiosInstance from '../utils/axios-interceptor';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm'
import TableWithExport from './TableWithExport';
import ChatPopup from './ChatPopup';

interface ProfilingDisplayProps {
  profilingData: any;
  updateRelationshipAnalysisStatus?: (status: string) => void;
  setRelationshipAnalysisResponse: React.Dispatch<React.SetStateAction<string>>;
  prevRelationshipAnalysisResponse?: string;
  onLoadingChange?: (isLoading: boolean) => void;
  onRetry?: React.MutableRefObject<(() => void) | null>;
  hasApiBeenCalled: boolean;
  markApiCalled: () => void;
  modifiedResponse?: any;
}





// interface FunctionResponse {
//   name: string;
//   result: TableAnalysisResult[];
// }

// Define the type for a part within the content, which might contain a function response
// interface Part {
//   functionResponse?: FunctionResponse;
// }

// Define the type for the main content object
// interface Content {
//   parts: Part[];
// }

// Define the type for a single entry in the top-level array
// interface DataEntry {
//   content: Content;
// }


const RelationshipViewComponent: React.FC<ProfilingDisplayProps> = ({ profilingData, updateRelationshipAnalysisStatus, setRelationshipAnalysisResponse, prevRelationshipAnalysisResponse, onLoadingChange, onRetry, hasApiBeenCalled, markApiCalled, modifiedResponse }) => {
  const [isLoading, setIsLoading] = useState(false);
  const [response, setResponse] = useState<string>(prevRelationshipAnalysisResponse || '');
  const [error, setError] = useState<string>('');
  const [hasAnalyzed, setHasAnalyzed] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);
  const [isScrolled, setIsScrolled] = useState(false);

  const hasSentRef = useRef(false);


   function getStoredSession() {
    const sessionId = sessionStorage.getItem("session_id");
    const appName = sessionStorage.getItem("app_name");
    const userId = sessionStorage.getItem("user_id");
    return { sessionId, appName, userId };
  }

  // Mock API call - replace with your actual API endpoint
  const sendProfilingMessage = async (message: string) => {
    setIsLoading(true);
    onLoadingChange?.(true);
    setError('');

    try {
      // Replace this with your actual API call
      const response = await axiosInstance.post('/messages/send',
        {
          appName: getStoredSession().appName, // Replace with actual values
          sessionId: getStoredSession().sessionId,
          userId: getStoredSession().userId,

          newMessage: {
          parts: [
            {
              text:  message + (profilingData ? ` with the provided profiling data.` : ".")
            }
          ],
          role: "user"
        },

        streaming: false,
        stateDelta: {}

        },
        {
        headers: {
          'Content-Type': 'application/json',
        },
      });

      if (!response.status || response.status !== 200) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const data = await response.data;

      // Extract the response text based on your API structure
      let responseText = "Analysis completed successfully.";
      let toolResponse = null

      if (data && Array.isArray(data)) {
        
        
        const modelResponse = data[0].text_response
        toolResponse = data[0].tool_response
        //  data.find(
        //   (item) =>
        //     item.content?.role === "model" &&
        //     item.content?.parts?.[0]?.text
        // );
        if (toolResponse) {
          responseText = modelResponse //.content.parts[0].text;
          if (updateRelationshipAnalysisStatus) {
            updateRelationshipAnalysisStatus(toolResponse);
          }
        }
      } 
      else if (typeof data === "object"){
        const _data = [data]
        if (_data.length <= 1) {
          console.warn('API response data is insufficient:', _data);
        }
        const modelResponse = _data.find(
          (item) =>
            item.content?.role === "model" &&
            item.content?.parts?.[0]?.text
        );
        if (modelResponse) {
          responseText = modelResponse.content.parts[0].text;
          if (updateRelationshipAnalysisStatus) {
            updateRelationshipAnalysisStatus(responseText);
          }
        }
      }
      
      else if (data.text) {
        responseText = data.text;
      } else if (data.response) {
        responseText = data.response;
      }

      setResponse(responseText);
      setRelationshipAnalysisResponse(responseText);
      setHasAnalyzed(true);
    } catch (err) {
      console.error('Error sending profiling message:', err);
      setError(err instanceof Error ? err.message : 'Failed to analyze profiling data');
    } finally {
      setIsLoading(false);
      onLoadingChange?.(false);
    }
  };

  // Handle modified response from chat modal
  useEffect(() => {
    if (modifiedResponse && Array.isArray(modifiedResponse) && modifiedResponse.length > 0) {
      const responseText = modifiedResponse[0].text_response || JSON.stringify(modifiedResponse[0]);
      setResponse(responseText);
      setRelationshipAnalysisResponse(responseText);
      setHasAnalyzed(true);
    }
  }, [modifiedResponse]);

  // Initialize with existing response if available
  useEffect(() => {
    if (prevRelationshipAnalysisResponse && !response) {
      setResponse(prevRelationshipAnalysisResponse);
      setHasAnalyzed(true);
      hasSentRef.current = true;
    }
  }, [prevRelationshipAnalysisResponse]);

  // Auto-send message when profiling data is available and no existing response
  useEffect(() => {
    // Skip auto-send if we have a modified response
    if (modifiedResponse && Array.isArray(modifiedResponse) && modifiedResponse.length > 0) {
      return;
    }
    
    if (profilingData && !hasAnalyzed && !isLoading && !hasSentRef.current && !response && !prevRelationshipAnalysisResponse && !hasApiBeenCalled) {
      hasSentRef.current = true;
      markApiCalled();

      const fileNames =
        profilingData.successful_uploads?.map((f: any) => f.table_name) || [];

      const message =
        fileNames.length > 0
          ? `Use profiling agent to Analyze and the [Relationship] of the data for the following files: ${fileNames.join(", ")}`
          : "Use profiling agent to analyze the relationship of the data";

      sendProfilingMessage(message);
    }
  }, [profilingData, hasAnalyzed, isLoading, prevRelationshipAnalysisResponse, hasApiBeenCalled, modifiedResponse]);


  const handleRetry = () => {
    setHasAnalyzed(false);
    setError('');
    setResponse('');
    hasSentRef.current = false; // reset ref so useEffect or retry can trigger

    const fileNames =
      profilingData?.successful_uploads?.map((f: any) => f.table_name) || [];

    const message =
      fileNames.length > 0
        ? `Use profiling agent to Analyze and the [Relationship] of the data for the following files: ${fileNames.join(", ")}`
        : "Use profiling agent to analyze the relationship of the data";

    sendProfilingMessage(message);
  };

  // Expose handleRetry method to parent component via callback
  useEffect(() => {
    if (onRetry) {
      onRetry.current = handleRetry;
    }
  }, [onRetry]);

  // Handle scroll for floating button
  useEffect(() => {
    const handleScroll = () => {
      setIsScrolled(window.scrollY > 100);
    };
    window.addEventListener('scroll', handleScroll);
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);


  return (
    <div className="relationship-view-component space-y-6">
     

      {/* Response Card */}
      <div className="relationship-content">
        <div className="p-4 border-b border-gray-300 bg-white rounded-t-lg">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-2">
              <div className={`w-3 h-3 rounded-full ${
                isLoading ? 'bg-yellow-500 animate-pulse' : 
                error ? 'bg-red-500' : 'bg-green-500'
              }`}></div>
              <h3 className="font-semibold text-gray-800">
                {isLoading ? 'Processing...' : error ? 'Relationship Analysis Error' : 'Relationship Analysis Results'}
              </h3>
             
             
            </div>
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
          </div>
        </div>
        
        <div className="p-6">
          {isLoading && (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="w-8 h-8 animate-spin text-font-blue mr-3" />
              <div className="text-center">
                <p className="text-gray-600 mb-1">Analyzing profiling data...</p>
                <p className="text-sm text-gray-500">This may take a few moments</p>
              </div>
            </div>
          )}
          
          {error && (
            <div className="flex items-start space-x-3 py-4">
              <AlertCircle className="w-6 h-6 text-red-500 mt-1 flex-shrink-0" />
              <div>
                <p className="text-red-800 font-medium mb-1">Analysis Failed</p>
                <p className="text-red-600 text-sm">{error}</p>
              </div>
            </div>
          )}
          
          {response && !isLoading && !error && (
            <div className="prose max-w-none">
              <div className="text-gray-800 whitespace-pre-wrap leading-relaxed">
                <Markdown remarkPlugins={[remarkGfm]}  components={{table: TableWithExport,}}>  
                    {response}
                </Markdown>

              </div>
              <div className="mt-4 pt-4 border-t border-gray-100">
                <p className="text-xs text-gray-500">
                  Analysis completed at {new Date().toLocaleString()}
                </p>
              </div>
            </div>
          )}
          
          {!isLoading && !error && !response && !hasAnalyzed && (
            <div className="text-center py-8 text-gray-500">
              <FileSearch className="w-12 h-12 mx-auto mb-3 text-gray-300" />
              <p>Waiting for profiling data...</p>
            </div>
          )}
        </div>
      </div>

          {/* <LogViewer /> */}
      <ChatPopup isOpen={chatOpen} onClose={() => setChatOpen(false)} currentStep='Data Anomaly Analysis' />

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

export default RelationshipViewComponent;