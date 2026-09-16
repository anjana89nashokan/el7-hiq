import { Plus, Trash2 } from 'lucide-react';

interface DartTableEntry {
  dartTable: string;
  column: string;
}

interface DartTableInputProps {
  readonly entries: DartTableEntry[];
  readonly onAdd: () => void;
  readonly onUpdate: (index: number, field: 'dartTable' | 'column', value: string) => void;
  readonly onRemove: (index: number) => void;
}

export default function DartTableInput({ entries, onAdd, onUpdate, onRemove }: DartTableInputProps) {
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-lg font-medium text-gray-700">Reference Table References</h3>
        <button
          onClick={onAdd}
          className="bg-brand-darkblue hover:bg-brand-darkblue/75 text-white p-2 rounded-lg flex items-center gap-1 transition-colors"
        >
          <Plus size={16} />
          Add Row
        </button>
      </div>

      <div className="grid grid-cols-12 gap-4 mb-3">
        <div className="col-span-5 text-sm font-medium text-gray-700">Reference Table</div>
        <div className="col-span-5 text-sm font-medium text-gray-700">Column</div>
        <div className="col-span-2 text-sm font-medium text-gray-700">Action</div>
      </div>

      <div className="group relative bg-gradient-to-r from-gray-50 to-white border border-gray-200 rounded-xl p-4 hover:shadow-md transition-all duration-200 hover:border-brand-blue/30 space-y-3">
        {entries.map((entry, index) => (
          <div key={`dart-entry-${index}`} className="grid grid-cols-12 gap-4 items-center">
            <label htmlFor={`dart-table-${index}`} className="sr-only">Reference Table {index + 1}</label>
            <input
              id={`dart-table-${index}`}
              type="text"
              value={entry.dartTable}
              onChange={(e) => onUpdate(index, 'dartTable', e.target.value)}
              placeholder="Enter Reference table name"
              className="col-span-5 border border-gray-300 rounded-lg px-4 py-3 focus:outline-none focus:ring-2 focus:ring-brand-blue focus:border-transparent transition-all duration-200 bg-white shadow-sm"
            />
            <label htmlFor={`dart-column-${index}`} className="sr-only">Column {index + 1}</label>
            <input
              id={`dart-column-${index}`}
              type="text"
              value={entry.column}
              onChange={(e) => onUpdate(index, 'column', e.target.value)}
              placeholder="Enter column name"
              className="col-span-5 border border-gray-300 rounded-lg px-4 py-3 focus:outline-none focus:ring-2 focus:ring-brand-blue focus:border-transparent transition-all duration-200 bg-white shadow-sm"
            />
            <div className="col-span-2">
              {entries.length > 1 && (
                <button
                  onClick={() => onRemove(index)}
                  className="text-red-500 hover:text-red-700 p-2"
                  title="Delete row"
                  aria-label={`Delete Reference table entry ${index + 1}`}
                >
                  <Trash2 size={16} />
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
