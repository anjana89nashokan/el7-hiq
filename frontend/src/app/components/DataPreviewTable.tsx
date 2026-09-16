type Props = { columns: string[]; rows: Record<string, string>[] };

export default function DataPreviewTable({ columns, rows }: Props) {
  return (
    <div className="bg-white overflow-auto max-h-96 border border-brand-light">
      <table className="min-w-full border-collapse border border-brand-light">
        <thead className="bg-brand-darkblue text-white text-xs">
          <tr>
            {columns.map((col) => (
              <th key={col} className="px-4 py-2">{col}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => {
            const rowKey = Object.values(row).join('-') || `row-${i}`;
            return (
              <tr key={rowKey} className={`text-xs ${i % 2 === 0 ? "bg-white" : "bg-brand-light"}`}>
                {columns.map((col) => (
                  <td key={col} className="px-4 py-2">{row[col]}</td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
