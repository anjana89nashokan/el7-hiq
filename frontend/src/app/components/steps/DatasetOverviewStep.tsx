import { FileSpreadsheet, Fullscreen, ChevronDown, ChevronUp, FileText, Bot, Send, Loader2, X } from 'lucide-react';
import { useState, useEffect } from 'react';
import type { KeyboardEvent } from 'react';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '../Tabs';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import TableWithExport from '../TableWithExport';
import TableItemDisplay from '../TableItemDisplay';
import FileInfoDisplay from '../FileInfoDisplay';
import EmptyState from '../EmptyState';
import LoadingSpinner from '../LoadingSpinner';
import NavigationButtons from '../NavigationButtons';
import { MESSAGES } from '../../config/messages';
import { sendProfilingHumanInLoopMessage } from '../../end-points/chatApi';

const MarkdownTable = ({ stepTitle, ...props }: { stepTitle?: string; [key: string]: any }) => (
  <TableWithExport stepTitle={stepTitle} {...props} />
);

const TableComponent = ({ stepTitle }: { stepTitle?: string }) => (props: any) => (
  <MarkdownTable stepTitle={stepTitle} {...props} />
);

interface DatasetOverviewStepProps {
  readonly profilingData: any;
  readonly activeTab: string;
  readonly setActiveTab: (tab: string) => void;
  readonly formatDate: (date: string) => string;
  readonly profileSummaryCollapsed: boolean;
  readonly setProfileSummaryCollapsed: (collapsed: boolean) => void;
  readonly isLoadingResponse: boolean;
  readonly initialMessageData: any[];
  readonly accordionStates: { [key: string]: boolean };
  readonly toggleAccordion: (key: string) => void;
  readonly setOpen: (open: boolean) => void;
  readonly onRetry: () => void;
  readonly onNext: () => void;
  readonly onApplyHumanInputChanges: (response: any) => void;
  readonly steps: any[];
  readonly currentStep: number;
}

interface HumanInputMessage {
  text: string;
  role: 'user' | 'assistant';
  mode?: string;
  response?: any;
}

