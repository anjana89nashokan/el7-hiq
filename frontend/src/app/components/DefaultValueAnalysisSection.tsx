interface DefaultValueAnalysisSectionProps {
  readonly data: any;
}

export default function DefaultValueAnalysisSection({ data }: DefaultValueAnalysisSectionProps) {

  // Validate root level
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    return <div className="mt-4 text-gray-500">Invalid or empty data</div>;
  }

  const entries = Object.entries(data);

  if (entries.length === 0) {
    return <div className="mt-4 text-gray-500">No data available</div>;
  }

  // Extract first valid object for table headers
  const firstValidEntry = entries.find(
    ([, value]) => value && typeof value === "object" && !Array.isArray(value)
  );

  if (!firstValidEntry) {
    return <div className="mt-4 text-gray-500">Data format not supported</div>;
  }

  const firstEntryObj = firstValidEntry[1];

  return (
    <div className="mt-4">
      <div
        className={`overflow-x-auto border border-gray-300 ${
          entries.length > 10 ? "max-h-96 overflow-y-auto" : ""
        }`}
      >
        <table className="min-w-full border-collapse border border-gray-300 text-sm">
          <thead className="bg-gray-100 sticky top-[-1px] z-10">
            <tr>
              <th className="border border-gray-300 px-2 py-1">Column</th>

              {Object.keys(firstEntryObj as object).map((key) => (
                <th key={key} className="border border-gray-300 px-2 py-1 capitalize">
                  {key.replaceAll("_", " ")}
                </th>
              ))}
            </tr>
          </thead>

          <tbody>
            {entries.map(([colName, colData]) => {
              if (!colData || typeof colData !== "object") return null;

              return (
                <tr key={colName}>
                  <td className="border border-gray-300 px-2 py-1 font-medium">
                    {colName}
                  </td>

                  {Object.keys(firstEntryObj as object).map((key, idx) => (
                    <td key={`${colName}-${idx}`} className="border border-gray-300 px-2 py-1">
                      {String((colData as any)[key] ?? "")}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
