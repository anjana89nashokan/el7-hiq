import { useCallback, useMemo, useState } from 'react';
import { useSSEStream } from '../hooks/useSSEStream';
import { GitCompare, Plus, Trash2 } from 'lucide-react';

interface DartTableEntry {
  table: string;
  columns: string;
}

const initialEntry: DartTableEntry = { table: '', columns: '' };

const getStoredSession = () => ({
  sessionId: sessionStorage.getItem('session_id') || crypto.randomUUID(),
  appName: sessionStorage.getItem('app_name') || 'datamap-copilot',
  userId: sessionStorage.getItem('user_id') || 'user-123'
});

export default function StreamingSimilarityResult() {
  const [dartTables, setDartTables] = useState<DartTableEntry[]>([initialEntry]);
  const [formError, setFormError] = useState<string>('');
  const [hasCompleted, setHasCompleted] = useState(false);

  const { sessionId, appName, userId } = getStoredSession();

  const {
    isStreaming,
    progress,
    statusMessage,
    error,
    result,
    llmAnalysis,
    startStream
  } = useSSEStream({
    featureType: 'similarity',
    onError: (msg) => setFormError(msg),
    onComplete: () => setHasCompleted(true)
  });

  const normalizedEntries = useMemo(
    () =>
      dartTables
        .map((entry) => ({ table: entry.table.trim(), columns: entry.columns.trim() }))
        .filter((entry) => entry.table && entry.columns),
    [dartTables]
  );

  const messageText = useMemo(() => {
    if (!normalizedEntries.length) {
      return 'Do similarity check for the Reference tables and column info:';
    }

    const tableLines = normalizedEntries.map((entry, index) => {
      const columns = entry.columns.split(',').map((col) => col.trim()).filter(Boolean);
      const columnText = columns.length ? columns.join(', ') : 'N/A';
      return `table ${index + 1}: ${entry.table} : columns: ${columnText}`;
    });

    return `Do similarity check for the Reference tables and column info : ${tableLines.join('; ')}`;
  }, [normalizedEntries]);

  const handleAddRow = () => setDartTables((prev) => [...prev, initialEntry]);

  const handleRemoveRow = (index: number) => {
    setDartTables((prev) => prev.filter((_, idx) => idx !== index));
  };

  const handleEntryChange = useCallback(
    (index: number, key: keyof DartTableEntry, value: string) => {
      setDartTables((prev) =>
        prev.map((entry, idx) => (idx === index ? { ...entry, [key]: value } : entry))
      );
    },
    []
  );

  const startSimilarityStream = async () => {
    setFormError('');

    if (!normalizedEntries.length) {
      setFormError('Add at least one Reference table with columns to continue.');
      return;
    }

    const requestPayload = {
      sessionId,
      appName,
      userId,
      newMessage: {
        parts: [{ text: messageText }],
        role: 'user'
      },
      streaming: true,
      stateDelta: {}
    };

    try {
      setHasCompleted(false);
      await startStream(requestPayload);
    } catch (err) {
      console.error('Streaming similarity failed', err);
      setFormError('Unable to start similarity stream. Please retry.');
    }
  };

  const finalMarkdown = useMemo(() => {
    if (!result) {
      return 'Waiting for similarity analysis to complete...';
    }
    return (
      (result as any).text_response ||
      (typeof result === 'string' ? result : 'Similarity analysis completed. Check tool response for details.')
    );
  }, [result]);

  return (
    <div className="space-y-6">
      <section className="grid gap-4 p-6 rounded-2xl bg-white shadow">
        <div className="flex items-center gap-3">
          <GitCompare className="text-brand-blue" />
          <div>
            <h1 className="text-xl font-semibold text-slate-900">
              Streaming Similarity Check
            </h1>
            <p className="text-sm text-slate-500">
              Submit Reference table names + column lists and watch the SSE-enabled agent
              stream progress for the large-scale similarity analysis.
            </p>
          </div>
        </div>

        <div className="grid gap-3">
          {dartTables.map((entry, index) => (
            <div key={index} className="border border-slate-200 rounded-xl p-4 grid gap-3">
              <div className="flex items-center justify-between">
                <span className="text-sm font-semibold text-slate-800">Table {index + 1}</span>
                {dartTables.length > 1 && (
                  <button
                    type="button"
                    className="text-red-500 hover:text-red-700 cursor-pointer"
                    onClick={() => handleRemoveRow(index)}
                  >
                    <Trash2 size={16} />
                  </button>
                )}
              </div>

              <label className="text-xs text-slate-500 uppercase tracking-wide">Reference table</label>
              <input
                className="w-full rounded-lg border border-slate-200 px-3 py-2 focus:border-brand-blue focus:outline-none"
                value={entry.table}
                onChange={(event) => handleEntryChange(index, 'table', event.target.value)}
                placeholder="e.g. ihg-dart-edw-dev2.DB_WRK.gender_lookup"
              />

              <label className="text-xs text-slate-500 uppercase tracking-wide">Columns</label>
              <input
                className="w-full rounded-lg border border-slate-200 px-3 py-2 focus:border-brand-blue focus:outline-none"
                value={entry.columns}
                onChange={(event) => handleEntryChange(index, 'columns', event.target.value)}
                placeholder="e.g. gender_code, gender_label"
              />
            </div>
          ))}
        </div>

        <button
          type="button"
          className="inline-flex items-center gap-2 rounded-xl bg-brand-blue px-4 py-2 text-sm font-semibold text-white hover:bg-brand-blue/90 cursor-pointer"
          onClick={handleAddRow}
        >
          <Plus size={16} /> Add another Reference table
        </button>

        <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 p-4 text-sm text-slate-600">
          {messageText}
        </div>

        <button
          type="button"
          className="w-full rounded-xl bg-gradient-to-r from-brand-blue to-brand-primary-hover px-4 py-3 text-sm font-semibold text-white disabled:opacity-60 cursor-pointer disabled:cursor-not-allowed"
          onClick={startSimilarityStream}
          disabled={isStreaming || !normalizedEntries.length}
        >
          {isStreaming ? 'Streaming similarity check...' : 'Start streaming similarity check'}
        </button>

        {(formError || error) && (
          <p className="text-sm text-red-500">{formError || error}</p>
        )}
      </section>

      <section className="grid gap-4">
        <div className="rounded-2xl bg-white p-6 shadow">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs uppercase tracking-wide text-slate-500">Progress</p>
              <p className="text-lg font-semibold text-slate-900">
                {Math.round(progress)}% · {statusMessage || 'Waiting for SSE events'}
              </p>
            </div>
            <div className="text-xs font-semibold text-slate-500">
              {isStreaming ? 'Streaming' : hasCompleted ? 'Finished' : 'Idle'}
            </div>
          </div>
          <div className="mt-4 h-2 rounded-full bg-slate-200">
            <div
              className="h-full rounded-full bg-brand-blue transition-all"
              style={{ width: `${Math.min(progress, 100)}%` }}
            />
          </div>
        </div>

        <div className="rounded-2xl bg-white p-6 shadow">
          <h2 className="text-sm font-semibold text-slate-500">LLM Analysis Trace</h2>
          <p className="mt-2 text-sm text-slate-800">
            {llmAnalysis || 'LLM tokens will stream here once the agent begins intelligent analysis.'}
          </p>
        </div>

        <div className="rounded-2xl bg-white p-6 shadow">
          <h2 className="text-sm font-semibold text-slate-500">Final similarity report</h2>
          <p className="mt-2 text-sm text-slate-800 whitespace-pre-wrap">
            {finalMarkdown}
          </p>
        </div>

        {result && (
          <div className="rounded-2xl bg-white p-6 shadow">
            <h2 className="text-sm font-semibold text-slate-500">Raw tool response</h2>
            <pre className="mt-3 max-h-72 overflow-auto text-[12px] text-slate-700">
              {JSON.stringify((result as any).tool_response || result, null, 2)}
            </pre>
          </div>
        )}
      </section>
    </div>
  );
}
