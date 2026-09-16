import { AlertTriangle } from "lucide-react";
import EmptyState from "./EmptyState";
import { MESSAGES } from "../config/messages";

interface NoDataViewProps {
  failedUploads?: { filename: string; error: string }[];
}

export default function NoDataView({ failedUploads }: NoDataViewProps) {
  return (
    <div className="flex flex-col items-center justify-center min-h-screen">
      <div className="text-center">
        <EmptyState
          icon={AlertTriangle}
          title={MESSAGES.NO_DATA.TITLE}
          description={MESSAGES.NO_DATA.NO_UPLOADS}
          iconSize={64}
          iconColor="text-red-500"
          titleColor="text-gray-700"
          descColor="text-gray-500"
        />
        {failedUploads && failedUploads.length > 0 && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-4 max-w-md mt-4">
            <h3 className="text-red-700 font-medium mb-2">Failed Uploads:</h3>
            <ul className="text-sm text-red-600 space-y-1">
              {failedUploads
                .filter((failed: any) => failed != null)
                .map((failed: any) => (
                  <li key={failed.filename}>
                    <strong>{failed.filename}:</strong> {failed.error}
                  </li>
                ))}
            </ul>
          </div>
        )}
        <button
          onClick={() => globalThis.history.back()}
          className="mt-4 bg-brand-darkblue hover:bg-brand-darkblue/75 text-white px-6 py-2 rounded-lg transition-colors"
        >
          {MESSAGES.BUTTONS.GO_BACK}
        </button>
      </div>
    </div>
  );
}