export default function DatasetOverviewStep({
  profilingData,
  activeTab,
  setActiveTab,
  formatDate,
  profileSummaryCollapsed,
  setProfileSummaryCollapsed,
  isLoadingResponse,
  initialMessageData,
  accordionStates,
  toggleAccordion,
  setOpen,
  onRetry,
  onNext,
  onApplyHumanInputChanges,
  steps,
  currentStep
}: DatasetOverviewStepProps) {
  const [isHumanInputOpen, setIsHumanInputOpen] = useState(false);
  const [humanInput, setHumanInput] = useState('');
  const [humanInputMessages, setHumanInputMessages] = useState<HumanInputMessage[]>([]);
  const [isSubmittingHumanInput, setIsSubmittingHumanInput] = useState(false);
  const [humanInputError, setHumanInputError] = useState('');

  const flatTabs = (profilingData.successful_uploads ?? [])
    .filter((file: any) => file != null)
    .flatMap((file: any) =>
      (file.access_info?.tables_created ?? [{ sheet_name: null, table_name: file.table_name, rows_uploaded: file.rows_uploaded }])
        .map((tc: any) => ({
          ...file,
          table_name: tc.table_name,
          rows_uploaded: tc.rows_uploaded,
          sheet_name: tc.sheet_name,
          duplicate_count: tc.duplicate_count ?? 0,
          sample_duplicates: tc.sample_duplicates ?? [],
          all_duplicates: tc.all_duplicates ?? [],
          _tabKey: tc.table_name,
          _tabLabel: tc.sheet_name ? `${file.filename}(${tc.sheet_name})` : file.filename,
        }))
    );

  // Auto-select first tab once flatTabs loads
  useEffect(() => {
    if (!activeTab && flatTabs.length > 0) {
      setActiveTab(flatTabs[0]._tabKey);
    }
  }, [flatTabs, activeTab, setActiveTab]);

  const handleSendHumanInput = async () => {
    const trimmedInput = humanInput.trim();
    if (!trimmedInput || isSubmittingHumanInput) return;

    const sessionId = sessionStorage.getItem('session_id') || '';
    const appName = sessionStorage.getItem('app_name') || '';
    const userId = sessionStorage.getItem('user_id') || '';

    setHumanInputMessages((prev) => [...prev, { role: 'user', text: trimmedInput }]);
    setHumanInput('');
    setHumanInputError('');
    setIsSubmittingHumanInput(true);

    try {
      const response = await sendProfilingHumanInLoopMessage({
        user_id: userId,
        session_id: sessionId,
        app_name: appName,
        user_message: trimmedInput,
      });

      setHumanInputMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          text: response?.text_response || response?.message || 'Profiling response updated.',
          mode: response?.mode,
          response,
        },
      ]);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to process human input.';
      setHumanInputError(message);
      setHumanInputMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          text: `Sorry, I could not apply that request. ${message}`,
        },
      ]);
    } finally {
      setIsSubmittingHumanInput(false);
    }
  };

  const handleHumanInputKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      void handleSendHumanInput();
    }
  };

  const applyHumanInputResponse = (response: any) => {
    onApplyHumanInputChanges(response);
    setIsHumanInputOpen(false);
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-xl text-brand-darkblue">Dataset Overview</h2>
        <button
          type="button"
          onClick={() => setIsHumanInputOpen(true)}
          className="p-2 rounded-full hover:bg-gray-100 transition-colors cursor-pointer flex items-center gap-2"
          disabled={isLoadingResponse || initialMessageData.length === 0}
        >
          <Bot size={22} className="text-gray-500 hover:text-brand-darkblue" />
          Chat
        </button>
      </div>

      {flatTabs.length > 0 ? (
        <Tabs value={activeTab} onValueChange={setActiveTab} className="mb-6">
          <TabsList className="gap-2 overflow-x-auto">
            {flatTabs.map((tab: any) => (
              <TabsTrigger
                key={tab._tabKey}
                value={tab._tabKey}
                title={tab._tabLabel || MESSAGES.DEFAULTS.UNNAMED_FILE}
                className={`flex items-center gap-1 text-sm transition-colors cursor-pointer shrink-0 max-w-[180px] ${activeTab === tab._tabKey ? 'text-brand-darkblue' : 'hover:bg-gray-100'}`}
              >
                <FileSpreadsheet size={16} className="shrink-0" />
                <span className="truncate">{tab._tabLabel || MESSAGES.DEFAULTS.UNNAMED_FILE}</span>
              </TabsTrigger>
            ))}
          </TabsList>
          {flatTabs.map((tab: any) => {
            const profilingToolData =
              initialMessageData[0]?.tool_response?.intelligent_profiling_tool_response?.result ||
              initialMessageData[0]?.tool_response?.result || [];
            const tabProfilingResult = profilingToolData.find((r: any) =>
              r.table_reference?.endsWith(tab._tabKey)
            );
            return (
              <TabsContent key={tab._tabKey} value={tab._tabKey} className="flex-col flex-1 border border-gray-300 overflow-hidden bg-white rounded-b-lg">
                <FileInfoDisplay file={tab} formatDate={formatDate} />
                {isLoadingResponse ? (
                  <div className="p-4"><LoadingSpinner size="sm" message={MESSAGES.LOADING.GENERATING_REPORT} /></div>
                ) : tabProfilingResult ? (
                  <div className="p-4">
                    <TableItemDisplay
                      tableItem={tabProfilingResult}
                      index={0}
                      accordionStates={accordionStates}
                      toggleAccordion={toggleAccordion}
                      sampleDuplicates={tab.sample_duplicates}
                      allDuplicates={tab.all_duplicates}
                      sheetName={tab.sheet_name}
                    />
                  </div>
                ) : null}
              </TabsContent>
            );
          })}
        </Tabs>
      ) : (
        <EmptyState
          icon={FileText}
          title={MESSAGES.NO_DATA.NO_FILES}
          description={MESSAGES.NO_DATA.NO_FILES_DESC}
        />
      )}

      {initialMessageData?.length > 0 && initialMessageData[0]?.text_response && (
        <div className="border border-gray-200 bg-white shadow-sm rounded-lg mb-6 p-4">
          <button
            className="cursor-pointer flex justify-between items-center w-full text-left bg-transparent border-0 p-0"
            onClick={() => setProfileSummaryCollapsed(!profileSummaryCollapsed)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                setProfileSummaryCollapsed(!profileSummaryCollapsed);
              }
            }}
          >
            <h3 className="text-md text-brand-darkblue">{MESSAGES.SECTIONS.PROFILING_SUMMARY}</h3>
            {profileSummaryCollapsed ? <ChevronDown size={20} /> : <ChevronUp size={20} />}
          </button>
          {!profileSummaryCollapsed && (
            <div className="prose-headings:text-brand-darkblue prose-h1:text-lg prose-h2:text-base prose-h3:text-sm prose-p:text-gray-700 prose-strong:text-gray-900 prose-ul:list-disc prose-ol:list-decimal prose-li:text-gray-700 prose-code:bg-gray-100 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-pre:bg-gray-50 prose-pre:border prose-pre:p-3 prose-pre:rounded-lg prose-blockquote:border-l-4 prose-blockquote:border-brand-blue prose-blockquote:pl-4 prose-blockquote:italic markdown-content pt-2">
              <Markdown
                remarkPlugins={[remarkGfm]}
                components={{ table: TableComponent({ stepTitle: steps.find((s) => s.id === currentStep)?.title }) }}
              >
                {initialMessageData[0].text_response}
              </Markdown>
            </div>
          )}
        </div>
      )}

      <button
        className="flex items-center justify-center w-full bg-brand-darkblue rounded-lg p-2 hover:bg-brand-darkblue/75 transition-colors text-white"
        onClick={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            setOpen(true);
          }
        }}
      >
        <Fullscreen size={24} className="text-white" />
        <p className="ml-2 text-white">{MESSAGES.SECTIONS.DETAILED_REPORT}</p>
      </button>

      <NavigationButtons
        onNext={onNext}
        onRetry={onRetry}
        nextLabel={MESSAGES.NAVIGATION.NEXT_RELATIONSHIP}
        showNext={steps[0].completed}
        showRetry={true}
        disabled={isLoadingResponse}
      />

      {isHumanInputOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4">
          <div className="flex max-h-[90vh] w-full max-w-3xl flex-col overflow-hidden rounded-lg bg-white shadow-xl">
            <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
              <div>
                <h3 className="text-base font-semibold text-brand-darkblue">Chat: Dataset Overview</h3>
                <p className="text-xs text-gray-500">Ask for a profiling correction, then apply the updated response.</p>
              </div>
              <button
                type="button"
                onClick={() => setIsHumanInputOpen(false)}
                className="rounded-md p-1 text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-800"
                aria-label="Close profiling human input"
              >
                <X size={20} />
              </button>
            </div>

            <div className="flex-1 space-y-3 overflow-y-auto p-4">
              {humanInputMessages.length === 0 && (
                <div className="rounded-md border border-dashed border-gray-300 bg-gray-50 p-4 text-sm text-gray-600">
                  Enter the change you want to make to the dataset overview profiling output.
                </div>
              )}

              {humanInputMessages.map((message, index) => (
                <div key={`${message.role}-${index}`} className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                  <div className={`max-w-[85%] rounded-lg px-4 py-3 text-sm ${message.role === 'user' ? 'bg-brand-darkblue text-white' : 'bg-gray-100 text-gray-800'}`}>
                    <div className="whitespace-pre-wrap">{message.text}</div>
                    {message.role === 'assistant' && message.mode === 'UPDATE' && (
                      <button
                        type="button"
                        onClick={() => applyHumanInputResponse(message.response)}
                        className="mt-3 rounded-md bg-green-600 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-green-700"
                      >
                        Apply changes
                      </button>
                    )}
                  </div>
                </div>
              ))}

              {isSubmittingHumanInput && (
                <div className="flex justify-start">
                  <div className="rounded-lg bg-gray-100 px-4 py-3">
                    <Loader2 size={18} className="animate-spin text-gray-600" />
                  </div>
                </div>
              )}

              {humanInputError && (
                <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                  {humanInputError}
                </div>
              )}
            </div>

            <div className="border-t border-gray-200 p-4">
              <div className="flex items-end gap-2">
                <textarea
                  value={humanInput}
                  onChange={(event) => setHumanInput(event.target.value)}
                  onKeyDown={handleHumanInputKeyDown}
                  placeholder="Describe the profiling change to apply..."
                  disabled={isSubmittingHumanInput}
                  rows={3}
                  className="min-h-[84px] flex-1 resize-none rounded-md border border-gray-300 px-3 py-2 text-sm outline-none transition-colors focus:border-brand-darkblue focus:ring-1 focus:ring-brand-darkblue disabled:bg-gray-50"
                />
                <button
                  type="button"
                  onClick={() => void handleSendHumanInput()}
                  disabled={!humanInput.trim() || isSubmittingHumanInput}
                  className="flex h-10 w-10 items-center justify-center rounded-md bg-brand-darkblue text-white transition-colors hover:bg-brand-darkblue/80 disabled:cursor-not-allowed disabled:opacity-60"
                  aria-label="Send profiling human input"
                >
                  {isSubmittingHumanInput ? <Loader2 size={18} className="animate-spin" /> : <Send size={18} />}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
