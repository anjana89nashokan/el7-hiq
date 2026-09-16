import { useState } from "react";
import type { FileLayoutField, FileLayoutResponse } from "../../end-points/extract/extractApi";

interface Props {
  readonly data: FileLayoutResponse;
  readonly loading: boolean;
  readonly reviewStatus?: "idle" | "approved" | "rejected";
  readonly approved?: boolean;
  readonly disabled?: boolean;
  readonly onApprove: (tables: Record<string, FileLayoutField[]>) => void;
  readonly onUpdate: (tables: Record<string, FileLayoutField[]>) => void;
  readonly onRetry?: (tables: Record<string, FileLayoutField[]>) => void;
  readonly onReactivate?: () => void;
}

const TEXTAREA_COLS = new Set(["description", "field description", "additional notes", "notes"]);
const WIDE_COLS = new Set(["name", "field name"]);

function FileLayoutTable({
  tableName,
  rows,
  editMode,
  onChange,
}: {
  tableName: string;
  rows: FileLayoutField[];
  editMode: boolean;
  onChange: (updated: FileLayoutField[]) => void;
}) {
  const [open, setOpen] = useState(true);
  const safeRows = Array.isArray(rows) ? rows : [];
  const columns = safeRows.length > 0 ? Object.keys(safeRows[0]) : [];

  const updateCell = (rowIdx: number, col: string, value: string) => {
    const updated = safeRows.map((r, i) => (i === rowIdx ? { ...r, [col]: value } : r));
    onChange(updated);
  };

  return (
    <div className="border border-gray-200 rounded-lg overflow-hidden mb-4">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex justify-between items-center px-4 py-3 bg-gray-50 hover:bg-gray-100 text-sm font-bold text-brand-darkblue"
      >
        {tableName}
        <span className="text-gray-400 font-normal">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs border-collapse">
            <thead>
              <tr className="bg-brand-darkblue/5">
                {columns.map((col) => (
                  <th key={col} className="px-3 py-2 text-left font-semibold text-brand-darkblue border-b border-gray-200 whitespace-nowrap capitalize">
                    {col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {safeRows.map((row, rowIdx) => (
                <tr key={rowIdx} className="border-b border-gray-100 hover:bg-gray-50">
                  {columns.map((col) => {
                    const colKey = col.toLowerCase();
                    const isTextarea = TEXTAREA_COLS.has(colKey);
                    const isWide = WIDE_COLS.has(colKey);
                    return (
                      <td key={col} className="px-3 py-2 align-top">
                        {editMode ? (
                          isTextarea ? (
                            <textarea
                              rows={3}
                              value={String(row[col] ?? "")}
                              onChange={(e) => updateCell(rowIdx, col, e.target.value)}
                              className="w-full min-w-[180px] px-2 py-1 border border-gray-300 rounded bg-white focus:outline-none focus:ring-1 focus:ring-brand-primary text-xs resize-none"
                            />
                          ) : (
                            <input
                              type="text"
                              value={String(row[col] ?? "")}
                              onChange={(e) => updateCell(rowIdx, col, e.target.value)}
                              className={`px-2 py-1 border border-gray-300 rounded bg-white focus:outline-none focus:ring-1 focus:ring-brand-primary text-xs ${
                                isWide ? "w-full min-w-[280px]" : "w-full min-w-[80px]"
                              }`}
                            />
                          )
                        ) : (
                          <span className="text-gray-700">
                            {row[col] != null ? String(row[col]) : <span className="text-gray-300 italic">—</span>}
                          </span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function FileLayoutReview({ data, loading, approved, disabled = false, onApprove, onUpdate, onReactivate }: Props) {
  const [editMode, setEditMode] = useState(false);
  const [editedTables, setEditedTables] = useState<Record<string, FileLayoutField[]>>(data.file_layout_tables);
  const [reactivated, setReactivated] = useState(false);

  const updateTable = (name: string, rows: FileLayoutField[]) =>
    setEditedTables((prev) => ({ ...prev, [name]: rows }));

  const handleCancel = () => {
    setEditedTables(data.file_layout_tables);
    setEditMode(false);
  };

  const isApproved = approved === true && !reactivated;
  const showReactivatedNote = (approved ?? false) && reactivated && !editMode;

  return (
    <div className="mt-2">
      <div className="flex items-center justify-between mb-1">
        <h3 className="text-base font-bold text-brand-darkblue">File Layout</h3>
        <span className="text-xs text-gray-400">
          {data.tables_extracted} table(s) · {data.total_pages} page(s) · {data.file_layout_filename}
        </span>
      </div>
      <p className="text-xs text-gray-400 mb-4">Session: {data.session_id}</p>

      {Object.entries(editedTables).map(([name, rows]) => (
        <FileLayoutTable
          key={name}
          tableName={name}
          rows={rows}
          editMode={editMode}
          onChange={(updated) => updateTable(name, updated)}
        />
      ))}

      <div className="flex items-center gap-3 mt-4 pt-4 border-t border-gray-200">
        {showReactivatedNote && (
          <p className="text-xs text-amber-600 italic">Note: Re-approve to apply changes.</p>
        )}
        {!editMode ? (
          <>
            <button
              type="button"
              disabled={disabled}
              onClick={() => { setEditMode(true); setReactivated(true); onReactivate?.(); }}
              className="px-4 py-2 text-sm border border-brand-darkblue text-brand-darkblue rounded-lg hover:bg-brand-surface disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              Edit
            </button>
            <button
              type="button"
              disabled={loading || isApproved || disabled}
              onClick={() => {
                setReactivated(false);
                onApprove(editedTables);
              }}
              className="px-4 py-2 text-sm bg-brand-primary text-white rounded-lg hover:bg-brand-primary-hover disabled:bg-gray-400 disabled:cursor-not-allowed transition-colors"
            >
              {loading ? "Approving..." : isApproved ? "Approved ✓" : "Approve"}
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              disabled={loading || disabled}
              onClick={() => { onUpdate(editedTables); setEditMode(false); }}
              className="px-4 py-2 text-sm bg-brand-primary text-white rounded-lg hover:bg-brand-primary-hover disabled:bg-gray-400 disabled:cursor-not-allowed transition-colors"
            >
              {loading ? "Saving…" : "Update"}
            </button>
            <button
              type="button"
              disabled={loading || disabled}
              onClick={handleCancel}
              className="px-4 py-2 text-sm border border-gray-300 text-gray-600 rounded-lg hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              Cancel
            </button>
          </>
        )}
      </div>
    </div>
  );
}