import { FileSpreadsheet, ChevronDown, ChevronUp, ChevronRight } from "lucide-react";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "../Tabs";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import TableWithExport from "../TableWithExport";
import TableItemDisplay from "../TableItemDisplay";
import { getCurrentPhase, allPhases, getPhaseTextClass, getPhaseIndicatorClass, getPhaseState } from "../../utils/streamingPhaseHelpers";

interface StreamingDatasetOverviewStepProps {
  profilingData: any;
  activeTab: string;
  setActiveTab: (tab: string) => void;
  formatDate: (date: string) => string;
  profileSummaryCollapsed: boolean;
  setProfileSummaryCollapsed: (collapsed: boolean) => void;
  isLoadingResponse: boolean;
  profilingProgress: number;
  profilingStatus: string;
  isAnalyzing: boolean;
  llmAnalysis: string;
  initialMessageData: any[];
  accordionStates: { [key: string]: boolean };
  toggleAccordion: (key: string) => void;
  steps: any[];
  currentStep: number;
  onNext: () => void;
}

const PhaseNameWord = ({ word }: { word: string }) => <span className="block">{word}</span>;

const TableWithExportWrapper = ({ stepTitle, ...props }: any) => (
  <TableWithExport {...props} stepTitle={stepTitle} />
);

export default function StreamingDatasetOverviewStep({
  profilingData,
  activeTab,
  setActiveTab,
  formatDate,
  profileSummaryCollapsed,
  setProfileSummaryCollapsed,
  isLoadingResponse,
  profilingProgress,
  profilingStatus,
  isAnalyzing,
  llmAnalysis,
  initialMessageData,
  accordionStates,
  toggleAccordion,
  steps,
  currentStep,
  onNext,
}: StreamingDatasetOverviewStepProps) {
  return (
    <div className="space-y-6">
      <h2 className="text-xl text-brand-blue">Dataset Overview</h2>

      <Tabs value={activeTab} onValueChange={setActiveTab} className="mb-6">
        <TabsList className="gap-2 overflow-x-auto">
          {profilingData.successful_uploads.map((file: any) => (
            <TabsTrigger
              key={file.file_id}
              value={file.file_id}
              className={`flex items-center text-sm transition-colors cursor-pointer ${
                activeTab === file.file_id ? "text-brand-blue" : "hover:bg-gray-100"
              }`}
            >
              <FileSpreadsheet size={16} /> {file.filename}
            </TabsTrigger>
          ))}
        </TabsList>

        {profilingData.successful_uploads.map((file: any) => (
          <TabsContent
            key={file.file_id}
            value={file.file_id}
            className="flex-col flex-1 border border-gray-300 overflow-hidden bg-white rounded-b-lg"
          >
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm text-brand-charcoal p-4">
              <div className="space-y-2">
                <p><strong>Session ID:</strong> {file.sessionID}</p>
                <p><strong>User:</strong> {file.user}</p>
                <p><strong>Created:</strong> {formatDate(file.createdDate)}</p>
                <p><strong>Last Updated:</strong> {formatDate(file.lastUpdateDate)}</p>
              </div>
              <div className="space-y-2">
                <p><strong>Table:</strong> {file.table_name}</p>
                <p><strong>Dataset:</strong> {file.dataset_id}</p>
                <p><strong>Project:</strong> {file.project_id}</p>
                <p><strong>Rows Uploaded:</strong> {file.rows_uploaded.toLocaleString()}</p>
              </div>
            </div>
          </TabsContent>
        ))}
      </Tabs>

      <div className="bg-brand-blue/5 border border-brand-blue/50 rounded-lg mb-6 p-4">
        <button
          className="w-full cursor-pointer flex justify-between items-center bg-transparent border-none p-0 text-left"
          onClick={() => setProfileSummaryCollapsed(!profileSummaryCollapsed)}
        >
          <h3 className="text-md text-brand-blue">Profiling Summary</h3>
          {profileSummaryCollapsed ? <ChevronDown size={20} /> : <ChevronUp size={20} />}
        </button>

        {!profileSummaryCollapsed && (
          <div className="profile-summary pt-2">
            {isLoadingResponse ? (
              <div className="space-y-5">
                <div className="bg-gradient-to-r from-brand-surface to-teal-50 border border-teal-200 rounded-lg p-4">
                  <div className="flex items-start gap-3">
                    <div className="flex-1">
                      <div className="flex items-center justify-between mb-1">
                        <h4 className="text-sm font-semibold text-gray-900">
                          Phase {getCurrentPhase(profilingProgress, isAnalyzing).id}: {getCurrentPhase(profilingProgress, isAnalyzing).name}
                        </h4>
                        <span className="text-xs font-medium text-font-blue bg-teal-100 px-2 py-1 rounded">
                          {profilingProgress.toFixed(1)}%
                        </span>
                      </div>
                      <p className="text-xs text-gray-600 mb-3">
                        {getCurrentPhase(profilingProgress, isAnalyzing).description}
                      </p>
                      {profilingStatus && (
                        <div className="bg-white/80 rounded px-2 py-1.5 border border-gray-200">
                          <p className="text-xs text-gray-700">{profilingStatus}</p>
                        </div>
                      )}
                    </div>
                  </div>
                </div>

                {isAnalyzing && llmAnalysis && (
                  <div className="bg-brand-surface border border-teal-200 rounded-lg p-4">
                    <div className="flex items-center gap-2 mb-2">
                      <h5 className="text-sm font-semibold text-brand-darkblue">AI Analysis Preview</h5>
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
                        <Markdown remarkPlugins={[remarkGfm]}>{llmAnalysis}</Markdown>
                      </div>
                    </div>
                    <p className="text-xs text-font-blue mt-2">✨ Streaming tokens in real-time from Gemini...</p>
                  </div>
                )}

                <div className="space-y-2">
                  <div className="w-full bg-gray-200 rounded-full h-3 overflow-hidden">
                    <div
                      className="bg-gradient-to-r from-brand-primary to-brand-primary-hover h-3 rounded-full transition-all duration-500 ease-out relative"
                      style={{ width: `${profilingProgress}%` }}
                    >
                      <div className="absolute inset-0 bg-white/20 animate-pulse"></div>
                    </div>
                  </div>
                </div>

                <div className="flex items-center justify-between gap-2 px-1">
                  {allPhases.map((phase, index) => {
                    const { isCompleted, isActive } = getPhaseState(phase, profilingProgress, isAnalyzing);
                    return (
                      <div key={phase.id} className="flex flex-col items-center flex-1">
                        <div className="flex items-center w-full mb-2">
                          {index > 0 && (
                            <div className={`flex-1 h-0.5 ${isCompleted || isActive ? "bg-brand-primary" : "bg-gray-300"} transition-colors duration-300`}></div>
                          )}
                          <div className={`w-4 h-4 rounded-full flex items-center justify-center transition-all duration-300 ${getPhaseIndicatorClass(isCompleted, isActive)}`} title={phase.name}></div>
                          {index < allPhases.length - 1 && (
                            <div className={`flex-1 h-0.5 ${isCompleted ? "bg-brand-primary" : "bg-gray-300"} transition-colors duration-300`}></div>
                          )}
                        </div>
                        <span className={`text-[10px] text-center leading-tight ${getPhaseTextClass(isActive, isCompleted)}`}>
                          {phase.name.split(" ").map((word, i) => <PhaseNameWord key={i} word={word} />)}
                        </span>
                      </div>
                    );
                  })}
                </div>

                <div className="flex items-center justify-center gap-2 text-xs text-gray-500">
                  <div className="animate-spin rounded-full h-3 w-3 border-b-2 border-brand-primary"></div>
                  <span>Processing in progress...</span>
                </div>
              </div>
            ) : (
              <div className="text-sm text-gray-700 prose max-w-none">
                {initialMessageData && initialMessageData.length > 0 ? (
                  (() => {
                    const profilingToolData = initialMessageData[0]?.result?.tool_response?.all_tables || initialMessageData[0]?.tool_response?.all_tables || [];
                    const textResponse = initialMessageData[0]?.result?.text_response || initialMessageData[0]?.text_response;
                    const hasToolData = profilingToolData && Array.isArray(profilingToolData) && profilingToolData.length > 0;
                    const hasTextResponse = textResponse && typeof textResponse === "string" && textResponse.trim().length > 0;

                    if (!hasToolData && !hasTextResponse) {
                      return (
                        <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-6 text-center">
                          <h3 className="text-lg text-yellow-700 mb-2">No Profiling Data</h3>
                          <p className="text-yellow-600">No profiling data available to display.</p>
                        </div>
                      );
                    }

                    return (
                      <div className="space-y-6">
                        {hasToolData && profilingToolData.map((tableItem: any, index: number) => (
                          <TableItemDisplay
                            key={`table-${tableItem.table_reference || tableItem.table_name || index}`}
                            tableItem={tableItem}
                            index={index}
                            accordionStates={accordionStates}
                            toggleAccordion={toggleAccordion}
                          />
                        ))}
                        {hasTextResponse && (
                          <div className="prose-headings:text-brand-blue prose-h1:text-lg prose-h2:text-base prose-h3:text-sm prose-p:text-gray-700 prose-strong:text-gray-900 prose-ul:list-disc prose-ol:list-decimal prose-li:text-gray-700 prose-code:bg-gray-100 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-pre:bg-gray-50 prose-pre:border prose-pre:p-3 prose-pre:rounded-lg prose-blockquote:border-l-4 prose-blockquote:border-brand-blue prose-blockquote:pl-4 prose-blockquote:italic markdown-content">
                            <Markdown
                              remarkPlugins={[remarkGfm]}
                              components={{
                                table: (props) => (
                                  <TableWithExportWrapper {...props} stepTitle={steps.find((s) => s.id === currentStep)?.title} />
                                ),
                              }}
                            >
                              {textResponse}
                            </Markdown>
                          </div>
                        )}
                      </div>
                    );
                  })()
                ) : (
                  <div className="bg-gray-50 border border-gray-200 rounded-lg p-6 text-center">
                    <h3 className="text-lg text-gray-600 mb-2">No Analysis Data</h3>
                    <p className="text-gray-500">No analysis data available to display.</p>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {steps[0].completed && (
        <div className="flex justify-end mt-6">
          <button
            onClick={onNext}
            className="bg-brand-blue hover:bg-brand-blue/75 text-white px-6 py-2 rounded-lg flex items-center gap-2 transition-colors cursor-pointer text"
          >
            Next: Relationship Analysis <ChevronRight size={16} />
          </button>
        </div>
      )}
    </div>
  );
}
