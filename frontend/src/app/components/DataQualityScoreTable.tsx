import React, { useState, useEffect } from "react";
import { ChevronRight, ChevronDown, Info, X } from "lucide-react";
import { dimensionInfo } from "../data/columnInfo";
import type { DimensionKey } from "../data/columnInfo";

interface DataQualityScoreTableProps {
  data: any;
}

const DataQualityScoreTable: React.FC<DataQualityScoreTableProps> = ({ data }) => {
  const [expandedRows, setExpandedRows] = useState<Record<string, boolean>>({});
  const [modalOpen, setModalOpen] = useState(false);
  const [selectedDimension, setSelectedDimension] = useState<string>("");

  const openModal = (dimension: string) => {
    setSelectedDimension(dimension);
    setModalOpen(true);
  };

  const closeModal = () => {
    setModalOpen(false);
    setSelectedDimension("");
  };

  const toggleExpand = (dimension: string) => {
    setExpandedRows((prev) => ({
      ...prev,
      [dimension]: !prev[dimension],
    }));
  };

  useEffect(() => {
    if (modalOpen) {
      document.body.style.overflow = "hidden";   // disable scroll
    } else {
      document.body.style.overflow = "auto";     // enable scroll back
    }

    // cleanup (in case component unmounts while modal is open)
    return () => {
      document.body.style.overflow = "auto";
    };
  }, [modalOpen]);


  if (!data) return null;

  const dimensionEntries = Object.entries(data.dimension_scores || {});

  return (
    <div className="mt-4">
      {/* Outer table wrapper */}
      <div
        className={`overflow-x-auto border border-gray-300 ${dimensionEntries.length > 10 ? "max-h-96 overflow-y-auto" : ""
          }`}
      >
        <table className="min-w-full border-collapse border border-gray-300 text-sm">
          <thead className="bg-gray-100 sticky top-[-1px] z-10">
            <tr>
              <th className="border border-gray-300 px-3 py-2 text-left">Dimension</th>
              <th className="border border-gray-300 px-3 py-2 text-left">Score</th>
              <th className="border border-gray-300 px-3 py-2 text-left w-16">Expand</th>
            </tr>
          </thead>

          <tbody>
            {dimensionEntries.map(([dimension, score]: any) => (
              <React.Fragment key={dimension}>
                <tr className="hover:bg-gray-50 transition">
                  <td className="border border-gray-300 px-3 py-2">
                    <div className="flex items-center gap-2 capitalize">
                      {dimension}
                      <button
                        onClick={() => openModal(dimension)}
                        className="text-brand-darkblue cursor-pointer"
                        title="View dimension information"
                      >
                        <Info size={14} />
                      </button>
                    </div>
                  </td>

                  <td className="border border-gray-300 px-3 py-2">{score}</td>

                  <td className="border border-gray-300 px-3 py-2 text-center">
                    <button
                      type="button"
                      onClick={() => toggleExpand(dimension)}
                      className="text-brand-darkblue hover:text-brand-darkblue transition"
                      title={expandedRows[dimension] ? "Collapse" : "Expand details"}
                    >
                      {expandedRows[dimension] ? (
                        <ChevronDown size={16} />
                      ) : (
                        <ChevronRight size={16} />
                      )}
                    </button>
                  </td>
                </tr>

                {expandedRows[dimension] && (
                  <tr>
                    <td colSpan={3} className="border border-gray-300 bg-white px-4 py-3">
                      <div className="text-xs text-gray-800">
                        <p className="font-semibold mb-2">
                          Per-column {dimension} scores:
                        </p>

                        <div
                          className={`border border-gray-300 ${Object.keys(data.per_column_scores || {}).length > 10
                            ? "max-h-60 overflow-y-auto"
                            : ""
                            }`}
                        >
                          <table className="min-w-full border-collapse border border-gray-300 text-sm">
                            <thead className="bg-gray-100 sticky top-[-1px] z-10">
                              <tr>
                                <th className="border border-gray-300 px-2 py-1">Column</th>
                                <th className="border border-gray-300 px-2 py-1">Score</th>
                              </tr>
                            </thead>

                            <tbody>
                              {Object.entries(data.per_column_scores || {}).map(
                                ([column, colData]: any) => (
                                  <tr key={column}>
                                    <td className="border border-gray-300 px-2 py-1 font-medium">
                                      {column}
                                    </td>
                                    <td className="border border-gray-300 px-2 py-1">
                                      {colData.dimension_scores?.[dimension] ?? "N/A"}
                                    </td>
                                  </tr>
                                )
                              )}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
              </React.Fragment>
            ))}
          </tbody>
        </table>
      </div>

      {/* Modal */}
      {modalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm transition-opacity">

          {/* Modal Container */}
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-5xl mx-4 animate-[fadeIn_0.2s_ease-out,scaleIn_0.2s_ease-out]">

            {/* Header */}
            <div className="flex justify-between items-center px-8 py-3 border-b border-gray-200">
              <h3 className="text-base font-bold text-brand-darkblue capitalize">{selectedDimension}</h3>
              <button
                onClick={closeModal}
                className="text-gray-500 hover:text-gray-700 transition cursor-pointer"
              >
                <X size={26} />
              </button>
            </div>

            {/* Content */}
            <div className="px-8 py-4 max-h-[75vh] overflow-y-auto">

              {(() => {
                const dimKey = selectedDimension.toLowerCase() as DimensionKey;
                const htmlContent =
                  dimensionInfo[dimKey] || dimensionInfo.default;

                return (
                  <div
                    className="prose prose-sm max-w-none text-gray-700 leading-relaxed modal-styling"
                    dangerouslySetInnerHTML={{
                      __html: htmlContent.replace(/\n/g, "")
                    }}
                  />
                );
              })()}
            </div>
          </div>
        </div>
      )}

    </div>
  );
};

export default DataQualityScoreTable;
