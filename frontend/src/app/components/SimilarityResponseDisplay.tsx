import React from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface SimilarityResponseDisplayProps {
  response: string;
}

const SimilarityResponseDisplay: React.FC<SimilarityResponseDisplayProps> = ({
  response,
}) => {
  if (!response) return null;

  let parsedData;
  try {
    parsedData = JSON.parse(response);
  } catch {
    return <div className="p-4 text-red-500">Error parsing response data.</div>;
  }

  const data = parsedData[0];
  const textResponse = data?.text_response || "";
  const toolResponse = data?.tool_response;

  return (
    <div className="mt-6 space-y-6 max-w-full overflow-hidden">
      <h3 className="text-xl font-semibold text-gray-800 px-1">
        Similarity Check Analysis
      </h3>

      {/* 1. ANALYSIS SECTION */}
      <div className="bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden">
        <div className="bg-gray-50 border-b border-gray-200 px-6 py-3">
          <h4 className="text-xs font-bold text-gray-500 uppercase tracking-wider">
            Detailed Report
          </h4>
        </div>

        <div className="p-6 overflow-x-auto">
          {/* Custom Markdown Styling */}
          <div className="prose prose-sm max-w-none prose-slate">
            <Markdown
              remarkPlugins={[remarkGfm]}
              components={{
                // Custom styling for the table container
                table: ({ node, ...props }) => (
                  <div className="my-6 w-full overflow-x-auto border border-gray-200 rounded-lg">
                    <table
                      className="min-w-[1000px] w-full divide-y divide-gray-200 border-collapse"
                      {...props}
                    />
                  </div>
                ),
                // Style table headers
                th: ({ node, ...props }) => (
                  <th
                    className="bg-gray-50 px-4 py-3 text-left text-xs font-bold text-gray-700 uppercase tracking-wider border-b border-gray-200 whitespace-nowrap"
                    {...props}
                  />
                ),
                // Style table cells
                td: ({ node, ...props }) => (
                  <td
                    className="px-4 py-4 text-sm text-gray-600 border-b border-gray-100"
                    {...props}
                  />
                ),
                // Style headers inside markdown
                h3: ({ node, ...props }) => (
                  <h3
                    className="text-lg font-bold text-gray-800 mt-8 mb-4 pb-2 border-b border-gray-100"
                    {...props}
                  />
                ),
                h1: ({ node, ...props }) => (
                  <h1
                    className="text-xl font-extrabold text-brand-darkblue mb-6"
                    {...props}
                  />
                ),
              }}
            >
              {textResponse}
            </Markdown>
          </div>
        </div>
      </div>

      {/* 2. SUMMARY SECTION (Grid Format) */}
      {toolResponse && toolResponse.summary && (
        <div className="bg-brand-surface border border-teal-100 rounded-xl overflow-hidden shadow-sm">
          <div className="bg-brand-darkblue px-6 py-3">
            <h4 className="text-sm font-bold text-white uppercase tracking-widest">
              Summary Statistics
            </h4>
          </div>
          <div className="p-6 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {Object.entries(toolResponse.summary).map(([key, value]) => {
              // Skip the long description for the grid
              if (key === "best_match_description") return null;

              return (
                <div key={key} className="flex flex-col">
                  <span className="text-xs font-semibold text-font-blue uppercase tracking-wide opacity-70">
                    {key.replace(/_/g, " ")}
                  </span>
                  <span className="text-2xl font-bold text-brand-darkblue mt-1">
                    {typeof value === "number" && value < 1 && value > 0
                      ? (value * 100).toFixed(1) + "%"
                      : String(value)}
                  </span>
                </div>
              );
            })}
          </div>

          {/* Best Match Description at the bottom of summary */}
          {toolResponse.summary.best_match_description && (
            <div className="px-6 py-4 bg-teal-100/50 border-t border-teal-100">
              <p className="text-sm text-font-blue leading-relaxed">
                <span className="font-bold">Summary Logic:</span>{" "}
                {toolResponse.summary.best_match_description}
              </p>
            </div>
          )}
        </div>
      )}

      {/* 3. POTENTIAL MATCHES TABLE (Standard Tool Response) */}
      {toolResponse &&
        toolResponse.potential_matches &&
        toolResponse.potential_matches.length > 0 && (
          <div className="bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden">
            <div className="bg-green-600 px-6 py-3 text-white">
              <h4 className="text-sm font-bold uppercase tracking-widest">
                Calculated Potential Matches
              </h4>
            </div>
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    {Object.keys(toolResponse.potential_matches[0]).map(
                      (key) => (
                        <th
                          key={key}
                          className="px-6 py-3 text-left text-xs font-bold text-gray-500 uppercase tracking-wider"
                        >
                          {key.replace(/_/g, " ")}
                        </th>
                      ),
                    )}
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200">
                  {toolResponse.potential_matches.map(
                    (match: any, index: number) => (
                      <tr
                        key={index}
                        className="hover:bg-gray-50 transition-colors"
                      >
                        {Object.values(match).map((value: any, i: number) => (
                          <td
                            key={i}
                            className="px-6 py-4 whitespace-nowrap text-sm text-gray-600"
                          >
                            {typeof value === "number"
                              ? value.toFixed(2)
                              : String(value)}
                          </td>
                        ))}
                      </tr>
                    ),
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}
    </div>
  );
};

export default SimilarityResponseDisplay;