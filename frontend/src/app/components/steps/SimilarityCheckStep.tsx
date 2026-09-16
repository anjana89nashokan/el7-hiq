import { type Dispatch, type SetStateAction, useState } from "react";
import { Zap, XCircle, Database, Bot, Send, Loader2, X } from "lucide-react";
import type { KeyboardEvent } from 'react';
import DartTableInput from "../DartTableInput";
import SimilarityResponseDisplay from "../SimilarityResponseDisplay";
import LoadingSpinner from "../LoadingSpinner";
import NavigationButtons from "../NavigationButtons";
import { DynamicSimilarity } from "../DynamicSimilarity";
import { sendSimilarityHITLMessage } from "../../end-points/chatApi";

interface SimilarityCheckStepProps {
  readonly dartTableEntries: { dartTable: string; column: string }[];
  readonly addDartTableEntry: () => void;
  readonly updateDartTableEntry: (
    index: number,
    field: "dartTable" | "column",
    value: string,
  ) => void;
  readonly removeDartTableEntry: (index: number) => void;
  readonly handleSimilarityCheck: () => void;
  readonly isLoadingSimilarity: boolean;
  readonly similarityResponse: string;
  readonly setSimilarityResponse: (response: string) => void;
  readonly setDartTableEntries: (
    entries: { dartTable: string; column: string; isType2: boolean }[],
  ) => void;
  readonly setSkippedSteps: Dispatch<SetStateAction<Set<number>>>;
  readonly steps: any[];
  readonly skippedSteps: Set<number>;
  readonly onPrevious: () => void;
  readonly onNext: () => void;
  readonly onRetry: () => void;
  readonly hasApiBeenCalled?: boolean;
  readonly markApiCalled?: () => void;
  readonly databaseName: string;
  readonly setDatabaseName: Dispatch<SetStateAction<string>>;
  readonly tableSchemaFields: any[];
  readonly dynamicFilters: any[];
  readonly setDynamicFilters: Dispatch<SetStateAction<any[]>>;
  readonly tableSchemaError: string;
  readonly handleValidateDatabase: () => void;
}

interface HumanInputMessage {
  text: string;
  role: 'user' | 'assistant';
  mode?: string;
  response?: any;
}

