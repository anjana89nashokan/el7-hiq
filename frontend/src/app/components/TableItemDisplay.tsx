import AccordionSection from './AccordionSection';
import TableSummarySection from './TableSummarySection';
import ColumnAnalysisSection from './ColumnAnalysisSection';
import DefaultValueAnalysisSection from './DefaultValueAnalysisSection';
import DataQualityScoreTable from './DataQualityScoreTable';
import { Download } from 'lucide-react';

interface TableItemDisplayProps {
  readonly tableItem: any;
  readonly index: number;
  readonly accordionStates: { [key: string]: boolean };
  readonly toggleAccordion: (key: string) => void;
  readonly sampleDuplicates?: Record<string, any>[];
  readonly allDuplicates?: Record<string, any>[];
  readonly sheetName?: string;
}

function downloadDuplicatesExcel(rows: Record<string, any>[], sheetName: string) {
  import('xlsx').then((XLSX) => {
    const ws = XLSX.utils.json_to_sheet(rows);
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, sheetName.slice(0, 31));
    XLSX.writeFile(wb, `${sheetName}_duplicates.xlsx`);
  });
}

export default function TableItemDisplay({ tableItem, index, accordionStates, toggleAccordion, sampleDuplicates, allDuplicates, sheetName }: TableItemDisplayProps) {
  const formatCellValue = (val: any) => {
    if (Array.isArray(val)) {
      return val.join(", ");
    }
    if (typeof val === "object") {
      return JSON.stringify(val);
    }
    return String(val ?? "");
  };

  return (
    <div className="mb-6 space-y-4">
      <h3 className="text-brand-darkblue font-semibold">Profiling Summary</h3>
      <h4 className="text-brand-darkblue mb-3">
        <span className="font-semibold">Table Reference:</span> {tableItem.table_reference || "N/A"}
      </h4>

      {tableItem.table_summary && (
        <AccordionSection
          title="Table Summary"
          isOpen={accordionStates[`table-${index}-summary`] ?? false}
          onToggle={() => toggleAccordion(`table-${index}-summary`)}
        >
          <TableSummarySection data={tableItem.table_summary} />
        </AccordionSection>
      )}

      {tableItem.column_analysis && (
        <AccordionSection
          title="Column Analysis"
          isOpen={accordionStates[`table-${index}-column`] ?? false}
          onToggle={() => toggleAccordion(`table-${index}-column`)}
        >
          <ColumnAnalysisSection data={tableItem.column_analysis} />
        </AccordionSection>
      )}

      {sampleDuplicates && sampleDuplicates.length > 0 && (() => {
        const cols = Object.keys(sampleDuplicates[0]);
        return (
          <AccordionSection
            title="Sample Duplicates"
            isOpen={accordionStates[`table-${index}-duplicates`] ?? false}
            onToggle={() => toggleAccordion(`table-${index}-duplicates`)}
          >
            <div className="pt-3 space-y-2">
              {allDuplicates && allDuplicates.length > 0 && (
                <div className="flex justify-end">
                  <button
                    onClick={() => downloadDuplicatesExcel(allDuplicates, sheetName || 'duplicates')}
                    className="flex items-center gap-1 text-xs text-brand-darkblue hover:text-brand-darkblue/75 border border-brand-darkblue rounded px-2 py-1 transition-colors cursor-pointer"
                  >
                    <Download size={13} /> Download Excel
                  </button>
                </div>
              )}
              <div className="overflow-x-auto rounded border border-gray-200">
                <table className="min-w-full border-collapse border border-gray-300 text-xs">
                  <thead className="bg-gray-100 sticky top-[-1px]">
                    <tr>
                      {cols.map((col) => (
                        <th key={col} className="border border-gray-300 px-2 py-1 text-left capitalize">{col.replaceAll('_', ' ')}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {sampleDuplicates.map((row, i) => (
                      <tr key={i} className="border-b border-gray-100 hover:bg-gray-50">
                        {cols.map((col) => (
                          <td key={col} className="border border-gray-300 px-2 py-1 text-gray-700">{String(row[col] ?? '')}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </AccordionSection>
        );
      })()}

      {tableItem.data_quality_score && (
        <AccordionSection
          title="Data Quality Score"
          isOpen={accordionStates[`table-${index}-quality`] ?? false}
          onToggle={() => toggleAccordion(`table-${index}-quality`)}
        >
          <DataQualityScoreTable data={tableItem.data_quality_score} />
        </AccordionSection>
      )}

      {tableItem.default_value_analysis && (
        <AccordionSection
          title="Default Value Analysis"
          isOpen={accordionStates[`table-${index}-default`] ?? false}
          onToggle={() => toggleAccordion(`table-${index}-default`)}
        >
          <DefaultValueAnalysisSection data={tableItem.default_value_analysis} />
        </AccordionSection>
      )}

      {tableItem.enhanced_analysis && (
        <AccordionSection
          title="Enhanced Analysis"
          isOpen={accordionStates[`table-${index}-enhanced`] ?? false}
          onToggle={() => toggleAccordion(`table-${index}-enhanced`)}
        >
        <div className="mt-3 space-y-4">
              <div className="mt-3 space-y-4">

                {/* Table Context */}
                {tableItem.enhanced_analysis.table_context && (
                  <div>
                    <h6 className="font-semibold text-sm mb-2 text-gray-800">Table Context</h6>
                    <div className="overflow-x-auto border border-gray-200">
                      <table className="min-w-full border-collapse border border-gray-300 text-xs">
                        <thead className="bg-gray-100 sticky top-[-1px]">
                          <tr>
                            <th className="border border-gray-300 px-2 py-1 text-left">Property</th>
                            <th className="border border-gray-300 px-2 py-1 text-left">Value</th>
                          </tr>
                        </thead>
                        <tbody>
                          {Object.entries(tableItem.enhanced_analysis.table_context).map(
                            ([key, val]: any) => (
                              <tr key={key}>
                                <td className="border border-gray-300 px-2 py-1 font-medium capitalize">
                                  {key.replaceAll("_", " ")}
                                </td>
                                <td className="border border-gray-300 px-2 py-1">
                                  {typeof val === "object" ? JSON.stringify(val, null, 2) : String(val)}
                                </td>
                              </tr>
                            )
                          )}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {/* Primary Key Recommendations */}
                {tableItem.enhanced_analysis.primary_key_recommendations &&
                  Array.isArray(tableItem.enhanced_analysis.primary_key_recommendations) &&
                  tableItem.enhanced_analysis.primary_key_recommendations.length > 0 && (
                    <div>
                      <h6 className="font-semibold text-sm mb-2 text-gray-800">
                        Primary Key Recommendations
                      </h6>
                      <div
                        className={`overflow-x-auto border border-gray-200 ${tableItem.enhanced_analysis.primary_key_recommendations.length > 10
                          ? "max-h-96 overflow-y-auto"
                          : ""
                          }`}
                      >
                        <table className="min-w-full border-collapse border border-gray-300 text-xs">
                          <thead className="bg-gray-100 sticky top-[-1px]">
                            <tr>
                              {Object.keys(
                                tableItem.enhanced_analysis.primary_key_recommendations[0]
                              ).map((key) => (
                                <th key={key} className="border border-gray-300 px-2 py-1 capitalize">
                                  {key.replaceAll("_", " ")}
                                </th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            {tableItem.enhanced_analysis.primary_key_recommendations.map(
                              (row: any, i: number) => (
                                <tr key={`pk-rec-${JSON.stringify(row).slice(0, 50)}-${i}`}>
                                  {Object.values(row).map((val: any, idx: number) => (
                                    <td key={`row-${i}-col-${idx}`} className="border border-gray-300 px-2 py-1">
                                      {formatCellValue(val)}
                                    </td>
                                  ))}
                                </tr>
                              )
                            )}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}

                {/* LLM Suggested Combos */}
                {tableItem.enhanced_analysis.llm_suggested_combos && (
                  <div>
                    <h6 className="font-semibold text-sm mb-2 text-gray-800">LLM Suggested Combos</h6>
                    {["two_column", "three_column", "four_column"].map((key) => {
                      const combos = tableItem.enhanced_analysis.llm_suggested_combos[key];
                      if (!combos || combos.length === 0) return null;
                      return (
                        <div key={key} className="mb-3">
                          <h6 className="text-xs font-medium mb-1 text-gray-700 capitalize">
                            {key.replaceAll("_", " ")}
                          </h6>
                          <div
                            className={`overflow-x-auto border border-gray-200 ${combos.length > 10 ? "max-h-60 overflow-y-auto" : ""
                              }`}
                          >
                            <table className="min-w-full border-collapse border border-gray-300 text-xs">
                              <thead className="bg-gray-100 sticky top-[-1px]">
                                <tr>
                                  <th className="border border-gray-300 px-2 py-1">Suggested Columns</th>
                                </tr>
                              </thead>
                              <tbody>
                                {combos.map((arr: any, i: number) => (
                                  <tr key={`${key}-combo-${Array.isArray(arr) ? arr.join('-') : String(arr)}-${i}`}>
                                    <td className="border border-gray-300 px-2 py-1">
                                      {Array.isArray(arr) ? arr.join(", ") : String(arr)}
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </div> 
        </AccordionSection>
      )}
    </div>
  );
}
