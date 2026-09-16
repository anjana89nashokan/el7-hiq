import React from "react";

type Column = { name: string; type?: string; data_type?: string };
type TableSchemaField = { table_name: string; columns: Column[] };
type Row = {
  id: string;
  fieldname?: string;
  value: string[];
  type: string;
  table_name: string;
};

export const DynamicSimilarity: React.FC<{
  tableSchemaFields: TableSchemaField[];
  dynamicFilters: any[];
  setDynamicFilters: React.Dispatch<React.SetStateAction<any[]>>;
}> = ({ tableSchemaFields = [], dynamicFilters, setDynamicFilters }) => {
  // rows per table: each row represents one chosen field + its comma-separated value string

  const addRow = (tableName: string, initial?: Partial<Row>) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    setDynamicFilters((r) => [
      ...r,
      {
        id,
        fieldname: initial?.fieldname ?? "",
        value: initial?.value ?? "",
        table_name: tableName,
        type: initial?.type,
      },
    ]);
  };
  const updateRow = (id: string, patch: Partial<Row>) => {
    setDynamicFilters((r) => {
      if (!Array.isArray(r)) return r;
      return r.map((row) => (row.id === id ? { ...row, ...patch } : row));
    });
  };

  const removeRow = (id: string) => {
    setDynamicFilters((r) => r.filter((row) => row.id !== id));
  };

  return (
    <div>
      <h3 className="font-medium">Dynamic Similarity Section</h3>
      <p className="text-sm text-gray-600 mb-4">
        For each table, add rows where you pick a field and provide
        comma-separated values. Use "Add multiple" to add several fields at
        once.
      </p>

      {tableSchemaFields.map((table) => (
        <div key={table.table_name} className="mb-6 border p-4 rounded">
          <div className="flex items-center justify-between mb-3">
            <h4 className="font-semibold">{table.table_name}</h4>

            <div className="flex items-center space-x-2">
              <button
                type="button"
                onClick={() => addRow(table.table_name)}
                className="text-sm bg-brand-primary text-white px-3 py-1 rounded"
              >
                Add field
              </button>
            </div>
          </div>

          <div className="space-y-2">
            {(dynamicFilters || []).filter(row => row.table_name === table.table_name).map((row) => (
              <div
                key={row.id}
                className="grid grid-cols-12 gap-2 items-center"
              >
                <div className="col-span-5">
                  <select
                    value={
                      row.fieldname + `,${row.data_type ?? row.type ?? ""}`
                    }
                    onChange={(e) => {
                      const value = e.target.value.split(",");
                      updateRow(row.id, {
                        fieldname: value[0],
                        type: value[1],
                      });
                    }}
                    className="w-full border rounded p-2"
                  >
                    <option value="">-- Select field --</option>
                    {table.columns.map((col) => (
                      <option
                        key={col.name}
                        value={col.name + `,${col.data_type ?? col.type ?? ""}`}
                      >
                        {col.name} ({col.data_type ?? col.type ?? "unknown"})
                      </option>
                    ))}
                  </select>
                </div>

                <div className="col-span-6">
                  <input
                    type="text"
                    placeholder="Comma-separated values, e.g. a,b,c"
                    value={row.value}
                    onChange={(e) =>
                      updateRow(row.id, {
                        value: e.target.value.split(","),
                      })
                    }
                    className="w-full border rounded p-2"
                  />
                </div>

                <div className="col-span-1 text-right">
                  <button
                    type="button"
                    onClick={() => removeRow(row.id)}
                    className="text-sm text-red-600 px-2"
                    aria-label={`Remove row ${row.id}`}
                  >
                    ✕
                  </button>
                </div>
              </div>
            ))}

            {(dynamicFilters || []).filter(row => row.table_name === table.table_name).length === 0 && (
              <p className="text-sm text-gray-500">
                No fields added. Click "Add field" or "Add multiple" to create a
                row.
              </p>
            )}
          </div>
        </div>
      ))}
    </div>
  );
};