export default function SimilarityCheckStep({
  dartTableEntries,
  addDartTableEntry,
  updateDartTableEntry,
  removeDartTableEntry,
  handleSimilarityCheck,
  isLoadingSimilarity,
  similarityResponse,
  setSimilarityResponse,
  setDartTableEntries,
  setSkippedSteps,
  skippedSteps,
  onPrevious,
  onNext,
  databaseName,
  setDatabaseName,
  tableSchemaFields,
  dynamicFilters,
  setDynamicFilters,
  tableSchemaError,
  handleValidateDatabase,
  markApiCalled
}: SimilarityCheckStepProps) {
  const [showDynamicSimilarity, setShowDynamicSimilarity] = useState(false);
  const [isHumanInputOpen, setIsHumanInputOpen] = useState(false);
  const [humanInput, setHumanInput] = useState('');
  const [humanInputMessages, setHumanInputMessages] = useState<HumanInputMessage[]>([]);
  const [isSubmittingHumanInput, setIsSubmittingHumanInput] = useState(false);
  const [humanInputError, setHumanInputError] = useState('');

  const handleNextClick = () => {
    if (skippedSteps.has(4)) {
      onNext();
    } else {
      setSkippedSteps((prev) => new Set([...prev, 4]));
      onNext();
    }
  };

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
      const response = await sendSimilarityHITLMessage({
        user_id: userId,
        session_id: sessionId,
        app_name: appName,
        user_message: trimmedInput,
      });

      const mode = response?.mode;
      let assistantText = response?.text_response || response?.message || '';
      if (mode === 'QUESTION') {
        assistantText = response?.text_response ?? 'No answer returned.';
      } else if (mode === 'UPDATE') {
        assistantText =
          'Proposed changes are ready. Click Apply changes to update the similarity results.';
      }

      setHumanInputMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          text: assistantText,
          mode: response?.mode,
          response,
        },
      ]);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to process request.';
      setHumanInputError(message);
      setHumanInputMessages((prev) => [
        ...prev,
        { role: 'assistant', text: `Sorry, I could not apply that request. ${message}` },
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

  const applyHumanInputResponse = async (response: any) => {
    if (!response) return;

    const sessionId = sessionStorage.getItem('session_id') || '';
    const appName = sessionStorage.getItem('app_name') || '';
    const userId = sessionStorage.getItem('user_id') || '';

    const textResponse = response.text_response ?? '';
    const toolPayload = response.tool_response ?? {};

    setHumanInputError('');
    setIsSubmittingHumanInput(true);

    try {
      if (sessionId && appName && userId) {
        await sendSimilarityHITLMessage({
          user_id: userId,
          session_id: sessionId,
          app_name: appName,
          user_message: 'Apply similarity changes',
          apply_changes: true,
          text_response: textResponse,
          tool_response: toolPayload,
        });
      }

      setSimilarityResponse(
        JSON.stringify([
          {
            text_response: textResponse,
            tool_response: toolPayload,
            should_update: true,
          },
        ]),
      );
      setIsHumanInputOpen(false);
    } catch (error) {
      const message =
        error instanceof Error ? error.message : 'Failed to apply similarity changes.';
      setHumanInputError(message);
    } finally {
      setIsSubmittingHumanInput(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Custom header to replace StepWrapper's header */}
      <div className="flex items-center justify-between">
        <h2 className="text-xl text-brand-darkblue">Similarity Check</h2>
        <button
          type="button"
          onClick={() => setIsHumanInputOpen(true)}
          className="p-2 rounded-full hover:bg-gray-100 transition-colors cursor-pointer flex items-center gap-2"
          //disabled={isLoadingSimilarity || !similarityResponse}
        >
          <Bot size={22} className="text-gray-500 hover:text-brand-darkblue" />
          Chat
        </button>
      </div>
      
      <div className="bg-white rounded-lg border border-gray-300 p-4">
        <div className="space-y-4">

        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">
            Database Name
          </label>
          <input
            type="text"
            value={databaseName}
            onChange={(e) => setDatabaseName(e.target.value)}
            className="w-full px-3 py-2 text-sm border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-brand-primary"
            placeholder="e.g., gender_lookup or country_codes"
          />
        </div>

        <DartTableInput
          entries={dartTableEntries}
          onAdd={addDartTableEntry}
          onUpdate={updateDartTableEntry}
          onRemove={removeDartTableEntry}
        />

        <div className="mt-4">
          <button
            onClick={() => {
              handleValidateDatabase();
              setShowDynamicSimilarity(true);
            }}
            disabled={isLoadingSimilarity || !databaseName || !dartTableEntries.some(entry => entry.dartTable.trim())}
            className="px-4 py-2 bg-green-600 text-white rounded-md hover:bg-green-700 disabled:bg-gray-400 disabled:cursor-not-allowed transition-colors flex items-center gap-2 text-xs font-medium"
            type="button"
          >
            <Database size={16} />
            Validate Database & Tables
          </button>
        </div>

        {showDynamicSimilarity && (
          <>
            <DynamicSimilarity
              tableSchemaFields={tableSchemaFields}
              dynamicFilters={dynamicFilters}
              setDynamicFilters={setDynamicFilters}
            />
            <div className="flex gap-3">
              <button
                onClick={() => {
                  markApiCalled?.();
                  handleSimilarityCheck();
                }}
                disabled={
                  isLoadingSimilarity ||
                  !dartTableEntries.some(entry => entry.dartTable.trim()) ||
                  !dynamicFilters.some(filter => filter.fieldname && filter.value && filter.value.length > 0)
                }
                className="bg-brand-blue hover:bg-brand-blue/75 disabled:bg-gray-400 text-white px-6 py-2 rounded-lg transition-colors flex items-center gap-2"
              >
                Start Similarity Check
              </button>
            </div>
          </>
        )}

        {tableSchemaError && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-4">
            <div className="flex items-center gap-2 text-red-700">
              <XCircle size={20} />
              <p className="font-medium">Table Validation Error</p>
            </div>
            <p className="text-sm text-red-600 mt-1">{tableSchemaError}</p>
          </div>
        )}

        {!similarityResponse && !isLoadingSimilarity && (
          <div className="bg-brand-surface border border-teal-200 rounded-lg p-4">
            <div className="flex items-center gap-2 text-font-blue">
              <Zap size={20} />
              <p className="font-medium">Similarity Check (Optional)</p>
            </div>
            <p className="text-sm text-font-blue mt-1">
              Enter Reference table references and click "Check Similarity" to
              compare with existing tables, or skip this step to continue.
            </p>
          </div>
        )}

        {similarityResponse && similarityResponse.includes("Error") && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-4">
            <div className="flex items-center gap-2 text-red-700">
              <XCircle size={20} />
              <p className="font-medium">Similarity Check Failed</p>
            </div>
            <p className="text-sm text-red-600 mt-1">
              An error occurred during the similarity check. Please try again or skip this step.
            </p>
          </div>
        )}

        {isLoadingSimilarity && (
          <LoadingSpinner message="Processing similarity check..." />
        )}

        {similarityResponse && (
          <div className="space-y-4">
            <SimilarityResponseDisplay response={similarityResponse} />
            <div className="flex items-center gap-2 justify-end">
              <button
                onClick={() => {
                  setSimilarityResponse("");
                  markApiCalled?.();
                  handleSimilarityCheck();
                }}
                disabled={isLoadingSimilarity}
                className="text-sm text-gray-600 hover:text-gray-900 flex items-center gap-1 disabled:opacity-50"
              >
                Retry
              </button>
              <button
                onClick={() => {
                  setSimilarityResponse("");
                  setShowDynamicSimilarity(false);
                  setDartTableEntries([{ dartTable: "", column: "", isType2: false }]);
                  setDynamicFilters([]);
                }}
                className="text-sm px-3 py-1 bg-gray-200 hover:bg-gray-300 rounded-md"
              >
                New Check
              </button>
            </div>
          </div>
        )}
        </div>
      </div>

      <NavigationButtons
        onPrevious={onPrevious}
        onNext={similarityResponse ? onNext : handleNextClick}
        previousLabel="Previous: Data Dictionary"
        nextLabel={similarityResponse ? "Next: Reference Suggestion" : "Skip Similarity Check"}
        showNext={true}
        showRetry={true}
        disabled={isLoadingSimilarity}
      />

      {/* Human Input Modal — mirrors DatasetOverviewStep */}
      {isHumanInputOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4">
          <div className="flex max-h-[90vh] w-full max-w-3xl flex-col overflow-hidden rounded-lg bg-white shadow-xl">
            <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
              <div>
                <h3 className="text-base font-semibold text-brand-darkblue">Chat: Similarity Check</h3>
                <p className="text-xs text-gray-500">Ask a question or request a change to the similarity results.</p>
              </div>
              <button
                type="button"
                onClick={() => setIsHumanInputOpen(false)}
                className="rounded-md p-1 text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-800"
                aria-label="Close similarity human input"
              >
                <X size={20} />
              </button>
            </div>

            <div className="flex-1 space-y-3 overflow-y-auto p-4">
              {humanInputMessages.length === 0 && (
                <div className="rounded-md border border-dashed border-gray-300 bg-gray-50 p-4 text-sm text-gray-600">
                  Enter a question about the results or a change you want to apply.
                </div>
              )}

              {humanInputMessages.map((message, index) => (
                <div key={`${message.role}-${index}`} className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                  <div className={`max-w-[85%] rounded-lg px-4 py-3 text-sm ${message.role === 'user' ? 'bg-brand-darkblue text-white' : 'bg-gray-100 text-gray-800'}`}>
                    <div className="whitespace-pre-wrap">{message.text}</div>
                    {message.role === 'assistant' && message.mode === 'UPDATE' && (
                      <button
                        type="button"
                        onClick={() => void applyHumanInputResponse(message.response)}
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
                  onChange={(e) => setHumanInput(e.target.value)}
                  onKeyDown={handleHumanInputKeyDown}
                  placeholder="Describe the change to apply or ask a question..."
                  disabled={isSubmittingHumanInput}
                  rows={3}
                  className="min-h-[84px] flex-1 resize-none rounded-md border border-gray-300 px-3 py-2 text-sm outline-none transition-colors focus:border-brand-darkblue focus:ring-1 focus:ring-brand-darkblue disabled:bg-gray-50"
                />
                <button
                  type="button"
                  onClick={() => void handleSendHumanInput()}
                  disabled={!humanInput.trim() || isSubmittingHumanInput}
                  className="flex h-10 w-10 items-center justify-center rounded-md bg-brand-darkblue text-white transition-colors hover:bg-brand-darkblue/80 disabled:cursor-not-allowed disabled:opacity-60"
                  aria-label="Send similarity human input"
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
