

interface TableSummarySectionProps {
  data: any;
}

export default function TableSummarySection({ data }: TableSummarySectionProps) {
  return (
    <div className={`overflow-x-auto mt-4 ${Object.keys(data || {}).length > 10 ? "max-h-96 overflow-y-auto" : ""}`}>
      <table className="min-w-full border-collapse border border-gray-300 text-sm">
        <thead className="bg-gray-100 sticky top-[-1px] z-10">
          <tr>
            {Object.keys(data).map((key) => (
              <th key={key} className="border border-gray-300 px-3 py-2 text-left capitalize">
                {key.replaceAll("_", " ")}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          <tr>
            {Object.values(data).map((value: any, idx: number) => (
              <td key={idx} className="border border-gray-300 px-3 py-2">
                {String(value)}
              </td>
            ))}
          </tr>
        </tbody>
      </table>
    </div>
  );
}