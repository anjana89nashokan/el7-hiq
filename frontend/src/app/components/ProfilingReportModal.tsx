import { X } from "lucide-react";

interface ProfilingReportModalProps {
  isOpen: boolean;
  onClose: () => void;
  reportUrl: string;
}

export default function ProfilingReportModal({ isOpen, onClose, reportUrl }: ProfilingReportModalProps) {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black bg-opacity-70 flex items-center justify-center z-50">
      <div className="relative w-full h-full flex justify-center items-center">
        <button
          onClick={onClose}
          className="absolute top-4 right-4 bg-white text-black rounded-full px-3 py-1 shadow-lg hover:bg-gray-200"
        >
          <X size={16} />
        </button>
        <iframe
          src={reportUrl}
          className="w-11/12 h-5/6 border rounded-lg bg-white"
          title="Profiling Report"
        />
      </div>
    </div>
  );
}
