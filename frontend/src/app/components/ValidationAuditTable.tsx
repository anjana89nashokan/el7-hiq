import React, { useState } from 'react';

interface ValidationAuditLog {
  system_finding: string;
  status: string;
  column_name: string;
  check_type: string;
  vendor_claim: string;
}

interface ValidationAuditTableProps {
  validationData: ValidationAuditLog[];
  onSave?: (editedData: ValidationAuditLog[]) => Promise<any>;
  onUpload?: () => void;
  isDdPresent?: boolean;
}

// interface DataDictionaryItem {
//   field_name: string;
//   data_type: string;
//   business_name: string;
//   field_description: string;
//   nullable: string;
//   default_value: string;
//   length: number;
//   primary_key: string;
//   foreign_key: string;
//   precision: number;
//   [key: string]: any;
// }

const ValidationAuditTable: React.FC<ValidationAuditTableProps> = ({ validationData, onSave }) => {
  const [isGenerating, setIsGenerating] = useState(false);

  // function getStoredSession() {
  //   const sessionId = sessionStorage.getItem('session_id');
  //   const appName = sessionStorage.getItem('app_name');
  //   const userId = sessionStorage.getItem('user_id');
  //   return { sessionId, appName, userId };
  // }

  const handleGenerateDataDictionary = async () => {
    if (onSave) {
      setIsGenerating(true);
      try {
        await onSave(validationData);
      } catch (error) {
        console.error('Error generating data dictionary:', error);
      } finally {
        setIsGenerating(false);
      }
    }
  };

  const getStatusColor = (status: string) => {
    switch (status.toLowerCase()) {
      case 'match':
        return 'bg-green-100 text-green-800';
      case 'mismatch':
        return 'bg-red-100 text-red-800';
      default:
        return 'bg-gray-100 text-gray-800';
    }
  };

  return (
    <div className="mt-6">
      <div className="flex justify-between items-center mb-4">
        <h3 className="text-lg font-semibold text-gray-800">Validation Audit Log</h3>
        <button
          onClick={handleGenerateDataDictionary}
          disabled={isGenerating}
          className="flex items-center gap-2 px-4 py-2 bg-brand-primary text-white rounded-lg hover:bg-brand-primary-hover transition-colors disabled:opacity-50"
        >
          {isGenerating ? (
            <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white"></div>
          ) : null}
          {isGenerating ? 'Generating...' : 'Generate Data Dictionary'}
        </button>
      </div>

      <div className="overflow-x-auto">
        <table className="min-w-full bg-white border border-gray-200 rounded-lg">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider border-b">
                Column Name
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider border-b">
                Check Type
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider border-b">
                System Finding
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider border-b">
                Vendor Claim
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider border-b">
                Status
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200">
            {validationData.map((row, index) => (
              <tr key={index} className="hover:bg-gray-50">
                <td className="px-4 py-3 text-sm text-gray-900">
                  {row.column_name}
                </td>
                <td className="px-4 py-3 text-sm text-gray-900">
                  {row.check_type}
                </td>
                <td className="px-4 py-3 text-sm text-gray-900">
                  {row.system_finding}
                </td>
                <td className="px-4 py-3 text-sm text-gray-900">
                  {row.vendor_claim}
                </td>
                <td className="px-4 py-3 text-sm">
                  <span className={`px-2 py-1 rounded-full text-xs font-medium ${getStatusColor(row.status)}`}>
                    {row.status}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default ValidationAuditTable;