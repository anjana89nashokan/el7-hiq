import { useMemo } from "react";

interface ColumnAnalysisSectionProps {
  data: any;
}

export default function ColumnAnalysisSection({ data }: ColumnAnalysisSectionProps) {
  // Build a global ordered key list from ALL rows
  const orderedKeys = useMemo(() => {
    if (!data) return [];

    const keySet = new Set<string>();

    // Collect keys from all columns
    Object.values(data).forEach((col: any) => {
      Object.keys(col).forEach((k) => keySet.add(k));
    });

    // Convert to array and preserve stable order
    return Array.from(keySet);
  }, [data]);

  return (
    <div
      className={`overflow-x-auto mt-4 ${
        Object.keys(data || {}).length > 10 ? "max-h-96 overflow-y-auto" : ""
      }`}
    >
      <table className="min-w-full border-collapse border border-gray-300 text-sm">
        <thead className="bg-gray-100 sticky top-[-1px] z-10">
          <tr>
            <th className="border border-gray-300 text-left px-2 py-1">Column</th>

            {orderedKeys.map((key) => (
              <th key={key} className="border border-gray-300 px-2 py-1 capitalize">
                {key.replaceAll("_", " ")}
              </th>
            ))}
          </tr>
        </thead>

        <tbody>
          {Object.entries(data).map(([colName, colData]: any) => (
            <tr key={colName}>
              <td className="border border-gray-300 px-2 py-1 font-medium">
                {colName}
              </td>

              {orderedKeys.map((k) => {
                const val = colData[k];
                return (
                  <td key={k} className="border border-gray-300 px-2 py-1">
                    {Array.isArray(val) ? val.join(", ") : String(val ?? "")}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
