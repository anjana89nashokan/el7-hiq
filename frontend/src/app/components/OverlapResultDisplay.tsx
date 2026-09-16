import type { OverlapResult } from '../state/reducers/dartReducer';

interface Props {
  result: OverlapResult;
}

export default function OverlapResultDisplay({ result }: Props) {
  const percentColor =
    result.data_overlap_percent >= 75 ? 'text-green-600' :
    result.data_overlap_percent >= 40 ? 'text-yellow-600' : 'text-red-600';

  return (
    <div className="mt-3 border border-gray-200 rounded p-3 bg-gray-50 text-xs space-y-3">
      <p className="font-semibold text-gray-700">Overlap Result</p>

      <div className="bg-white border border-gray-200 rounded p-2">
        <p className="text-gray-500 font-medium mb-0.5">Summary</p>
        <p className="text-gray-700">{result.overlap_summary}</p>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <div className="bg-white border border-gray-200 rounded p-2">
          <p className="text-gray-500 font-medium mb-0.5">Source Values Checked</p>
          <p className="text-lg font-bold text-gray-800">{result.source_values_checked}</p>
        </div>
        <div className="bg-white border border-gray-200 rounded p-2">
          <p className="text-gray-500 font-medium mb-0.5">Data Overlap</p>
          <p className={`text-lg font-bold ${percentColor}`}>{result.data_overlap_percent}%</p>
        </div>
      </div>

      {result.sample_matching_values.length > 0 && (
        <div className="bg-white border border-green-200 rounded p-2">
          <p className="text-gray-500 font-medium mb-1">Sample Matching Values</p>
          <div className="flex flex-wrap gap-1">
            {result.sample_matching_values.map((v, i) => (
              <span key={i} className="bg-green-100 text-green-700 font-mono px-1.5 py-0.5 rounded">{v}</span>
            ))}
          </div>
        </div>
      )}

      {result.sample_non_matching_values.length > 0 && (
        <div className="bg-white border border-red-200 rounded p-2">
          <p className="text-gray-500 font-medium mb-1">Sample Non-Matching Values</p>
          <div className="flex flex-wrap gap-1">
            {result.sample_non_matching_values.map((v, i) => (
              <span key={i} className="bg-red-100 text-red-600 font-mono px-1.5 py-0.5 rounded">{v}</span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
