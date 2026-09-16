import { useEffect, useState } from "react";

/* ---------- Types ---------- */

interface RichNode {
  tagName?: string;
  type?: string;
  value?: string;
  children?: RichNode[];
}

interface TableWithExportProps {
  node?: RichNode;
  stepTitle?: string;
}

const TableWithExport = ({ node, stepTitle }: TableWithExportProps) => {
  const [tableData, setTableData] = useState<Record<string, string>[]>([]);
  const [headers, setHeaders] = useState<string[]>([]);

  /* ---------- Shared safe text extractor ---------- */

  const extractText = (n?: RichNode): string => {
    if (!n) return "";

    if (n.type === "text") {
      return n.value ?? "";
    }

    return n.children?.map(extractText).join("") ?? "";
  };

  /* ---------- Extract table structure ---------- */

  useEffect(() => {
    if (!node?.children) {
      setHeaders([]);
      setTableData([]);
      return;
    }

    const thead = node.children.find((c) => c.tagName === "thead");
    const tbody = node.children.find((c) => c.tagName === "tbody");

    if (!thead?.children || !tbody?.children) {
      setHeaders([]);
      setTableData([]);
      return;
    }

    /* ---- headers ---- */

    const headerRow = thead.children.find((c) => c.tagName === "tr");

    const extractedHeaders =
      headerRow?.children
        ?.filter((c) => c.tagName === "th")
        .map((th) => extractText(th)) ?? [];

    setHeaders(extractedHeaders);

    /* ---- body rows ---- */

    const dataRows =
      tbody.children
        ?.filter((c) => c.tagName === "tr")
        .map((tr) => {
          const cells =
            tr.children
              ?.filter((c) => c.tagName === "td")
              .map((td) => extractText(td)) ?? [];

          const obj: Record<string, string> = {};

          extractedHeaders.forEach((h, i) => {
            obj[h] = cells[i] ?? "";
          });

          return obj;
        }) ?? [];

    setTableData(dataRows);
  }, [node]);

  /* ---------- Nothing to show ---------- */

  if (!headers.length || !tableData.length) {
    return null;
  }

  /* ---------- Export ---------- */

  const exportToCSV = () => {
    const csvContent = [
      headers.map((h) => `"${h}"`).join(","),
      ...tableData.map((row) =>
        headers.map((h) => `"${row[h] ?? ""}"`).join(",")
      ),
    ].join("\n");

    const timestamp = new Date()
      .toISOString()
      .slice(0, 19)
      .replaceAll(":", "-")
      .replaceAll(".", "-");

    const stepName =
      stepTitle
        ?.toLowerCase()
        .replaceAll(/[^a-z0-9]/g, "-") ?? "table-export";

    const filename = `${stepName}-${timestamp}.csv`;

    const blob = new Blob([csvContent], { type: "text/csv" });
    const url = globalThis.URL.createObjectURL(blob);

    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.append(a);
    a.click();
    a.remove();

    globalThis.URL.revokeObjectURL(url);
  };

  /* ---------- UI ---------- */

  return (
    <div className="my-6">
      <div className="overflow-hidden rounded-2xl shadow-lg border border-gray-200 overflow-x-auto">
        <table className="min-w-full border-collapse">
          <thead className="bg-gradient-to-r from-brand-primary to-brand-primary text-white">
            <tr>
              {headers.map((header) => (
                <th
                  key={header}
                  className="border border-gray-200 px-4 py-2 text-left"
                >
                  {header}
                </th>
              ))}
            </tr>
          </thead>

          <tbody>
            {tableData.map((row) => (
              <tr key={JSON.stringify(row)} className="hover:bg-gray-50 transition-colors">
                {headers.map((header) => (
                  <td
                    key={header}
                    className="border border-gray-200 px-4 py-2 text-sm text-gray-700"
                  >
                    {row[header]}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-4 flex justify-end">
        <button
          onClick={exportToCSV}
          className="px-4 py-2 bg-brand-primary hover:bg-brand-primary-hover text-white font-medium rounded-lg shadow-md transition"
        >
          Export as CSV
        </button>
      </div>
    </div>
  );
};

export default TableWithExport;
