import { MESSAGES } from '../config/messages';

interface FileInfoDisplayProps {
  file: {
    table_name?: string;
    sessionID?: string;
    createdDate?: string;
    lastUpdateDate?: string;
    dataset_id?: string;
    project_id?: string;
    rows_uploaded?: number;
    duplicate_count?: number;
  };
  formatDate: (date: string) => string;
}

export default function FileInfoDisplay({ file, formatDate }: FileInfoDisplayProps) {
  const hasDuplicates = (file.duplicate_count ?? 0) > 0;

  return (
    <div className="border border-gray-200 rounded-lg p-4 m-4 space-y-2 bg-gray-50">
      <p className="text-sm font-semibold text-brand-darkblue">{file.table_name || MESSAGES.DEFAULTS.NA}</p>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-1 text-sm text-brand-charcoal">
        <div className="space-y-1">
          <p><span className="font-medium">Session ID:</span> {file.sessionID || MESSAGES.DEFAULTS.NA}</p>
          <p><span className="font-medium">Created:</span> {formatDate(file.createdDate || '')}</p>
          <p><span className="font-medium">Last Updated:</span> {formatDate(file.lastUpdateDate || '')}</p>
          <p>
            <span className="font-medium">Duplicate Rows:</span>{' '}
            <span className={hasDuplicates ? 'text-red-600 font-semibold' : 'text-green-600'}>
              {file.duplicate_count ?? 0}
            </span>
          </p>
        </div>
        <div className="space-y-1">
          <p><span className="font-medium">Dataset:</span> {file.dataset_id || MESSAGES.DEFAULTS.NA}</p>
          <p><span className="font-medium">Project:</span> {file.project_id || MESSAGES.DEFAULTS.NA}</p>
          <p><span className="font-medium">Rows Uploaded:</span> {file.rows_uploaded ? file.rows_uploaded.toLocaleString() : '0'}</p>
        </div>
      </div>
    </div>
  );
}
